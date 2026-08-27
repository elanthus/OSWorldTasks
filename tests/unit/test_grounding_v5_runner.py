"""No-cost journal, settlement, call-cap, and policy-state tests for v5."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import (
    AttemptIdentity,
    CallCaps,
    Partition,
    PolicyManifest,
    content_digest,
)
from pixelgym.grounding.v5.coordinates import IDENTITY_ADAPTER
from pixelgym.grounding.v5.fixtures import scripted_policy_manifest
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import JournalConflictError, V5AttemptJournal
from pixelgym.grounding.v5.manifests import partition_manifest
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
from pixelgym.serialization import canonical_json_bytes

ROOT = Path(__file__).parents[2]


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


class ZeroCompletionRetryPolicy(ScriptedStatefulPolicy):
    def retryable_response_code(self, canonical_response: bytes) -> str | None:
        response = json.loads(canonical_response)
        usage = response["usage"]
        if (
            response["finish_reason"] == "error"
            and response["content"] == ""
            and usage.get("completion_tokens") == 0
            and usage.get("cost") == "0"
        ):
            return "zero_completion_error"
        return None


def retry_manifest() -> PolicyManifest:
    values = policy_manifest().__dict__.copy()
    values.pop("policy_id")
    values.update(
        {
            "max_model_attempts_per_action": 2,
            "max_cancellation_requests_per_attempt": 0,
            "max_reconciliation_requests_per_attempt": 0,
            "transport_retry_rule": "one-same-route-zero-completion-error-v1",
        }
    )
    return PolicyManifest.build(**values)


def zero_completion_error_response(response_id: str) -> dict[str, object]:
    return {
        "response_id": response_id,
        "model": "no-cost-scripted-policy",
        "content": "",
        "finish_reason": "error",
        "usage": {"prompt_tokens": 1, "completion_tokens": 0, "cost": "0"},
    }


def reserve_prior_attempt(
    journal: V5AttemptJournal,
    identity: AttemptIdentity,
    *,
    request_digest: str,
    idempotency_key: str,
) -> None:
    journal.record_attempt_started(
        identity,
        provider_endpoint_identity="http://127.0.0.1:9999",
        request_digest=request_digest,
        idempotency_key=idempotency_key,
        model_attempt_reservation=1,
        control_request_reservation=1,
        pre_call_checkpoint=b"{}",
    )


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


def test_v5_runner_supports_a_bounded_multi_task_pilot_horizon(tmp_path: Path) -> None:
    seed = 5000
    task = generate_task(seed)
    journal = V5AttemptJournal(tmp_path / "journal.sqlite")
    transport = ScriptedTransport()
    result = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(2, 2, 4, 6),
    ).run(trial_id="trial-two-action-pilot", task=task, action_limit=2)

    assert result.classification == "pilot_action_limit"
    assert result.environment_actions == 2
    assert result.model_attempts == 2
    assert len(transport.model_requests) == 2


@pytest.mark.parametrize("action_limit", [0, -1, 10_000, 1.5, True])
def test_v5_runner_rejects_invalid_pilot_action_limit(
    tmp_path: Path, action_limit: object
) -> None:
    seed = 5000
    task = generate_task(seed)
    runner = V5Runner(
        journal=V5AttemptJournal(tmp_path / f"journal-{action_limit}.sqlite"),
        manifest=policy_manifest(),
        transport=ScriptedTransport(),
        policy=scripted_policy(seed),
        approved_caps=episode_caps(seed),
    )
    with pytest.raises(ValueError, match="action_limit"):
        runner.run(trial_id="trial-invalid-limit", task=task, action_limit=action_limit)  # type: ignore[arg-type]


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
            self.release.wait(timeout=5.0)
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


def test_v5_runner_retries_one_zero_completion_error_and_retains_both_attempts(
    tmp_path: Path,
) -> None:
    seed = 5000
    task = generate_task(seed)
    base_policy = scripted_policy(seed)
    policy = ZeroCompletionRetryPolicy(base_policy.actions)
    transport = ScriptedTransport(
        [TransportOutcome("response", zero_completion_error_response("empty-first"))]
    )
    journal = V5AttemptJournal(tmp_path / "retry-success.sqlite")

    result = V5Runner(
        journal=journal,
        manifest=retry_manifest(),
        transport=transport,
        policy=policy,
        approved_caps=CallCaps(1, 2, 0, 2),
    ).run(trial_id="trial-retry-success", task=task, action_limit=1)

    assert result.classification == "pilot_action_limit"
    assert result.environment_actions == 1
    assert result.model_attempts == result.provider_wire_requests == 2
    events = journal.events("trial-retry-success")
    assert [event.kind for event in events].count("attempt_started") == 2
    assert [event.kind for event in events].count("attempt_completed") == 2
    retry_event = next(
        event for event in events if event.kind == "retryable_provider_response"
    )
    assert retry_event.payload["failure_code"] == "zero_completion_error"
    assert retry_event.payload["next_attempt_permitted"] is True
    assert retry_event.payload["retry_rule"] == "one-same-route-zero-completion-error-v1"
    assert retry_event.payload["response_digest"].startswith("sha256:")
    starts = [event for event in events if event.kind == "attempt_started"]
    assert starts[0].payload["request_digest"] == starts[1].payload["request_digest"]
    assert starts[0].payload["idempotency_key"] != starts[1].payload["idempotency_key"]
    candidate = next(event for event in events if event.kind == "parsed_action_candidate")
    assert candidate.payload["attempt_identities"] == [
        "trial-retry-success/step-0000/attempt-00",
        "trial-retry-success/step-0000/attempt-01",
    ]


def test_v5_runner_stops_after_second_zero_completion_error(tmp_path: Path) -> None:
    seed = 5000
    base_policy = scripted_policy(seed)
    transport = ScriptedTransport(
        [
            TransportOutcome("response", zero_completion_error_response("empty-first")),
            TransportOutcome("response", zero_completion_error_response("empty-second")),
        ]
    )
    journal = V5AttemptJournal(tmp_path / "retry-exhausted.sqlite")

    result = V5Runner(
        journal=journal,
        manifest=retry_manifest(),
        transport=transport,
        policy=ZeroCompletionRetryPolicy(base_policy.actions),
        approved_caps=CallCaps(1, 2, 0, 2),
    ).run(
        trial_id="trial-retry-exhausted",
        task=generate_task(seed),
        action_limit=1,
    )

    assert result.classification == "infrastructure_failure"
    assert result.environment_actions == 0
    assert result.model_attempts == result.provider_wire_requests == 2
    events = journal.events("trial-retry-exhausted")
    assert [event.kind for event in events].count("retryable_provider_response") == 2
    failure = next(
        event for event in events if event.kind == "sealed_unsuccessful_result"
    )
    assert failure.payload["failure_code"] == "retryable_response_exhausted"


def test_v5_parse_failure_after_retry_binds_both_attempts(tmp_path: Path) -> None:
    seed = 5000
    base_policy = scripted_policy(seed)
    malformed_response = {
        "response_id": "malformed-second",
        "model": "no-cost-scripted-policy",
        "content": "not-json",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    journal = V5AttemptJournal(tmp_path / "retry-parse-failure.sqlite")

    result = V5Runner(
        journal=journal,
        manifest=retry_manifest(),
        transport=ScriptedTransport(
            [
                TransportOutcome(
                    "response", zero_completion_error_response("empty-first")
                ),
                TransportOutcome("response", malformed_response),
            ]
        ),
        policy=ZeroCompletionRetryPolicy(base_policy.actions),
        approved_caps=CallCaps(1, 2, 0, 2),
    ).run(
        trial_id="trial-retry-parse-failure",
        task=generate_task(seed),
        action_limit=1,
    )

    assert result.classification == "invalid_output"
    failure = next(
        event
        for event in journal.events("trial-retry-parse-failure")
        if event.kind == "sealed_unsuccessful_result"
    )
    assert failure.attempt_index == 1
    assert failure.payload["attempt_identities"] == [
        "trial-retry-parse-failure/step-0000/attempt-00",
        "trial-retry-parse-failure/step-0000/attempt-01",
    ]


def test_v5_recovery_never_issues_an_unrecorded_retry(tmp_path: Path) -> None:
    seed = 5000
    base_policy = scripted_policy(seed)
    transport = ScriptedTransport(
        [TransportOutcome("response", zero_completion_error_response("empty-first"))]
    )
    journal = V5AttemptJournal(tmp_path / "retry-interrupted.sqlite")
    trial_id = "trial-retry-interrupted"
    with pytest.raises(InjectedInterruption, match="retryable_provider_response"):
        V5Runner(
            journal=journal,
            manifest=retry_manifest(),
            transport=transport,
            policy=ZeroCompletionRetryPolicy(base_policy.actions),
            approved_caps=CallCaps(1, 2, 0, 2),
            interrupt_after="retryable_provider_response",
        ).run(trial_id=trial_id, task=generate_task(seed), action_limit=1)

    recovered = V5Runner(
        journal=journal,
        manifest=retry_manifest(),
        transport=transport,
        policy=ZeroCompletionRetryPolicy(base_policy.actions),
        approved_caps=CallCaps(1, 2, 0, 2),
    ).recover_step(
        trial_id=trial_id,
        step_index=0,
        task=generate_task(seed),
        backend=V5FakeBackend(),
    )

    assert recovered["classification"] == "infrastructure_failure"
    assert recovered["reason"] == "retry_interrupted_before_next_attempt"
    assert len(transport.model_requests) == 1
    failure = next(
        event
        for event in journal.events(trial_id)
        if event.kind == "sealed_unsuccessful_result"
    )
    assert failure.payload["failure_code"] == "retry_interrupted_before_next_attempt"


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


def test_v5_recovery_seals_parse_failure_without_dispatch(tmp_path: Path) -> None:
    seed = 5000
    response = {
        "response_id": "malformed-recovery",
        "model": "no-cost-scripted-policy",
        "content": "not-json",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    journal = V5AttemptJournal(tmp_path / "recovery-parse.sqlite")
    transport = ScriptedTransport([TransportOutcome("response", response)])
    trial_id = "trial-recovery-parse"
    with pytest.raises(InjectedInterruption, match="canonical_response_persisted"):
        V5Runner(
            journal=journal,
            manifest=policy_manifest(),
            transport=transport,
            policy=scripted_policy(seed),
            approved_caps=episode_caps(seed),
            interrupt_after="canonical_response_persisted",
        ).run(trial_id=trial_id, task=generate_task(seed))

    backend = V5FakeBackend()
    recovered = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=episode_caps(seed),
    ).recover_step(
        trial_id=trial_id,
        step_index=0,
        task=generate_task(seed),
        backend=backend,
    )
    assert recovered["classification"] == "invalid_output"
    assert recovered["redispatched"] is False
    assert backend.action_count == 0
    assert [event.kind for event in journal.events(trial_id)][-1] == (
        "sealed_unsuccessful_result"
    )
    assert len(transport.model_requests) == 1


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


def test_v5_journal_object_and_role_insert_roll_back_together(tmp_path: Path) -> None:
    journal = V5AttemptJournal(tmp_path / "object-transaction.sqlite")
    journal._connection.execute(
        """
        CREATE TRIGGER reject_object_role
        BEFORE INSERT ON object_roles
        BEGIN
          SELECT RAISE(ABORT, 'injected interruption');
        END
        """
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected interruption"):
        journal.put_object("policy_checkpoint", b"transactional-object")
    object_count = journal._connection.execute(
        "SELECT COUNT(*) FROM objects"
    ).fetchone()[0]
    assert object_count == 0


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
    partitions = {partition: partition_manifest(partition) for partition in Partition}
    plan = call_cap_plan(
        manifest,
        partition_manifests=partitions,
        approved_calibration_manifest_digest=(
            partitions[Partition.CALIBRATION]["manifest_digest"]
        ),
    )
    assert plan["provider_calls_made"] == 0
    assert plan["policy_manifest_digest"] == content_digest(manifest.to_dict())
    assert plan["partition_manifest_digests"] == {
        partition.value: partitions[partition]["manifest_digest"]
        for partition in Partition
    }
    for phase in plan["phases"].values():
        assert phase["provider_wire_request_cap"] == (
            phase["model_attempt_cap"] + phase["provider_control_request_cap"]
        )


def test_v5_call_plan_rejects_tampered_partition_manifest() -> None:
    partitions = {partition: partition_manifest(partition) for partition in Partition}
    partitions[Partition.CALIBRATION]["records"][0]["max_episode_steps"] += 1
    with pytest.raises(ValueError, match="calibration partition manifest digest mismatch"):
        call_cap_plan(
            policy_manifest(),
            partition_manifests=partitions,
            approved_calibration_manifest_digest=(
                partitions[Partition.CALIBRATION]["manifest_digest"]
            ),
        )


def test_v5_call_plan_rejects_unapproved_calibration_manifest_digest() -> None:
    partitions = {partition: partition_manifest(partition) for partition in Partition}
    with pytest.raises(
        ValueError, match="approved calibration partition manifest digest mismatch"
    ):
        call_cap_plan(
            policy_manifest(),
            partition_manifests=partitions,
            approved_calibration_manifest_digest="sha256:" + "0" * 64,
        )


def test_v5_call_plan_uses_sealed_partition_action_caps() -> None:
    partitions = {partition: partition_manifest(partition) for partition in Partition}
    revised = deepcopy(partitions[Partition.CALIBRATION])
    revised["records"][0]["max_episode_steps"] += 1
    unsigned = dict(revised)
    unsigned.pop("manifest_digest")
    revised["manifest_digest"] = content_digest(unsigned)
    partitions[Partition.CALIBRATION] = revised

    plan = call_cap_plan(
        policy_manifest(),
        partition_manifests=partitions,
        approved_calibration_manifest_digest=revised["manifest_digest"],
    )
    assert plan["phases"]["calibration"]["environment_action_cap"] == sum(
        record["max_episode_steps"] for record in revised["records"]
    )


def test_v5_checked_in_scripted_cap_plan_uses_checked_in_partition_bytes() -> None:
    partitions = {
        partition: json.loads(
            (
                ROOT
                / "artifacts/grounding-v5-manifests"
                / f"{partition.value}.json"
            ).read_text(encoding="utf-8")
        )
        for partition in Partition
    }
    expected = call_cap_plan(
        scripted_policy_manifest(),
        partition_manifests=partitions,
        approved_calibration_manifest_digest=(
            partitions[Partition.CALIBRATION]["manifest_digest"]
        ),
    )
    stored = ROOT / "artifacts/grounding-v5-scripted-call-cap-plan.json"
    assert stored.read_bytes() == canonical_json_bytes(expected) + b"\n"


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
    reserve_prior_attempt(
        journal,
        identity,
        request_digest="sha256:" + "a" * 64,
        idempotency_key="prior-model-attempt",
    )
    journal.record_control_request_reserved(identity, request_kind="reconcile")

    transport = ScriptedTransport()
    restarted = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(task.max_episode_steps, 1, 2, 4),
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
    reserve_prior_attempt(
        journal,
        identity,
        request_digest="sha256:" + "a" * 64,
        idempotency_key="unknown-control-outcome",
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
    reserve_prior_attempt(
        journal,
        prior,
        request_digest="sha256:" + "a" * 64,
        idempotency_key="prior-control",
    )
    journal.record_control_request_reserved(prior, request_kind="cancel")
    current = AttemptIdentity("current-control", 0, 0)
    reserve_prior_attempt(
        journal,
        current,
        request_digest="sha256:" + "b" * 64,
        idempotency_key="current-control",
    )
    transport = ScriptedTransport()
    runner = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(40, 3, 1, 5),
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
    reserve_prior_attempt(
        journal,
        identity,
        request_digest="sha256:" + "a" * 64,
        idempotency_key="prior-wire",
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


def test_v5_concurrent_runners_cannot_overreserve_model_cap(tmp_path: Path) -> None:
    path = tmp_path / "atomic-cap.sqlite"
    journals = (V5AttemptJournal(path), V5AttemptJournal(path))
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def reserve(index: int) -> None:
        identity = AttemptIdentity(f"concurrent-{index}", 0, 0)
        barrier.wait()
        try:
            _event, created = journals[index].reserve_attempt_started(
                identity,
                provider_endpoint_identity="http://127.0.0.1:9999",
                request_digest="sha256:" + str(index) * 64,
                idempotency_key=f"concurrent-{index}",
                model_attempt_reservation=1,
                control_request_reservation=0,
                pre_call_checkpoint=b"{}",
                approved_caps=CallCaps(1, 1, 0, 1),
            )
            outcomes.append("created" if created else "duplicate")
        except RuntimeError as exc:
            outcomes.append(str(exc))

    threads = [threading.Thread(target=reserve, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["approved model-attempt cap reached", "created"]
    assert journals[0].call_counts() == (1, 0)


def test_v5_recovery_with_reconciliation_disabled_sends_no_control_call(
    tmp_path: Path,
) -> None:
    seed = 5000
    task = generate_task(seed)
    policy = scripted_policy(seed)
    journal = V5AttemptJournal(tmp_path / "reconciliation-disabled.sqlite")
    identity = AttemptIdentity("no-reconciliation", 0, 0)
    journal.record_attempt_started(
        identity,
        provider_endpoint_identity="http://127.0.0.1:9999",
        request_digest="sha256:" + "a" * 64,
        idempotency_key="no-reconciliation",
        model_attempt_reservation=1,
        control_request_reservation=0,
        pre_call_checkpoint=policy.reset(task.instruction),
    )
    base = policy_manifest()
    values = base.__dict__.copy()
    values.pop("policy_id")
    values["max_reconciliation_requests_per_attempt"] = 0
    values["reconciliation_deadline_seconds"] = 0.0
    manifest = PolicyManifest.build(**values)
    transport = ScriptedTransport()
    recovered = V5Runner(
        journal=journal,
        manifest=manifest,
        transport=transport,
        policy=policy,
        approved_caps=CallCaps(40, 1, 0, 1),
    ).recover_step(
        trial_id=identity.trial_id,
        step_index=0,
        task=task,
        backend=V5FakeBackend(),
    )
    assert recovered["reason"] == "reconciliation_disabled"
    assert not transport.control_requests
    assert journal.terminal_attempt(identity).kind == "unknown_outcome_infrastructure_failure"


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
