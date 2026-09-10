"""Frozen contracts for serving v5 stateful policy systems (``pixelgym-serving-v2``).

This module is the S2 deliverable of ``plans/v5-policy-serving.md``. It defines the Python side
of three JSON Schemas: the stateful policy package, the ``/api/v2/episodes`` session messages,
and the immutable episode records the serving host writes. It contains no runtime behaviour:
no provider call, no policy execution, no storage. Later stages build on these types and are
not allowed to change their meaning without a new schema version.

Boundaries encoded here:

* A package binds the v5 ``PolicyManifest`` by digest and carries its evidence class.
  ``calibration``-class packages exist only for demo deployments.
* A caller sees identity, caps, actions, and sealed failure codes. It never sees policy state,
  raw provider text, or anything the environment would not give the policy.
* Screenshots sent to ``act`` are recorded by digest only. The final screenshot given to
  ``close`` is stored as an authoritative object and referenced by digest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pixelgym.actions import KEY_ALLOWLIST_VERSION
from pixelgym.grounding.v5.contracts import (
    ACTION_SCHEMA_VERSION,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    SandboxManifest,
)
from pixelgym.grounding.v5.contracts import (
    PolicyManifest as V5PolicyManifest,
)
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes

PACKAGE_SCHEMA_VERSION = "pixelgym-stateful-policy-package-v1"
SESSION_SCHEMA_VERSION = "pixelgym-serving-session-v2"
EPISODE_RECORD_SCHEMA_VERSION = "pixelgym-serving-episode-record-v1"
PACKAGE_KIND = "stateful-v5"
MAX_TASK_INSTRUCTION_CHARS = 4000
MAX_ENCODED_IMAGE_CHARS = 6990508
SCREENSHOT_MEDIA_TYPES = frozenset({"image/png", "image/jpeg"})

_EPISODE_ID_RE = re.compile(r"^ep-[0-9a-f]{32}$")
_INTENT_ID_RE = re.compile(r"^intent-[0-9a-f]{32}$")
_PREFIXED_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceClass(StrEnum):
    CALIBRATION = "calibration"
    CONFIRMATORY = "confirmatory"

    @property
    def run_kind(self) -> str:
        return "d56-calibration" if self is EvidenceClass.CALIBRATION else "d59-confirmatory"


class SealedFailure(StrEnum):
    """Terminal per-step outcomes; each seals the episode and permits no further attempt."""

    PARSE_FAILURE = "parse_failure"
    INVALID_ACTION = "invalid_action"
    REQUEST_FAILURE = "request_failure"
    CAP_REACHED = "cap_reached"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    MAX_STEPS_REACHED = "max_steps_reached"


class TerminalClassification(StrEnum):
    CLOSED_BY_CALLER = "closed_by_caller"
    TERMINATED = "terminated"
    TRUNCATED = "truncated"
    PARSE_FAILURE = "parse_failure"
    INVALID_ACTION = "invalid_action"
    REQUEST_FAILURE = "request_failure"
    CAP_REACHED = "cap_reached"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    MAX_STEPS_REACHED = "max_steps_reached"


class IntentStatus(StrEnum):
    NONE = "none"
    ISSUED = "issued"
    RESULT_REPORTED = "result_reported"
    SEALED = "sealed"


class SessionResumePhase(StrEnum):
    """Last durable serving boundary used to recover one episode."""

    INITIALIZED = "initialized"
    PRE_CALL = "pre_call"
    POST_ATTEMPT = "post_attempt"
    POST_PARSE = "post_parse"
    INTENT_ISSUED = "intent_issued"
    POST_DISPATCH = "post_dispatch"
    SEALED = "sealed"
    CLOSED = "closed"


def _require_prefixed_digest(value: str, name: str) -> None:
    if not isinstance(value, str) or not _PREFIXED_DIGEST_RE.fullmatch(value):
        raise ValueError(f"{name} must be a canonical sha256: digest")


def _require_digest(value: str, name: str) -> None:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{name} must be a bare SHA-256 hex digest")


def _decode_v5_manifest(value: dict[str, Any]) -> V5PolicyManifest:
    """Rebuild the embedded manifest so its own frozen identity checks run."""

    fields = dict(value)
    sandbox_value = fields.get("sandbox")
    if not isinstance(sandbox_value, dict):
        raise TypeError("policy_manifest sandbox must be an object")
    try:
        fields["sandbox"] = SandboxManifest(
            runtime_digest=sandbox_value["runtime_digest"],
            network_policy_version=sandbox_value["network_policy_version"],
            provider_endpoint=sandbox_value["provider_endpoint"],
            endpoint_allowlist_digest=sandbox_value["endpoint_allowlist_digest"],
            denied_capabilities=tuple(sandbox_value["denied_capabilities"]),
        )
        inference_parameters = fields.get("inference_parameters")
        if not isinstance(inference_parameters, list) or any(
            not isinstance(item, list)
            or len(item) != 2
            or any(not isinstance(component, str) for component in item)
            for item in inference_parameters
        ):
            raise ValueError("policy_manifest inference_parameters are malformed")
        fields["inference_parameters"] = tuple(
            (item[0], item[1]) for item in inference_parameters
        )
        return V5PolicyManifest(**fields)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("policy_manifest violates the frozen v5 contract") from exc


@dataclass(frozen=True)
class ServingIdentity:
    """Identity attached to every response and record; exact, never inferred."""

    policy_id: str
    deployment_id: str
    exact_policy_version: str
    evidence_class: EvidenceClass

    def __post_init__(self) -> None:
        _require_prefixed_digest(self.policy_id, "policy_id")
        if not self.deployment_id or not self.exact_policy_version:
            raise ValueError("deployment_id and exact_policy_version must be non-empty")
        object.__setattr__(self, "evidence_class", EvidenceClass(self.evidence_class))

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "deployment_id": self.deployment_id,
            "exact_policy_version": self.exact_policy_version,
            "evidence_class": self.evidence_class.value,
        }


@dataclass(frozen=True)
class ServedAction:
    """One PixelGym action as the API expresses it: a symbolic type plus its operands.

    Validation of bounds and key indices is *not* done here; the host validates the parsed
    candidate through ``pixelgym.actions.validate_action`` against the package's screen and
    allowlist version before this object is ever constructed. This type only fixes the wire
    shape: operands not used by the action type are null, never zero.
    """

    action_type: str
    x: int | None = None
    y: int | None = None
    key: int | None = None

    def __post_init__(self) -> None:
        if self.action_type not in {"NOOP", "CLICK", "KEY"}:
            raise ValueError("action_type must be NOOP, CLICK, or KEY")
        operands = {"x": self.x, "y": self.y, "key": self.key}
        expected = {"NOOP": set(), "CLICK": {"x", "y"}, "KEY": {"key"}}[self.action_type]
        for name, value in operands.items():
            if name in expected:
                if type(value) is not int or value < 0:
                    raise ValueError(f"{self.action_type} requires a non-negative integer {name}")
            elif value is not None:
                raise ValueError(f"{self.action_type} must not carry {name}")

    def to_dict(self) -> dict[str, Any]:
        return {"action_type": self.action_type, "x": self.x, "y": self.y, "key": self.key}


@dataclass(frozen=True)
class ReportedResult:
    """What the caller reports about dispatching the previous intent; feeds the reducer only."""

    reward: float
    terminated: bool
    truncated: bool
    screenshot_sha256: str

    def __post_init__(self) -> None:
        if type(self.reward) is not float or self.reward not in (0.0, 1.0):
            raise ValueError("reward must be the float 0.0 or 1.0")
        if type(self.terminated) is not bool or type(self.truncated) is not bool:
            raise ValueError("terminated and truncated must be booleans")
        if self.terminated and self.truncated:
            raise ValueError("terminated and truncated are never both true")
        _require_prefixed_digest(self.screenshot_sha256, "screenshot_sha256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reward": self.reward,
            "terminated": self.terminated,
            "truncated": self.truncated,
            "screenshot_sha256": self.screenshot_sha256,
        }


@dataclass(frozen=True)
class EvidenceBinding:
    """The stored v5 run a package's candidacy rests on, bound by plan and summary digests."""

    run_kind: str
    run_reference: str
    plan_sha256: str
    summary_sha256: str
    assigned_episodes: int
    attempted_episodes: int
    exact_successes: int

    def __post_init__(self) -> None:
        if self.run_kind not in {"d56-calibration", "d59-confirmatory"}:
            raise ValueError("run_kind must be d56-calibration or d59-confirmatory")
        if not self.run_reference:
            raise ValueError("run_reference must be non-empty")
        _require_prefixed_digest(self.plan_sha256, "plan_sha256")
        _require_prefixed_digest(self.summary_sha256, "summary_sha256")
        if self.assigned_episodes <= 0:
            raise ValueError("assigned_episodes must be positive")
        if not 0 <= self.exact_successes <= self.attempted_episodes <= self.assigned_episodes:
            raise ValueError("episode counts must satisfy successes <= attempted <= assigned")

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class StatefulPolicyPackage:
    """Exact-version package of one v5 policy system for registration and serving.

    Construction revalidates the embedded manifest and every duplicated outer binding so a
    deserialized package cannot substitute different policy bytes behind a retained policy ID.
    """

    policy_manifest: dict[str, Any]
    policy_manifest_sha256: str
    v5_policy_id: str
    provider: str
    model: str
    model_alias_disclosure: str | None
    sandbox_manifest_sha256: str
    package_source_sha256: str
    dependency_lock_sha256: str
    max_steps: int
    evidence_class: EvidenceClass
    evidence: EvidenceBinding
    code_revision: str
    code_state: str
    source_tree_sha256: str | None
    source_provenance_verified: bool
    source_provenance_failure_reason: str | None
    schema_version: str = PACKAGE_SCHEMA_VERSION
    kind: str = PACKAGE_KIND
    screen: dict[str, int] = field(
        default_factory=lambda: {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT}
    )
    action_schema_version: str = ACTION_SCHEMA_VERSION
    key_allowlist_version: int = KEY_ALLOWLIST_VERSION
    policy_id: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != PACKAGE_SCHEMA_VERSION or self.kind != PACKAGE_KIND:
            raise ValueError("unsupported stateful package schema or kind")
        for name in (
            "policy_manifest_sha256",
            "sandbox_manifest_sha256",
            "package_source_sha256",
            "dependency_lock_sha256",
        ):
            _require_digest(getattr(self, name), name)
        if not re.fullmatch(r"policy-[0-9a-f]{20}", self.v5_policy_id):
            raise ValueError("v5_policy_id must be a v5 policy identifier")
        manifest = _decode_v5_manifest(self.policy_manifest)
        if manifest.to_dict() != self.policy_manifest:
            raise ValueError("policy_manifest is not the canonical frozen v5 document")
        manifest_sha256 = sha256_bytes(canonical_json_bytes(manifest.identity_fields()))
        if self.policy_manifest_sha256 != manifest_sha256:
            raise ValueError("policy_manifest_sha256 does not match the embedded manifest")
        if self.v5_policy_id != manifest.policy_id:
            raise ValueError("v5_policy_id does not match the embedded manifest")
        if self.provider != manifest.provider or self.model != manifest.model:
            raise ValueError("provider and model must match the embedded manifest")
        sandbox_sha256 = sha256_bytes(canonical_json_bytes(manifest.sandbox.to_dict()))
        if self.sandbox_manifest_sha256 != sandbox_sha256:
            raise ValueError("sandbox_manifest_sha256 does not match the embedded manifest")
        if self.screen != {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT}:
            raise ValueError("screen must match the embedded v5 inference dimensions")
        if self.action_schema_version != ACTION_SCHEMA_VERSION:
            raise ValueError("action_schema_version must match the embedded v5 contract")
        if self.key_allowlist_version != KEY_ALLOWLIST_VERSION:
            raise ValueError("key_allowlist_version must match the embedded v5 contract")
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        object.__setattr__(self, "evidence_class", EvidenceClass(self.evidence_class))
        if self.evidence.run_kind != self.evidence_class.run_kind:
            raise ValueError("evidence run_kind does not match evidence_class")
        if self.code_state not in {"clean", "dirty", "unverifiable"}:
            raise ValueError("code_state is invalid")
        if self.code_state == "unverifiable":
            if self.code_revision != "unverifiable":
                raise ValueError("unverifiable code requires code_revision='unverifiable'")
            if self.source_tree_sha256 is not None:
                raise ValueError("unverifiable code must not carry a source-tree digest")
            if self.source_provenance_verified is not False:
                raise ValueError("unverifiable code cannot have verified source provenance")
            if not isinstance(self.source_provenance_failure_reason, str) or not (
                self.source_provenance_failure_reason
            ):
                raise ValueError("unverifiable code requires a provenance failure reason")
        else:
            if not isinstance(self.code_revision, str) or not re.fullmatch(
                r"[0-9a-f]{40}", self.code_revision
            ):
                raise ValueError("verifiable code_revision must be a 40-character Git SHA")
            if self.source_tree_sha256 is None:
                raise ValueError("verifiable code requires a source-tree digest")
            _require_digest(self.source_tree_sha256, "source_tree_sha256")
            if self.source_provenance_verified is not True:
                raise ValueError("verifiable code requires verified source provenance")
            if self.source_provenance_failure_reason is not None:
                raise ValueError("verifiable code cannot carry a provenance failure reason")
        expected = "sha256:" + sha256_bytes(canonical_json_bytes(self.identity_dict()))
        if self.policy_id != expected:
            raise ValueError("policy_id does not match the package identity")

    @classmethod
    def build(
        cls,
        *,
        manifest: V5PolicyManifest,
        model_alias_disclosure: str | None,
        package_source_sha256: str,
        dependency_lock_sha256: str,
        max_steps: int,
        evidence_class: EvidenceClass,
        evidence: EvidenceBinding,
        code_revision: str,
        code_state: str,
        source_tree_sha256: str | None,
        source_provenance_verified: bool,
        source_provenance_failure_reason: str | None,
    ) -> StatefulPolicyPackage:
        """Bind a validated v5 manifest and derive the package's content-addressed policy ID."""
        values: dict[str, Any] = {
            "policy_manifest": manifest.to_dict(),
            "policy_manifest_sha256": sha256_bytes(
                canonical_json_bytes(manifest.identity_fields())
            ),
            "v5_policy_id": manifest.policy_id,
            "provider": manifest.provider,
            "model": manifest.model,
            "model_alias_disclosure": model_alias_disclosure,
            "sandbox_manifest_sha256": sha256_bytes(
                canonical_json_bytes(manifest.sandbox.to_dict())
            ),
            "package_source_sha256": package_source_sha256,
            "dependency_lock_sha256": dependency_lock_sha256,
            "max_steps": max_steps,
            "evidence_class": EvidenceClass(evidence_class),
            "evidence": evidence,
            "code_revision": code_revision,
            "code_state": code_state,
            "source_tree_sha256": source_tree_sha256,
            "source_provenance_verified": source_provenance_verified,
            "source_provenance_failure_reason": source_provenance_failure_reason,
        }
        provisional = cls.__new__(cls)
        for name, value in values.items():
            object.__setattr__(provisional, name, value)
        for name in ("schema_version", "kind", "action_schema_version", "key_allowlist_version"):
            object.__setattr__(provisional, name, cls.__dataclass_fields__[name].default)
        object.__setattr__(provisional, "screen", {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT})
        policy_id = "sha256:" + sha256_bytes(canonical_json_bytes(provisional.identity_dict()))
        return cls(policy_id=policy_id, **values)

    def identity_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        value.pop("policy_id")
        # The embedded manifest is identity-bound through its digest; embedding it again in the
        # identity would let two renderings of the same manifest produce different package IDs.
        value.pop("policy_manifest")
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "policy_manifest": dict(self.policy_manifest),
            "policy_manifest_sha256": self.policy_manifest_sha256,
            "v5_policy_id": self.v5_policy_id,
            "provider": self.provider,
            "model": self.model,
            "model_alias_disclosure": self.model_alias_disclosure,
            "sandbox_manifest_sha256": self.sandbox_manifest_sha256,
            "package_source_sha256": self.package_source_sha256,
            "dependency_lock_sha256": self.dependency_lock_sha256,
            "screen": dict(self.screen),
            "action_schema_version": self.action_schema_version,
            "key_allowlist_version": self.key_allowlist_version,
            "max_steps": self.max_steps,
            "evidence_class": self.evidence_class.value,
            "evidence": self.evidence.to_dict(),
            "code_revision": self.code_revision,
            "code_state": self.code_state,
            "source_tree_sha256": self.source_tree_sha256,
            "source_provenance_verified": self.source_provenance_verified,
            "source_provenance_failure_reason": self.source_provenance_failure_reason,
            "policy_id": self.policy_id,
        }


def episode_id_is_valid(value: str) -> bool:
    return bool(_EPISODE_ID_RE.fullmatch(value))


def intent_id_is_valid(value: str) -> bool:
    return bool(_INTENT_ID_RE.fullmatch(value))


@dataclass(frozen=True)
class StepCheckpoints:
    """Digests of the four policy-state checkpoints the v5 transaction writes per step."""

    pre_call: str
    post_attempt: str | None
    post_parse: str | None
    post_dispatch: str | None

    def __post_init__(self) -> None:
        _require_prefixed_digest(self.pre_call, "pre_call")
        for name in ("post_attempt", "post_parse", "post_dispatch"):
            value = getattr(self, name)
            if value is not None:
                _require_prefixed_digest(value, name)
        if self.post_parse is not None and self.post_attempt is None:
            raise ValueError("post_parse requires a post_attempt checkpoint")
        if self.post_dispatch is not None and self.post_parse is None:
            raise ValueError("post_dispatch requires a post_parse checkpoint")

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class EpisodeSessionState:
    """Durable per-episode state at one recoverable serving boundary.

    Policy checkpoint bytes live in the authoritative object store. This record contains only
    their digest and object key plus the minimum state needed to recover or fail closed after a
    process restart. The attempt journal remains the authority for provider-attempt settlement.
    """

    episode_id: str
    client_episode_ref: str
    identity: ServingIdentity
    created_at_utc: str
    updated_at_utc: str
    revision: int
    resume_phase: SessionResumePhase
    task_instruction_sha256: str
    screen: dict[str, int]
    max_steps: int
    max_model_attempts_per_action: int
    deployment_attempt_cap: int
    step_index: int
    policy_checkpoint_sha256: str
    policy_checkpoint_object_key: str
    last_intent_id: str | None
    last_intent_status: IntentStatus
    last_action: ServedAction | None
    sealed_failure: SealedFailure | None
    terminal_classification: TerminalClassification | None
    model_attempts: int
    provider_control_requests: int
    usage: dict[str, float] | None
    attributed_cost_usd: float | None
    schema_version: str = SESSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SESSION_SCHEMA_VERSION:
            raise ValueError("unsupported serving session-store schema")
        if not episode_id_is_valid(self.episode_id):
            raise ValueError("episode_id is malformed")
        if not self.client_episode_ref:
            raise ValueError("client_episode_ref must be non-empty")
        object.__setattr__(self, "resume_phase", SessionResumePhase(self.resume_phase))
        object.__setattr__(self, "last_intent_status", IntentStatus(self.last_intent_status))
        if self.sealed_failure is not None:
            object.__setattr__(self, "sealed_failure", SealedFailure(self.sealed_failure))
        if self.terminal_classification is not None:
            object.__setattr__(
                self,
                "terminal_classification",
                TerminalClassification(self.terminal_classification),
            )
        _require_prefixed_digest(self.task_instruction_sha256, "task_instruction_sha256")
        _require_prefixed_digest(self.policy_checkpoint_sha256, "policy_checkpoint_sha256")
        if not self.policy_checkpoint_object_key:
            raise ValueError("policy_checkpoint_object_key must be non-empty")
        if self.screen != {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT}:
            raise ValueError("session screen must match the v5 inference dimensions")
        for name in (
            "revision",
            "step_index",
            "model_attempts",
            "provider_control_requests",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("max_steps", "max_model_attempts_per_action", "deployment_attempt_cap"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (self.last_intent_id is None) != (self.last_action is None):
            raise ValueError("last_intent_id and last_action are stored together")
        if self.last_intent_id is not None and not intent_id_is_valid(self.last_intent_id):
            raise ValueError("last_intent_id is malformed")
        if self.last_intent_status is IntentStatus.NONE and self.last_intent_id is not None:
            raise ValueError("an absent last intent must not carry an id or action")
        if self.last_intent_status in {IntentStatus.ISSUED, IntentStatus.RESULT_REPORTED} and (
            self.last_intent_id is None
        ):
            raise ValueError("an issued or reported intent requires its id and action")
        if self.resume_phase is SessionResumePhase.INTENT_ISSUED and (
            self.last_intent_status is not IntentStatus.ISSUED
        ):
            raise ValueError("intent_issued phase requires an outstanding issued intent")
        if self.last_intent_status is IntentStatus.ISSUED and (
            self.resume_phase is not SessionResumePhase.INTENT_ISSUED
        ):
            raise ValueError("an outstanding intent requires the intent_issued phase")
        terminal = self.resume_phase in {SessionResumePhase.SEALED, SessionResumePhase.CLOSED}
        if terminal != (self.terminal_classification is not None):
            raise ValueError("only a terminal session carries a terminal classification")
        if (self.resume_phase is SessionResumePhase.SEALED) != (self.sealed_failure is not None):
            raise ValueError("only a sealed session carries a sealed failure")
        if self.sealed_failure is not None and (
            self.terminal_classification is None
            or self.terminal_classification.value != self.sealed_failure.value
        ):
            raise ValueError("sealed failure and terminal classification must match")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "client_episode_ref": self.client_episode_ref,
            "identity": self.identity.to_dict(),
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "revision": self.revision,
            "resume_phase": self.resume_phase.value,
            "task_instruction_sha256": self.task_instruction_sha256,
            "screen": dict(self.screen),
            "max_steps": self.max_steps,
            "max_model_attempts_per_action": self.max_model_attempts_per_action,
            "deployment_attempt_cap": self.deployment_attempt_cap,
            "step_index": self.step_index,
            "policy_checkpoint_sha256": self.policy_checkpoint_sha256,
            "policy_checkpoint_object_key": self.policy_checkpoint_object_key,
            "last_intent_id": self.last_intent_id,
            "last_intent_status": self.last_intent_status.value,
            "last_action": None if self.last_action is None else self.last_action.to_dict(),
            "sealed_failure": None if self.sealed_failure is None else self.sealed_failure.value,
            "terminal_classification": (
                None
                if self.terminal_classification is None
                else self.terminal_classification.value
            ),
            "model_attempts": self.model_attempts,
            "provider_control_requests": self.provider_control_requests,
            "usage": None if self.usage is None else dict(self.usage),
            "attributed_cost_usd": self.attributed_cost_usd,
        }


@dataclass(frozen=True)
class EpisodeStepRecord:
    """Immutable record of one ``act``: what was received, attempted, sealed, and returned."""

    episode_id: str
    step_index: int
    identity: ServingIdentity
    recorded_at_utc: str
    screenshot_sha256: str
    previous_intent_id: str | None
    previous_result: ReportedResult | None
    attempt_ids: tuple[str, ...]
    canonical_response_sha256s: tuple[str, ...]
    checkpoints: StepCheckpoints
    intent_id: str | None
    action: ServedAction | None
    sealed_failure: SealedFailure | None
    provider_request_ids: tuple[str, ...]
    latency_ms: float | None
    usage: dict[str, float] | None
    attributed_cost_usd: float | None

    def __post_init__(self) -> None:
        if not episode_id_is_valid(self.episode_id):
            raise ValueError("episode_id is malformed")
        if self.step_index < 0:
            raise ValueError("step_index must be non-negative")
        _require_prefixed_digest(self.screenshot_sha256, "screenshot_sha256")
        if (self.previous_intent_id is None) != (self.previous_result is None):
            raise ValueError("previous_intent_id and previous_result are reported together")
        if self.previous_intent_id is not None and not intent_id_is_valid(self.previous_intent_id):
            raise ValueError("previous_intent_id is malformed")
        if self.step_index == 0 and self.previous_intent_id is not None:
            raise ValueError("the first step cannot report a previous intent")
        if self.step_index > 0 and self.previous_intent_id is None:
            raise ValueError("every step after the first must report the previous intent")
        sealed = self.sealed_failure is not None
        if sealed == (self.intent_id is not None) or sealed == (self.action is not None):
            raise ValueError(
                "a step yields exactly one of an intent with action or a sealed failure"
            )
        if self.intent_id is not None and not intent_id_is_valid(self.intent_id):
            raise ValueError("intent_id is malformed")
        if self.sealed_failure is not None:
            object.__setattr__(self, "sealed_failure", SealedFailure(self.sealed_failure))
        for digest in self.canonical_response_sha256s:
            _require_prefixed_digest(digest, "canonical_response_sha256s")
        if len(self.canonical_response_sha256s) > len(self.attempt_ids):
            raise ValueError("more canonical responses than attempts")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EPISODE_RECORD_SCHEMA_VERSION,
            "record_kind": "episode_step",
            "episode_id": self.episode_id,
            "step_index": self.step_index,
            "identity": self.identity.to_dict(),
            "recorded_at_utc": self.recorded_at_utc,
            "screenshot_sha256": self.screenshot_sha256,
            "previous_intent_id": self.previous_intent_id,
            "previous_result": None
            if self.previous_result is None
            else self.previous_result.to_dict(),
            "attempt_ids": list(self.attempt_ids),
            "canonical_response_sha256s": list(self.canonical_response_sha256s),
            "checkpoints": self.checkpoints.to_dict(),
            "intent_id": self.intent_id,
            "action": None if self.action is None else self.action.to_dict(),
            "sealed_failure": None if self.sealed_failure is None else self.sealed_failure.value,
            "provider_request_ids": list(self.provider_request_ids),
            "latency_ms": self.latency_ms,
            "usage": None if self.usage is None else dict(self.usage),
            "attributed_cost_usd": self.attributed_cost_usd,
        }


@dataclass(frozen=True)
class EpisodeOpenedRecord:
    episode_id: str
    client_episode_ref: str
    identity: ServingIdentity
    opened_at_utc: str
    task_instruction_sha256: str
    screen: dict[str, int]
    max_steps: int
    deployment_attempt_cap: int

    def __post_init__(self) -> None:
        if not episode_id_is_valid(self.episode_id):
            raise ValueError("episode_id is malformed")
        _require_prefixed_digest(self.task_instruction_sha256, "task_instruction_sha256")
        if self.max_steps <= 0 or self.deployment_attempt_cap <= 0:
            raise ValueError("caps must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EPISODE_RECORD_SCHEMA_VERSION,
            "record_kind": "episode_opened",
            "episode_id": self.episode_id,
            "client_episode_ref": self.client_episode_ref,
            "identity": self.identity.to_dict(),
            "opened_at_utc": self.opened_at_utc,
            "task_instruction_sha256": self.task_instruction_sha256,
            "screen": dict(self.screen),
            "max_steps": self.max_steps,
            "deployment_attempt_cap": self.deployment_attempt_cap,
        }


@dataclass(frozen=True)
class EpisodeClosedRecord:
    episode_id: str
    identity: ServingIdentity
    closed_at_utc: str
    terminal_classification: TerminalClassification
    final_intent_id: str | None
    final_result: ReportedResult | None
    final_screenshot_sha256: str | None
    final_screenshot_object_key: str | None
    steps: int
    model_attempts: int
    provider_control_requests: int
    usage: dict[str, float] | None
    attributed_cost_usd: float | None

    def __post_init__(self) -> None:
        if not episode_id_is_valid(self.episode_id):
            raise ValueError("episode_id is malformed")
        object.__setattr__(
            self, "terminal_classification", TerminalClassification(self.terminal_classification)
        )
        if (self.final_intent_id is None) != (self.final_result is None):
            raise ValueError("final_intent_id and final_result are reported together")
        if (self.final_screenshot_sha256 is None) != (self.final_screenshot_object_key is None):
            raise ValueError("a stored final screenshot has both a digest and an object key")
        if self.final_result is not None and self.final_screenshot_sha256 is None:
            raise ValueError("a reported final result requires the stored final screenshot")
        if self.final_screenshot_sha256 is not None:
            _require_prefixed_digest(self.final_screenshot_sha256, "final_screenshot_sha256")
        if min(self.steps, self.model_attempts, self.provider_control_requests) < 0:
            raise ValueError("counts must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EPISODE_RECORD_SCHEMA_VERSION,
            "record_kind": "episode_closed",
            "episode_id": self.episode_id,
            "identity": self.identity.to_dict(),
            "closed_at_utc": self.closed_at_utc,
            "terminal_classification": self.terminal_classification.value,
            "final_intent_id": self.final_intent_id,
            "final_result": None if self.final_result is None else self.final_result.to_dict(),
            "final_screenshot_sha256": self.final_screenshot_sha256,
            "final_screenshot_object_key": self.final_screenshot_object_key,
            "steps": self.steps,
            "model_attempts": self.model_attempts,
            "provider_control_requests": self.provider_control_requests,
            "usage": None if self.usage is None else dict(self.usage),
            "attributed_cost_usd": self.attributed_cost_usd,
        }
