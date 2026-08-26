"""Fast no-cost contract tests for the PixelGym v5 agent benchmark."""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest

from pixelgym.actions import ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.v5.admission import validate_task_admission
from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import (
    Partition,
    SandboxManifest,
    V5Task,
    WorkflowFamily,
)
from pixelgym.grounding.v5.coordinates import IDENTITY_ADAPTER, NORMALIZED_1000_ADAPTER
from pixelgym.grounding.v5.evidence import (
    CredentialValidationError,
    V5EvidenceStore,
    redact_publishable,
    validate_credential_free,
)
from pixelgym.grounding.v5.generator import generate_task, tasks_for_partition, validate_generator
from pixelgym.grounding.v5.manifests import (
    D56_EXCLUDED_CALIBRATION_SEEDS,
    d56_calibration_manifest,
    partition_manifest,
)
from pixelgym.grounding.v5.metrics import (
    clustered_bootstrap_difference,
    exact_mcnemar_pvalue,
    paired_success_table,
    summarize_sealed_episodes,
    wilson_interval,
)
from pixelgym.grounding.v5.policies import Mutation, golden_actions, mutation_trace
from pixelgym.grounding.v5.sandbox import (
    DENIED_CAPABILITIES,
    SANDBOX_POLICY_VERSION,
    build_sandbox_manifest,
)
from pixelgym.grounding.v5.seeds import SEED_RECORDS, validate_seed_contract
from pixelgym.serialization import canonical_json_bytes

ROOT = Path(__file__).parents[2]


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_nested_keys(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(_nested_keys(child) for child in value))
    return set()


def test_v5_seed_and_generator_contract_freezes_allocations() -> None:
    seed_summary = validate_seed_contract()
    assert seed_summary["partition_counts"] == {
        "development": 24,
        "calibration": 60,
        "confirmatory": 96,
    }
    assert seed_summary["difficulty_band_counts"] == {
        "regression_canary": 36,
        "frontier": 108,
        "ceiling_probe": 36,
    }
    summary = validate_generator()
    assert summary["task_count"] == 180
    assert summary["family_counts"] == {family.value: 30 for family in WorkflowFamily}
    assert len({record.seed for record in SEED_RECORDS}) == 180


@pytest.mark.parametrize("partition", tuple(Partition))
def test_v5_checked_in_partition_manifests_match_current_sources(
    partition: Partition,
) -> None:
    stored = ROOT / "artifacts/grounding-v5-manifests" / f"{partition.value}.json"
    assert stored.read_bytes() == canonical_json_bytes(partition_manifest(partition)) + b"\n"


def test_v5_d56_calibration_manifest_excludes_the_complete_pilot_allocation() -> None:
    source = partition_manifest(Partition.CALIBRATION)
    derived = d56_calibration_manifest()

    assert source["episode_count"] == 60
    assert derived["schema_version"] == "pixelgym-agent-v5-partition-v3"
    assert derived["episode_count"] == 50
    assert sum(record["max_episode_steps"] for record in derived["records"]) == 1431
    assert derived["derivation"]["source_manifest_digest"] == source["manifest_digest"]
    assert derived["derivation"]["excluded_seeds"] == list(
        D56_EXCLUDED_CALIBRATION_SEEDS
    )
    assert set(D56_EXCLUDED_CALIBRATION_SEEDS).isdisjoint(
        record["seed_record"]["seed"] for record in derived["records"]
    )
    stored = ROOT / "artifacts/grounding-v5-manifests/calibration-d56.json"
    assert stored.read_bytes() == canonical_json_bytes(derived) + b"\n"


def test_v5_generated_difficulty_bounds_and_exact_slack() -> None:
    for record in SEED_RECORDS:
        task = generate_task(record.seed)
        assert 8 <= len(task.stages) <= 14
        assert 18 <= task.optimal_low_level_actions <= 40
        assert task.max_episode_steps == task.optimal_low_level_actions + task.correction_slack
        assert task.correction_slack == max(
            6, -(-task.optimal_low_level_actions // 4)
        )
        assert sum(len(stage.required_text) for stage in task.stages) <= 12
        assert all(len(stage.required_text) <= 5 for stage in task.stages)


def test_v5_robustness_twins_share_semantics_but_not_task_identity() -> None:
    twins: dict[str, list[V5Task]] = {}
    for task in tasks_for_partition(Partition.CONFIRMATORY):
        if task.seed_record.robustness_pair:
            twins.setdefault(task.seed_record.logical_id, []).append(task)
    assert len(twins) == 24
    for pair in twins.values():
        assert len(pair) == 2
        assert len({task.semantic_digest for task in pair}) == 1
        assert len({task.task_id for task in pair}) == 2
        assert pair[0].public_dict() != pair[1].public_dict()


def test_v5_policy_visible_task_excludes_privileged_annotations() -> None:
    public = generate_task(5000).public_dict()
    assert not {
        "target_control_id",
        "required_text",
        "critical",
        "dependency_id",
        "expected_result",
        "seed",
        "bbox",
    } & _nested_keys(public)


@pytest.mark.parametrize("seed", [5000, 5004, 5008, 5012, 5016, 5020])
def test_v5_golden_policy_reaches_sparse_reward_once_for_every_family(seed: int) -> None:
    task = generate_task(seed)
    backend = V5FakeBackend()
    env = PixelGuiEnv(
        backend, instruction=task.instruction, max_episode_steps=task.max_episode_steps
    )
    try:
        _observation, info = env.reset(seed=seed)
        assert info == {"task_id": task.task_id}
        actions = golden_actions(task, backend)
        assert len(actions) == task.optimal_low_level_actions
        rewards = []
        for action in actions:
            _observation, reward, terminated, truncated, info = env.step(action)
            rewards.append(reward)
            assert set(info) == {"task_id"}
        assert rewards[:-1] == [0.0] * (len(rewards) - 1)
        assert rewards[-1] == 1.0
        assert terminated and not truncated
        with pytest.raises(RuntimeError, match="after the episode already ended"):
            env.step(actions[-1])
    finally:
        env.close()


def test_v5_wrong_irreversible_commit_cannot_later_succeed() -> None:
    task = generate_task(5000)
    backend = V5FakeBackend()
    backend.reset(task.seed)
    actions = list(golden_actions(task, backend))
    planner = V5FakeBackend()
    planner.reset(task.seed)
    for action in actions[:-1]:
        if action["action_type"] == int(ActionType.CLICK):
            planner.click(action["x"], action["y"])
        elif action["action_type"] == int(ActionType.KEY):
            from pixelgym.actions import KEY_ALLOWLIST

            planner.key(KEY_ALLOWLIST[action["key"]])
    wrong = next(
        control
        for control in planner.visible_controls()
        if control.control_id != task.stages[-1].target_control_id
    )
    actions[-1] = {
        "action_type": int(ActionType.CLICK),
        "x": wrong.center[0],
        "y": wrong.center[1],
        "key": 0,
    }
    trace = mutation_trace(task, Mutation.STEP_BUDGET_EXHAUSTION)
    env = PixelGuiEnv(
        backend, instruction=task.instruction, max_episode_steps=task.max_episode_steps
    )
    try:
        env.reset(seed=task.seed)
        for action in actions:
            _observation, reward, terminated, _truncated, _info = env.step(action)
        assert reward == 0 and not terminated and backend.irreversible_failure
        while True:
            _observation, reward, terminated, truncated, _info = env.step(trace.actions[0])
            if truncated:
                break
        assert reward == 0 and not terminated
    finally:
        env.close()


def test_v5_repair_control_only_appears_for_declared_recovery_error() -> None:
    tasks = (generate_task(record.seed) for record in SEED_RECORDS)
    task = next(task for task in tasks if any(stage.recovery_stage for stage in task.stages))
    recovery_index = next(
        index for index, stage in enumerate(task.stages) if stage.recovery_stage
    )
    backend = V5FakeBackend()
    backend.reset(task.seed)
    for stage in task.stages[:recovery_index]:
        if stage.required_text:
            backend.click(*backend.control_center("text_input"))
            for character in stage.required_text:
                backend.key(character)
            backend.click(*backend.control_center("continue"))
        else:
            backend.click(*backend.control_center(stage.target_control_id))
    recovery = task.stages[recovery_index]
    wrong = next(
        control for control in recovery.controls if control.control_id != recovery.target_control_id
    )
    backend.click(*backend.control_center(wrong.control_id))
    assert "repair_implicated" not in {
        control.control_id for control in backend.visible_controls()
    }
    backend.click(*backend.control_center(recovery.target_control_id))
    assert [control.control_id for control in backend.visible_controls()] == [
        "repair_implicated"
    ]
    checkpoint = backend.checkpoint()
    restored = V5FakeBackend()
    restored.restore(checkpoint)
    restored.click(*restored.control_center("repair_implicated"))
    assert restored.stage_index == recovery_index + 1


def test_v5_text_control_order_and_geometry_follow_frozen_stage_controls() -> None:
    seed = next(record.seed for record in SEED_RECORDS if record.variant == "twin_b")
    task = generate_task(seed)
    backend = V5FakeBackend()
    backend.reset(seed)
    backend.click(*backend.control_center(task.stages[0].target_control_id))
    text_stage = task.stages[1]
    visible = backend.visible_controls()
    assert [control.control_id for control in visible] == [
        control.control_id for control in text_stage.controls
    ]
    assert [control.bbox for control in visible] == [
        (190, 430, 834, 488),
        (190, 502, 834, 560),
    ]


def test_v5_no_cost_admission_covers_golden_recovery_mutations_and_floor() -> None:
    record = validate_task_admission(generate_task(5016))
    assert record["golden"]["reward_sum"] == 1.0
    assert record["recovery"]["reward_sum"] == 1.0
    assert set(record["mutations"]) == {mutation.value for mutation in Mutation}
    mutation_digests = {
        mutation["trace_digest"] for mutation in record["mutations"].values()
    }
    assert len(mutation_digests) == len(Mutation)
    assert (
        record["mutations"][Mutation.STALE_TASK_SUBMISSION.value]["trace_digest"]
        != record["mutations"][Mutation.STEP_BUDGET_EXHAUSTION.value]["trace_digest"]
    )
    assert record["mutations"][Mutation.STALE_TASK_SUBMISSION.value][
        "stale_submission_validation"
    ] == {
        "submitted": True,
        "task_id_matches": False,
        "success": False,
        "rejected": True,
    }
    assert record["mutations"][Mutation.STEP_BUDGET_EXHAUSTION.value][
        "stale_submission_validation"
    ] is None
    assert record["random_floor_reward_sum"] == 0.0


def test_v5_browser_app_has_no_runtime_network_or_time_sources() -> None:
    source = (ROOT / "pixelgym/grounding/v5/v5_app/static/app.js").read_text()
    prohibited = (
        r"\bfetch\s*\(",
        r"\bXMLHttpRequest\b",
        r"\bWebSocket\s*\(",
        r"\bEventSource\s*\(",
        r"\bDate\s*\(",
        r"\bsetTimeout\s*\(",
        r"\bsetInterval\s*\(",
    )
    assert all(re.search(pattern, source) is None for pattern in prohibited)


def test_v5_coordinate_adapters_cover_corners_and_reject_boundaries() -> None:
    assert IDENTITY_ADAPTER.transform(0, 0) == (0, 0)
    assert IDENTITY_ADAPTER.transform(1023, 767) == (1023, 767)
    assert NORMALIZED_1000_ADAPTER.transform(0, 0) == (0, 0)
    assert NORMALIZED_1000_ADAPTER.transform(999, 999) == (1023, 767)
    assert IDENTITY_ADAPTER.source_digest != NORMALIZED_1000_ADAPTER.source_digest
    with pytest.raises(ValueError, match="outside"):
        NORMALIZED_1000_ADAPTER.transform(1000, 0)


@pytest.mark.parametrize(
    "value",
    [
        {"api_key": "not-echoed"},
        {"url": "https://user:pass@example.invalid"},
        {"url": "https://example.invalid?access_token=not-echoed"},
        {"value": "secret://runtime/provider"},
        {"value": "sk-abcdefghijklmnop"},
    ],
)
def test_v5_credential_validator_fails_closed_without_echoing_candidate(value: object) -> None:
    with pytest.raises(CredentialValidationError) as captured:
        validate_credential_free(value)
    assert "not-echoed" not in str(captured.value)
    assert "abcdefghijklmnop" not in str(captured.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:password@example.invalid",
        "https://example.invalid?api_key=secret",
        "https://example.invalid/provider",
    ],
)
def test_v5_sandbox_manifest_rejects_non_origin_or_credential_endpoint(
    endpoint: str,
) -> None:
    with pytest.raises(ValueError):
        build_sandbox_manifest(
            runtime_digest="sha256:" + "1" * 64,
            provider_endpoint=endpoint,
        )


def test_v5_sandbox_manifest_rejects_forged_endpoint_binding() -> None:
    with pytest.raises(ValueError, match="allowlist digest"):
        SandboxManifest(
            runtime_digest="sha256:" + "1" * 64,
            network_policy_version=SANDBOX_POLICY_VERSION,
            provider_endpoint="https://provider.example.invalid",
            endpoint_allowlist_digest="sha256:" + "0" * 64,
            denied_capabilities=DENIED_CAPABILITIES,
        )


def test_v5_authoritative_redaction_binding_and_integrity(tmp_path: Path) -> None:
    authoritative = {
        "task_id": "v5-test",
        "app_url": "http://private-host.local/task",
        "username": "private-user",
        "result": {"success": True},
    }
    store = V5EvidenceStore(tmp_path / "evidence")
    store.put_authoritative("task.json", authoritative)
    derivative, relation = store.publish_derivative("task.json", authoritative)
    assert redact_publishable(authoritative)["app_url"] == "[redacted]"
    assert derivative.sha256 != relation.sha256
    report = store.integrity_report()
    assert report["object_count"] == 3
    assert all(row["verified"] for row in report["objects"])


def test_v5_publishable_derivative_requires_stored_authoritative_source(tmp_path: Path) -> None:
    store = V5EvidenceStore(tmp_path / "evidence")
    with pytest.raises(ValueError, match="stored before publication"):
        store.publish_derivative("missing.json", {"result": "ok"})
    assert redact_publishable({"url": "https://private.example.invalid/path"}) == {
        "url": "[redacted-url]"
    }


def test_v5_packaged_task_schema_accepts_all_generated_tasks() -> None:
    schema = json.loads(
        (ROOT / "pixelgym/grounding/v5/schemas/task.schema.json").read_text()
    )
    for task in tasks_for_partition(Partition.DEVELOPMENT):
        jsonschema.validate(task.canonical_dict(), schema)


def test_v5_metric_denominators_paired_exact_and_clustered_bootstrap() -> None:
    lower, upper = wilson_interval(8, 10)
    assert 0.49 < lower < 0.50 and 0.94 < upper < 0.95
    table = paired_success_table(
        {"a": True, "b": True, "c": False},
        {"a": True, "b": False, "c": True},
    )
    assert table == {
        "both_success": 1,
        "first_only": 1,
        "second_only": 1,
        "both_failure": 0,
    }
    assert exact_mcnemar_pvalue(1, 1) == 1.0
    rows = (("pair-1", True, False), ("pair-1", True, True), ("single", False, True))
    assert clustered_bootstrap_difference(rows, seed=42, samples=500) == (
        clustered_bootstrap_difference(rows, seed=42, samples=500)
    )
    summary = summarize_sealed_episodes(
        (
            {"success": True, "classification": "success_termination"},
            {"success": False, "classification": "invalid_output"},
        )
    )
    assert summary.total == 2 and summary.successful == 1 and summary.estimate == 0.5


def test_core_fake_backend_v5_checkpoint_restores_exact_state() -> None:
    backend = FakeBackend()
    backend.reset(7)
    backend.click(*backend.layout.controls[next(iter(backend.layout.controls))].center)
    backend.key("A")
    checkpoint = backend.checkpoint()
    record = backend.environment_resume_record(step_count=2)
    restored = FakeBackend()
    restored.restore(checkpoint)
    restored.verify_resume_record(record, step_count=2)
    assert restored.checkpoint() == checkpoint
    assert (restored.screenshot() == backend.screenshot()).all()


@pytest.mark.parametrize("checkpoint", [b"[]", b'{"schema_version":"pixelgym-core-fake-checkpoint-v1"}'])
def test_core_fake_backend_v5_checkpoint_rejects_malformed_shape(checkpoint: bytes) -> None:
    with pytest.raises(ValueError):
        FakeBackend().restore(checkpoint)


@pytest.mark.parametrize("checkpoint", [b"[]", b'{"schema_version":"pixelgym-v5-fake-checkpoint-v1"}'])
def test_v5_fake_backend_checkpoint_rejects_malformed_shape(checkpoint: bytes) -> None:
    with pytest.raises(ValueError):
        V5FakeBackend().restore(checkpoint)
