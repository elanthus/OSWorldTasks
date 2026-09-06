from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps, content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.serialization import canonical_json_bytes
from scripts.audit_grounding_v5_d56_gemini_full_calibration import (
    IntegrityAuditError,
    _audit_journal,
)


def _create_markerless_journal_schema(path: Path) -> None:
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


def _complete_journal(path: Path) -> dict[str, object]:
    journal = V5AttemptJournal(path)
    identity = AttemptIdentity("trial-1", 0, 0)
    try:
        initial_screenshot = journal.put_object("screenshot", b"initial-pixels")
        initial_checkpoint = journal.put_object(
            "environment_checkpoint", canonical_json_bytes({"stage": 0})
        )
        initial_resume = journal.put_object(
            "environment_resume_record", canonical_json_bytes({"step": 0})
        )
        journal.append_event(
            event_key="trial-1/initial_screenshot",
            kind="initial_screenshot",
            trial_id="trial-1",
            step_index=0,
            payload={
                "screenshot_digest": initial_screenshot,
                "task_id": "task-1",
                "environment_checkpoint_digest": initial_checkpoint,
                "environment_resume_digest": initial_resume,
            },
        )
        journal.reserve_attempt_started(
            identity,
            provider_endpoint_identity="provider",
            request_digest="sha256:request",
            idempotency_key="sha256:idempotency",
            model_attempt_reservation=1,
            control_request_reservation=0,
            pre_call_checkpoint=canonical_json_bytes({"history": []}),
            approved_caps=CallCaps(1, 1, 0, 1),
        )
        response_event, _response = journal.persist_canonical_response(
            identity,
            {
                "response_id": "response-1",
                "model": "model-1",
                "content": '{"action_type":0,"x":0,"y":0,"key":0}',
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
        journal.seal_attempt_terminal(
            identity,
            kind="attempt_completed",
            post_attempt_checkpoint=canonical_json_bytes({"history": ["response-1"]}),
            response_digest=response_event.payload["canonical_response_digest"],
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )
        candidate = journal.put_object(
            "parsed_action_candidate",
            canonical_json_bytes({"action_type": 0, "x": 0, "y": 0, "key": 0}),
        )
        post_parse = journal.put_object(
            "policy_checkpoint", canonical_json_bytes({"history": ["parsed"]})
        )
        journal.append_event(
            event_key="trial-1/step-0000/parsed_action_candidate",
            kind="parsed_action_candidate",
            trial_id="trial-1",
            step_index=0,
            payload={
                "candidate_digest": candidate,
                "attempt_identities": [identity.key],
                "parser_version": "parser-v1",
                "post_parse_checkpoint_digest": post_parse,
            },
        )
        environment_checkpoint = journal.put_object(
            "environment_checkpoint", canonical_json_bytes({"stage": 0})
        )
        environment_resume = journal.put_object(
            "environment_resume_record", canonical_json_bytes({"step": 0})
        )
        action = journal.put_object(
            "sealed_action", canonical_json_bytes({"action_type": 0, "x": 0, "y": 0, "key": 0})
        )
        intent = {
            "candidate_digest": candidate,
            "action_digest": action,
            "environment_resume_digest": environment_resume,
            "environment_checkpoint_digest": environment_checkpoint,
        }
        intent_digest = content_digest(intent)
        journal.append_event(
            event_key="trial-1/step-0000/sealed_action_intent",
            kind="sealed_action_intent",
            trial_id="trial-1",
            step_index=0,
            payload={**intent, "sealed_intent_digest": intent_digest},
        )
        journal.append_event(
            event_key="trial-1/step-0000/dispatch_started",
            kind="dispatch_started",
            trial_id="trial-1",
            step_index=0,
            payload={
                "sealed_intent_digest": intent_digest,
                "backend_acceptance": "not_yet_invoked",
            },
        )
        final_screenshot = journal.put_object("screenshot", b"final-pixels")
        post_dispatch = journal.put_object(
            "policy_checkpoint", canonical_json_bytes({"history": ["dispatched"]})
        )
        final_checkpoint = journal.put_object(
            "environment_checkpoint", canonical_json_bytes({"stage": 1})
        )
        final_resume = journal.put_object(
            "environment_resume_record", canonical_json_bytes({"step": 1})
        )
        journal.append_event(
            event_key="trial-1/step-0000/dispatch_committed",
            kind="dispatch_committed",
            trial_id="trial-1",
            step_index=0,
            payload={
                "sealed_intent_digest": intent_digest,
                "backend_acceptance": "accepted_once",
                "commit_result_digest": "sha256:result",
                "post_dispatch_checkpoint_digest": post_dispatch,
                "environment_checkpoint_digest": final_checkpoint,
                "environment_resume_digest": final_resume,
                "screenshot_digest": final_screenshot,
                "reward": 0.0,
                "terminated": False,
                "truncated": False,
            },
        )
        return journal.integrity_report()
    finally:
        journal.close()


def test_read_only_audit_verifies_objects_events_and_lineage(tmp_path: Path) -> None:
    path = tmp_path / "attempts.sqlite"
    expected = _complete_journal(path)

    report = _audit_journal(path, expected)

    assert report["sqlite_integrity_check"] == "ok"
    assert report["integrity"] == expected
    assert report["model_attempt_reservations"] == 1
    assert report["provider_control_request_reservations"] == 0
    assert report["event_counts"]["dispatch_committed"] == 1


def test_read_only_audit_reports_recorded_digest_version(tmp_path: Path) -> None:
    v1_path = tmp_path / "v1.sqlite"
    _create_markerless_journal_schema(v1_path)
    v1_expected = _complete_journal(v1_path)
    v2_path = tmp_path / "v2.sqlite"
    v2_expected = _complete_journal(v2_path)

    v1_report = _audit_journal(v1_path, v1_expected)
    v2_report = _audit_journal(v2_path, v2_expected)

    assert "digest_version" not in v1_expected
    assert v1_report["integrity"]["digest_version"] == "v1"
    assert v2_expected["digest_version"] == "v2"
    assert v2_report["integrity"]["digest_version"] == "v2"


def test_read_only_audit_rejects_changed_object_bytes(tmp_path: Path) -> None:
    path = tmp_path / "attempts.sqlite"
    expected = _complete_journal(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE objects SET data = ? WHERE digest = (SELECT digest FROM objects LIMIT 1)",
            (b"changed",),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(IntegrityAuditError, match="digest mismatch"):
        _audit_journal(path, expected)
