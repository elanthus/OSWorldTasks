"""Frozen platform contracts with strict, JSON-safe representations."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from pixelgym.platform.fingerprints import sha256_bytes


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
    provider_latency_p50_ms: float | None = None
    provider_latency_max_ms: float | None = None
    evaluation_end_to_end_duration_ms: float | None = None
    total_cost_usd: float | None = None
    cost_usd_per_example: float | None = None
    proposal_coverage: float | None = None
    conditional_mark_selection_accuracy: float | None = None
    primary_metric: str = "accuracy"
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
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value

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
    # Renderer identity is absent (None) only for policies packaged before the renderer
    # was bound into the manifest; such packages are readable but cannot be newly
    # registered or activated (see verify_renderer_binding in policy.py).
    renderer_version: str | None = None
    renderer_sha256: str | None = None
    prompt_template_text: str | None = None
    policy_id: str = ""

    def __post_init__(self) -> None:
        renderer_fields = (self.renderer_version, self.renderer_sha256, self.prompt_template_text)
        if any(item is not None for item in renderer_fields) and any(
            item is None for item in renderer_fields
        ):
            raise ValueError("policy manifest renderer identity fields must be all present or all absent")
        if self.prompt_template_text is not None and self.prompt_sha256 != sha256_bytes(
            self.prompt_template_text.encode("utf-8")
        ):
            raise ValueError("packaged prompt digest does not match the packaged prompt bytes")

    def identity_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("policy_id")
        if self.source_provenance_failure_reason == "legacy_schema_missing_provenance":
            for field_name in (
                "code_state",
                "source_tree_sha256",
                "source_provenance_verified",
                "source_provenance_failure_reason",
            ):
                value.pop(field_name)
        # A null failure reason is omitted from the policy identity; any recorded
        # failure reason remains identity-bound to its provenance evidence.
        elif value["source_provenance_failure_reason"] is None:
            value.pop("source_provenance_failure_reason")
        # Packages built before renderer identity existed never bound these fields
        # into their policy_id; preserve their already-stored identity unchanged.
        if self.renderer_version is None:
            for field_name in ("renderer_version", "renderer_sha256", "prompt_template_text"):
                value.pop(field_name)
        return value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
