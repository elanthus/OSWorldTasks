"""Durable fake-send reservations shared by S7 phases and service restarts."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pixelgym.grounding.v5.contracts import TransportOutcome
from pixelgym.grounding.v5.runner import ScriptedTransport
from pixelgym.platform.s7_policy import policy_document


class S7CapReached(RuntimeError):
    pass


class S7SendBudget:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._open() as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS campaign("
                "started REAL NOT NULL, active_seconds REAL NOT NULL, paused INTEGER NOT NULL, "
                "activation_approval TEXT);"
                "CREATE TABLE IF NOT EXISTS sends("
                "request_key TEXT PRIMARY KEY, phase TEXT NOT NULL, reserved REAL NOT NULL);"
                "CREATE TRIGGER IF NOT EXISTS sends_no_update BEFORE UPDATE ON sends "
                "BEGIN SELECT RAISE(ABORT, 'append only'); END;"
                "CREATE TRIGGER IF NOT EXISTS sends_no_delete BEFORE DELETE ON sends "
                "BEGIN SELECT RAISE(ABORT, 'append only'); END;"
            )
            db.execute(
                "INSERT INTO campaign SELECT ?, 0, 0, NULL "
                "WHERE NOT EXISTS(SELECT 1 FROM campaign)",
                (time.time(),),
            )
        path.chmod(0o600)

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, phase: str, request_key: str) -> None:
        policy = policy_document()
        caps = policy["phase_caps"]
        if phase not in caps or not request_key:
            raise ValueError("unknown S7 phase or missing request identity")
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            started, active_seconds, paused = db.execute(
                "SELECT started, active_seconds, paused FROM campaign"
            ).fetchone()
            if paused:
                raise S7CapReached("S7 fake sends are paused pending exact-package owner review")
            if active_seconds + time.time() - started >= 1800:
                raise S7CapReached("S7 campaign wall-time limit reached")
            count = db.execute("SELECT count(*) FROM sends").fetchone()[0]
            phase_count = db.execute(
                "SELECT count(*) FROM sends WHERE phase = ?", (phase,)
            ).fetchone()[0]
            if count >= policy["campaign_cap"] or phase_count >= caps[phase]:
                raise S7CapReached("S7 approved fake-send cap reached")
            try:
                db.execute("INSERT INTO sends VALUES (?, ?, ?)", (request_key, phase, time.time()))
            except sqlite3.IntegrityError as exc:
                raise S7CapReached("S7 request already reserved; never send it again") from exc

    def counts(self) -> dict[str, int]:
        with self._open() as db:
            return dict(db.execute("SELECT phase, count(*) FROM sends GROUP BY phase"))

    def pause_for_owner_review(self) -> None:
        """Exclude the human review wait from the 30-minute active execution allowance."""
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            started, elapsed, paused = db.execute(
                "SELECT started, active_seconds, paused FROM campaign"
            ).fetchone()
            if paused or self.counts() != {
                "qualification": policy_document()["phase_caps"]["qualification"]
            }:
                raise ValueError("S7 can pause only after its one complete qualification")
            db.execute(
                "UPDATE campaign SET active_seconds = ?, paused = 1",
                (elapsed + time.time() - started,),
            )

    def resume_after_owner_review(self, approval_sha256: str) -> None:
        """Operator-only bookkeeping; caller must verify the exact activation approval first."""
        import re

        if not re.fullmatch(r"sha256:[0-9a-f]{64}", approval_sha256):
            raise ValueError("exact activation approval digest is required")
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT paused FROM campaign").fetchone() != (1,):
                raise ValueError("S7 campaign is not awaiting owner review")
            db.execute(
                "UPDATE campaign SET started = ?, paused = 0, activation_approval = ?",
                (time.time(), approval_sha256),
            )


class BudgetedScriptedTransport(ScriptedTransport):
    """The sole S7 transport: fake sends only, with no injectable network transport."""

    def __init__(
        self,
        budget: S7SendBudget,
        *,
        phase: str,
        outcomes: list[TransportOutcome] | None = None,
    ) -> None:
        super().__init__(outcomes)
        self.budget = budget
        self.phase = phase

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        self.budget.reserve(self.phase, idempotency_key)
        return super().send(
            request, idempotency_key=idempotency_key, deadline_seconds=deadline_seconds
        )

    def cancel(self, *, idempotency_key: str, mode: str) -> Literal["cancelled", "unknown"]:
        raise S7CapReached("S7 provider control requests are forbidden")

    def reconcile(self, *, idempotency_key: str, deadline_seconds: float) -> TransportOutcome:
        raise S7CapReached("S7 provider control requests are forbidden")
