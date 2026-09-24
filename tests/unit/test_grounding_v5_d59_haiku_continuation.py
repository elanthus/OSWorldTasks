"""Continuation preserves failures, reservations, and the exact unrun suffix."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps, content_digest
from pixelgym.grounding.v5.d59_haiku_execution import sha256_file
from pixelgym.grounding.v5.journal import CallCapExceededError, V5AttemptJournal
from scripts import continue_grounding_v5_d59_haiku as continuation


@pytest.fixture
def settled(tmp_path):
    plan = json.loads((continuation.ROOT / continuation.frozen.PLAN_PATH).read_text())
    jobs = plan["primary_jobs"] + plan["reliability_jobs"]
    rows = []
    journal = V5AttemptJournal(tmp_path / "attempts.sqlite")
    invocations = claude.ClaudeInvocationJournal(tmp_path / "invocations.sqlite")
    for index, job in enumerate(jobs[:7]):
        trial = job["trial_id"]
        key = f"key-{index}"
        row = {
            **job,
            "classification": "invalid_output" if index == 6 else "success_termination",
            "environment_actions": 0,
            "model_attempts": 1,
            "provider_control_requests": 0,
            "provider_wire_requests": 1,
        }
        rows.append(row)
        journal.append_event(
            event_key=f"{trial}/start",
            kind="d59_assignment_started",
            trial_id=trial,
            step_index=0,
            payload={"job": job},
        )
        journal.append_event(
            event_key=f"{trial}/attempt",
            kind="attempt_started",
            trial_id=trial,
            step_index=0,
            attempt_index=0,
            payload={"idempotency_key": key, "model_attempt_reservation": 1},
        )
        journal.append_event(
            event_key=f"{trial}/terminal",
            kind="attempt_completed",
            trial_id=trial,
            step_index=0,
            attempt_index=0,
            payload={},
        )
        if index == 6:
            journal.append_event(
                event_key=f"{trial}/parse",
                kind="sealed_unsuccessful_result",
                trial_id=trial,
                step_index=0,
                payload={"failure_code": "parse_failure"},
            )
        journal.append_event(
            event_key=f"{trial}/complete",
            kind="d59_assignment_completed",
            trial_id=trial,
            step_index=0,
            payload=row,
        )
        invocations.reserve(idempotency_key=key, request_digest="sha256:fixture")
        invocations.finish(
            key,
            status="response",
            exit_code=0,
            raw_stdout="{}",
            raw_stderr="",
            outcome={"transport_outcome": {"status": "response"}},
        )
    summary = {
        "execution_plan_digest": plan["execution_plan_digest"],
        "assigned": len(jobs),
        "attempted": 7,
        "completed": 7,
        "unrun": len(jobs) - 7,
        "results": rows,
        "stop_reason": "invalid_output",
        "error": None,
        "subprocesses_closed": True,
        "unresolved_invocations": 0,
        "incremental_experiment_charge_usd": "0.00",
        "informational_list_price_equivalent_usd": "0.00",
        "provider_processes_started": 7,
        "journal_integrity": journal.integrity_report(),
        "invocation_integrity": invocations.integrity_report(),
    }
    journal.close()
    invocations.close()
    return tmp_path, plan, summary


def test_only_unrun_suffix_and_existing_failure_retained(settled):
    _, plan, summary = settled
    before = deepcopy(summary)
    jobs = continuation.stopped_prefix(plan, summary)
    assert len(jobs) == 425
    assert jobs[0]["trial_id"] == "d59-haiku-r3-primary-6003-stateless-r0"
    assert summary == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempted", 8),
        ("unresolved_invocations", 1),
        ("subprocesses_closed", False),
        ("stop_reason", "infrastructure_failure"),
        ("incremental_experiment_charge_usd", "0.01"),
    ],
)
def test_refuses_unsettled_or_other_failures(settled, field, value):
    _, plan, summary = settled
    summary[field] = value
    with pytest.raises(ValueError, match="fully settled"):
        continuation.stopped_prefix(plan, summary)


def test_rejects_skipped_or_reordered_results(settled):
    _, plan, summary = settled
    summary["results"][0], summary["results"][1] = summary["results"][1], summary["results"][0]
    with pytest.raises(ValueError, match="exact frozen prefix"):
        continuation.stopped_prefix(plan, summary)


def test_backups_keep_prior_reservations_without_changing_original(settled):
    output, _, summary = settled
    original = sha256_file(output / "attempts.sqlite")
    copy = output / "continuation"
    copy.mkdir()
    for name in ("attempts.sqlite", "invocations.sqlite"):
        continuation.copy_database(output / name, copy / name)
    ledger = continuation.validate_journals(copy, summary)
    assert ledger.processes_started == 7
    assert ledger.reserve("key-0") is False
    journal = V5AttemptJournal(copy / "attempts.sqlite")
    assert journal.call_counts() == (7, 0)
    with pytest.raises(CallCapExceededError):
        journal.reserve_attempt_started(
            AttemptIdentity("next", 0, 0),
            provider_endpoint_identity="fixture",
            request_digest="sha256:fixture",
            idempotency_key="new-request",
            model_attempt_reservation=1,
            control_request_reservation=0,
            pre_call_checkpoint=b"{}",
            approved_caps=CallCaps(100, 7, 0, 7),
        )
    journal.append_event(
        event_key="new",
        kind="owner_continuation_authorized",
        trial_id="next",
        step_index=0,
        payload={},
    )
    journal.close()
    assert sha256_file(output / "attempts.sqlite") == original


def test_rejects_journal_tampering(settled):
    output, _, summary = settled
    journal = V5AttemptJournal(output / "attempts.sqlite")
    journal.append_event(
        event_key="unexpected",
        kind="d59_assignment_started",
        trial_id="unexpected",
        step_index=0,
        payload={},
    )
    journal.close()
    with pytest.raises(ValueError, match="journal differs"):
        continuation.validate_journals(output, summary)


def test_continues_after_invalid_output_but_stops_on_transport_failure(settled, monkeypatch):
    output, plan, summary = settled
    started = datetime.now(UTC) - timedelta(hours=1)
    amendment = {
        "owner_statement": continuation.OWNER_STATEMENT,
        "execution_plan_digest": plan["execution_plan_digest"],
        "predecessor_summary_digest": content_digest(summary),
        "continue_outcomes": sorted(continuation.CONTINUE_OUTCOMES),
        "continuation_source_sha256": sha256_file(continuation.Path(continuation.__file__)),
        "next_trial_id": continuation.stopped_prefix(plan, summary)[0]["trial_id"],
        "original_started_at": started.isoformat(),
        "deadline": (started + timedelta(hours=168)).isoformat(),
        "source_revision": "fixture",
    }
    continuation.write_object(output / "continuation-approval.json", amendment)
    manifests = {
        mode: SimpleNamespace(request_deadline_seconds=300) for mode in ("history", "stateless")
    }
    monkeypatch.setattr(continuation, "inputs", lambda _: (plan, summary, None, manifests))
    calls = []
    classifications = iter(["invalid_output", "success_termination", "infrastructure_failure"])

    class Runner:
        def __init__(self, **kwargs):
            assert kwargs["journal"].call_counts() == (7, 0)
            assert kwargs["approved_caps"] == continuation.APPROVED_CAPS

        def run(self, *, trial_id, **kwargs):
            calls.append(trial_id)
            row = {
                "classification": next(classifications),
                "environment_actions": 0,
                "model_attempts": 0,
                "provider_control_requests": 0,
                "provider_wire_requests": 0,
            }
            return SimpleNamespace(to_dict=lambda: row)

    class Transport:
        subprocesses_closed = False

        def __init__(self, **kwargs):
            assert kwargs["allow_connection_retry"] is True
            assert kwargs["api_retry_limit"] == 0

        def close(self):
            self.subprocesses_closed = True

    monkeypatch.setattr(continuation, "CliMemoryRunner", Runner)
    monkeypatch.setattr(continuation.claude, "ClaudeCodeTransport", Transport)
    monkeypatch.setattr(continuation, "episode_measurements", lambda *args: {})
    continuation.execute(output)
    result = continuation.read_object(output / "summary.json")
    assert result["results"][:7] == summary["results"]
    assert result["completed"] == 10
    assert result["unrun"] == 422
    assert result["stop_reason"] == "infrastructure_failure"
    assert result["subprocesses_closed"] is True
    assert calls == [job["trial_id"] for job in continuation.stopped_prefix(plan, summary)[:3]]
    assert result["results"][7]["classification"] == "invalid_output"
    with pytest.raises((FileExistsError, ValueError)):
        continuation.execute(output)
    assert len(calls) == 3
