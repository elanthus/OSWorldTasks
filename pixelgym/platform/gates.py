"""Pure fail-closed promotion gate evaluation."""

from __future__ import annotations

import math

from pixelgym.platform.contracts import (
    CompletenessObservation,
    GateObservation,
    GatePolicy,
    GateReport,
    RunSummary,
)

GATE_REPORT_SCHEMA_VERSION = "pixelgym-promotion-gate-report-v1"


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def evaluate_gates(policy: GatePolicy, summary: RunSummary) -> GateReport:
    reasons: list[str] = []
    count_evidence = (
        summary.expected_count > 0
        and 0 <= summary.correct_count <= summary.scored_count <= summary.expected_count
        and summary.unique_record_count >= 0
    )
    expected_accuracy = (
        summary.correct_count / summary.expected_count if summary.expected_count > 0 else None
    )
    accuracy_consistent = (
        _finite(summary.accuracy)
        and expected_accuracy is not None
        and math.isclose(summary.accuracy, expected_accuracy, rel_tol=0.0, abs_tol=1e-12)
    )
    accuracy_passed = bool(
        count_evidence
        and accuracy_consistent
        and summary.accuracy >= policy.minimum_accuracy
    )
    if not accuracy_passed:
        reasons.append("accuracy is missing, inconsistent with counts, non-finite, or below the minimum")

    cost_evidence = (
        _finite(summary.cost_usd_per_100)
        and summary.unpriced_call_count == 0
        and summary.priced_call_count == summary.scored_count
        and summary.priced_call_count + summary.unpriced_call_count == summary.scored_count
    )
    cost_passed = bool(
        cost_evidence and summary.cost_usd_per_100 <= policy.maximum_cost_usd_per_100
    )
    if not cost_passed:
        reasons.append("cost is missing, unpriced, incomplete, or above the maximum")

    latency_evidence = (
        _finite(summary.provider_latency_p95_ms)
        and summary.latency_measured_count >= policy.minimum_measured_count
        and summary.latency_measured_count == summary.scored_count
    )
    latency_passed = bool(
        latency_evidence
        and summary.provider_latency_p95_ms <= policy.maximum_provider_latency_p95_ms
    )
    if not latency_passed:
        reasons.append("latency evidence is missing, insufficient, non-finite, or above the maximum")

    completeness_passed = (
        count_evidence
        and summary.scored_count == summary.expected_count
        and summary.unique_record_count == summary.expected_count
    )
    if not completeness_passed:
        reasons.append("expected records are missing or duplicated")

    compatibility_passed = (
        summary.dataset_fingerprint == policy.required_dataset_fingerprint
        and summary.scorer_version == policy.required_scorer_version
        and summary.target_semantics == policy.required_target_semantics
        and (summary.synthetic_provider if policy.synthetic_only else True)
    )
    if not compatibility_passed:
        reasons.append("dataset, scorer, target semantics, or provider class is incompatible")

    code_revision_passed = policy.dirty_code_allowed or not summary.dirty_code
    if not code_revision_passed:
        reasons.append("dirty code revisions are forbidden by this gate policy")

    if policy.confidence_bound_required:
        reasons.append("confidence-bound evidence is required but absent from run summary v1")

    passed = all(
        (
            accuracy_passed,
            cost_passed,
            latency_passed,
            completeness_passed,
            compatibility_passed,
            code_revision_passed,
            not policy.confidence_bound_required,
        )
    )
    return GateReport(
        schema_version=GATE_REPORT_SCHEMA_VERSION,
        gate_policy_version=policy.policy_version,
        run_id=summary.run_id,
        dataset_fingerprint=summary.dataset_fingerprint,
        policy_id=summary.policy_id,
        accuracy=GateObservation(summary.accuracy, policy.minimum_accuracy, accuracy_passed),
        cost_usd_per_100=GateObservation(
            summary.cost_usd_per_100, policy.maximum_cost_usd_per_100, cost_passed
        ),
        provider_latency_p95_ms=GateObservation(
            summary.provider_latency_p95_ms,
            policy.maximum_provider_latency_p95_ms,
            latency_passed,
        ),
        completeness=CompletenessObservation(
            summary.expected_count,
            summary.scored_count,
            summary.unique_record_count,
            completeness_passed,
        ),
        compatibility_passed=compatibility_passed,
        code_revision_passed=code_revision_passed,
        overall_passed=passed,
        reasons=tuple(reasons),
    )
