"""Offline, clock-injected regressions for shared owner-driven OAuth renewal."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5.auth_recovery import (
    AUTHENTICATION_RECOVERY_VERSION,
    AuthRecoveryStopped,
    CredentialSnapshot,
    OAuthRecoveryGate,
    load_oauth_snapshot,
)
from pixelgym.grounding.v5.evidence import validate_credential_free

OLD = "sk-ant-oat-test-old-private-value"
NEW = "sk-ant-oat-test-new-private-value"
OTHER = "sk-ant-oat-test-other-private-value"
PROOF = {"organization_type": "claude_max", "model_requests": 0}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def make_gate(clock: Clock, **overrides: Any) -> tuple[OAuthRecoveryGate, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    arguments = {
        "initial": CredentialSnapshot(OLD, 1_000),
        "load": lambda: CredentialSnapshot(NEW, 10_000),
        "verify": lambda value: PROOF,
        "on_event": events.append,
        "stop_requested": lambda: False,
        "subprocesses_closed": lambda: True,
        "monotonic": clock,
        "wall_time": clock,
    }
    arguments.update(overrides)
    return OAuthRecoveryGate(**arguments), events


def test_constructor_and_valid_lease_make_no_authentication_requests() -> None:
    clock = Clock()

    def forbidden(*args: Any) -> Any:
        raise AssertionError("valid credential must not cause auth IO")

    gate, events = make_gate(clock, load=forbidden, verify=forbidden)
    gate.begin_attempt()
    assert gate.credential() == OLD
    clock.advance(11)
    gate.end_attempt()
    assert gate.elapsed_seconds() == 11
    assert events == []


def test_expired_initial_waits_for_verified_replacement() -> None:
    clock = Clock()
    calls = []
    gate, events = make_gate(
        clock,
        initial=CredentialSnapshot(OLD, -1),
        verify=lambda value: calls.append(value) or PROOF,
    )
    gate.begin_attempt()
    gate.end_attempt()
    assert calls == [NEW]
    assert gate.credential() == NEW
    assert [event["event"] for event in events] == [
        "auth_pause_started",
        "auth_verification",
        "auth_resumed",
    ]
    assert events[1]["authentication_reads_attempted"] == 1
    assert events[0]["reason"] == "expired_or_near_expiry"
    assert all(event["model_requests"] == 0 for event in events)


def test_verification_time_and_each_wait_are_excluded_exactly_once() -> None:
    clock = Clock()
    replacements = iter([CredentialSnapshot(NEW, 10_000), CredentialSnapshot(OTHER, 10_000)])

    def verify(value: str) -> dict[str, Any]:
        clock.advance(25)
        return PROOF

    gate, events = make_gate(clock, load=lambda: next(replacements), verify=verify)
    clock.advance(8)
    for _ in range(2):
        gate.require_login()
        gate.begin_attempt()
        gate.end_attempt()
        assert gate.elapsed_seconds() == 8
    assert clock.now == 58
    assert [e["excluded_wait_seconds"] for e in events if e["event"] == "auth_resumed"] == [
        25,
        25,
    ]
    clock.advance(4)
    assert gate.elapsed_seconds() == 12


def test_rejected_value_is_verified_once_until_a_different_value_arrives() -> None:
    clock = Clock()
    current = CredentialSnapshot(NEW, 10_000)
    waits = 0
    calls = []

    def wait(condition: threading.Condition, seconds: float) -> None:
        nonlocal waits, current
        waits += 1
        clock.advance(10)
        if waits == 2:
            current = CredentialSnapshot(OTHER, 10_000)

    def verify(value: str) -> dict[str, Any]:
        calls.append(value)
        if value == NEW:
            raise ValueError("private provider response " + NEW)
        return PROOF

    gate, events = make_gate(clock, load=lambda: current, verify=verify, wait=wait)
    gate.require_login()
    gate.begin_attempt()
    gate.end_attempt()
    assert calls == [NEW, OTHER]
    assert gate.credential() == OTHER
    assert gate.elapsed_seconds() == 0
    validate_credential_free(events)
    assert all(value not in json.dumps(events) for value in (OLD, NEW, OTHER))


@pytest.mark.parametrize(
    "proof",
    [
        {},
        {"organization_type": "claude_pro", "model_requests": 0},
        {"organization_type": "claude_max", "model_requests": 1},
        {"organization_type": "claude_max", "model_requests": False},
        {"organization_type": "claude_max"},
    ],
)
def test_identity_failure_never_resumes(proof: dict[str, Any]) -> None:
    clock = Clock()
    stopped = False

    def wait(condition: threading.Condition, seconds: float) -> None:
        nonlocal stopped
        stopped = True

    gate, events = make_gate(
        clock, verify=lambda value: proof, stop_requested=lambda: stopped, wait=wait
    )
    gate.require_login()
    with pytest.raises(AuthRecoveryStopped):
        gate.begin_attempt()
    assert gate.credential() == OLD
    assert "auth_resumed" not in [event["event"] for event in events]
    assert events[-1]["reason"] == "identity_not_verified"


def test_same_initial_value_waits_without_verifying_and_can_be_cancelled() -> None:
    clock = Clock()
    stopped = False

    def wait(condition: threading.Condition, seconds: float) -> None:
        nonlocal stopped
        clock.advance(40)
        stopped = True

    def forbidden(value: str) -> dict[str, Any]:
        raise AssertionError("unchanged rejected value must not be checked again")

    gate, events = make_gate(
        clock,
        load=lambda: CredentialSnapshot(OLD, 10_000),
        verify=forbidden,
        stop_requested=lambda: stopped,
        wait=wait,
    )
    gate.require_login()
    with pytest.raises(AuthRecoveryStopped):
        gate.begin_attempt()
    assert gate.elapsed_seconds() == 0
    assert [event["event"] for event in events] == ["auth_pause_started"]


@pytest.mark.parametrize("verification_seconds", [900, 1_000, 1_100])
def test_validity_is_checked_again_after_verification(verification_seconds: int) -> None:
    clock = Clock()
    stopped = False

    def verify(value: str) -> dict[str, Any]:
        clock.advance(verification_seconds)
        return PROOF

    def wait(condition: threading.Condition, seconds: float) -> None:
        nonlocal stopped
        stopped = True

    gate, events = make_gate(
        clock,
        load=lambda: CredentialSnapshot(NEW, 1_000),
        verify=verify,
        wait=wait,
        stop_requested=lambda: stopped,
    )
    gate.require_login()
    with pytest.raises(AuthRecoveryStopped):
        gate.begin_attempt()
    assert gate.credential() == OLD
    assert not any(event["event"] == "auth_resumed" for event in events)
    assert events[-1] == {
        "event": "auth_verification_failed",
        "model_requests": 0,
        "reason": "insufficient_validity",
    }


def test_recovery_protocol_version_is_explicit() -> None:
    assert AUTHENTICATION_RECOVERY_VERSION == "pixelgym-owner-oauth-recovery-v1"


def test_too_short_replacement_is_not_sent_to_verifier() -> None:
    clock = Clock()
    current = CredentialSnapshot(NEW, 124)
    calls = []

    def wait(condition: threading.Condition, seconds: float) -> None:
        nonlocal current
        current = CredentialSnapshot(OTHER, 10_000)

    gate, events = make_gate(
        clock,
        load=lambda: current,
        verify=lambda value: calls.append(value) or PROOF,
        wait=wait,
    )
    gate.require_login()
    gate.begin_attempt()
    gate.end_attempt()
    assert calls == [OTHER]
    assert events[1]["reason"] == "insufficient_validity"


@pytest.mark.parametrize("closed,stopped", [(False, False), (True, True)])
def test_unsettled_process_or_ledger_stop_forbids_recovery(closed: bool, stopped: bool) -> None:
    clock = Clock()
    gate, events = make_gate(
        clock, subprocesses_closed=lambda: closed, stop_requested=lambda: stopped
    )
    gate.require_login()
    with pytest.raises(AuthRecoveryStopped):
        gate.begin_attempt()
    assert events == []


def test_invalid_owner_files_are_sanitized_without_a_retry_storm() -> None:
    clock = Clock()
    stopped = False
    waits = 0

    def load() -> CredentialSnapshot:
        raise ValueError(NEW)

    def wait(condition: threading.Condition, seconds: float) -> None:
        nonlocal stopped, waits
        waits += 1
        stopped = waits == 3

    gate, events = make_gate(clock, load=load, wait=wait, stop_requested=lambda: stopped)
    gate.require_login()
    with pytest.raises(AuthRecoveryStopped):
        gate.begin_attempt()
    assert sum(event["event"] == "auth_verification_failed" for event in events) == 1
    validate_credential_free(events)


def test_two_workers_share_one_verification_and_one_excluded_pause() -> None:
    clock = Clock()
    verifying = threading.Event()
    waiting = threading.Event()
    release = threading.Event()
    calls = []

    def verify(value: str) -> dict[str, Any]:
        calls.append(value)
        verifying.set()
        assert release.wait(timeout=2)
        clock.advance(50)
        return PROOF

    def wait(condition: threading.Condition, seconds: float) -> None:
        waiting.set()
        assert condition.wait(timeout=2)

    gate, events = make_gate(clock, verify=verify, wait=wait)
    gate.require_login()

    def worker() -> str:
        gate.begin_attempt()
        try:
            return gate.credential()
        finally:
            gate.end_attempt()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker)
        assert verifying.wait(timeout=2)
        second = pool.submit(worker)
        assert waiting.wait(timeout=2)
        release.set()
        assert first.result(timeout=2) == NEW
        assert second.result(timeout=2) == NEW
    assert calls == [NEW]
    assert gate.elapsed_seconds() == 0
    assert sum(event["event"] == "auth_pause_started" for event in events) == 1
    assert sum(event["event"] == "auth_resumed" for event in events) == 1


def test_active_workers_drain_before_any_runtime_is_excluded() -> None:
    clock = Clock()
    blocked = threading.Event()

    def wait(condition: threading.Condition, seconds: float) -> None:
        blocked.set()
        assert condition.wait(timeout=2)

    def verify(value: str) -> dict[str, Any]:
        clock.advance(30)
        return PROOF

    gate, events = make_gate(clock, wait=wait, verify=verify)
    gate.begin_attempt()
    gate.require_login()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(gate.begin_attempt)
        assert blocked.wait(timeout=2)
        clock.advance(14)
        assert gate.elapsed_seconds() == 14
        assert events == []
        gate.end_attempt()
        pending.result(timeout=2)
    gate.end_attempt()
    assert gate.elapsed_seconds() == 14
    assert events[-1]["excluded_wait_seconds"] == 30


def test_unpaired_release_and_invalid_bounds_are_rejected() -> None:
    clock = Clock()
    gate, _ = make_gate(clock)
    with pytest.raises(RuntimeError, match="lease"):
        gate.end_attempt()
    with pytest.raises(ValueError):
        make_gate(clock, wait_seconds=0)
    with pytest.raises(ValueError):
        make_gate(clock, minimum_validity_seconds=float("inf"))
    assert OLD not in repr(CredentialSnapshot(OLD, 10_000))


def private_files(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.chmod(0o700)
    env = tmp_path / ".env"
    env.write_text("CLAUDE_CODE_OAUTH_TOKEN='" + NEW + "'\n")
    env.chmod(0o600)
    receipt = tmp_path / "credential-provisioning-check.json"
    receipt.write_text(
        json.dumps(
            {
                "authentication_metadata": {
                    "expires_at_utc": datetime.fromtimestamp(10_000, UTC).isoformat()
                }
            }
        )
    )
    receipt.chmod(0o600)
    return env, receipt


def test_loads_only_the_helpers_private_data_and_expiry(tmp_path: Path) -> None:
    env, _ = private_files(tmp_path)
    snapshot = load_oauth_snapshot(env)
    assert snapshot.value == NEW
    assert snapshot.expires_at == 10_000


@pytest.mark.parametrize("target", ["env", "receipt", "directory", "symlink", "fifo"])
def test_rejects_unprotected_or_symlink_sources(tmp_path: Path, target: str) -> None:
    env, receipt = private_files(tmp_path)
    if target == "env":
        env.chmod(0o644)
    elif target == "receipt":
        receipt.chmod(0o644)
    elif target == "directory":
        tmp_path.chmod(0o755)
    elif target == "symlink":
        destination = tmp_path / "real-env"
        env.rename(destination)
        env.symlink_to(destination)
    else:
        env.unlink()
        os.mkfifo(env, mode=0o600)
    with pytest.raises(ValueError, match="unavailable or invalid") as failure:
        load_oauth_snapshot(env)
    assert NEW not in str(failure.value)


def test_rejects_files_owned_by_someone_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, _ = private_files(tmp_path)
    uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: uid + 1)
    with pytest.raises(ValueError, match="unavailable or invalid"):
        load_oauth_snapshot(env)


@pytest.mark.parametrize("receipt_source", ["null", "[]", "{}", "not json"])
def test_invalid_receipt_does_not_leak_source(tmp_path: Path, receipt_source: str) -> None:
    env, receipt = private_files(tmp_path)
    receipt.write_text(receipt_source)
    with pytest.raises(ValueError, match="unavailable or invalid") as failure:
        load_oauth_snapshot(env)
    assert NEW not in str(failure.value)


@pytest.mark.parametrize(
    "source",
    [
        "CLAUDE_CODE_OAUTH_TOKEN='" + NEW + "'\nOTHER=value\n",
        "CLAUDE_CODE_OAUTH_TOKEN='" + NEW + "'; echo unsafe",
        "export CLAUDE_CODE_OAUTH_TOKEN='" + NEW + "'",
        "OTHER='" + NEW + "'",
        "CLAUDE_CODE_OAUTH_TOKEN='bad value'",
    ],
)
def test_shell_text_is_never_executed_and_errors_are_safe(tmp_path: Path, source: str) -> None:
    env, receipt = private_files(tmp_path)
    env.write_text(source)
    os.utime(receipt, ns=(env.stat().st_mtime_ns + 1, env.stat().st_mtime_ns + 1))
    with pytest.raises(ValueError) as failure:
        load_oauth_snapshot(env)
    assert NEW not in str(failure.value)


def test_rejects_missing_timezone_or_incomplete_helper_update(tmp_path: Path) -> None:
    env, receipt = private_files(tmp_path)
    os.utime(receipt, ns=(env.stat().st_mtime_ns - 1, env.stat().st_mtime_ns - 1))
    with pytest.raises(ValueError):
        load_oauth_snapshot(env)
    receipt.write_text('{"authentication_metadata":{"expires_at_utc":"2030-01-01"}}')
    with pytest.raises(ValueError):
        load_oauth_snapshot(env)
