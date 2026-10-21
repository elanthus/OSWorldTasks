"""Pure, versioned S7 scripted qualification gates. No execution or storage clients."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.platform.stateful_contracts import EvidenceClass, StatefulPolicyPackage
from pixelgym.platform.stateful_control import StatefulGateObservation, StatefulGateReport

VERSION = "s7-scripted-serving-controls-v1"
EVIDENCE_LABEL = "credential-free-scripted-engineering-validation"
RULES = (
    ("assigned_episode_coverage", "at_least", 1.0),
    ("package_outcome_matches_plan", "at_least", 1),
    ("shared_control_cases_matched", "at_least", 7),
    ("invalid_backend_dispatches", "at_most", 0),
    ("duplicate_attempts_or_dispatches", "at_most", 0),
    ("replay_mismatches", "at_most", 0),
    ("unresolved_attempt_outcomes", "at_most", 0),
    ("artifact_verification_failures", "at_most", 0),
    ("real_provider_requests", "at_most", 0),
    ("provider_control_requests", "at_most", 0),
    ("monetary_spend_usd", "at_most", 0),
)


def policy_document() -> dict[str, Any]:
    return {
        "version": VERSION,
        "evidence_label": EVIDENCE_LABEL,
        "evidence_class": "calibration",
        "deployment_tier": "demo",
        "deployment_attempt_caps": {"A": 4, "B": 24},
        "phase_caps": {"qualification": 77, "A": 4, "B": 24, "smoke": 6},
        "campaign_cap": 111,
        "real_provider_cap": 0,
        "rules": [
            {"name": name, "comparator": comparator, "threshold": threshold}
            for name, comparator, threshold in RULES
        ],
    }


POLICY_SHA256 = content_digest(policy_document()).removeprefix("sha256:")


def validate_report_rules(report: StatefulGateReport) -> None:
    """Reject a digest/version substitution or an omitted/changed approved rule."""
    if report.gate_policy_version != VERSION or report.gate_policy_sha256 != POLICY_SHA256:
        raise ValueError("S7 gate policy version or digest differs from the frozen policy")
    actual = tuple((o.name, o.comparator, o.threshold) for o in report.observations)
    if actual != RULES:
        raise ValueError("S7 gate report does not contain the exact approved rules")


def evaluate(package: StatefulPolicyPackage, summary: Mapping[str, Any]) -> StatefulGateReport:
    """Evaluate stored observations; missing quantities fail closed, never become zero."""
    if set(summary) != {"schema_version", "evidence_label", "role", "metrics", "binding", "inputs"}:
        raise ValueError("unsupported S7 summary fields")
    if summary["schema_version"] != "pixelgym-s7-summary-v1":
        raise ValueError("unsupported S7 summary schema")
    if summary["evidence_label"] != EVIDENCE_LABEL or summary["role"] not in {"A", "B"}:
        raise ValueError("S7 summary must disclose its scripted package role")
    if content_digest(dict(summary)) != package.evidence.summary_sha256:
        raise ValueError("S7 summary digest does not match the package")
    if summary["binding"] != {
        "plan_sha256": package.evidence.plan_sha256,
        "run_reference": package.evidence.run_reference,
        "v5_policy_id": package.v5_policy_id,
        "package_source_sha256": package.package_source_sha256,
    }:
        raise ValueError("S7 summary identity differs from the package")
    metrics = summary["metrics"]
    if not isinstance(metrics, dict) or set(metrics) - {r[0] for r in RULES}:
        raise ValueError("unsupported S7 observation names")
    observations = []
    for name, comparator, threshold in RULES:
        value = metrics.get(name)
        if value is not None and type(value) in {int, float} and value < 0:
            raise ValueError("S7 observations cannot be negative")
        # The contract rejects bools, non-numbers, and non-finite values.
        passed = (
            value is not None
            and type(value) in {int, float}
            and (value >= threshold if comparator == "at_least" else value <= threshold)
        )
        observations.append(StatefulGateObservation(name, value, threshold, comparator, passed))
    role = summary["role"]
    expected_successes = int(role == "B")
    execution = package.v5_manifest()
    binding_passed = (
        package.evidence_class is EvidenceClass.CALIBRATION
        and package.evidence.assigned_episodes == package.evidence.attempted_episodes == 1
        and package.evidence.exact_successes == expected_successes
        and package.max_steps == (1 if role == "A" else 26)
        and package.provider == "in-process-fake"
        and package.model == "no-cost-scripted-policy-v1"
        and execution.max_model_attempts_per_action == 1
        and execution.max_cancellation_requests_per_attempt == 0
        and execution.max_reconciliation_requests_per_attempt == 0
        and execution.request_deadline_seconds == 5.0
        and execution.cancellation_mode == "none"
        and execution.reconciliation_deadline_seconds == 0.0
        and execution.dirty_worktree_policy == "reject"
        and execution.sandbox.provider_endpoint == "http://127.0.0.1:9"
    )
    code_passed = (
        package.code_state == "clean"
        and package.source_provenance_verified
        and package.code_revision == package.v5_manifest().code_revision
    )
    reasons = [o.name for o in observations if not o.passed]
    if not binding_passed:
        reasons.append("package specification or episode allocation differs from the S7 plan")
    if not code_passed:
        reasons.append("clean bound source provenance is required")
    return StatefulGateReport(
        gate_policy_version=VERSION,
        gate_policy_sha256=POLICY_SHA256,
        policy_id=package.policy_id,
        run_reference=package.evidence.run_reference,
        evidence_class=package.evidence_class,
        evidence_plan_sha256=package.evidence.plan_sha256,
        evidence_summary_sha256=package.evidence.summary_sha256,
        observations=tuple(observations),
        code_revision_passed=code_passed,
        overall_passed=not reasons,
        reasons=tuple(reasons),
    )
