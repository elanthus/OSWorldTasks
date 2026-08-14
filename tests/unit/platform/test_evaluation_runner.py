from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelgym.platform.evaluation import (
    EvaluationRunner,
    PlatformProviderResponse,
    ScriptedReplayProvider,
    percentile_r7,
)
from pixelgym.platform.immutable_store import LocalImmutableStore
from pixelgym.platform.mlflow_tracking import InMemoryTracking


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
):
    version = 1 if variant == "baseline" else 2
    return EvaluationRunner(
        repository_root=repository_root,
        store=LocalImmutableStore(tmp_path / "immutable"),
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
    revised_summary, revised_report, _ = revised.run(max_calls=100)
    assert baseline_summary.accuracy == 0.56
    assert not baseline_report.overall_passed
    assert revised_summary.accuracy == 1.0
    assert revised_report.overall_passed


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


class InvalidProvider:
    name = "scripted-demo"
    model = "day3-replay-revised-v2"
    synthetic = True

    def __init__(self) -> None:
        self.call_ids: list[str] = []

    def invoke(self, **request):
        self.call_ids.append(request["request_id"])
        return PlatformProviderResponse("not-json", 25.0, {}, 0.0)


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
    first_envelope = json.loads(LocalImmutableStore(tmp_path / "immutable").get_verified(references[0]))
    assert first_envelope["raw_response"] == "not-json"
    assert first_envelope["request_status"] == "responded"


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
    monkeypatch.setattr(module, "parse_prediction", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("crash after receipt")))
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


def test_frozen_p95_method_has_explicit_boundary() -> None:
    assert percentile_r7([0, 10, 20, 30, 40], 0.95) == pytest.approx(38.0)
