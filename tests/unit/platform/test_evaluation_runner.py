from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from pixelgym.platform.contracts import ArtifactRef
from pixelgym.platform.evaluation import (
    EvaluationRunner,
    PlatformProviderResponse,
    ScriptedReplayProvider,
    percentile_r7,
)
from pixelgym.platform.immutable_store import LocalImmutableStore
from pixelgym.platform.mlflow_tracking import RUN_PARAM_KEYS, InMemoryTracking
from pixelgym.platform.schema_validation import ContractValidationError


def _runner(
    *,
    repository_root: Path,
    tmp_path: Path,
    gate_policy,
    policy_factory,
    variant: str,
    provider=None,
    tracking=None,
    submission: str | None = None,
    store=None,
):
    version = 1 if variant == "baseline" else 2
    return EvaluationRunner(
        repository_root=repository_root,
        store=store or LocalImmutableStore(tmp_path / "immutable"),
        tracking=tracking or InMemoryTracking(),
        provider=provider
        or ScriptedReplayProvider(
            repository_root / "artifacts/grounding-predictions.jsonl", variant=variant
        ),
        policy=policy_factory(version),
        gate_policy=gate_policy,
        dataset_fingerprint=gate_policy.required_dataset_fingerprint,
        submission_id=submission or f"submission-{variant}",
        metaflow_pathspec="GroundingEvaluationFlow/1",
    )


def test_scripted_baseline_is_blocked_and_revised_is_only_eligible(
    repository_root: Path, tmp_path: Path, gate_policy, policy_factory
) -> None:
    baseline = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path / "a",
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="baseline",
    )
    revised = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path / "b",
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="revised",
    )
    baseline_summary, baseline_report, _ = baseline.run(max_calls=100)
    revised_summary, revised_report, references = revised.run(max_calls=100)
    assert baseline_summary.accuracy == 0.56
    assert not baseline_report.overall_passed
    assert revised_summary.accuracy == 1.0
    assert revised_report.overall_passed
    assert revised_summary.provider_latency_p50_ms == 25.0
    assert revised_summary.provider_latency_max_ms == 25.0
    assert revised_summary.evaluation_end_to_end_duration_ms is not None
    assert revised_summary.evaluation_end_to_end_duration_ms > 0
    assert revised_summary.total_cost_usd == 0.0
    assert revised_summary.cost_usd_per_example == 0.0
    tracking = revised.tracking
    assert isinstance(tracking, InMemoryTracking)
    run = tracking.runs[revised_summary.run_id]
    assert set(run.params) == set(RUN_PARAM_KEYS)
    assert len(run.dataset_inputs) == 1
    assert run.dataset_inputs[0].fingerprint == revised_summary.dataset_fingerprint
    assert {
        "run-manifest-reference.json",
        "raw-response-index-reference.json",
        "parsed-predictions-reference.json",
        "per-example-scores-reference.json",
        "summary-reference.json",
        "gate-report-reference.json",
        "environment-manifest-reference.json",
        "policy-package-reference.json",
        "representative-images-reference.json",
    } <= set(run.artifacts)
    compatible = tracking.search_compatible_runs(
        dataset_fingerprint=revised_summary.dataset_fingerprint,
        scorer_version=revised_summary.scorer_version,
        target_semantics=revised_summary.target_semantics,
    )
    assert [item.run_id for item in compatible] == [revised_summary.run_id]
    tracking.policy_tags[revised.policy.policy_id]["approval_status"] = "approved"
    run.tags["gate_status"] = "failed"
    tracking.register_policy(revised_summary.run_id, revised.policy)
    assert tracking.policy_tags[revised.policy.policy_id] == {
        "policy_id": revised.policy.policy_id,
        "gate_status": "failed",
        "approval_status": "approved",
    }
    by_key = {item.logical_key: item for item in references}
    prediction_bytes = revised.store.get_verified(
        by_key["runs/submission-revised/predictions.jsonl"]
    )
    score_bytes = revised.store.get_verified(
        by_key["runs/submission-revised/per-example-scores.jsonl"]
    )
    assert prediction_bytes != score_bytes
    prediction = json.loads(prediction_bytes.splitlines()[0])
    score = json.loads(score_bytes.splitlines()[0])
    assert "parsed_prediction" in prediction and "correct" not in prediction
    assert "correct" in score and "parsed_prediction" not in score
    run_manifest = json.loads(
        revised.store.get_verified(by_key["runs/submission-revised/run-manifest.json"])
    )
    assert run_manifest["status"] == "Running"


def test_dataset_input_is_cached_after_success(
    repository_root: Path, tmp_path: Path, gate_policy, policy_factory
) -> None:
    runner = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path,
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="revised",
    )

    first = runner._dataset_input()

    assert runner._dataset_input() is first


def test_empty_aggregate_has_no_cost_totals(
    repository_root: Path, tmp_path: Path, gate_policy, policy_factory
) -> None:
    runner = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path,
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="revised",
    )

    summary = runner.aggregate_metrics([], run_id="empty-run")

    assert summary.total_cost_usd is None
    assert summary.cost_usd_per_example is None
    assert summary.cost_usd_per_100 is None


def test_revised_rollback_seed_has_distinct_provider_identity(
    repository_root: Path,
) -> None:
    provider = ScriptedReplayProvider(
        repository_root / "artifacts/grounding-predictions.jsonl",
        variant="revised",
        model="day3-replay-revised-rollback-seed-v1",
    )
    assert provider.model == "day3-replay-revised-rollback-seed-v1"
    assert provider.synthetic is True


def test_resume_reuses_verified_raw_responses_without_duplicate_calls(
    repository_root: Path, tmp_path: Path, gate_policy, policy_factory
) -> None:
    provider = ScriptedReplayProvider(
        repository_root / "artifacts/grounding-predictions.jsonl", variant="revised"
    )
    tracking = InMemoryTracking()
    runner = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path,
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="revised",
        provider=provider,
        tracking=tracking,
    )
    first = runner.run(max_calls=100)
    second = runner.run(max_calls=100)
    assert first[0].to_dict() == second[0].to_dict()
    assert len(provider.call_ids) == 100
    assert len(set(provider.call_ids)) == 100


class _OperationRecordingLocalStore(LocalImmutableStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.operations: list[str] = []

    def get_reference(self, logical_key: str) -> ArtifactRef | None:
        self.operations.append("get_reference")
        return super().get_reference(logical_key)

    def _get_verified_unlocked(self, reference: ArtifactRef) -> bytes:
        self.operations.append("get_verified")
        return super()._get_verified_unlocked(reference)


def test_resume_probes_cached_envelopes_without_payload_gets(
    repository_root: Path, tmp_path: Path, gate_policy, policy_factory
) -> None:
    provider = ScriptedReplayProvider(
        repository_root / "artifacts/grounding-predictions.jsonl", variant="revised"
    )
    store = _OperationRecordingLocalStore(tmp_path / "immutable")
    runner = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path,
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="revised",
        provider=provider,
        store=store,
    )
    first_four = runner.build_shards(shard_size=4, max_calls=100)[0]
    cached_shard = {**first_four, "example_ids": first_four["example_ids"][:3]}
    runner.evaluate_shard(cached_shard, max_calls=100)
    assert len(provider.call_ids) == 3

    store.operations.clear()
    runner.evaluate_shard(cached_shard, max_calls=100)
    assert store.operations == ["get_reference"] * 3
    assert len(provider.call_ids) == 3

    operations_before_first_uncached_call: list[str] = []
    runner.provider_response_hook = lambda _: operations_before_first_uncached_call.extend(
        store.operations
    )
    store.operations.clear()
    runner.evaluate_shard(first_four, max_calls=100)

    assert operations_before_first_uncached_call == ["get_reference"] * 4
    assert len(provider.call_ids) == 4


class InvalidProvider:
    name = "scripted-demo"
    model = "day3-replay-revised-v2"
    synthetic = True

    def __init__(self) -> None:
        self.call_ids: list[str] = []

    def invoke(self, **request):
        self.call_ids.append(request["request_id"])
        return PlatformProviderResponse("not-json", 25.0, {}, 0.0)


class InvalidUsageProvider(InvalidProvider):
    def invoke(self, **request):
        self.call_ids.append(request["request_id"])
        return PlatformProviderResponse(
            "{}",
            25.0,
            {"input_tokens": "unknown"},  # type: ignore[dict-item]
            0.0,
        )


def test_invalid_answers_are_final_and_raw_is_stored_before_parser(
    repository_root: Path, tmp_path: Path, gate_policy, policy_factory
) -> None:
    provider = InvalidProvider()
    runner = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path,
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="revised",
        provider=provider,
    )
    summary, report, references = runner.run(max_calls=100)
    assert summary.invalid_count == 100
    assert not report.overall_passed
    assert len(provider.call_ids) == 100
    first_raw = next(
        reference for reference in references if reference.logical_key.startswith("raw-responses/")
    )
    first_envelope = json.loads(LocalImmutableStore(tmp_path / "immutable").get_verified(first_raw))
    assert first_envelope["raw_response"] == "not-json"
    assert first_envelope["request_status"] == "responded"
    assert first_envelope["started_at_utc"] is None
    assert "does not expose" in first_envelope["started_at_missing_reason"]


def test_failure_after_receipt_resumes_from_raw_without_duplicate_first_call(
    repository_root: Path, tmp_path: Path, gate_policy, policy_factory, monkeypatch
) -> None:
    provider = ScriptedReplayProvider(
        repository_root / "artifacts/grounding-predictions.jsonl", variant="revised"
    )
    tracking = InMemoryTracking()
    runner = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path,
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="revised",
        provider=provider,
        tracking=tracking,
    )
    from pixelgym.platform import evaluation as module

    original = module.parse_prediction
    monkeypatch.setattr(
        module,
        "parse_prediction",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("crash after receipt")),
    )
    with pytest.raises(RuntimeError, match="after receipt"):
        runner.run(max_calls=100)
    assert len(provider.call_ids) == 1
    assert next(iter(tracking.runs.values())).status == "FAILED"
    monkeypatch.setattr(module, "parse_prediction", original)
    summary, report, _ = runner.run(max_calls=100)
    assert summary.accuracy == 1.0 and report.overall_passed
    assert len(provider.call_ids) == 100


def test_call_cap_is_enforced_before_provider_execution(
    repository_root: Path, tmp_path: Path, gate_policy, policy_factory
) -> None:
    provider = InvalidProvider()
    runner = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path,
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="revised",
        provider=provider,
    )
    with pytest.raises(RuntimeError, match="call cap"):
        runner.run(max_calls=99)
    assert provider.call_ids == []


def test_schema_invalid_gate_configuration_is_rejected_before_provider_execution(
    repository_root: Path, tmp_path: Path, gate_policy, policy_factory
) -> None:
    provider = InvalidProvider()

    with pytest.raises(ContractValidationError, match="gate_policy"):
        _runner(
            repository_root=repository_root,
            tmp_path=tmp_path,
            gate_policy=dataclasses.replace(gate_policy, schema_version="unsupported"),
            policy_factory=policy_factory,
            variant="revised",
            provider=provider,
        )

    assert provider.call_ids == []


def test_schema_invalid_usage_is_rejected_before_immutable_write(
    repository_root: Path, tmp_path: Path, gate_policy, policy_factory
) -> None:
    provider = InvalidUsageProvider()
    runner = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path,
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="revised",
        provider=provider,
    )
    shard = runner.build_shards(shard_size=1, max_calls=100)[0]

    with pytest.raises(ContractValidationError, match="raw_response"):
        runner.evaluate_shard(shard, max_calls=100)

    assert len(provider.call_ids) == 1
    assert not (tmp_path / "immutable/objects").exists()


def test_duplicate_overlay_example_ids_are_rejected(
    repository_root: Path, tmp_path: Path, gate_policy, policy_factory
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    dataset_line = (
        (repository_root / "artifacts/grounding-dataset.jsonl").read_text().splitlines()[0]
    )
    overlay_line = (
        (repository_root / "artifacts/grounding-overlays.jsonl").read_text().splitlines()[0]
    )
    (artifact_dir / "grounding-dataset.jsonl").write_text(dataset_line + "\n")
    (artifact_dir / "grounding-overlays.jsonl").write_text(
        overlay_line + "\n" + overlay_line + "\n"
    )
    runner = EvaluationRunner(
        repository_root=tmp_path,
        store=LocalImmutableStore(tmp_path / "immutable"),
        tracking=None,
        provider=InvalidProvider(),
        policy=policy_factory(2),
        gate_policy=gate_policy,
        dataset_fingerprint=gate_policy.required_dataset_fingerprint,
        submission_id="submission-duplicate-overlay",
        metaflow_pathspec="GroundingEvaluationFlow/duplicate-overlay",
    )

    with pytest.raises(ValueError, match="overlay example IDs must be unique"):
        runner.build_shards(shard_size=1, max_calls=1)


def test_verified_envelopes_are_not_read_and_hashed_again_during_parse(
    repository_root: Path,
    tmp_path: Path,
    gate_policy,
    policy_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path,
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="revised",
    )
    shard = runner.build_shards(shard_size=1, max_calls=100)[0]
    raw = runner.evaluate_shard(shard, max_calls=100)
    original_get_verified = runner.store.get_verified
    verified_reads = 0

    def count_verified_reads(reference):
        nonlocal verified_reads
        verified_reads += 1
        return original_get_verified(reference)

    monkeypatch.setattr(runner.store, "get_verified", count_verified_reads)
    verified = runner.verify_raw_artifacts(raw, require_complete=False)
    records = runner.parse_and_score(verified, require_complete=False)

    assert len(records) == 1
    assert verified_reads == 1


def test_schema_invalid_raw_envelope_is_rejected_before_scoring(
    repository_root: Path,
    tmp_path: Path,
    gate_policy,
    policy_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path,
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="revised",
    )
    shard = runner.build_shards(shard_size=1, max_calls=100)[0]
    raw = runner.evaluate_shard(shard, max_calls=100)
    verified = runner.verify_raw_artifacts(raw, require_complete=False)
    verified[0]["envelope"]["unexpected"] = "must fail closed"
    monkeypatch.setattr(
        "pixelgym.platform.evaluation.parse_prediction",
        lambda *args, **kwargs: pytest.fail("schema-invalid evidence reached the parser"),
    )

    with pytest.raises(ContractValidationError, match="raw_response"):
        runner.parse_and_score(verified, require_complete=False)


def test_failure_finalization_uses_known_run_when_inputs_later_become_invalid(
    repository_root: Path,
    tmp_path: Path,
    gate_policy,
    policy_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracking = InMemoryTracking()
    runner = _runner(
        repository_root=repository_root,
        tmp_path=tmp_path,
        gate_policy=gate_policy,
        policy_factory=policy_factory,
        variant="revised",
        tracking=tracking,
    )
    original_inputs = runner._inputs
    input_reads = 0

    def fail_after_run_creation():
        nonlocal input_reads
        input_reads += 1
        if input_reads > 1:
            raise ValueError("overlay example IDs must be unique")
        return original_inputs()

    monkeypatch.setattr(runner, "_inputs", fail_after_run_creation)
    with pytest.raises(ValueError, match="overlay example IDs must be unique"):
        runner.run(max_calls=100)

    assert input_reads == 2
    assert next(iter(tracking.runs.values())).status == "FAILED"


def test_frozen_p95_method_has_explicit_boundary() -> None:
    assert percentile_r7([0, 10, 20, 30, 40], 0.95) == pytest.approx(38.0)
