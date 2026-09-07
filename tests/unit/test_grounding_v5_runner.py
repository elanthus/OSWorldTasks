"""No-cost journal, settlement, call-cap, and policy-state tests for v5."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
from copy import deepcopy
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import (
    AttemptIdentity,
    CallCaps,
    CliFault,
    CliFaultKind,
    CostKnowledge,
    ModelAttemptConsumption,
    Partition,
    PolicyManifest,
    content_digest,
)
from pixelgym.grounding.v5.coordinates import IDENTITY_ADAPTER
from pixelgym.grounding.v5.fixtures import scripted_policy_manifest
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import JournalConflictError, V5AttemptJournal
from pixelgym.grounding.v5.manifests import partition_manifest
from pixelgym.grounding.v5.panel_policy import SpendLedger
from pixelgym.grounding.v5.planning import call_cap_plan
from pixelgym.grounding.v5.policies import golden_actions
from pixelgym.grounding.v5.runner import (
    InjectedInterruption,
    PolicyVisibleResult,
    ScriptedStatefulPolicy,
    ScriptedTransport,
    TransportOutcome,
    V5Runner,
    attempted_episode_count,
    summarize_outcome_denominators,
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


def reconciliation_manifest(limit: int) -> PolicyManifest:
    values = policy_manifest().__dict__.copy()
    values.pop("policy_id")
    values["max_cancellation_requests_per_attempt"] = 0
    values["max_reconciliation_requests_per_attempt"] = limit
    values["reconciliation_deadline_seconds"] = float(limit)
    return PolicyManifest.build(**values)


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


class CapturingPolicy(ScriptedStatefulPolicy):
    """Records the complete argument objects supplied at every policy hook."""

    def __init__(self, actions: tuple[dict[str, int], ...]) -> None:
        super().__init__(actions)
        self.received: list[tuple[str, tuple[object, ...]]] = []

    def _capture(self, hook: str, *values: object) -> None:
        self.received.append((hook, values))

    def reset(self, task_instruction: str) -> bytes:
        self._capture("reset", task_instruction)
        return super().reset(task_instruction)

    def build_request(self, state: bytes, screenshot: bytes) -> dict[str, Any]:
        self._capture("build_request", state, screenshot)
        return super().build_request(state, screenshot)

    def reduce_state(self, state: bytes, canonical_response: bytes) -> bytes:
        self._capture("reduce_state", state, canonical_response)
        return super().reduce_state(state, canonical_response)

    def failure_state(self, state: bytes, failure_code: str) -> bytes:
        self._capture("failure_state", state, failure_code)
        return super().failure_state(state, failure_code)

    def retryable_response_code(self, canonical_response: bytes) -> str | None:
        self._capture("retryable_response_code", canonical_response)
        return super().retryable_response_code(canonical_response)

    def parse(self, canonical_response: bytes, state: bytes) -> dict[str, Any]:
        self._capture("parse", canonical_response, state)
        return super().parse(canonical_response, state)

    def post_parse_state(self, state: bytes, candidate: dict[str, Any]) -> bytes:
        self._capture("post_parse_state", state, candidate)
        return super().post_parse_state(state, candidate)

    def post_dispatch_state(
        self,
        state: bytes,
        action: dict[str, int],
        result: PolicyVisibleResult,
    ) -> bytes:
        self._capture("post_dispatch_state", state, action, result)
        return super().post_dispatch_state(state, action, result)

    def close(self) -> None:
        self._capture("close")
        super().close()


class DiagnosticSentinelBackend(V5FakeBackend):
    def read_privileged_diagnostic(self) -> dict[str, object]:
        return {
            **super().read_privileged_diagnostic(),
            "expected_values": "host-only-expected-value-7dfc",
            "boxes": [[11, 22, 33, 44]],
            "backend_checkpoint": "host-only-backend-checkpoint-8ab1",
            "diagnostic": "host-only-diagnostic-2c94",
            "wrong_irreversible_commit": True,
        }


def capturing_policy(seed: int) -> CapturingPolicy:
    return CapturingPolicy(scripted_policy(seed).actions)


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


def rate_limit_retry_manifest() -> PolicyManifest:
    values = retry_manifest().__dict__.copy()
    values.pop("policy_id")
    values.update(
        {
            "transport_retry_rule": (
                "one-same-route-zero-completion-or-http-429-after-bounded-backoff-v2"
            ),
            "inference_parameters": (
                ("max_rate_limit_retries_per_action", "1"),
                ("rate_limit_backoff_base_seconds", "2.0"),
                ("rate_limit_backoff_max_seconds", "60.0"),
                ("runner_deadline_safety_margin_seconds", "1.0"),
            ),
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
        "privileged_dispatch_diagnostic",
        "dispatch_committed",
    ]
    assert len(transport.model_requests) == task.optimal_low_level_actions
    assert not transport.control_requests
    assert journal.integrity_report()["event_count"] > 0


def test_policy_visible_result_has_one_strict_authorized_schema() -> None:
    value = PolicyVisibleResult(
        screenshot_digest="sha256:" + "a" * 64,
        reward=0.0,
        terminated=False,
        truncated=True,
        step_index=7,
    )

    assert {field.name for field in fields(PolicyVisibleResult)} == {
        "screenshot_digest",
        "reward",
        "terminated",
        "truncated",
        "step_index",
    }
    assert PolicyVisibleResult.from_dict(value.to_dict()) == value
    with pytest.raises(ValueError, match="allowed schema"):
        PolicyVisibleResult.from_dict({**value.to_dict(), "diagnostic": {}})
    with pytest.raises(TypeError, match="reward"):
        PolicyVisibleResult(
            screenshot_digest=value.screenshot_digest,
            reward=0,  # type: ignore[arg-type]
            terminated=False,
            truncated=False,
            step_index=0,
        )
    with pytest.raises(TypeError, match="screenshot digest"):
        PolicyVisibleResult.from_dict(
            {**value.to_dict(), "screenshot_digest": 7}
        )
    with pytest.raises(TypeError, match="episode flags"):
        PolicyVisibleResult(
            screenshot_digest=value.screenshot_digest,
            reward=0.0,
            terminated=0,  # type: ignore[arg-type]
            truncated=False,
            step_index=0,
        )


def test_runner_never_exposes_privileged_dispatch_state_to_any_policy_hook(
    tmp_path: Path,
) -> None:
    seed = 5000
    policy = capturing_policy(seed)
    backend = DiagnosticSentinelBackend()
    journal = V5AttemptJournal(tmp_path / "policy-visible.sqlite")

    result = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=ScriptedTransport(),
        policy=policy,
        approved_caps=episode_caps(seed),
    ).run(
        trial_id="trial-policy-visible",
        task=generate_task(seed),
        backend=backend,
        action_limit=1,
    )

    assert result.classification == "pilot_action_limit"
    received_result = next(
        values[2]
        for hook, values in policy.received
        if hook == "post_dispatch_state"
    )
    assert isinstance(received_result, PolicyVisibleResult)
    assert not hasattr(received_result, "__dict__")
    assert received_result.step_index == 0
    structured_arguments = [
        value
        for _hook, values in policy.received
        for value in values
        if not isinstance(value, (bytes, bytearray))
    ]
    received_text = repr(structured_arguments)
    for forbidden in (
        "host-only-diagnostic-2c94",
        "host-only-expected-value-7dfc",
        "host-only-backend-checkpoint-8ab1",
        "stage_index",
        "irreversible_failure",
        "wrong_irreversible_commit",
        "expected_values",
        "boxes",
        "backend_checkpoint",
        "diagnostic",
    ):
        assert forbidden not in received_text

    dispatch = journal.event("trial-policy-visible/step-0000/dispatch_committed")
    assert dispatch is not None
    assert "diagnostic" not in dispatch.payload
    checkpoint = journal.get_object(
        dispatch.payload["post_dispatch_checkpoint_digest"],
        expected_kind="policy_checkpoint",
    )
    for forbidden in (
        b"host-only-diagnostic-2c94",
        b"host-only-expected-value-7dfc",
        b"host-only-backend-checkpoint-8ab1",
        b"stage_index",
        b"irreversible_failure",
        b"wrong_irreversible_commit",
        b"expected_values",
        b"boxes",
        b"backend_checkpoint",
        b"diagnostic",
    ):
        assert forbidden not in checkpoint

    diagnostic_event = journal.event(
        dispatch.payload["privileged_diagnostic_event_key"]
    )
    assert diagnostic_event is not None
    assert diagnostic_event.kind == "privileged_dispatch_diagnostic"
    assert diagnostic_event.payload["diagnostic"]["stage_index"] == 1
    assert (
        diagnostic_event.payload["diagnostic"]["expected_values"]
        == "host-only-expected-value-7dfc"
    )
    assert diagnostic_event.payload["policy_visible_result_digest"] == (
        dispatch.payload["commit_result_digest"]
    )
    assert diagnostic_event.payload["diagnostic_digest"] == (
        dispatch.payload["privileged_diagnostic_digest"]
    )


@pytest.mark.parametrize(
    "outcome",
    [
        TransportOutcome("deadline", failure_code="request_deadline"),
        TransportOutcome("pre_send_failure", failure_code="request_rejected"),
    ],
)
def test_timeout_and_error_paths_checkpoint_only_policy_supplied_state(
    tmp_path: Path, outcome: TransportOutcome
) -> None:
    seed = 5000
    policy = capturing_policy(seed)
    journal = V5AttemptJournal(tmp_path / f"{outcome.status}.sqlite")

    result = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=ScriptedTransport([outcome]),
        policy=policy,
        approved_caps=episode_caps(seed),
    ).run(trial_id=f"trial-{outcome.status}", task=generate_task(seed), action_limit=1)

    assert result.classification in {"request_failure", "infrastructure_failure"}
    hooks = [hook for hook, _values in policy.received]
    assert "failure_state" in hooks
    assert "post_dispatch_state" not in hooks
    terminal = next(
        event
        for event in journal.events(f"trial-{outcome.status}")
        if event.kind
        in {
            "confirmed_cancellation",
            "confirmed_no_response_timeout",
            "unknown_outcome_infrastructure_failure",
        }
    )
    checkpoint = journal.get_object(
        terminal.payload["post_attempt_checkpoint_digest"],
        expected_kind="policy_checkpoint",
    )
    assert b"diagnostic" not in checkpoint
    assert b"stage_index" not in checkpoint


def test_v5_runner_binds_transport_spend_ledger_to_attempt_journal(tmp_path: Path) -> None:
    class JournalBoundTransport(ScriptedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.spend_journal: V5AttemptJournal | None = None

        def bind_spend_journal(self, journal: V5AttemptJournal) -> None:
            self.spend_journal = journal

    journal = V5AttemptJournal(tmp_path / "spend-binding.sqlite")
    transport = JournalBoundTransport()

    V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(5000),
        approved_caps=episode_caps(5000),
    )

    assert transport.spend_journal is journal


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
    assert Counter(event.kind for event in journal.events("trial-timeout"))[
        "sealed_unsuccessful_result"
    ] == 1


@pytest.mark.parametrize(
    ("transport_status", "expected_settlement", "expected_classification"),
    [
        ("deadline", "unknown", "infrastructure_failure"),
        ("pre_send_failure", "zero", "request_failure"),
    ],
)
def test_v5_runner_settles_spend_when_provider_controls_are_disabled(
    tmp_path: Path,
    transport_status: str,
    expected_settlement: str,
    expected_classification: str,
) -> None:
    class SpendSettlementTransport(ScriptedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.settlements: list[tuple[str, str]] = []

        def send(
            self,
            request: dict[str, object],
            *,
            idempotency_key: str,
            deadline_seconds: float,
        ) -> TransportOutcome:
            del deadline_seconds
            self.model_requests.append(
                {"idempotency_key": idempotency_key, "request": request}
            )
            return TransportOutcome(transport_status)  # type: ignore[arg-type]

        def settle_unknown_spend(self, *, idempotency_key: str) -> None:
            self.settlements.append(("unknown", idempotency_key))

        def settle_zero_charge_spend(
            self, *, idempotency_key: str, reason: str
        ) -> None:
            del reason
            self.settlements.append(("zero", idempotency_key))

    values = policy_manifest().__dict__.copy()
    values.pop("policy_id")
    values.update(
        {
            "max_cancellation_requests_per_attempt": 0,
            "max_reconciliation_requests_per_attempt": 0,
        }
    )
    manifest = PolicyManifest.build(**values)
    transport = SpendSettlementTransport()
    result = V5Runner(
        journal=V5AttemptJournal(tmp_path / f"spend-{transport_status}.sqlite"),
        manifest=manifest,
        transport=transport,
        policy=scripted_policy(5000),
        approved_caps=CallCaps(1, 1, 0, 1),
    ).run(
        trial_id=f"trial-spend-{transport_status}",
        task=generate_task(5000),
        action_limit=1,
    )

    assert result.classification == expected_classification
    assert len(transport.model_requests) == 1
    assert transport.settlements == [
        (expected_settlement, transport.model_requests[0]["idempotency_key"])
    ]


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


@pytest.mark.parametrize("reconciliation_limit", [0, 1])
def test_v5_recover_step_returns_settled_failure_without_appending(
    tmp_path: Path, reconciliation_limit: int
) -> None:
    seed = 5000
    task = generate_task(seed)
    trial_id = f"trial-settled-http-400-{reconciliation_limit}"
    journal = V5AttemptJournal(tmp_path / f"settled-{reconciliation_limit}.sqlite")
    transport = ScriptedTransport(
        [TransportOutcome("unknown", failure_code="http_400_bad_request")]
    )
    manifest = reconciliation_manifest(reconciliation_limit)
    caps = CallCaps(
        task.max_episode_steps,
        1,
        reconciliation_limit,
        1 + reconciliation_limit,
    )

    with pytest.raises(InjectedInterruption, match="attempt_terminal"):
        V5Runner(
            journal=journal,
            manifest=manifest,
            transport=transport,
            policy=scripted_policy(seed),
            approved_caps=caps,
            interrupt_after="attempt_terminal",
        ).run(trial_id=trial_id, task=task, action_limit=1)

    events = journal.events(trial_id)
    assert [event.kind for event in events][-2:] == [
        "unknown_outcome_infrastructure_failure",
        "sealed_unsuccessful_result",
    ]
    before = journal.integrity_report()
    before_event_chain_digest = before["event_chain_digest"]
    recovered = V5Runner(
        journal=journal,
        manifest=manifest,
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=caps,
    ).recover_step(
        trial_id=trial_id,
        step_index=0,
        task=task,
        backend=V5FakeBackend(),
    )

    assert recovered == {
        "classification": "infrastructure_failure",
        "redispatched": False,
    }
    after = journal.integrity_report()
    assert after["event_chain_digest"] == before_event_chain_digest
    assert after == before
    assert len(transport.model_requests) == 1
    assert len(transport.control_requests) == reconciliation_limit


@pytest.mark.parametrize("reconciliation_limit", [0, 1])
def test_v5_recover_step_preserves_old_sealed_terminal_event_chain(
    tmp_path: Path, reconciliation_limit: int
) -> None:
    seed = 5000
    task = generate_task(seed)
    trial_id = f"old-sealed-http-400-{reconciliation_limit}"
    identity = AttemptIdentity(trial_id, 0, 0)
    journal = V5AttemptJournal(tmp_path / f"old-sealed-{reconciliation_limit}.sqlite")
    policy = scripted_policy(seed)
    pre_state = policy.reset(task.instruction)
    journal.record_attempt_started(
        identity,
        provider_endpoint_identity="http://127.0.0.1:9999",
        request_digest="sha256:" + "a" * 64,
        idempotency_key="old-http-400",
        model_attempt_reservation=1,
        control_request_reservation=reconciliation_limit,
        pre_call_checkpoint=pre_state,
    )
    journal.seal_attempt_terminal(
        identity,
        kind="unknown_outcome_infrastructure_failure",
        post_attempt_checkpoint=policy.failure_state(
            pre_state, "http_400_bad_request"
        ),
        failure_code="http_400_bad_request",
    )
    assert "sealed_unsuccessful_result" not in {
        event.kind for event in journal.events(trial_id)
    }
    before = journal.integrity_report()
    before_event_chain_digest = before["event_chain_digest"]
    transport = ScriptedTransport()

    recovered = V5Runner(
        journal=journal,
        manifest=reconciliation_manifest(reconciliation_limit),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(
            task.max_episode_steps,
            1,
            reconciliation_limit,
            1 + reconciliation_limit,
        ),
    ).recover_step(
        trial_id=trial_id,
        step_index=0,
        task=task,
        backend=V5FakeBackend(),
    )

    assert recovered == {
        "classification": "infrastructure_failure",
        "redispatched": False,
    }
    after = journal.integrity_report()
    assert after["event_chain_digest"] == before_event_chain_digest
    assert after == before
    assert not transport.control_requests


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


def test_v5_runner_retries_one_429_as_a_new_journaled_attempt(tmp_path: Path) -> None:
    seed = 5000
    task = generate_task(seed)
    policy = scripted_policy(seed)
    transport = ScriptedTransport(
        [
            TransportOutcome(
                "rate_limited",
                failure_code="http_429_rate_limit",
                retry_after_seconds=3.0,
                backoff_source="retry_after",
            )
        ]
    )
    journal = V5AttemptJournal(tmp_path / "rate-limit-retry.sqlite")

    result = V5Runner(
        journal=journal,
        manifest=rate_limit_retry_manifest(),
        transport=transport,
        policy=policy,
        approved_caps=CallCaps(1, 2, 0, 2),
    ).run(trial_id="trial-rate-limit-retry", task=task, action_limit=1)

    assert result.classification == "pilot_action_limit"
    assert result.model_attempts == result.provider_wire_requests == 2
    assert len(transport.model_requests) == 2
    events = journal.events("trial-rate-limit-retry")
    assert [event.kind for event in events].count("attempt_started") == 2
    assert [event.kind for event in events].count("confirmed_no_response_timeout") == 1
    rate_limit = next(event for event in events if event.kind == "retryable_rate_limit")
    assert rate_limit.payload == {
        "failure_code": "http_429_rate_limit",
        "retry_after_seconds": 3.0,
        "backoff_source": "retry_after",
        "retry_rule": (
            "one-same-route-zero-completion-or-http-429-after-bounded-backoff-v2"
        ),
        "next_attempt_permitted": True,
        "bounded_retries_used": 0,
        "bounded_retry_budget": 1,
    }
    starts = [event for event in events if event.kind == "attempt_started"]
    assert starts[0].payload["request_digest"] == starts[1].payload["request_digest"]
    assert starts[0].payload["idempotency_key"] != starts[1].payload["idempotency_key"]


def test_v5_runner_does_not_reissue_429_retry_after_interruption(tmp_path: Path) -> None:
    seed = 5000
    task = generate_task(seed)
    transport = ScriptedTransport(
        [
            TransportOutcome(
                "rate_limited",
                failure_code="http_429_rate_limit",
                retry_after_seconds=2.0,
                backoff_source="exponential_fallback",
            )
        ]
    )
    journal = V5AttemptJournal(tmp_path / "rate-limit-interrupted.sqlite")
    trial_id = "trial-rate-limit-interrupted"

    with pytest.raises(InjectedInterruption, match="retryable_rate_limit"):
        V5Runner(
            journal=journal,
            manifest=rate_limit_retry_manifest(),
            transport=transport,
            policy=scripted_policy(seed),
            approved_caps=CallCaps(1, 2, 0, 2),
            interrupt_after="retryable_rate_limit",
        ).run(trial_id=trial_id, task=task, action_limit=1)

    recovered = V5Runner(
        journal=journal,
        manifest=rate_limit_retry_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(1, 2, 0, 2),
    ).recover_step(
        trial_id=trial_id,
        step_index=0,
        task=task,
        backend=V5FakeBackend(),
    )

    assert recovered["classification"] == "infrastructure_failure"
    assert recovered["reason"] == "retry_interrupted_before_next_attempt"
    assert len(transport.model_requests) == 1


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


def test_v5_recovery_preserves_policy_violation_classification(tmp_path: Path) -> None:
    seed = 5000
    response = {
        "response_id": "policy-violation-recovery",
        "model": "no-cost-scripted-policy",
        "content": "",
        "finish_reason": "policy_violation",
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "policy_violation": "credential_shaped_output",
        },
    }
    journal = V5AttemptJournal(tmp_path / "recovery-policy.sqlite")
    transport = ScriptedTransport([TransportOutcome("policy_violation", response)])
    trial_id = "trial-recovery-policy"
    with pytest.raises(InjectedInterruption, match="canonical_response_persisted"):
        V5Runner(
            journal=journal,
            manifest=policy_manifest(),
            transport=transport,
            policy=scripted_policy(seed),
            approved_caps=episode_caps(seed),
            interrupt_after="canonical_response_persisted",
        ).run(trial_id=trial_id, task=generate_task(seed))

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
        backend=V5FakeBackend(),
    )

    assert recovered["classification"] == "policy_violation"
    assert recovered["redispatched"] is False
    assert len(transport.model_requests) == 1
    sealed_recovery = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=episode_caps(seed),
    ).recover_step(
        trial_id=trial_id,
        step_index=0,
        task=generate_task(seed),
        backend=V5FakeBackend(),
    )
    assert sealed_recovery["classification"] == "policy_violation"
    journal.close()


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


def test_v5_control_reservation_is_checked_before_attempt_started_or_transport(
    tmp_path: Path,
) -> None:
    seed = 5000
    task = generate_task(seed)
    journal = V5AttemptJournal(tmp_path / "control-reservation-cap.sqlite")
    transport = ScriptedTransport(
        [TransportOutcome("deadline", failure_code="request_deadline")]
    )
    runner = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(1, 1, 2, 1),
    )

    with pytest.raises(RuntimeError, match="provider-wire-request cap"):
        runner.run(trial_id="trial-control-reservation-cap", task=task, action_limit=1)

    events = journal.events("trial-control-reservation-cap")
    assert "attempt_started" not in {event.kind for event in events}
    assert len(transport.model_requests) + len(transport.control_requests) == 0


def test_v5_recovery_classifies_an_attempt_refused_by_control_reservation_cap(
    tmp_path: Path,
) -> None:
    seed = 5000
    task = generate_task(seed)
    trial_id = "trial-control-reservation-recovery"
    journal = V5AttemptJournal(tmp_path / "control-reservation-recovery.sqlite")
    transport = ScriptedTransport(
        [TransportOutcome("deadline", failure_code="request_deadline")]
    )
    runner = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(1, 1, 2, 1),
    )

    with pytest.raises(RuntimeError, match="provider-wire-request cap"):
        runner.run(trial_id=trial_id, task=task, action_limit=1)
    wire_requests_before_recovery = len(transport.model_requests) + len(
        transport.control_requests
    )

    recovered = runner.recover_step(
        trial_id=trial_id,
        step_index=0,
        task=task,
        backend=V5FakeBackend(),
    )

    assert recovered == {
        "classification": "attempt_not_started",
        "reason": "no_attempt_reservation",
        "redispatched": False,
    }
    assert len(transport.model_requests) + len(transport.control_requests) == (
        wire_requests_before_recovery
    )
    assert wire_requests_before_recovery == 0


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


@pytest.fixture
def legacy_post_dispatch_journal(
    tmp_path: Path,
) -> tuple[V5AttemptJournal, str, bytes]:
    """A pre-visible-result committed record and policy checkpoint."""

    trial_id = "legacy-post-dispatch"
    backend = V5FakeBackend()
    backend.reset(5000)
    journal_path = tmp_path / "legacy-post-dispatch.sqlite"
    journal = V5AttemptJournal(journal_path)
    legacy_state = canonical_json_bytes(
        {
            "instruction": generate_task(5000).instruction,
            "action_index": 1,
            "history": [{"result_digest": "sha256:" + "9" * 64}],
        }
    )
    screenshot_digest = journal.put_object(
        "screenshot", backend.screenshot().tobytes()
    )
    environment_checkpoint_digest = journal.put_object(
        "environment_checkpoint", backend.checkpoint()
    )
    environment_resume_digest = journal.put_object(
        "environment_resume_record",
        canonical_json_bytes(backend.environment_resume_record(step_count=1).to_dict()),
    )
    policy_checkpoint_digest = journal.put_object("policy_checkpoint", legacy_state)
    legacy_result = {
        "screenshot_digest": screenshot_digest,
        "reward": 0.0,
        "terminated": False,
        "truncated": False,
        "diagnostic": backend.read_privileged_diagnostic(),
    }
    journal.append_event(
        event_key=f"{trial_id}/step-0000/dispatch_committed",
        kind="dispatch_committed",
        trial_id=trial_id,
        step_index=0,
        payload={
            "sealed_intent_digest": "sha256:" + "8" * 64,
            "backend_acceptance": "accepted_once",
            "commit_result_digest": content_digest(legacy_result),
            "post_dispatch_checkpoint_digest": policy_checkpoint_digest,
            "environment_checkpoint_digest": environment_checkpoint_digest,
            "environment_resume_digest": environment_resume_digest,
            **legacy_result,
        },
    )
    backend.close()
    before = journal.integrity_report()
    journal.close()
    reopened = V5AttemptJournal(journal_path)
    assert reopened.integrity_report() == before
    return reopened, trial_id, legacy_state


def test_resume_loads_pre_split_dispatch_evidence_without_replaying_diagnostic(
    legacy_post_dispatch_journal: tuple[V5AttemptJournal, str, bytes],
) -> None:
    journal, trial_id, legacy_state = legacy_post_dispatch_journal
    policy = capturing_policy(5000)
    before = journal.integrity_report()

    recovered = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=ScriptedTransport(),
        policy=policy,
        approved_caps=episode_caps(5000),
    ).recover_step(
        trial_id=trial_id,
        step_index=0,
        task=generate_task(5000),
        backend=V5FakeBackend(),
    )

    assert recovered == {
        "classification": "already_committed",
        "state": legacy_state,
        "redispatched": False,
    }
    assert policy.received == []
    assert journal.integrity_report() == before
    journal.close()


def test_resume_rejects_tampered_legacy_embedded_diagnostic(
    legacy_post_dispatch_journal: tuple[V5AttemptJournal, str, bytes],
) -> None:
    journal, trial_id, _legacy_state = legacy_post_dispatch_journal
    journal_path = journal.path
    dispatch_key = f"{trial_id}/step-0000/dispatch_committed"
    dispatch = journal.event(dispatch_key)
    assert dispatch is not None
    tampered_payload = deepcopy(dispatch.payload)
    tampered_payload["diagnostic"]["event"] = "tampered-legacy-diagnostic"
    journal.close()
    connection = sqlite3.connect(journal_path)
    connection.execute(
        "UPDATE events SET payload = ? WHERE event_key = ?",
        (canonical_json_bytes(tampered_payload), dispatch_key),
    )
    connection.commit()
    connection.close()
    tampered = V5AttemptJournal(journal_path)

    with pytest.raises(
        RuntimeError, match="legacy committed dispatch result digest mismatch"
    ):
        V5Runner(
            journal=tampered,
            manifest=policy_manifest(),
            transport=ScriptedTransport(),
            policy=capturing_policy(5000),
            approved_caps=episode_caps(5000),
        ).recover_step(
            trial_id=trial_id,
            step_index=0,
            task=generate_task(5000),
            backend=V5FakeBackend(),
        )
    tampered.close()


def test_resume_validates_split_host_diagnostic_without_replaying_policy_hook(
    tmp_path: Path,
) -> None:
    seed = 5000
    trial_id = "split-post-dispatch"
    journal_path = tmp_path / "split-post-dispatch.sqlite"
    journal = V5AttemptJournal(journal_path)
    original_policy = capturing_policy(seed)
    with pytest.raises(InjectedInterruption, match="dispatch_committed"):
        V5Runner(
            journal=journal,
            manifest=policy_manifest(),
            transport=ScriptedTransport(),
            policy=original_policy,
            approved_caps=episode_caps(seed),
            interrupt_after="dispatch_committed",
        ).run(trial_id=trial_id, task=generate_task(seed), action_limit=1)
    committed = journal.event(f"{trial_id}/step-0000/dispatch_committed")
    assert committed is not None
    expected_state = journal.get_object(
        committed.payload["post_dispatch_checkpoint_digest"],
        expected_kind="policy_checkpoint",
    )
    recovery_policy = capturing_policy(seed)

    recovered = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=ScriptedTransport(),
        policy=recovery_policy,
        approved_caps=episode_caps(seed),
    ).recover_step(
        trial_id=trial_id,
        step_index=0,
        task=generate_task(seed),
        backend=V5FakeBackend(),
    )

    assert recovered["state"] == expected_state
    assert recovery_policy.received == []
    diagnostic_key = committed.payload["privileged_diagnostic_event_key"]
    diagnostic_event = journal.event(diagnostic_key)
    assert diagnostic_event is not None
    tampered_payload = deepcopy(diagnostic_event.payload)
    tampered_payload["diagnostic"]["event"] = "tampered-host-diagnostic"
    journal.close()
    connection = sqlite3.connect(journal_path)
    connection.execute(
        "UPDATE events SET payload = ? WHERE event_key = ?",
        (canonical_json_bytes(tampered_payload), diagnostic_key),
    )
    connection.commit()
    connection.close()
    tampered = V5AttemptJournal(journal_path)
    with pytest.raises(RuntimeError, match="privileged dispatch evidence"):
        V5Runner(
            journal=tampered,
            manifest=policy_manifest(),
            transport=ScriptedTransport(),
            policy=capturing_policy(seed),
            approved_caps=episode_caps(seed),
        ).recover_step(
            trial_id=trial_id,
            step_index=0,
            task=generate_task(seed),
            backend=V5FakeBackend(),
        )
    tampered.close()


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


def bounded_retry_manifest(
    attempts: int = 4, budget: int = 3, rate_limit_budget: int | None = None
) -> PolicyManifest:
    values = retry_manifest().__dict__.copy()
    values.pop("policy_id")
    values.update(
        {
            "max_model_attempts_per_action": attempts,
            "transport_retry_rule": (
                "bounded-same-route-zero-completion-http-429-or-transient-transport-"
                "fault-after-bounded-backoff-v3"
            ),
            "inference_parameters": (
                (
                    "max_rate_limit_retries_per_action",
                    str(budget if rate_limit_budget is None else rate_limit_budget),
                ),
                ("max_bounded_retries_per_action", str(budget)),
                ("rate_limit_backoff_base_seconds", "2.0"),
                ("rate_limit_backoff_max_seconds", "60.0"),
            ),
        }
    )
    return PolicyManifest.build(**values)


def transport_fault(seconds: float = 2.0) -> TransportOutcome:
    return TransportOutcome(
        "transport_fault",
        failure_code="URLError",
        retry_after_seconds=seconds,
        backoff_source="exponential_fallback",
    )


def test_v5_runner_retries_a_dropped_request_then_succeeds(tmp_path: Path) -> None:
    seed = 5000
    task = generate_task(seed)
    transport = ScriptedTransport([transport_fault(), transport_fault()])
    journal = V5AttemptJournal(tmp_path / "transport-fault-retry.sqlite")

    result = V5Runner(
        journal=journal,
        manifest=bounded_retry_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(1, 4, 0, 4),
    ).run(trial_id="trial-transport-fault", task=task, action_limit=1)

    # Two dropped sends are absorbed; the third reaches the model and dispatches.
    assert result.classification == "pilot_action_limit"
    assert result.model_attempts == 3
    events = journal.events("trial-transport-fault")
    kinds = [event.kind for event in events]
    assert kinds.count("attempt_started") == 3
    assert kinds.count("retryable_transport_fault") == 2
    assert kinds.count("unknown_outcome_infrastructure_failure") == 2
    fault = next(event for event in events if event.kind == "retryable_transport_fault")
    assert fault.payload["failure_code"] == "URLError"
    assert fault.payload["next_attempt_permitted"] is True
    assert fault.payload["bounded_retry_budget"] == 3
    journal.close()


def test_summary_separates_failure_denominators(
    tmp_path: Path,
) -> None:
    seed = 5000
    task = generate_task(seed)
    journal = V5AttemptJournal(tmp_path / "mixed-outcomes.sqlite")
    caps = CallCaps(3, 3, 3, 6)
    response = {
        "response_id": "malformed-model-output",
        "model": "no-cost-scripted-policy",
        "content": "not-json",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    outcomes = (
        ScriptedTransport(),
        ScriptedTransport([TransportOutcome("response", response)]),
        ScriptedTransport(
            [TransportOutcome("transport_fault", failure_code="connection_reset")]
        ),
    )
    results = []
    for index, transport in enumerate(outcomes):
        result = V5Runner(
            journal=journal,
            manifest=policy_manifest(),
            transport=transport,
            policy=scripted_policy(seed),
            approved_caps=caps,
        ).run(
            trial_id=f"mixed-outcome-{index}",
            task=task,
            action_limit=1,
        )
        results.append(result.to_dict())

    summary = {
        "outcome_denominators": summarize_outcome_denominators(results),
        "journal_integrity": journal.integrity_report(),
    }
    assert summary["outcome_denominators"] == {
        "attempted": 3,
        "invalid_output": 1,
        "infrastructure_failure": 1,
    }
    journal.close()


def test_summary_counts_started_episode_without_completed_result(tmp_path: Path) -> None:
    journal = V5AttemptJournal(tmp_path / "started-without-result.sqlite")
    journal.append_event(
        event_key="started-trial/initial_screenshot",
        kind="initial_screenshot",
        trial_id="started-trial",
        step_index=0,
        payload={"screenshot_digest": "sha256:" + "a" * 64},
    )

    assert summarize_outcome_denominators(
        [], attempted_episodes=attempted_episode_count(journal)
    ) == {
        "attempted": 1,
        "invalid_output": 0,
        "infrastructure_failure": 0,
    }
    journal.close()


def test_v5_recover_step_preserves_cli_fault_classification(tmp_path: Path) -> None:
    seed = 5000
    trial_id = "trial-recovery-cli-nonzero"
    journal = V5AttemptJournal(tmp_path / "recovery-cli-nonzero.sqlite")
    fault = CliFault(
        kind=CliFaultKind.NONZERO_EXIT,
        code="cli_nonzero_exit",
        phase="post_send",
        classification="infrastructure_failure",
        model_attempt_consumption=ModelAttemptConsumption.UNKNOWN,
        cost_knowledge=CostKnowledge.UNKNOWN,
    )
    journal.append_event(
        event_key=f"{trial_id}/step-0000/attempt-00/sealed_cli_nonzero_exit",
        kind="sealed_unsuccessful_result",
        trial_id=trial_id,
        step_index=0,
        attempt_index=0,
        payload={
            "failure_code": "cli_nonzero_exit",
            "cli_fault": fault.to_dict(),
        },
    )

    recovered = V5Runner(
        journal=journal,
        manifest=policy_manifest(),
        transport=ScriptedTransport(),
        policy=scripted_policy(seed),
        approved_caps=episode_caps(seed),
    ).recover_step(
        trial_id=trial_id,
        step_index=0,
        task=generate_task(seed),
        backend=V5FakeBackend(),
    )

    assert recovered == {
        "classification": "infrastructure_failure",
        "redispatched": False,
    }
    journal.close()


def test_v5_runner_settles_after_the_bounded_retry_budget_is_spent(tmp_path: Path) -> None:
    seed = 5000
    task = generate_task(seed)
    transport = ScriptedTransport([transport_fault() for _ in range(4)])
    journal = V5AttemptJournal(tmp_path / "transport-fault-exhausted.sqlite")

    result = V5Runner(
        journal=journal,
        manifest=bounded_retry_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(1, 4, 0, 4),
    ).run(trial_id="trial-fault-exhausted", task=task, action_limit=1)

    # Four faults exhaust one initial attempt plus three retries.
    assert result.classification == "infrastructure_failure"
    assert result.model_attempts == 4
    assert len(transport.model_requests) == 4
    events = journal.events("trial-fault-exhausted")
    assert [event.kind for event in events].count("retryable_transport_fault") == 4
    sealed = next(
        event
        for event in events
        if event.kind == "sealed_unsuccessful_result"
    )
    assert sealed.payload["failure_code"] == "transport_fault_retry_exhausted"
    journal.close()


def test_v5_runner_recovers_final_transport_fault_as_retry_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = 5000
    task = generate_task(seed)
    transport = ScriptedTransport([transport_fault() for _ in range(4)])
    journal = V5AttemptJournal(tmp_path / "transport-fault-final-interrupted.sqlite")
    trial_id = "trial-transport-fault-final-interrupted"
    runner = V5Runner(
        journal=journal,
        manifest=bounded_retry_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(1, 4, 0, 4),
    )
    fault_events = 0

    def interrupt_after_final_fault(name: str) -> None:
        nonlocal fault_events
        if name != "retryable_transport_fault":
            return
        fault_events += 1
        if fault_events == 4:
            raise InjectedInterruption(name)

    monkeypatch.setattr(runner, "_boundary", interrupt_after_final_fault)

    with pytest.raises(InjectedInterruption, match="retryable_transport_fault"):
        runner.run(trial_id=trial_id, task=task, action_limit=1)

    recovered = V5Runner(
        journal=journal,
        manifest=bounded_retry_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(1, 4, 0, 4),
    ).recover_step(
        trial_id=trial_id,
        step_index=0,
        task=task,
        backend=V5FakeBackend(),
    )

    assert recovered["classification"] == "infrastructure_failure"
    assert recovered["reason"] == "transport_fault_retry_exhausted"
    assert recovered["redispatched"] is False
    assert len(transport.model_requests) == 4
    sealed = next(
        event
        for event in journal.events(trial_id)
        if event.kind == "sealed_unsuccessful_result"
    )
    assert sealed.payload["failure_code"] == "transport_fault_retry_exhausted"
    assert sealed.payload["policy_checkpoint_digest"].startswith("sha256:")
    journal.close()


def test_v5_runner_shares_one_retry_budget_across_429_and_transport_faults(
    tmp_path: Path,
) -> None:
    seed = 5000
    task = generate_task(seed)
    transport = ScriptedTransport(
        [
            TransportOutcome(
                "rate_limited",
                failure_code="http_429_rate_limit",
                retry_after_seconds=3.0,
                backoff_source="retry_after",
            ),
            transport_fault(),
            TransportOutcome(
                "rate_limited",
                failure_code="http_429_rate_limit",
                retry_after_seconds=3.0,
                backoff_source="retry_after",
            ),
            transport_fault(),
        ]
    )
    journal = V5AttemptJournal(tmp_path / "mixed-retry.sqlite")

    result = V5Runner(
        journal=journal,
        manifest=bounded_retry_manifest(),
        transport=transport,
        policy=scripted_policy(seed),
        approved_caps=CallCaps(1, 4, 0, 4),
    ).run(trial_id="trial-mixed-retry", task=task, action_limit=1)

    # A mixed fault streak draws from the same budget: four sends, not eight.
    assert result.model_attempts == 4
    assert len(transport.model_requests) == 4
    kinds = [event.kind for event in journal.events("trial-mixed-retry")]
    assert kinds.count("retryable_rate_limit") == 2
    assert kinds.count("retryable_transport_fault") == 2
    assert result.classification == "infrastructure_failure"
    journal.close()


def test_v5_runner_rejects_a_bounded_budget_wider_than_the_attempt_cap() -> None:
    with pytest.raises(ValueError, match="bounded retry cap"):
        V5Runner(
            journal=object(),  # type: ignore[arg-type]
            manifest=bounded_retry_manifest(attempts=2, budget=2, rate_limit_budget=1),
            transport=ScriptedTransport(),
            policy=scripted_policy(5000),
            approved_caps=CallCaps(1, 2, 0, 2),
        )


@pytest.mark.parametrize("settlement", ["in_flight", "known", "reconciled_known"])
def test_spend_reservation_replay_neither_duplicates_nor_loses_exposure(
    tmp_path: Path, settlement: str
) -> None:
    journal_path = tmp_path / f"spend-replay-{settlement}.sqlite"
    request_maximum = Decimal("1.00")
    journal = V5AttemptJournal(journal_path)
    ledger = SpendLedger(request_maximum, Decimal(0), journal=journal)
    assert ledger.reserve_wire("attempt-one", request_maximum)
    if settlement == "reconciled_known":
        assert ledger.reserve_unknown_charge(
            "attempt-one", request_maximum
        ) == request_maximum
    if settlement != "in_flight":
        assert ledger.record_cost(
            "attempt-one", Decimal("0.25"), request_maximum
        )
    journal.close()
    del ledger

    resumed_journal = V5AttemptJournal(journal_path)
    resumed = SpendLedger(request_maximum, Decimal(0), journal=resumed_journal)

    assert resumed.wire_requests_sent == 1
    assert resumed.unknown_reservation_usd == 0
    if settlement != "in_flight":
        assert resumed.in_flight_reservation_usd == 0
        assert resumed.spent_usd == Decimal("0.25")
    else:
        assert resumed.in_flight_reservation_usd == request_maximum
        assert resumed.spent_usd == 0
    assert (resumed.in_flight_reservation_usd > 0) is not (
        resumed.spent_usd > 0
    )
    resumed_journal.close()
