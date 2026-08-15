from __future__ import annotations

import dataclasses
import math

import pytest

from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.mlflow_tracking import InMemoryTracking
from pixelgym.platform.policy import is_verified_clean_revision, verify_policy_manifest


@pytest.mark.parametrize(
    ("field", "value", "reason_fragment"),
    [
        ("accuracy", 0.799999, "accuracy"),
        ("cost_usd_per_100", 0.000001, "cost"),
        ("provider_latency_p95_ms", 100.000001, "latency"),
        ("scored_count", 99, "missing or duplicated"),
        ("dataset_fingerprint", "sha256:" + "0" * 64, "incompatible"),
        ("dirty_code", True, "dirty code"),
    ],
)
def test_each_gate_fails_independently_and_boundaries_pass(
    passing_evidence, gate_policy, field: str, value: object, reason_fragment: str
) -> None:
    _policy, summary, boundary_report = passing_evidence
    assert boundary_report.overall_passed
    changed = dataclasses.replace(summary, **{field: value})
    report = evaluate_gates(gate_policy, changed)
    assert not report.overall_passed
    assert any(reason_fragment in reason for reason in report.reasons)


@pytest.mark.parametrize("value", [None, math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["accuracy", "cost_usd_per_100", "provider_latency_p95_ms"])
def test_missing_and_nonfinite_gate_evidence_fails_closed(
    passing_evidence, gate_policy, field: str, value: float | None
) -> None:
    _, summary, _ = passing_evidence
    report = evaluate_gates(gate_policy, dataclasses.replace(summary, **{field: value}))
    assert not report.overall_passed


def test_unpriced_or_insufficient_latency_counts_fail_closed(passing_evidence, gate_policy) -> None:
    _, summary, _ = passing_evidence
    unpriced = evaluate_gates(
        gate_policy,
        dataclasses.replace(summary, priced_call_count=99, unpriced_call_count=1),
    )
    insufficient = evaluate_gates(
        gate_policy, dataclasses.replace(summary, latency_measured_count=99)
    )
    assert not unpriced.cost_usd_per_100.passed
    assert not insufficient.provider_latency_p95_ms.passed


def test_accuracy_and_measurement_counts_must_be_internally_consistent(
    passing_evidence, gate_policy
) -> None:
    _, summary, _ = passing_evidence
    inconsistent_accuracy = evaluate_gates(
        gate_policy, dataclasses.replace(summary, accuracy=0.9)
    )
    extra_latency = evaluate_gates(
        gate_policy, dataclasses.replace(summary, latency_measured_count=101)
    )
    missing_price_record = evaluate_gates(
        gate_policy,
        dataclasses.replace(summary, priced_call_count=99, unpriced_call_count=0),
    )
    assert not inconsistent_accuracy.accuracy.passed
    assert not extra_latency.provider_latency_p95_ms.passed
    assert not missing_price_record.cost_usd_per_100.passed


def test_any_policy_component_changes_policy_id(policy_factory) -> None:
    base = policy_factory()
    changed = policy_factory(model="another-exact-model")
    assert base.policy_id != changed.policy_id
    verify_policy_manifest(base)
    with pytest.raises(ValueError, match="digest"):
        verify_policy_manifest(dataclasses.replace(base, model="tampered"))


@pytest.mark.parametrize(
    ("revision", "expected"),
    [("a" * 40, True), ("ABCDEF" * 7, False), ("clean-main", False), ("unknown-dirty", False)],
)
def test_only_exact_lowercase_git_commit_has_valid_format(
    revision: str, expected: bool
) -> None:
    assert is_verified_clean_revision(revision) is expected


def test_unverifiable_revision_fails_the_clean_code_gate(
    passing_evidence, gate_policy
) -> None:
    policy, summary, _ = passing_evidence
    assert is_verified_clean_revision(policy.code_revision)
    report = evaluate_gates(
        gate_policy,
        dataclasses.replace(summary, dirty_code=False, code_state="clean", code_provenance_verified=False),
    )
    assert not report.code_revision_passed


def test_gate_report_is_byte_stable(passing_evidence, gate_policy) -> None:
    _, summary, first = passing_evidence
    second = evaluate_gates(gate_policy, summary)
    assert first.to_dict() == second.to_dict()


def test_tracking_contract_is_idempotent_and_params_are_immutable(
    passing_evidence, policy_factory
) -> None:
    policy, summary, report = passing_evidence
    tracking = InMemoryTracking()
    params = {
        "dataset_fingerprint": summary.dataset_fingerprint,
        "dataset_protocol_version": "pixelgym-grounding-v1",
        "dataset_example_count": 100,
        "prompt_name": policy.prompt_name,
        "prompt_version": policy.prompt_version,
        "prompt_sha256": policy.prompt_sha256,
        "provider": policy.provider,
        "model": policy.model,
        "condition": policy.condition,
        "parser_version": policy.parser_version,
        "scorer_version": policy.scorer_version,
        "target_semantics": policy.target_semantics,
        "price_catalog_version": "pixelgym-demo-prices-v1",
        "code_revision": policy.code_revision,
        "code_state": policy.code_state,
        "source_tree_sha256": policy.source_tree_sha256,
        "source_provenance_verified": policy.source_provenance_verified,
        "dependency_lock_sha256": policy.dependency_lock_sha256,
        "python_version": "3.12.0",
        "submission_id": "submission-1",
        "synthetic_provider": True,
    }
    run_id = tracking.create_or_recover_run("submission-1", params)
    assert tracking.create_or_recover_run("submission-1", params) == run_id
    tracking.reconcile_pathspec(run_id, "GroundingEvaluationFlow/1")
    tracking.log_summary(run_id, summary, report, [])
    assert set(tracking.runs[run_id].artifacts) == {
        "summary.json", "gate-report.json", "immutable-artifact-index.json"
    }
    with pytest.raises(ValueError, match="changed"):
        tracking.create_or_recover_run("submission-1", {**params, "model": "different"})
    with pytest.raises(ValueError, match="unsafe"):
        tracking.create_or_recover_run("submission-' OR 1=1", params)
