"""Pure fail-closed promotion gate evaluation."""

from __future__ import annotations

import math

from pixelgym.platform.contracts import (
    CompletenessObservation,
    ConfidenceBoundObservation,
    GateObservation,
    GatePolicy,
    GateReport,
    RunSummary,
)

GATE_REPORT_SCHEMA_VERSION = "pixelgym-promotion-gate-report-v1"
WILSON_LOWER_BOUND_METHOD = "wilson-score-one-sided-v1"


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _one_sided_normal_quantile(confidence_level: float) -> float:
    """Return the standard-normal quantile for a one-sided confidence level.

    ``statistics.NormalDist`` is in the standard library, so the calculation is
    deterministic and does not make the platform depend on a floating statistics
    package.  The policy records the level; the report records it and the method.
    """
    from statistics import NormalDist

    return NormalDist().inv_cdf(confidence_level)


def _wilson_lower_bound(*, successes: int, sample_size: int, confidence_level: float) -> float | None:
    """One-sided Wilson score lower bound for Bernoulli accuracy observations."""
    if not (_is_count(successes) and _is_count(sample_size) and 0 < sample_size):
        return None
    if successes > sample_size:
        return None
    z = _one_sided_normal_quantile(confidence_level)
    proportion = successes / sample_size
    z_squared = z * z
    denominator = 1 + z_squared / sample_size
    centre = proportion + z_squared / (2 * sample_size)
    margin = z * math.sqrt(
        proportion * (1 - proportion) / sample_size + z_squared / (4 * sample_size**2)
    )
    return (centre - margin) / denominator


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def evaluate_gates(policy: GatePolicy, summary: RunSummary) -> GateReport:
    reasons: list[str] = []
    count_evidence = (
        summary.expected_count > 0
        and all(
            _is_count(value)
            for value in (
                summary.expected_count,
                summary.scored_count,
                summary.unique_record_count,
                summary.correct_count,
                summary.invalid_count,
                summary.request_failure_count,
            )
        )
        and summary.correct_count <= summary.scored_count <= summary.expected_count
        # Invalid and request-failure records are retained and counted as incorrect;
        # neither may be hidden by claiming it was a correct observation.
        and summary.correct_count + summary.invalid_count + summary.request_failure_count
        <= summary.scored_count
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

    clean_provenance = (
        summary.code_state == "clean"
        and summary.code_provenance_verified
        and not summary.dirty_code
    )
    code_revision_passed = policy.dirty_code_allowed or clean_provenance
    if not code_revision_passed:
        reasons.append(
            "dirty code or clean Git/build source provenance is missing, malformed, or unverifiable"
        )

    lower_bound = _wilson_lower_bound(
        successes=summary.correct_count,
        sample_size=summary.scored_count,
        confidence_level=policy.confidence_level,
    )
    confidence_bound_passed = bool(
        policy.confidence_bound_required
        and count_evidence
        and accuracy_consistent
        and lower_bound is not None
        and lower_bound >= policy.minimum_accuracy
    )
    confidence_bound = (
        ConfidenceBoundObservation(
            observed=lower_bound,
            threshold=policy.minimum_accuracy,
            passed=confidence_bound_passed,
            method=WILSON_LOWER_BOUND_METHOD,
            confidence_level=policy.confidence_level,
            success_count=summary.correct_count,
            sample_count=summary.scored_count,
        )
        if policy.confidence_bound_required
        else None
    )
    if policy.confidence_bound_required and not confidence_bound_passed:
        reasons.append(
            "one-sided Wilson accuracy lower confidence bound is missing, inconsistent, "
            "insufficient, or below the minimum"
        )

    passed = all(
        (
            accuracy_passed,
            cost_passed,
            latency_passed,
            completeness_passed,
            compatibility_passed,
            code_revision_passed,
            not policy.confidence_bound_required or confidence_bound_passed,
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
        confidence_bound=confidence_bound,
        reasons=tuple(reasons),
    )
