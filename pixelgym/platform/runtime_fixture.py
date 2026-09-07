"""Durable, cross-process provider fixture for Metaflow runtime tests.

This adapter models the property a paid provider must supply for a strong billing guarantee:
repeating the same idempotency key may create another transport attempt, but it does not create a
second billable operation. It is activated only by the flow's explicit test-hook environment.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from pixelgym.platform.evaluation import PlatformProviderResponse, ScriptedReplayProvider
from pixelgym.serialization import load_jsonl

_SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS responses (
  request_id TEXT PRIMARY KEY,
  example_id TEXT NOT NULL,
  raw_response TEXT,
  latency_ms REAL,
  usage_json TEXT,
  cost_usd REAL,
  request_failure TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
  attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT NOT NULL,
  cache_hit INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  active INTEGER NOT NULL,
  max_active INTEGER NOT NULL,
  billable_calls INTEGER NOT NULL,
  barrier_reached INTEGER NOT NULL
);
INSERT OR IGNORE INTO counters VALUES (1, 0, 0, 0, 0);
"""


class LedgeredScriptedReplayProvider:
    """Scripted replay with a SQLite idempotency and concurrency ledger."""

    name = "scripted-demo"
    synthetic = True

    def __init__(
        self,
        prediction_path: Path,
        *,
        variant: str,
        model: str | None = None,
        ledger_path: Path,
        concurrency_barrier: int = 1,
    ) -> None:
        if concurrency_barrier <= 0:
            raise ValueError("concurrency barrier must be positive")
        self.latency_ms: float | None
        if variant in {"baseline", "revised"}:
            delegate = ScriptedReplayProvider(prediction_path, variant=variant, model=model)
            self.condition = delegate.condition
            self.model = delegate.model
            self.latency_ms = delegate.latency_ms
            self.responses = delegate.responses
        elif variant in {"invalid", "request_failure"}:
            self.condition = "raw"
            self.model = model or f"day3-replay-{variant}-v1"
            self.latency_ms = 25.0 if variant == "invalid" else None
            rows = load_jsonl(prediction_path)
            self.responses = {
                row["example_id"]: "not-json" if variant == "invalid" else None
                for row in rows
                if row.get("condition") == "raw"
            }
        else:
            raise ValueError("scripted variant must be baseline, revised, invalid, or request_failure")
        self.variant = variant
        self.ledger_path = ledger_path
        self.concurrency_barrier = concurrency_barrier
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            ledger_path.with_name(ledger_path.name + ".init.lock"),
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.ledger_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _wait_for_concurrency_barrier(self) -> None:
        if self.concurrency_barrier == 1:
            return
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT max_active, barrier_reached FROM counters WHERE singleton = 1"
                ).fetchone()
                if row["max_active"] >= self.concurrency_barrier:
                    connection.execute(
                        "UPDATE counters SET barrier_reached = 1 WHERE singleton = 1"
                    )
                    return
                if row["barrier_reached"]:
                    return
            time.sleep(0.01)
        raise RuntimeError("provider concurrency barrier timed out")

    def invoke(
        self,
        *,
        request_id: str,
        example_id: str,
        condition: str,
        image_path: Path,
        prompt: str,
        schema: dict[str, Any],
    ) -> PlatformProviderResponse:
        del image_path, prompt, schema
        if condition != self.condition or example_id not in self.responses:
            response = PlatformProviderResponse(
                None, self.latency_ms, {}, 0.0, "fixture missing"
            )
        elif self.variant == "request_failure":
            response = PlatformProviderResponse(
                None, None, None, None, "deterministic scripted request failure"
            )
        else:
            response = PlatformProviderResponse(
                self.responses[example_id],
                self.latency_ms,
                {"input_tokens": 0, "output_tokens": 0},
                0.0,
            )

        connection = self._connect()
        active_incremented = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM responses WHERE request_id = ?", (request_id,)
            ).fetchone()
            cache_hit = existing is not None
            connection.execute(
                "INSERT INTO attempts(request_id, cache_hit) VALUES (?, ?)",
                (request_id, int(cache_hit)),
            )
            connection.execute(
                """
                UPDATE counters
                SET active = active + 1,
                    max_active = MAX(max_active, active + 1)
                WHERE singleton = 1
                """
            )
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO responses VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        example_id,
                        response.raw_response,
                        response.latency_ms,
                        json.dumps(response.usage, sort_keys=True),
                        response.cost_usd,
                        response.request_failure,
                    ),
                )
                connection.execute(
                    "UPDATE counters SET billable_calls = billable_calls + 1 WHERE singleton = 1"
                )
                selected = response
            else:
                if existing["example_id"] != example_id:
                    raise ValueError("provider idempotency key was reused for another example")
                selected = PlatformProviderResponse(
                    existing["raw_response"],
                    existing["latency_ms"],
                    json.loads(existing["usage_json"]),
                    existing["cost_usd"],
                    existing["request_failure"],
                )
            connection.commit()
            active_incremented = True

            self._wait_for_concurrency_barrier()
            return selected
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            if active_incremented:
                with self._connect() as cleanup:
                    cleanup.execute(
                        "UPDATE counters SET active = MAX(active - 1, 0) WHERE singleton = 1"
                    )
            connection.close()


def provider_ledger_snapshot(path: Path) -> dict[str, int]:
    """Return stable counters used by the runtime acceptance tests."""
    with sqlite3.connect(path) as connection:
        attempts, unique_attempts, cache_hits = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT request_id), COALESCE(SUM(cache_hit), 0) FROM attempts"
        ).fetchone()
        active, max_active, billable_calls, _ = connection.execute(
            "SELECT active, max_active, billable_calls, barrier_reached FROM counters WHERE singleton = 1"
        ).fetchone()
    return {
        "attempts": int(attempts),
        "unique_request_ids": int(unique_attempts),
        "cache_hits": int(cache_hits),
        "active": int(active),
        "max_active": int(max_active),
        "billable_calls": int(billable_calls),
    }
