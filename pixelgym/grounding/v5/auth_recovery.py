"""Owner-driven OAuth renewal without replaying model attempts or weakening identity."""

from __future__ import annotations

import json
import math
import os
import shlex
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

AUTHENTICATION_RECOVERY_VERSION = "pixelgym-owner-oauth-recovery-v1"


class AuthRecoveryStopped(RuntimeError):
    """A campaign stop or unresolved process forbids credential recovery."""


@dataclass(frozen=True)
class CredentialSnapshot:
    value: str = field(repr=False)
    expires_at: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, str)
            or not self.value.startswith("sk-ant-oat")
            or any(character.isspace() for character in self.value)
            or type(self.expires_at) not in (int, float)
            or not math.isfinite(self.expires_at)
        ):
            raise ValueError("invalid owner OAuth snapshot")


def _private_read(path: Path) -> tuple[str, os.stat_result]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ValueError("owner authentication files must be regular and mode 0600")
        content = stream.read(262_145)
        if len(content) > 262_144:
            raise ValueError("owner authentication file exceeds its size limit")
    return content, metadata


def _unchanged(path: Path, previous: os.stat_result) -> bool:
    current = path.lstat()
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    return all(getattr(previous, name) == getattr(current, name) for name in fields)


def load_oauth_snapshot(env_file: Path) -> CredentialSnapshot:
    """Read the helper's private files as data; never execute shell text or echo it."""

    try:
        directory = env_file.parent.lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) & 0o077
        ):
            raise ValueError("owner authentication directory must be private")
        receipt_file = env_file.with_name("credential-provisioning-check.json")
        source, source_stat = _private_read(env_file)
        receipt_source, receipt_stat = _private_read(receipt_file)
        # The helper replaces the env file before its receipt. Reject a partial
        # replacement, or a file changed while the two records were being read.
        if (
            receipt_stat.st_mtime_ns < source_stat.st_mtime_ns
            or not _unchanged(env_file, source_stat)
            or not _unchanged(receipt_file, receipt_stat)
        ):
            raise ValueError("owner authentication update is incomplete")
        words = shlex.split(source, comments=False, posix=True)
        prefix = "CLAUDE_CODE_OAUTH_TOKEN="
        if len(words) != 1 or not words[0].startswith(prefix):
            raise ValueError("owner authentication file must contain one OAuth assignment")
        receipt = json.loads(receipt_source)
        expiry = receipt["authentication_metadata"]["expires_at_utc"]
        if not isinstance(expiry, str):
            raise TypeError("owner authentication expiry is missing")
        parsed = datetime.fromisoformat(expiry)
        if parsed.tzinfo is None:
            raise ValueError("owner authentication expiry must include its timezone")
        return CredentialSnapshot(words[0][len(prefix) :], parsed.timestamp())
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("owner OAuth files are unavailable or invalid") from None


class OAuthRecoveryGate:
    """One shared renewal gate and runtime clock for every campaign worker.

    Acquire a lease before reserving an attempt or entering a request deadline;
    release it after the bounded send has settled. The injected verifier must
    compare the complete frozen CLI identity as well as verify the same Max
    credential. Its raw result and exceptions are never put in an event.
    """

    def __init__(
        self,
        *,
        initial: CredentialSnapshot,
        load: Callable[[], CredentialSnapshot],
        verify: Callable[[str], dict[str, Any]],
        on_event: Callable[[dict[str, Any]], None],
        stop_requested: Callable[[], bool],
        subprocesses_closed: Callable[[], bool],
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        wait_seconds: float = 0.5,
        minimum_validity_seconds: float = 125.0,
        wait: Callable[[threading.Condition, float], None] | None = None,
    ) -> None:
        if (
            not math.isfinite(wait_seconds)
            or wait_seconds <= 0
            or not math.isfinite(minimum_validity_seconds)
            or minimum_validity_seconds <= 0
        ):
            raise ValueError("authentication wait and validity bounds must be positive")
        self._current = initial
        self._load = load
        self._verify = verify
        self._on_event = on_event
        self._stop_requested = stop_requested
        self._subprocesses_closed = subprocesses_closed
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._wait_seconds = wait_seconds
        self._minimum_validity = minimum_validity_seconds
        self._wait = wait or (lambda condition, seconds: condition.wait(timeout=seconds))
        self._condition = threading.Condition()
        self._started = monotonic()
        self._paused_total = 0.0
        self._pause_started: float | None = None
        self._active = 0
        self._required = False
        self._pause_reason = "expired_or_near_expiry"
        self._leader = False
        self._seen = {initial.value}

    def _check_stop(self) -> None:
        if self._stop_requested():
            raise AuthRecoveryStopped("campaign stop forbids authentication recovery")

    def _valid(self, snapshot: CredentialSnapshot) -> bool:
        return snapshot.expires_at - self._wall_time() >= self._minimum_validity

    def _event(self, name: str, **fields: Any) -> None:
        self._on_event({"event": name, "model_requests": 0, **fields})

    def credential(self) -> str:
        """Return the active secret only for an already leased launch environment."""

        with self._condition:
            return self._current.value

    def elapsed_seconds(self) -> float:
        with self._condition:
            now = self._monotonic()
            paused = self._paused_total
            if self._pause_started is not None:
                paused += now - self._pause_started
            return max(0.0, now - self._started - paused)

    def require_login(self) -> None:
        """Invalidate this generation after a confirmed rejection; never wait here."""

        with self._condition:
            self._required = True
            self._pause_reason = "confirmed_authentication_rejection"
            self._condition.notify_all()

    def end_attempt(self) -> None:
        with self._condition:
            if self._active <= 0:
                raise RuntimeError("authentication attempt lease was not acquired")
            self._active -= 1
            self._condition.notify_all()

    def begin_attempt(self) -> None:
        while True:
            with self._condition:
                self._check_stop()
                if not self._required and self._valid(self._current):
                    self._active += 1
                    return
                self._required = True
                if self._active or self._leader:
                    self._wait(self._condition, self._wait_seconds)
                    continue
                if not self._subprocesses_closed():
                    raise AuthRecoveryStopped(
                        "unsettled subprocess forbids authentication recovery"
                    )
                if self._pause_started is None:
                    self._pause_started = self._monotonic()
                    self._event("auth_pause_started", active_attempts=0, reason=self._pause_reason)
                self._leader = True
            try:
                self._recover()
            finally:
                with self._condition:
                    self._leader = False
                    self._condition.notify_all()

    def _recover(self) -> None:
        load_failed = False
        while True:
            with self._condition:
                self._check_stop()
            try:
                candidate = self._load()
            except (OSError, ValueError, TypeError):
                if not load_failed:
                    self._event("auth_verification_failed", reason="owner_files_invalid")
                load_failed = True
                candidate = None
            if candidate is not None and candidate.value not in self._seen:
                self._seen.add(candidate.value)
                if not self._valid(candidate):
                    self._event("auth_verification_failed", reason="insufficient_validity")
                else:
                    self._event("auth_verification", authentication_reads_attempted=1)
                    try:
                        proof = self._verify(candidate.value)
                        verified = (
                            isinstance(proof, dict)
                            and proof.get("organization_type") == "claude_max"
                            and type(proof.get("model_requests")) is int
                            and proof["model_requests"] == 0
                        )
                    except Exception:  # noqa: BLE001 - never publish provider/credential errors
                        verified = False
                    with self._condition:
                        self._check_stop()
                        if verified and self._valid(candidate):
                            assert self._pause_started is not None
                            paused = max(0.0, self._monotonic() - self._pause_started)
                            self._event("auth_resumed", excluded_wait_seconds=paused)
                            self._current = candidate
                            self._paused_total += paused
                            self._pause_started = None
                            self._required = False
                            self._pause_reason = "expired_or_near_expiry"
                            self._condition.notify_all()
                            return
                    self._event(
                        "auth_verification_failed",
                        reason="insufficient_validity" if verified else "identity_not_verified",
                    )
            with self._condition:
                self._check_stop()
                self._wait(self._condition, self._wait_seconds)
