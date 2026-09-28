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
def settled(tmp_path, request):
    reset = getattr(request, "param", False)
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
            "classification": ("infrastructure_failure" if reset else "invalid_output")
            if index == 6
            else "success_termination",
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
            kind="unknown_outcome_infrastructure_failure"
            if reset and index == 6
            else "attempt_completed",
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
                payload=reset_failure() if reset else {"failure_code": "parse_failure"},
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
            status="infrastructure_failure" if reset and index == 6 else "response",
            exit_code=1 if reset and index == 6 else 0,
            raw_stdout="{}",
            raw_stderr="",
            outcome={
                "process_confirmed_stopped": True,
                "transport_outcome": {
                    "status": "transport_fault",
                    "fault": reset_failure()["cli_fault"],
                },
            }
            if reset and index == 6
            else {"transport_outcome": {"status": "response"}},
        )
    summary = {
        "execution_plan_digest": plan["execution_plan_digest"],
        "assigned": len(jobs),
        "attempted": 7,
        "completed": 7,
        "unrun": len(jobs) - 7,
        "results": rows,
        "stop_reason": "infrastructure_failure" if reset else "invalid_output",
        "error": None,
        "subprocesses_closed": True,
        "unresolved_invocations": 1 if reset else 0,
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


def test_continues_after_malformed_and_reset_failures_but_not_other_faults(settled, monkeypatch):
    output, plan, summary = settled
    started = datetime.now(UTC) - timedelta(hours=1)
    amendment = {
        "owner_statement": continuation.OWNER_STATEMENT,
        "continuation_rule": continuation.CONTINUATION_RULE,
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
    classifications = iter(
        [
            "invalid_output",
            "infrastructure_failure",
            "success_termination",
            "infrastructure_failure",
        ]
    )

    class Runner:
        def __init__(self, **kwargs):
            self.journal = kwargs["journal"]
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
            if row["classification"] == "infrastructure_failure" and len(calls) == 2:
                self.journal.append_event(
                    event_key=trial_id + "/reset",
                    kind="sealed_unsuccessful_result",
                    trial_id=trial_id,
                    step_index=0,
                    payload=reset_failure(),
                )
            return SimpleNamespace(to_dict=lambda: row)

    class Transport:
        subprocesses_closed = True

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
    assert result["completed"] == 11
    assert result["unrun"] == 421
    assert result["stop_reason"] == "infrastructure_failure"
    assert result["subprocesses_closed"] is True
    assert calls == [job["trial_id"] for job in continuation.stopped_prefix(plan, summary)[:4]]
    assert result["results"][7]["classification"] == "invalid_output"
    with pytest.raises((FileExistsError, ValueError)):
        continuation.execute(output)
    assert len(calls) == 4


def reset_failure():
    return {
        "failure_code": "transport_fault_retry_exhausted",
        "cli_fault": {
            "kind": "connection_reset",
            "code": "cli_connection_reset",
            "classification": "infrastructure_failure",
        },
    }


@pytest.mark.parametrize("settled", [True], indirect=True)
def test_preserves_reset_failure_and_unknown_completion_without_replay(settled):
    output, plan, summary = settled
    before = deepcopy(summary)
    ledger = continuation.validate_journals(output, summary)
    assert ledger.processes_started == 7
    assert ledger.unresolved == {"key-6"}
    assert not ledger.blocked
    assert len(continuation.stopped_prefix(plan, summary)) == 425
    assert summary == before


@pytest.mark.parametrize(
    "kind,code",
    [
        ("authentication", "cli_authentication"),
        ("process_timeout", "cli_process_timeout"),
        ("connection_reset", "diagnostic_only"),
    ],
)
def test_other_faults_do_not_get_connection_reset_continuation(kind, code):
    failure = reset_failure()
    failure["cli_fault"].update(kind=kind, code=code)
    event = SimpleNamespace(trial_id="trial", kind="sealed_unsuccessful_result", payload=failure)
    assert not continuation.may_continue(
        {"trial_id": "trial", "classification": "infrastructure_failure"}, [event]
    )


def test_unconfirmed_process_cannot_be_restored_as_safe_reset():
    record = {
        "status": "infrastructure_failure",
        "credential_redacted": False,
        "raw_stdout": "ECONNRESET",
        "outcome": {
            "process_confirmed_stopped": False,
            "transport_outcome": {
                "status": "transport_fault",
                "fault": reset_failure()["cli_fault"],
            },
        },
    }
    assert not continuation.confirmed_reset(record)
    record["outcome"]["process_confirmed_stopped"] = True
    assert continuation.confirmed_reset(record)


def test_repeated_continuation_keeps_original_deadline(tmp_path):
    started = datetime(2026, 9, 24, 4, 48, tzinfo=UTC)
    prior = {
        "original_started_at": started.isoformat(),
        "deadline": (started + timedelta(hours=168)).isoformat(),
    }
    continuation.write_object(tmp_path / "continuation-approval.json", prior)
    continuation.write_object(
        tmp_path / "launch.json", {"started_at": (started + timedelta(hours=15)).isoformat()}
    )
    summary = {"continuation_digest": content_digest(prior)}
    assert continuation.original_start(tmp_path, summary) == started
    prior["deadline"] = (started + timedelta(hours=169)).isoformat()
    continuation.write_object(tmp_path / "continuation-approval.json", prior)
    summary["continuation_digest"] = content_digest(prior)
    with pytest.raises(ValueError, match="extended"):
        continuation.original_start(tmp_path, summary)


def test_rejects_inconsistent_unknown_completion_count(settled):
    output, _, summary = settled
    summary["unresolved_invocations"] = 1
    with pytest.raises(ValueError, match="unresolved invocation accounting"):
        continuation.validate_journals(output, summary)


@pytest.mark.parametrize("stopped", [True, False])
def test_restores_recovered_timeout_only_when_local_process_stopped(settled, stopped):
    output, _, summary = settled
    invocations = claude.ClaudeInvocationJournal(output / "invocations.sqlite")
    invocations.finish(
        "key-0",
        status="timeout",
        exit_code=-15,
        raw_stdout="",
        raw_stderr="",
        outcome={
            "process_confirmed_stopped": stopped,
            "transport_outcome": {
                "status": "deadline",
                "fault": {"kind": "process_timeout", "code": "claude_process_timeout"},
            },
        },
    )
    summary["invocation_integrity"] = invocations.integrity_report()
    summary["unresolved_invocations"] = 1
    invocations.close()
    if stopped:
        ledger = continuation.validate_journals(output, summary)
        assert ledger.unresolved == {"key-0"}
        assert ledger.processes_started == 7
        assert not ledger.blocked
    else:
        with pytest.raises(ValueError, match="unsettled"):
            continuation.validate_journals(output, summary)
