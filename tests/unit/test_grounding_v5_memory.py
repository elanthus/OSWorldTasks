"""Memory construct, delayed validation, durable screenshot state, and old-version isolation."""

from __future__ import annotations

import json
from collections import Counter
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from pixelgym.grounding.v5.admission import replay_actions, validate_task_admission
from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import CallCaps, Partition, content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_generator import (
    MEMORY_GENERATOR_VERSION,
    development_counterfactuals,
    generate_memory_task,
    permute_controls,
    seed_record,
)
from pixelgym.grounding.v5.memory_plan import config_from_price_snapshot, pilot_plan
from pixelgym.grounding.v5.panel_policy import GEMINI_STATEFUL, SpendLedger
from pixelgym.grounding.v5.policies import _append_golden_stage, click_action, noop_action
from pixelgym.grounding.v5.runner import (
    InjectedInterruption,
    PolicyVisibleResult,
    ScriptedTransport,
    TransportOutcome,
)
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    ScreenshotMemoryRunner,
    build_screenshot_policy_manifest,
)
from pixelgym.serialization import canonical_json_bytes

ROOT = Path(__file__).parents[2]


def advance(backend: MemoryBackend, until: int) -> None:
    actions: list[dict[str, int]] = []
    for stage in backend.task.stages[backend.stage_index : until]:
        _append_golden_stage(backend, stage, actions)


def test_reserved_seed_allocation_without_generating_confirmation() -> None:
    records = [seed_record(seed) for seed in range(6000, 6144)]
    assert len({record.logical_id for record in records}) == 120
    assert all(record.partition is Partition.CONFIRMATORY for record in records)
    assert set(Counter(record.family for record in records).values()) == {24}
    assert Counter(record.difficulty_band.value for record in records) == {
        "regression_canary": 30,
        "frontier": 84,
        "ceiling_probe": 30,
    }
    with pytest.raises(TypeError):
        generate_memory_task(True)
    with pytest.raises(ValueError):
        generate_memory_task(9999)


def test_versioned_generator_uniqueness_and_target_independent_layout() -> None:
    counts: Counter[int] = Counter()
    for seed in range(5000, 5024):
        task = generate_memory_task(seed)
        assert task == generate_memory_task(seed)
        assert task.canonical_dict()["generator_version"] == MEMORY_GENERATOR_VERSION
        assert task.max_episode_steps <= 32
        for index in (5, 7):
            stage = task.stages[index]
            assert len({control.label for control in stage.controls}) == 3
            assert all(control.label.startswith("Match row ") for control in stage.controls)
            counts.update(
                [
                    next(
                        i
                        for i, control in enumerate(stage.controls)
                        if control.control_id == stage.target_control_id
                    )
                ]
            )
    assert set(counts) == {0, 1, 2}
    controls = generate_memory_task(5000).stages[5].controls
    reordered = permute_controls(controls, layout_key="layout", stage_id="5", variant="base")
    renamed = tuple(replace(control, label="irrelevant") for control in controls)
    assert [c.control_id for c in reordered] == [
        c.control_id
        for c in permute_controls(
            renamed,
            layout_key="layout",
            stage_id="5",
            variant="base",
        )
    ]
    assert generate_memory_task(5112).semantic_digest == generate_memory_task(5113).semantic_digest
    schema = json.loads(
        (ROOT / "pixelgym/grounding/v5/schemas/memory-task.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(generate_memory_task(5000).canonical_dict())


@pytest.mark.parametrize("seed", (5000, 5004, 5008, 5012, 5016, 5020))
def test_counterfactual_consumer_pixels_identical_but_answers_differ(seed: int) -> None:
    base = generate_memory_task(seed)
    for task, source, consumer in zip(development_counterfactuals(seed), (0, 2), (5, 7)):
        assert task.instruction == base.instruction
        assert task.stages[source].facts != base.stages[source].facts
        assert task.stages[consumer].target_control_id != base.stages[consumer].target_control_id
        assert task.stages[consumer].public_dict() == base.stages[consumer].public_dict()
        backends = [MemoryBackend(), MemoryBackend()]
        try:
            for backend, candidate in zip(backends, (base, task)):
                backend.reset(candidate.seed)
                advance(backend, consumer)
            assert np.array_equal(backends[0].screenshot(), backends[1].screenshot())
        finally:
            for backend in backends:
                backend.close()


@pytest.mark.parametrize("seed", (5000, 5016))
@pytest.mark.parametrize("consumer", (5, 7))
def test_no_correctness_feedback_before_submission_and_resume(seed: int, consumer: int) -> None:
    good, bad = MemoryBackend(), MemoryBackend()
    try:
        for backend in (good, bad):
            backend.reset(seed)
            advance(backend, consumer)
        stage = good.task.stages[consumer]
        wrong = next(
            control.control_id
            for control in stage.controls
            if control.control_id != stage.target_control_id
        )
        good.click(*good.control_center(stage.target_control_id))
        bad.click(*bad.control_center(wrong))
        assert np.array_equal(good.screenshot(), bad.screenshot())
        assert (
            good.read_privileged_diagnostic()["event"] == bad.read_privileged_diagnostic()["event"]
        )
        checkpoint = bad.checkpoint()
        restored = MemoryBackend()
        try:
            restored.restore(checkpoint)
            assert restored.checkpoint() == checkpoint
            assert np.array_equal(restored.screenshot(), bad.screenshot())
            invalid = json.loads(checkpoint)
            invalid["deferred_choices"] = {}
            with pytest.raises(ValueError, match="deferred choices"):
                restored.restore(canonical_json_bytes(invalid))
        finally:
            restored.close()
        with pytest.raises(ValueError, match="schema"):
            V5FakeBackend().restore(checkpoint)
    finally:
        good.close()
        bad.close()


def test_wrong_deferred_choice_never_earns_reward_at_correct_commit() -> None:
    backend = MemoryBackend()
    backend.reset(5000)
    task = backend.task
    actions: list[dict[str, int]] = []
    try:
        for index, stage in enumerate(task.stages):
            if index == 5:
                wrong = next(
                    control
                    for control in backend.visible_controls()
                    if control.control_id != stage.target_control_id
                )
                actions.append(click_action(*wrong.center))
                backend.click(*wrong.center)
            else:
                _append_golden_stage(backend, stage, actions)
        assert backend.irreversible_failure
    finally:
        backend.close()
    actions.extend(noop_action() for _ in range(task.max_episode_steps - len(actions)))
    result = replay_actions(task, tuple(actions), backend_factory=MemoryBackend)
    assert not any(result.rewards)
    assert result.truncated and not result.terminated
    assert "wrong_irreversible_commit" in result.diagnostic_events


@pytest.mark.parametrize("seed", (5000, 5016))
def test_successor_reuses_full_admission_semantics(seed: int) -> None:
    result = validate_task_admission(generate_memory_task(seed), backend_factory=MemoryBackend)
    assert result["golden"]["reward_sum"] == 1
    assert result["recovery"]["reward_sum"] == 1
    assert len(result["mutations"]) == 6


def test_screenshot_history_is_pure_bounded_and_reset_between_episodes() -> None:
    policy = ScreenshotMemoryPolicy(GEMINI_STATEFUL, retain_screenshots=True)
    control = ScreenshotMemoryPolicy(GEMINI_STATEFUL, retain_screenshots=False)
    first = bytes(1024 * 768 * 3)
    second = bytes([127]) * len(first)
    initial = policy.reset("instruction")
    state = policy.observe_screenshot(initial, first)
    assert policy.observe_screenshot(state, first) == state
    with pytest.raises(ValueError, match="changed"):
        policy.observe_screenshot(state, second)
    action = noop_action()
    visible = PolicyVisibleResult("sha256:" + "1" * 64, 0.0, False, False, 0)
    state = policy.post_dispatch_state(state, action, visible)
    state = policy.observe_screenshot(state, second)
    restored = ScreenshotMemoryPolicy(GEMINI_STATEFUL, retain_screenshots=True)
    request = restored.build_request(state, second)
    assert request == policy.build_request(state, second)
    items = request["messages"][1]["content"]
    assert sum(item["type"] == "image_url" for item in items) == 2
    assert not any("reward" in item.get("text", "") for item in items)
    assert policy.reset("instruction") == initial
    stateless = control.observe_screenshot(control.reset("instruction"), second)
    assert json.loads(stateless) == {"instruction": "instruction"}
    control_request = control.build_request(stateless, second)
    assert (
        sum(item["type"] == "image_url" for item in control_request["messages"][1]["content"]) == 1
    )
    assert control_request["messages"][0] == request["messages"][0]
    with pytest.raises(ValueError, match="byte length"):
        policy.observe_screenshot(initial, b"bad")
    for _ in range(30):
        state = policy.post_dispatch_state(state, action, visible)
        state = policy.observe_screenshot(state, second)
    state = policy.post_dispatch_state(state, action, visible)
    with pytest.raises(ValueError, match="frozen action boundary"):
        policy.observe_screenshot(state, second)


@pytest.mark.parametrize("boundary", ("attempt_started", "canonical_response_persisted"))
def test_screenshot_checkpoint_survives_interruption_without_hidden_cache(
    tmp_path: Path,
    boundary: str,
) -> None:
    config = replace(
        GEMINI_STATEFUL, max_model_attempts_per_action=1, max_rate_limit_retries_per_action=0
    )
    manifest = build_screenshot_policy_manifest(
        ROOT, config=config, code_revision="test-revision", retain_screenshots=True
    )
    response = {
        "response_id": "fake",
        "model": config.model,
        "content": json.dumps(noop_action()),
        "usage": {"upstream_provider": config.response_provider, "price_guard": "ok", "cost": 0},
        "finish_reason": "stop",
    }
    transport = ScriptedTransport([TransportOutcome("response", response)])
    caps = CallCaps(1, 1, 0, 1)
    task = generate_memory_task(5000)
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        runner = ScreenshotMemoryRunner(
            journal=journal,
            manifest=manifest,
            policy=ScreenshotMemoryPolicy(config, retain_screenshots=True),
            transport=transport,
            approved_caps=caps,
            interrupt_after=boundary,
        )
        with pytest.raises(InjectedInterruption):
            runner.run(trial_id="memory", task=task, backend=MemoryBackend(), action_limit=1)
        event = next(event for event in journal.events("memory") if event.kind == "attempt_started")
        state = journal.get_object(
            event.payload["pre_call_checkpoint_digest"], expected_kind="policy_checkpoint"
        )
        assert len(json.loads(state)["frames"]) == 1
        before = len(transport.model_requests)
        fresh = ScreenshotMemoryRunner(
            journal=journal,
            manifest=manifest,
            policy=ScreenshotMemoryPolicy(config, retain_screenshots=True),
            transport=transport,
            approved_caps=caps,
        )
        backend = MemoryBackend()
        try:
            outcome = fresh.recover_step(
                trial_id="memory", step_index=0, task=task, backend=backend
            )
            assert len(transport.model_requests) == before
            assert outcome["classification"] == (
                "dispatched"
                if boundary == "canonical_response_persisted"
                else "infrastructure_failure"
            )
        finally:
            backend.close()


def test_matched_manifests_only_change_memory_intervention() -> None:
    first = build_screenshot_policy_manifest(
        ROOT, config=GEMINI_STATEFUL, code_revision="test", retain_screenshots=True
    ).to_dict()
    second = build_screenshot_policy_manifest(
        ROOT, config=GEMINI_STATEFUL, code_revision="test", retain_screenshots=False
    ).to_dict()
    differences = {key for key in first if first[key] != second[key]}
    assert differences == {"policy_id", "memory_policy_version", "state_reducer_version"}
    assert content_digest(first) != content_digest(second)


def test_second_step_recovery_retains_earlier_screenshot_and_dispatched_action(
    tmp_path: Path,
) -> None:
    class InterruptSecondResponse(ScreenshotMemoryRunner):
        def _boundary(self, name: str) -> None:
            if name == "canonical_response_persisted" and self.model_attempts == 2:
                raise InjectedInterruption(name)

    config = replace(
        GEMINI_STATEFUL, max_model_attempts_per_action=1, max_rate_limit_retries_per_action=0
    )
    manifest = build_screenshot_policy_manifest(
        ROOT, config=config, code_revision="test", retain_screenshots=True
    )
    response = {
        "response_id": "fake",
        "model": config.model,
        "content": json.dumps(noop_action()),
        "usage": {"upstream_provider": config.response_provider, "price_guard": "ok", "cost": 0},
        "finish_reason": "stop",
    }
    transport = ScriptedTransport([TransportOutcome("response", response)] * 2)
    task = generate_memory_task(5000)
    caps = CallCaps(2, 2, 0, 2)
    with closing(V5AttemptJournal(tmp_path / "second-step.sqlite")) as journal:
        runner = InterruptSecondResponse(
            journal=journal,
            manifest=manifest,
            policy=ScreenshotMemoryPolicy(config, retain_screenshots=True),
            transport=transport,
            approved_caps=caps,
        )
        with pytest.raises(InjectedInterruption):
            runner.run(trial_id="second-step", task=task, backend=MemoryBackend(), action_limit=2)
        event = [
            event for event in journal.events("second-step") if event.kind == "attempt_started"
        ][-1]
        state = json.loads(
            journal.get_object(
                event.payload["pre_call_checkpoint_digest"], expected_kind="policy_checkpoint"
            )
        )
        assert len(state["frames"]) == 2 and state["actions"] == [noop_action()]
        assert len(transport.model_requests) == 2
        backend = MemoryBackend()
        try:
            fresh = ScreenshotMemoryRunner(
                journal=journal,
                manifest=manifest,
                policy=ScreenshotMemoryPolicy(config, retain_screenshots=True),
                transport=transport,
                approved_caps=caps,
            )
            outcome = fresh.recover_step(
                trial_id="second-step", step_index=1, task=task, backend=backend
            )
            assert outcome["classification"] == "dispatched"
            assert json.loads(outcome["state"])["frames"] == state["frames"]
            assert json.loads(outcome["state"])["actions"] == [noop_action()] * 2
            assert len(transport.model_requests) == 2
        finally:
            backend.close()


def test_diagnostic_plan_counts_scripted_actions_and_full_upstream_reservation() -> None:
    snapshot = json.loads(
        (ROOT / "artifacts/grounding-v5-d58-design/gemini-price-snapshot.json").read_text()
    )
    plan = pilot_plan(ROOT, snapshot=snapshot, code_revision="test")
    assert not plan["execution_enabled"] and plan["confirmatory_request_cap"] == 0
    assert len(plan["cases"]) == 10
    assert Counter(case["consumer_index"] for case in plan["cases"]) == {5: 5, 7: 5}
    caps = plan["caps"]
    assert caps["provider_wire_request_cap"] == caps["model_attempt_cap"] == 20
    assert caps["environment_action_cap"] == 20 + 2 * sum(
        case["scripted_prefix_action_count"] for case in plan["cases"]
    )
    assert plan["spend"]["aggregate_successor_ceiling_usd"] == "5.00"
    assert Decimal(plan["spend"]["per_request_reservation_usd"]) == Decimal("1.443225600")
    assert not plan["spend"]["all_requests_guaranteed_to_fit"]
    assert all(
        manifest["context_limit"] == 1048576 for manifest in plan["policy_manifests"].values()
    )
    for bad in ("NaN", "Infinity", "-1"):
        snapshot["endpoints"][0]["pricing"]["prompt"] = bad
        with pytest.raises(ValueError, match="finite positive"):
            config_from_price_snapshot(snapshot)


def test_five_dollar_shared_ledger_keeps_unknown_charges_across_arms_and_resume(
    tmp_path: Path,
) -> None:
    bound = Decimal("1.443225600")
    path = tmp_path / "aggregate.sqlite"
    with closing(V5AttemptJournal(path)) as journal:
        ledger = SpendLedger(Decimal(5), Decimal(0), journal=journal)
        assert ledger.reserve_wire("history/one", bound)
        ledger.reserve_unknown_charge("history/one", bound)
        assert ledger.reserve_wire("stateless/one", bound)
        ledger.reserve_unknown_charge("stateless/one", bound)
    with closing(V5AttemptJournal(path)) as journal:
        ledger = SpendLedger(Decimal(5), Decimal(0), journal=journal)
        assert ledger.unknown_reservation_usd == 2 * bound
        assert ledger.reserve_wire("history/two", bound)
        assert not ledger.reserve_wire("stateless/two", bound)
        assert ledger.wire_requests_sent == 3
