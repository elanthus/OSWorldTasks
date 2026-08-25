"""Durable, runner-owned attempt and exactly-once dispatch journal."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pixelgym.grounding.v5.contracts import AttemptIdentity, content_digest, sha256_bytes
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.serialization import canonical_json_bytes

TerminalAttemptKind = Literal[
    "attempt_completed",
    "confirmed_cancellation",
    "confirmed_no_response_timeout",
    "unknown_outcome_infrastructure_failure",
]
TERMINAL_ATTEMPT_KINDS = frozenset(
    {
        "attempt_completed",
        "confirmed_cancellation",
        "confirmed_no_response_timeout",
        "unknown_outcome_infrastructure_failure",
    }
)


class JournalConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class JournalEvent:
    sequence: int
    event_key: str
    kind: str
    trial_id: str
    step_index: int
    attempt_index: int | None
    payload: dict[str, Any]


class V5AttemptJournal:
    """SQLite journal with FULL synchronous commits and unique terminal states."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS objects (
                digest TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                data BLOB NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS object_roles (
                digest TEXT NOT NULL,
                kind TEXT NOT NULL,
                PRIMARY KEY (digest, kind),
                FOREIGN KEY (digest) REFERENCES objects(digest)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                trial_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                attempt_index INTEGER,
                payload BLOB NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS one_attempt_terminal
            ON events(trial_id, step_index, attempt_index)
            WHERE kind IN (
                'attempt_completed', 'confirmed_cancellation',
                'confirmed_no_response_timeout', 'unknown_outcome_infrastructure_failure'
            )
            """
        )
        self._connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS one_dispatch_started
            ON events(trial_id, step_index) WHERE kind = 'dispatch_started'
            """
        )
        self._connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS one_dispatch_committed
            ON events(trial_id, step_index) WHERE kind = 'dispatch_committed'
            """
        )

    def close(self) -> None:
        self._connection.close()

    def put_object(self, kind: str, data: bytes) -> str:
        if not isinstance(data, bytes):
            raise TypeError("journal object must be bytes")
        digest = "sha256:" + sha256_bytes(data)
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT kind, data FROM objects WHERE digest = ?", (digest,)
            ).fetchone()
            if existing is not None and existing[1] != data:
                raise JournalConflictError("content digest collision")
            self._connection.execute(
                "INSERT OR IGNORE INTO objects(digest, kind, data) VALUES (?, ?, ?)",
                (digest, kind, data),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO object_roles(digest, kind) VALUES (?, ?)",
                (digest, kind),
            )
        return digest

    def get_object(self, digest: str, *, expected_kind: str | None = None) -> bytes:
        row = self._connection.execute(
            "SELECT kind, data FROM objects WHERE digest = ?", (digest,)
        ).fetchone()
        if row is None:
            raise KeyError(f"journal object {digest} is missing")
        if expected_kind is not None:
            role = self._connection.execute(
                "SELECT 1 FROM object_roles WHERE digest = ? AND kind = ?",
                (digest, expected_kind),
            ).fetchone()
            if role is None:
                raise JournalConflictError("journal object kind mismatch")
        data = bytes(row[1])
        if "sha256:" + sha256_bytes(data) != digest:
            raise JournalConflictError("journal object digest mismatch")
        return data

    def append_event(
        self,
        *,
        event_key: str,
        kind: str,
        trial_id: str,
        step_index: int,
        payload: dict[str, Any],
        attempt_index: int | None = None,
    ) -> JournalEvent:
        validate_credential_free(payload)
        encoded = canonical_json_bytes(payload)
        with self._lock:
            try:
                with self._connection:
                    self._connection.execute(
                        """
                        INSERT INTO events(
                            event_key, kind, trial_id, step_index, attempt_index, payload
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (event_key, kind, trial_id, step_index, attempt_index, encoded),
                    )
            except sqlite3.IntegrityError as exc:
                existing = self._connection.execute(
                    """
                    SELECT sequence, event_key, kind, trial_id, step_index, attempt_index, payload
                    FROM events WHERE event_key = ?
                    """,
                    (event_key,),
                ).fetchone()
                if existing is not None and bytes(existing[6]) == encoded and existing[2] == kind:
                    return self._event_from_row(existing)
                raise JournalConflictError("conflicting or duplicate terminal journal event") from exc
        event = self.event(event_key)
        assert event is not None
        return event

    def record_attempt_started(
        self,
        identity: AttemptIdentity,
        *,
        provider_endpoint_identity: str,
        request_digest: str,
        idempotency_key: str,
        model_attempt_reservation: int,
        control_request_reservation: int,
        pre_call_checkpoint: bytes,
    ) -> JournalEvent:
        checkpoint_digest = self.put_object("policy_checkpoint", pre_call_checkpoint)
        return self.append_event(
            event_key=f"{identity.key}/attempt_started",
            kind="attempt_started",
            trial_id=identity.trial_id,
            step_index=identity.step_index,
            attempt_index=identity.attempt_index,
            payload={
                "identity": identity.key,
                "provider_endpoint_identity": provider_endpoint_identity,
                "request_digest": request_digest,
                "idempotency_key": idempotency_key,
                "model_attempt_reservation": model_attempt_reservation,
                "control_request_reservation": control_request_reservation,
                "pre_call_checkpoint_digest": checkpoint_digest,
            },
        )

    def persist_canonical_response(
        self, identity: AttemptIdentity, response: dict[str, Any]
    ) -> tuple[JournalEvent, bytes]:
        validate_credential_free(response)
        allowed = {"response_id", "model", "content", "finish_reason", "usage"}
        if set(response) != allowed:
            raise ValueError("canonical response fields do not match the frozen capture schema")
        encoded = canonical_json_bytes(response)
        digest = self.put_object("canonical_provider_response", encoded)
        event = self.append_event(
            event_key=f"{identity.key}/canonical_response_persisted",
            kind="canonical_response_persisted",
            trial_id=identity.trial_id,
            step_index=identity.step_index,
            attempt_index=identity.attempt_index,
            payload={"canonical_response_digest": digest, "size": len(encoded)},
        )
        return event, encoded

    def seal_attempt_terminal(
        self,
        identity: AttemptIdentity,
        *,
        kind: TerminalAttemptKind,
        post_attempt_checkpoint: bytes,
        response_digest: str | None = None,
        usage: dict[str, int] | None = None,
        failure_code: str | None = None,
    ) -> JournalEvent:
        checkpoint_digest = self.put_object("policy_checkpoint", post_attempt_checkpoint)
        return self.append_event(
            event_key=f"{identity.key}/{kind}",
            kind=kind,
            trial_id=identity.trial_id,
            step_index=identity.step_index,
            attempt_index=identity.attempt_index,
            payload={
                "response_digest": response_digest,
                "usage": usage or {},
                "failure_code": failure_code,
                "post_attempt_checkpoint_digest": checkpoint_digest,
            },
        )

    def event(self, event_key: str) -> JournalEvent | None:
        row = self._connection.execute(
            """
            SELECT sequence, event_key, kind, trial_id, step_index, attempt_index, payload
            FROM events WHERE event_key = ?
            """,
            (event_key,),
        ).fetchone()
        return None if row is None else self._event_from_row(row)

    def events(self, trial_id: str | None = None) -> tuple[JournalEvent, ...]:
        query = (
            "SELECT sequence, event_key, kind, trial_id, step_index, attempt_index, payload "
            "FROM events"
        )
        parameters: tuple[object, ...] = ()
        if trial_id is not None:
            query += " WHERE trial_id = ?"
            parameters = (trial_id,)
        query += " ORDER BY sequence"
        return tuple(self._event_from_row(row) for row in self._connection.execute(query, parameters))

    def terminal_attempt(self, identity: AttemptIdentity) -> JournalEvent | None:
        row = self._connection.execute(
            """
            SELECT sequence, event_key, kind, trial_id, step_index, attempt_index, payload
            FROM events
            WHERE trial_id = ? AND step_index = ? AND attempt_index = ?
              AND kind IN (
                'attempt_completed', 'confirmed_cancellation',
                'confirmed_no_response_timeout', 'unknown_outcome_infrastructure_failure'
              )
            """,
            (identity.trial_id, identity.step_index, identity.attempt_index),
        ).fetchone()
        return None if row is None else self._event_from_row(row)

    def integrity_report(self) -> dict[str, Any]:
        object_rows = self._connection.execute("SELECT digest, kind, data FROM objects").fetchall()
        for digest, _kind, data in object_rows:
            if "sha256:" + sha256_bytes(bytes(data)) != digest:
                raise JournalConflictError("journal integrity verification failed")
        events = self.events()
        return {
            "schema_version": "pixelgym-agent-v5-journal-integrity-v1",
            "object_count": len(object_rows),
            "event_count": len(events),
            "event_chain_digest": content_digest(
                [
                    {
                        "sequence": event.sequence,
                        "event_key": event.event_key,
                        "kind": event.kind,
                        "payload": event.payload,
                    }
                    for event in events
                ]
            ),
        }

    @staticmethod
    def _event_from_row(row: tuple[Any, ...]) -> JournalEvent:
        return JournalEvent(
            sequence=int(row[0]),
            event_key=str(row[1]),
            kind=str(row[2]),
            trial_id=str(row[3]),
            step_index=int(row[4]),
            attempt_index=None if row[5] is None else int(row[5]),
            payload=json.loads(bytes(row[6])),
        )
