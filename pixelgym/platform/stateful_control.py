"""Control-plane contracts for the ``stateful-v5`` policy kind (v5 serving stage S6).

The Milestone 4 control plane was built around one policy shape: a stateless grounding
manifest with a point-prediction gate report. S6 lets the same ledger (candidates, approvals,
deployments, the active pointer, and the audit log) carry a second, explicitly discriminated
kind without changing the meaning of any existing grounding row.

What lives here:

* :class:`PolicyKind` and :func:`stored_policy_kind`, the single discriminator. A stored policy
  document is ``stateful-v5`` only when its ``kind`` field says so; every other document keeps
  its existing grounding interpretation.
* :class:`StatefulGateReport`, the container a v5 gate evaluation produces. S6 fixes only its
  binding and fail-closed consistency rules. Which observations a v5 gate reads, their
  thresholds, and the frozen partition are the human S7 decision; the control store accepts a
  report only when its gate-policy digest is in an operator-approved set, which is empty by
  default.
* :class:`ServingTerms`, the human-approved per-version deployment attempt cap and deployment
  tier. A ``calibration``-class package may be served only on the ``demo`` tier.
* :func:`stateful_package_from_dict`, which rebuilds a stored package so every frozen S2
  identity check runs again on read.

This module has no storage, provider, or serving behaviour.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pixelgym.platform.contracts import ArtifactRef, CandidateState
from pixelgym.platform.stateful_contracts import (
    PACKAGE_KIND,
    EvidenceBinding,
    EvidenceClass,
    StatefulPolicyPackage,
)

STATEFUL_GATE_REPORT_SCHEMA_VERSION = "pixelgym-stateful-gate-report-v1"
SERVING_TERMS_SCHEMA_VERSION = "pixelgym-stateful-serving-terms-v1"

_PREFIXED_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_OBSERVATION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class PolicyKind(StrEnum):
    """Discriminator for the policy shapes the control plane can register and serve."""

    GROUNDING = "grounding"
    STATEFUL_V5 = PACKAGE_KIND


class DeploymentTier(StrEnum):
    DEMO = "demo"
    STANDARD = "standard"


def stored_policy_kind(policy_value: object) -> PolicyKind:
    """Return the kind of a stored policy document without reinterpreting grounding rows.

    Grounding manifests have no ``kind`` field. Only an explicit ``stateful-v5`` marker selects
    the stateful path; malformed or unknown documents stay on the grounding path, whose own
    strict validation then rejects them exactly as before S6.
    """

    if isinstance(policy_value, Mapping) and policy_value.get("kind") == PACKAGE_KIND:
        return PolicyKind.STATEFUL_V5
    return PolicyKind.GROUNDING


def tier_permits_evidence(tier: DeploymentTier, evidence_class: EvidenceClass) -> bool:
    """Calibration evidence never supports more than a demo deployment."""

    return DeploymentTier(tier) is DeploymentTier.DEMO or (
        EvidenceClass(evidence_class) is EvidenceClass.CONFIRMATORY
    )


def _require_prefixed_digest(value: object, name: str) -> None:
    if not isinstance(value, str) or not _PREFIXED_DIGEST_RE.fullmatch(value):
        raise ValueError(f"{name} must be a canonical sha256: digest")


def _require_digest(value: object, name: str) -> None:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{name} must be a bare SHA-256 hex digest")


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    return float(value)


@dataclass(frozen=True)
class StatefulGateObservation:
    """One gated quantity; ``passed`` is rechecked so a report cannot overstate a value."""

    name: str
    observed: float | None
    threshold: float
    comparator: str
    passed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _OBSERVATION_NAME_RE.fullmatch(self.name):
            raise ValueError("observation name must be a short lowercase identifier")
        if self.comparator not in {"at_least", "at_most"}:
            raise ValueError("observation comparator must be at_least or at_most")
        threshold = _finite_number(self.threshold, "observation threshold")
        observed = None if self.observed is None else _finite_number(self.observed, "observed")
        if type(self.passed) is not bool:
            raise ValueError("observation passed must be a boolean")
        # Equality with a threshold passes, matching the Milestone 4 gate rule; a missing
        # observation never passes.
        expected = observed is not None and (
            observed >= threshold if self.comparator == "at_least" else observed <= threshold
        )
        if self.passed != expected:
            raise ValueError(f"observation {self.name} passed flag contradicts its values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "observed": self.observed,
            "threshold": self.threshold,
            "comparator": self.comparator,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class StatefulGateReport:
    """Gate outcome bound to one stateful package, its evidence, and a frozen gate policy."""

    gate_policy_version: str
    gate_policy_sha256: str
    policy_id: str
    run_reference: str
    evidence_class: EvidenceClass
    evidence_plan_sha256: str
    evidence_summary_sha256: str
    observations: tuple[StatefulGateObservation, ...]
    code_revision_passed: bool
    overall_passed: bool
    reasons: tuple[str, ...] = ()
    schema_version: str = STATEFUL_GATE_REPORT_SCHEMA_VERSION
    kind: str = PACKAGE_KIND

    def __post_init__(self) -> None:
        if self.schema_version != STATEFUL_GATE_REPORT_SCHEMA_VERSION or self.kind != PACKAGE_KIND:
            raise ValueError("unsupported stateful gate report schema or kind")
        if not isinstance(self.gate_policy_version, str) or not self.gate_policy_version:
            raise ValueError("gate_policy_version must be non-empty")
        _require_digest(self.gate_policy_sha256, "gate_policy_sha256")
        _require_prefixed_digest(self.policy_id, "policy_id")
        if not isinstance(self.run_reference, str) or not self.run_reference:
            raise ValueError("run_reference must be non-empty")
        object.__setattr__(self, "evidence_class", EvidenceClass(self.evidence_class))
        _require_prefixed_digest(self.evidence_plan_sha256, "evidence_plan_sha256")
        _require_prefixed_digest(self.evidence_summary_sha256, "evidence_summary_sha256")
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "reasons", tuple(self.reasons))
        names = [item.name for item in self.observations]
        if len(names) != len(set(names)):
            raise ValueError("gate observation names must be unique")
        if type(self.code_revision_passed) is not bool or type(self.overall_passed) is not bool:
            raise ValueError("gate outcome flags must be booleans")
        if any(not isinstance(reason, str) or not reason for reason in self.reasons):
            raise ValueError("gate reasons must be non-empty strings")
        if self.overall_passed:
            # Conjunctive and fail closed: a pass needs at least one gated observation, every
            # observation passing, verified code provenance, and no blocking reason.
            if not self.observations:
                raise ValueError("a passing stateful gate report needs gated observations")
            if not all(item.passed for item in self.observations):
                raise ValueError("passing stateful gate report has a failed observation")
            if not self.code_revision_passed or self.reasons:
                raise ValueError("passing stateful gate report has failed components or reasons")
        elif not self.reasons:
            raise ValueError("failed stateful gate report must retain a blocking reason")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StatefulGateReport:
        fields = dict(value)
        fields["observations"] = tuple(
            StatefulGateObservation(**item) for item in fields["observations"]
        )
        fields["reasons"] = tuple(fields["reasons"])
        return cls(**fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "gate_policy_version": self.gate_policy_version,
            "gate_policy_sha256": self.gate_policy_sha256,
            "policy_id": self.policy_id,
            "run_reference": self.run_reference,
            "evidence_class": self.evidence_class.value,
            "evidence_plan_sha256": self.evidence_plan_sha256,
            "evidence_summary_sha256": self.evidence_summary_sha256,
            "observations": [item.to_dict() for item in self.observations],
            "code_revision_passed": self.code_revision_passed,
            "overall_passed": self.overall_passed,
            "reasons": list(self.reasons),
        }

    def binding_errors(self, package: StatefulPolicyPackage) -> list[str]:
        """Return every identity mismatch between this report and the package it gates."""

        errors = []
        if self.policy_id != package.policy_id:
            errors.append("gate report policy identity does not match the package")
        if self.evidence_class is not package.evidence_class:
            errors.append("gate report evidence class does not match the package")
        if self.run_reference != package.evidence.run_reference:
            errors.append("gate report run does not match the package evidence")
        if (
            self.evidence_plan_sha256 != package.evidence.plan_sha256
            or self.evidence_summary_sha256 != package.evidence.summary_sha256
        ):
            errors.append("gate report evidence digests do not match the package")
        return errors


@dataclass(frozen=True)
class ServingTerms:
    """Human-approved serving terms for one exact stateful package version."""

    terms_id: str
    candidate_id: str
    policy_id: str
    deployment_attempt_cap: int
    deployment_tier: DeploymentTier
    actor: str
    reason: str
    created_at_utc: str

    def __post_init__(self) -> None:
        if not self.terms_id or not self.candidate_id or not self.actor or not self.reason:
            raise ValueError("serving terms identity fields must be non-empty")
        _require_prefixed_digest(self.policy_id, "policy_id")
        if type(self.deployment_attempt_cap) is not int or self.deployment_attempt_cap <= 0:
            raise ValueError("deployment_attempt_cap must be a positive integer")
        object.__setattr__(self, "deployment_tier", DeploymentTier(self.deployment_tier))

    def to_dict(self) -> dict[str, Any]:
        return {
            "terms_id": self.terms_id,
            "candidate_id": self.candidate_id,
            "policy_id": self.policy_id,
            "deployment_attempt_cap": self.deployment_attempt_cap,
            "deployment_tier": self.deployment_tier.value,
            "actor": self.actor,
            "reason": self.reason,
            "created_at_utc": self.created_at_utc,
        }


@dataclass(frozen=True)
class StatefulCandidateRecord:
    """A ``stateful-v5`` candidate as the control store holds it."""

    candidate_id: str
    source_run_id: str
    package: StatefulPolicyPackage
    gate_report: StatefulGateReport
    gate_report_sha256: str
    artifacts: tuple[ArtifactRef, ...]
    state: CandidateState
    version: int

    @property
    def kind(self) -> PolicyKind:
        return PolicyKind.STATEFUL_V5

    @property
    def policy_id(self) -> str:
        return self.package.policy_id


def stateful_package_from_dict(value: Mapping[str, Any]) -> StatefulPolicyPackage:
    """Rebuild a stored package; construction reruns every frozen S2 identity check."""

    fields = dict(value)
    evidence = fields.get("evidence")
    if not isinstance(evidence, Mapping):
        raise TypeError("stateful package evidence must be an object")
    fields["evidence"] = EvidenceBinding(**evidence)
    package = StatefulPolicyPackage(**fields)
    if package.to_dict() != dict(value):
        raise ValueError("stored stateful package is not its canonical document")
    return package


def artifact_digest(reference: ArtifactRef) -> str:
    """Normalize an artifact digest to the ``sha256:`` form the package binding uses."""

    return "sha256:" + reference.sha256.removeprefix("sha256:")
