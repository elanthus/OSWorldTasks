"""Frozen platform contracts with strict, JSON-safe representations."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class CandidateState(StrEnum):
    GATE_FAILED = "GateFailed"
    ELIGIBLE = "Eligible"
    APPROVED = "Approved"


class RunStatus(StrEnum):
    SUBMITTED = "Submitted"
    RUNNING = "Running"
    COMPLETE = "Complete"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass(frozen=True)
class ArtifactRef:
    logical_key: str
    uri: str
    version_id: str
    sha256: str
    size: int
    media_type: str
    retention_status: str

    def __post_init__(self) -> None:
        if not self.logical_key or not self.uri or not self.version_id:
            raise ValueError("artifact identity fields must be nonempty")
        digest = self.sha256.removeprefix("sha256:")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("artifact sha256 must be a lowercase SHA-256 digest")
        if self.size < 0:
            raise ValueError("artifact size must be nonnegative")
        if not self.media_type or not self.retention_status:
            raise ValueError("artifact media type and retention status are required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GatePolicy:
    schema_version: str
    policy_version: str
    minimum_accuracy: float
    maximum_cost_usd_per_100: float
    maximum_provider_latency_p95_ms: float
    minimum_measured_count: int
    required_dataset_fingerprint: str
    required_scorer_version: str
    required_target_semantics: str
    dirty_code_allowed: bool = False
    confidence_bound_required: bool = False
    confidence_level: float = 0.95
    synthetic_only: bool = True

    def __post_init__(self) -> None:
        numeric = (
            self.minimum_accuracy,
            self.maximum_cost_usd_per_100,
            self.maximum_provider_latency_p95_ms,
        )
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("gate thresholds must be finite")
        if not 0 <= self.minimum_accuracy <= 1:
            raise ValueError("minimum_accuracy must be between zero and one")
        if self.maximum_cost_usd_per_100 < 0 or self.maximum_provider_latency_p95_ms < 0:
            raise ValueError("maximum cost and latency must be nonnegative")
        if self.minimum_measured_count <= 0:
            raise ValueError("minimum_measured_count must be positive")
        if not math.isfinite(self.confidence_level) or not 0 < self.confidence_level < 1:
            raise ValueError("confidence level must be strictly between zero and one")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    dataset_fingerprint: str
    policy_id: str
    scorer_version: str
    target_semantics: str
    expected_count: int
    scored_count: int
    unique_record_count: int
    correct_count: int
    accuracy: float | None
    cost_usd_per_100: float | None
    priced_call_count: int
    unpriced_call_count: int
    provider_latency_p95_ms: float | None
    latency_measured_count: int
    invalid_count: int = 0
    request_failure_count: int = 0
    dirty_code: bool = False
    code_state: str = "unverifiable"
    code_provenance_verified: bool = False
    synthetic_provider: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GateObservation:
    observed: float | None
    threshold: float
    passed: bool


@dataclass(frozen=True)
class CompletenessObservation:
    expected: int
    scored: int
    unique: int
    passed: bool


@dataclass(frozen=True)
class ConfidenceBoundObservation:
    """Auditable one-sided binomial lower confidence bound for accuracy."""

    observed: float | None
    threshold: float
    passed: bool
    method: str
    confidence_level: float
    success_count: int
    sample_count: int


@dataclass(frozen=True)
class GateReport:
    schema_version: str
    gate_policy_version: str
    run_id: str
    dataset_fingerprint: str
    policy_id: str
    accuracy: GateObservation
    cost_usd_per_100: GateObservation
    provider_latency_p95_ms: GateObservation
    completeness: CompletenessObservation
    compatibility_passed: bool
    code_revision_passed: bool
    overall_passed: bool
    confidence_bound: ConfidenceBoundObservation | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> GateReport:
        return cls(
            **{
                **value,
                "accuracy": GateObservation(**value["accuracy"]),
                "cost_usd_per_100": GateObservation(**value["cost_usd_per_100"]),
                "provider_latency_p95_ms": GateObservation(
                    **value["provider_latency_p95_ms"]
                ),
                "completeness": CompletenessObservation(**value["completeness"]),
                "confidence_bound": (
                    ConfidenceBoundObservation(**value["confidence_bound"])
                    if value.get("confidence_bound") is not None
                    else None
                ),
                "reasons": tuple(value["reasons"]),
            }
        )


@dataclass(frozen=True)
class PolicyManifest:
    schema_version: str
    provider: str
    model: str
    model_alias_disclosure: str | None
    prompt_name: str
    prompt_version: int
    prompt_sha256: str
    condition: str
    parameters: dict[str, Any]
    parser_version: str
    scorer_version: str
    overlay_version: str
    target_semantics: str
    code_revision: str
    code_state: str
    source_tree_sha256: str | None
    source_provenance_verified: bool
    dependency_lock_sha256: str
    source_provenance_failure_reason: str | None = None
    policy_id: str = ""

    def identity_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("policy_id")
        # Preserve verification of policy rows created before this optional field
        # existed, but bind every newly recorded failure reason to its evidence.
        if value["source_provenance_failure_reason"] is None:
            value.pop("source_provenance_failure_reason")
        return value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
