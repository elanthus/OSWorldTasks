"""No-cost journal, settlement, call-cap, and policy-state tests for v5."""

from __future__ import annotations

import json
import threading
from collections import Counter
from pathlib import Path

import pytest

from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import (
    AttemptIdentity,
    CallCaps,
    PolicyManifest,
)
from pixelgym.grounding.v5.coordinates import IDENTITY_ADAPTER
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import JournalConflictError, V5AttemptJournal
from pixelgym.grounding.v5.planning import call_cap_plan
from pixelgym.grounding.v5.policies import golden_actions
from pixelgym.grounding.v5.runner import (
    InjectedInterruption,
    ScriptedStatefulPolicy,
    ScriptedTransport,
    TransportOutcome,
    V5Runner,
)
from pixelgym.grounding.v5.sandbox import build_sandbox_manifest, validate_capability_handles


def policy_manifest() -> PolicyManifest:
    sandbox = build_sandbox_manifest(
        runtime_digest="sha256:" + "1" * 64,
        provider_endpoint="http://127.0.0.1:9999",
    )
    return PolicyManifest.build(
        provider="fake",
        model="no-cost-scripted-policy",
        exact_snapshot=True,
        harness_digest="sha256:" + "2" * 64,
        dependency_lock_digest="sha256:" + "3" * 64,
        system_prompt_digest="sha256:" + "4" * 64,
        task_renderer_version="v1",
        response_schema_version="v1",
        state_reducer_version="v1",
        parser_version="v1",
        memory_policy_version="stateful-v1",
        coordinate_adapter=IDENTITY_ADAPTER.name,
        coordinate_adapter_digest=IDENTITY_ADAPTER.source_digest,
        coordinate_input_convention="1024x768",
        max_model_attempts_per_action=1,
        max_cancellation_requests_per_attempt=1,
        max_reconciliation_requests_per_attempt=1,
        request_deadline_seconds=5.0,
        cancellation_mode="cancel-once",
        reconciliation_deadline_seconds=1.0,
        sandbox=sandbox,
        code_revision="test-revision",
        dirty_worktree_policy="reject",
    )


def episode_caps(seed: int) -> CallCaps:
    task = generate_task(seed)
    return CallCaps.calculate(
        max_episode_steps=(task.max_episode_steps,),
        max_model_attempts_per_action=1,
        max_cancellation_requests_per_attempt=1,
        max_reconciliation_requests_per_attempt=1,
    )


def scripted_policy(seed: int) -> ScriptedStatefulPolicy:
    task = generate_task(seed)
    backend = V5FakeBackend()
    backend.reset(seed)
    actions = golden_actions(task, backend)
    backend.close()
    return ScriptedStatefulPolicy(actions)


def test_v5_runner_orders_canonical_attempt_candidate_and_dispatch_records(tmp_path: Path) -> None:
    seed = 5000
    task = generate_task(seed)
    journal = V5AttemptJournal(tmp_path / "journal.sqlite")
    transport = ScriptedTransport()
    result = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=episode_caps(seed),
    ).run(trial_id="trial-success", task=task)
    assert result.success and result.environment_actions == task.optimal_low_level_actions
    first_step_kinds = [
        event.kind for event in journal.events("trial-success") if event.step_index == 0
    ]
    assert first_step_kinds == [
        "initial_screenshot",
        "attempt_started",
        "canonical_response_persisted",
        "attempt_completed",
        "parsed_action_candidate",
        "sealed_action_intent",
        "dispatch_started",
        "dispatch_committed",
    ]
    assert len(transport.model_requests) == task.optimal_low_level_actions
    assert not transport.control_requests
    assert journal.integrity_report()["event_count"] > 0


def test_v5_deadline_settles_once_without_hidden_retry(tmp_path: Path) -> None:
    seed = 5000
    journal = V5AttemptJournal(tmp_path / "journal.sqlite")
    transport = ScriptedTransport([TransportOutcome("deadline")])
    result = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=episode_caps(seed),
    ).run(trial_id="trial-timeout", task=generate_task(seed))
    assert not result.success and result.classification == "request_failure"
    assert len(transport.model_requests) == 1
    assert transport.control_requests == [
        (
            "cancel",
            journal.events("trial-timeout")[1].payload["idempotency_key"],
        )
    ]
    assert Counter(event.kind for event in journal.events("trial-timeout"))[
        "confirmed_cancellation"
    ] == 1


def test_v5_hanging_transport_cannot_extend_runner_or_start_retry(tmp_path: Path) -> None:
    class HangingTransport(ScriptedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.release = threading.Event()

        def send(
            self, request: dict[str, object], *, idempotency_key: str, deadline_seconds: float
        ) -> TransportOutcome:
            del deadline_seconds
            self.model_requests.append(
                {"idempotency_key": idempotency_key, "request": request}
            )
            self.release.wait()
            return TransportOutcome("unknown", failure_code="released_after_deadline")

    seed = 5000
    base = policy_manifest()
    values = base.__dict__.copy()
    values.pop("policy_id")
    values["request_deadline_seconds"] = 0.01
    manifest = PolicyManifest.build(**values)
    journal = V5AttemptJournal(tmp_path / "hanging.sqlite")
    transport = HangingTransport()
    try:
        result = V5Runner(
            journal=journal,
            manifest=manifest,
            transport=transport,
            policy=scripted_policy(seed),
            approved_caps=episode_caps(seed),
        ).run(trial_id="trial-hanging", task=generate_task(seed))
    finally:
        transport.release.set()
    assert result.classification == "request_failure"
    assert len(transport.model_requests) == 1
    assert [kind for kind, _identity in transport.control_requests] == ["cancel"]
    assert Counter(event.kind for event in journal.events("trial-hanging"))[
        "confirmed_cancellation"
    ] == 1


def test_v5_unknown_post_send_outcome_is_not_retried(tmp_path: Path) -> None:
    seed = 5000
    journal = V5AttemptJournal(tmp_path / "journal.sqlite")
    transport = ScriptedTransport([TransportOutcome("unknown")])
    result = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=episode_caps(seed),
    ).run(trial_id="trial-unknown", task=generate_task(seed))
    assert result.classification == "infrastructure_failure"
    assert len(transport.model_requests) == 1
    assert [kind for kind, _identity in transport.control_requests] == ["reconcile"]


def test_v5_parse_failure_seals_failure_without_dispatch(tmp_path: Path) -> None:
    seed = 5000
    response = {
        "response_id": "malformed",
        "model": "no-cost-scripted-policy",
        "content": "not-json",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    journal = V5AttemptJournal(tmp_path / "journal.sqlite")
    result = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=ScriptedTransport([TransportOutcome("response", response)]),
        policy=scripted_policy(seed),
        approved_caps=episode_caps(seed),
    ).run(trial_id="trial-parse", task=generate_task(seed))
    assert result.classification == "invalid_output" and result.environment_actions == 0
    kinds = [event.kind for event in journal.events("trial-parse")]
    assert "sealed_unsuccessful_result" in kinds
    assert "dispatch_started" not in kinds


def test_v5_journal_enforces_one_terminal_record_and_verified_objects(tmp_path: Path) -> None:
    journal = V5AttemptJournal(tmp_path / "journal.sqlite")
    identity = AttemptIdentity("trial", 0, 0)
    journal.record_attempt_started(
        identity,
        provider_endpoint_identity="http://127.0.0.1:9999",
        request_digest="sha256:" + "a" * 64,
        idempotency_key="idempotent",
        model_attempt_reservation=1,
        control_request_reservation=0,
        pre_call_checkpoint=b"{}",
    )
    journal.seal_attempt_terminal(
        identity,
        kind="confirmed_no_response_timeout",
        post_attempt_checkpoint=b"{}",
        failure_code="pre_send",
    )
    with pytest.raises(JournalConflictError):
        journal.seal_attempt_terminal(
            identity,
            kind="unknown_outcome_infrastructure_failure",
            post_attempt_checkpoint=b"{}",
            failure_code="unknown",
        )
    assert journal.integrity_report()["event_count"] == 2


def test_v5_journal_allows_one_content_object_to_have_multiple_roles(tmp_path: Path) -> None:
    journal = V5AttemptJournal(tmp_path / "roles.sqlite")
    digest = journal.put_object("parsed_action_candidate", b"same-bytes")
    assert journal.put_object("sealed_action", b"same-bytes") == digest
    assert journal.get_object(digest, expected_kind="parsed_action_candidate") == b"same-bytes"
    assert journal.get_object(digest, expected_kind="sealed_action") == b"same-bytes"


def test_v5_policy_reset_discards_cross_episode_state() -> None:
    policy = scripted_policy(5000)
    initial = policy.reset("instruction")
    state = json.loads(initial)
    state["history"].append({"task_specific": True})
    reset = json.loads(policy.reset("instruction"))
    assert reset["history"] == [] and reset["action_index"] == 0
    assert policy.reset_calls == 2


def test_v5_policy_manifest_identity_changes_with_adapter() -> None:
    first = policy_manifest()
    values = first.__dict__.copy()
    values.pop("policy_id")
    values["coordinate_adapter"] = "different-adapter"
    second = PolicyManifest.build(**values)
    assert first.policy_id != second.policy_id


def test_v5_capability_contract_rejects_all_forbidden_handles() -> None:
    for capability in sorted(policy_manifest().sandbox.REQUIRED_DENIALS):
        with pytest.raises(ValueError, match="forbidden"):
            validate_capability_handles({capability: object()})


def test_v5_plan_only_command_formula_makes_no_provider_calls() -> None:
    manifest = policy_manifest()
    plan = call_cap_plan(manifest)
    assert plan["provider_calls_made"] == 0
    for phase in plan["phases"].values():
        assert phase["provider_wire_request_cap"] == (
            phase["model_attempt_cap"] + phase["provider_control_request_cap"]
        )


def test_v5_model_cap_is_checked_before_attempt_started_or_transport(tmp_path: Path) -> None:
    seed = 5000
    task = generate_task(seed)
    journal = V5AttemptJournal(tmp_path / "zero-cap.sqlite")
    transport = ScriptedTransport()
    with pytest.raises(RuntimeError, match="model-attempt cap"):
        V5Runner(
            journal=journal,
            manifest=policy_manifest(),
            transport=transport,
            policy=scripted_policy(seed),
            approved_caps=CallCaps(task.max_episode_steps, 0, 0, 0),
        ).run(trial_id="trial-zero-cap", task=task)
    assert not transport.model_requests
    assert "attempt_started" not in {
        event.kind for event in journal.events("trial-zero-cap")
    }


def test_v5_restart_reconstructs_run_wide_call_counts_and_enforces_cap(
    tmp_path: Path,
) -> None:
    seed = 5000
    task = generate_task(seed)
    journal = V5AttemptJournal(tmp_path / "restart-cap.sqlite")
    identity = AttemptIdentity("prior-process", 0, 0)
    journal.record_attempt_started(
        identity,
        provider_endpoint_identity="http://127.0.0.1:9999",
        request_digest="sha256:" + "a" * 64,
        idempotency_key="prior-model-attempt",
        model_attempt_reservation=1,
        control_request_reservation=1,
        pre_call_checkpoint=b"{}",
    )
    journal.record_control_request_reserved(identity, request_kind="reconcile")

    transport = ScriptedTransport()
    restarted = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(task.max_episode_steps, 1, 1, 2),
    )
    assert restarted.model_attempts == 1
    assert restarted.control_requests == 1
    with pytest.raises(RuntimeError, match="model-attempt cap"):
        restarted.run(trial_id="after-restart", task=task)
    assert not transport.model_requests
    assert "attempt_started" not in {
        event.kind for event in journal.events("after-restart")
    }


def test_v5_restart_never_reissues_durably_reserved_control_request(tmp_path: Path) -> None:
    seed = 5000
    journal = V5AttemptJournal(tmp_path / "restart-control.sqlite")
    identity = AttemptIdentity("interrupted-control", 0, 0)
    journal.record_attempt_started(
        identity,
        provider_endpoint_identity="http://127.0.0.1:9999",
        request_digest="sha256:" + "a" * 64,
        idempotency_key="unknown-control-outcome",
        model_attempt_reservation=1,
        control_request_reservation=1,
        pre_call_checkpoint=b"{}",
    )
    journal.record_control_request_reserved(identity, request_kind="reconcile")
    transport = ScriptedTransport()
    recovered = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=episode_caps(seed),
    ).recover_step(
        trial_id=identity.trial_id,
        step_index=identity.step_index,
        task=generate_task(seed),
        backend=V5FakeBackend(),
    )
    assert recovered == {
        "classification": "infrastructure_failure",
        "reason": "control_request_reserved_without_settlement",
        "redispatched": False,
    }
    assert not transport.control_requests


def test_v5_restart_enforces_durable_control_request_cap(tmp_path: Path) -> None:
    seed = 5000
    journal = V5AttemptJournal(tmp_path / "restart-control-cap.sqlite")
    prior = AttemptIdentity("prior-control", 0, 0)
    journal.record_attempt_started(
        prior,
        provider_endpoint_identity="http://127.0.0.1:9999",
        request_digest="sha256:" + "a" * 64,
        idempotency_key="prior-control",
        model_attempt_reservation=1,
        control_request_reservation=1,
        pre_call_checkpoint=b"{}",
    )
    journal.record_control_request_reserved(prior, request_kind="cancel")
    current = AttemptIdentity("current-control", 0, 0)
    journal.record_attempt_started(
        current,
        provider_endpoint_identity="http://127.0.0.1:9999",
        request_digest="sha256:" + "b" * 64,
        idempotency_key="current-control",
        model_attempt_reservation=1,
        control_request_reservation=1,
        pre_call_checkpoint=b"{}",
    )
    transport = ScriptedTransport()
    runner = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(40, 2, 1, 3),
    )
    with pytest.raises(RuntimeError, match="provider-control-request cap"):
        runner.recover_step(
            trial_id=current.trial_id,
            step_index=0,
            task=generate_task(seed),
            backend=V5FakeBackend(),
        )
    assert not transport.control_requests
    assert runner.control_requests == 1


def test_v5_restart_enforces_durable_total_wire_cap(tmp_path: Path) -> None:
    seed = 5000
    task = generate_task(seed)
    journal = V5AttemptJournal(tmp_path / "restart-wire-cap.sqlite")
    identity = AttemptIdentity("prior-wire", 0, 0)
    journal.record_attempt_started(
        identity,
        provider_endpoint_identity="http://127.0.0.1:9999",
        request_digest="sha256:" + "a" * 64,
        idempotency_key="prior-wire",
        model_attempt_reservation=1,
        control_request_reservation=1,
        pre_call_checkpoint=b"{}",
    )
    journal.record_control_request_reserved(identity, request_kind="reconcile")
    transport = ScriptedTransport()
    runner = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(task.max_episode_steps, 2, 1, 2),
    )
    with pytest.raises(RuntimeError, match="provider-wire-request cap"):
        runner.run(trial_id="after-wire-cap", task=task)
    assert not transport.model_requests


@pytest.mark.parametrize(
    ("boundary", "expected", "recovered_dispatch"),
    [
        ("attempt_started", "infrastructure_failure", False),
        ("provider_receipt", "dispatched", True),
        ("canonical_response_persisted", "dispatched", True),
        ("attempt_terminal", "dispatched", True),
        ("before_parse", "dispatched", True),
        ("parsed_action_candidate", "dispatched", True),
        ("sealed_action_intent", "dispatched", True),
        ("before_dispatch", "dispatched", True),
        ("dispatch_started", "infrastructure_failure", False),
        ("backend_accepted", "infrastructure_failure", False),
        ("dispatch_committed", "already_committed", False),
    ],
)
def test_v5_forced_interruption_recovers_without_duplicate_request_or_action(
    tmp_path: Path, boundary: str, expected: str, recovered_dispatch: bool
) -> None:
    seed = 5000
    task = generate_task(seed)
    journal = V5AttemptJournal(tmp_path / f"{boundary}.sqlite")
    transport = ScriptedTransport()
    original_policy = scripted_policy(seed)
    interrupted = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=original_policy,
        approved_caps=episode_caps(seed),
        interrupt_after=boundary,
    )
    with pytest.raises(InjectedInterruption, match=boundary):
        interrupted.run(trial_id=f"trial-{boundary}", task=task)

    recovery_policy = scripted_policy(seed)
    recovery_backend = V5FakeBackend()
    recovered = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=recovery_policy,
        approved_caps=episode_caps(seed),
    ).recover_step(
        trial_id=f"trial-{boundary}",
        step_index=0,
        task=task,
        backend=recovery_backend,
    )
    assert recovered["classification"] == expected
    assert recovered.get("redispatched", False) is recovered_dispatch
    assert len(transport.model_requests) <= 1
    assert recovery_backend.action_count == int(recovered_dispatch)
    if boundary == "parsed_action_candidate":
        assert original_policy.parse_calls == 1
        assert recovery_policy.parse_calls == 0
    if boundary == "provider_receipt":
        assert [kind for kind, _identity in transport.control_requests] == ["reconcile"]


def test_v5_recovery_rejects_tampered_environment_binding(tmp_path: Path) -> None:
    seed = 5000
    task = generate_task(seed)
    journal = V5AttemptJournal(tmp_path / "tampered.sqlite")
    with pytest.raises(InjectedInterruption):
        V5Runner(
            journal=journal,
            manifest=policy_manifest(),
            transport=ScriptedTransport(),
            policy=scripted_policy(seed),
            approved_caps=episode_caps(seed),
            interrupt_after="sealed_action_intent",
        ).run(trial_id="trial-tampered", task=task)
    backend = V5FakeBackend()
    backend.reset(5001)
    # Recovery restores from the sealed checkpoint rather than trusting the
    # caller's current backend state, then verifies every binding.
    recovered = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=ScriptedTransport(),
        policy=scripted_policy(seed),
        approved_caps=episode_caps(seed),
    ).recover_step(
        trial_id="trial-tampered", step_index=0, task=task, backend=backend
    )
    assert recovered["classification"] == "dispatched"
    assert backend.task.task_id == task.task_id
