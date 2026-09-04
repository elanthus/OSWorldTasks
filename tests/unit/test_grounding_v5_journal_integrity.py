"""Versioned event-chain integrity coverage for the v5 attempt journal."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.evidence import journal_integrity_audit_record
from pixelgym.grounding.v5.journal import JournalConflictError, V5AttemptJournal


def _create_markerless_v1_schema(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE objects (
                digest TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                data BLOB NOT NULL
            );
            CREATE TABLE object_roles (
                digest TEXT NOT NULL,
                kind TEXT NOT NULL,
                PRIMARY KEY (digest, kind),
                FOREIGN KEY (digest) REFERENCES objects(digest)
            );
            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                trial_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                attempt_index INTEGER,
                payload BLOB NOT NULL
            );
            """
        )
    finally:
        connection.close()


def _append_attempt_event(journal: V5AttemptJournal) -> None:
    journal.append_event(
        event_key="trial-1/step-0002/attempt-0003/attempt_started",
        kind="attempt_started",
        trial_id="trial-1",
        step_index=2,
        attempt_index=3,
        payload={"model_attempt_reservation": 1},
    )


def _rewrite_attempt_index(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE events SET attempt_index = 4")
        connection.commit()
    finally:
        connection.close()


def test_v2_digest_changes_when_stored_attempt_identity_is_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "v2.sqlite"
    journal = V5AttemptJournal(path)
    _append_attempt_event(journal)
    before = journal.integrity_report()
    event = journal.events()[0]
    journal.close()

    assert before["digest_version"] == "v2"
    assert before["event_chain_digest"] == content_digest(
        [
            {
                "sequence": event.sequence,
                "event_key": event.event_key,
                "kind": event.kind,
                "trial_id": event.trial_id,
                "step_index": event.step_index,
                "attempt_index": event.attempt_index,
                "payload": event.payload,
            }
        ]
    )

    _rewrite_attempt_index(path)
    reopened = V5AttemptJournal(path)
    try:
        after = reopened.integrity_report()
    finally:
        reopened.close()

    assert after["digest_version"] == "v2"
    assert after["event_chain_digest"] != before["event_chain_digest"]


def test_markerless_v1_digest_retains_legacy_identity_limitation(tmp_path: Path) -> None:
    path = tmp_path / "v1.sqlite"
    _create_markerless_v1_schema(path)
    journal = V5AttemptJournal(path)
    _append_attempt_event(journal)
    before = journal.integrity_report()
    journal.close()

    assert before["schema_version"] == "pixelgym-agent-v5-journal-integrity-v1"
    assert "digest_version" not in before
    assert journal_integrity_audit_record(before)["digest_version"] == "v1"

    _rewrite_attempt_index(path)
    reopened = V5AttemptJournal(path)
    try:
        after = reopened.integrity_report()
    finally:
        reopened.close()

    assert after == before


def test_v2_metadata_table_with_missing_marker_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "marker-tamper.sqlite"
    journal = V5AttemptJournal(path)
    _append_attempt_event(journal)
    journal.close()
    connection = sqlite3.connect(path)
    try:
        connection.execute("DELETE FROM journal_metadata")
        connection.commit()
    finally:
        connection.close()

    reopened = V5AttemptJournal(path)
    try:
        with pytest.raises(JournalConflictError, match="digest version metadata"):
            reopened.integrity_report()
    finally:
        reopened.close()
