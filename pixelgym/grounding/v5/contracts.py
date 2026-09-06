"""Frozen, canonical contracts for the PixelGym v5 benchmark."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal
from urllib.parse import urlsplit

from pixelgym.actions import KEY_ALLOWLIST_VERSION
from pixelgym.backends import base as _backend_base
from pixelgym.serialization import canonical_json_bytes

EnvironmentResumeRecord = _backend_base.EnvironmentResumeRecord
content_digest = _backend_base.content_digest
sha256_bytes = _backend_base.sha256_bytes

PROTOCOL_VERSION = "pixelgym-agent-v5"
GENERATOR_VERSION = "pixelgym-agent-v5-generator-v1"
TASK_SCHEMA_VERSION = "pixelgym-agent-v5-task-v1"
POLICY_SCHEMA_VERSION = "pixelgym-agent-v5-policy-v1"
ATTEMPT_SCHEMA_VERSION = "pixelgym-agent-v5-attempt-v1"
TRACE_SCHEMA_VERSION = "pixelgym-agent-v5-trace-v1"
REDACTION_POLICY_VERSION = "pixelgym-agent-v5-redaction-v1"
ACTION_SCHEMA_VERSION = "pixelgym-action-v1"
SCREEN_WIDTH = 1024
SCREEN_HEIGHT = 768


class Partition(StrEnum):
    DEVELOPMENT = "development"
    CALIBRATION = "calibration"
    CONFIRMATORY = "confirmatory"


class WorkflowFamily(StrEnum):
    EVIDENCE_AGGREGATION = "evidence_aggregation"
    DEFERRED_JOIN = "deferred_join"
    CONDITIONAL_PRECEDENCE = "conditional_precedence"
    REVISION_AFTER_REVEAL = "revision_after_reveal"
    VISIBLE_ERROR_RECOVERY = "visible_error_recovery"
    REVIEW_AND_COMMIT = "review_and_commit"


class DifficultyBand(StrEnum):
    REGRESSION = "regression_canary"
    FRONTIER = "frontier"
    CEILING = "ceiling_probe"


class StageKind(StrEnum):
    CLICK = "click"
    TEXT = "text"
    REVIEW = "review"
    COMMIT = "commit"


class CliFaultKind(StrEnum):
    """Stable process/request fault kinds shared by every CLI transport."""

    PRE_SEND = "pre_send"
    PROCESS_START = "process_start"
    PROCESS_TIMEOUT = "process_timeout"
    PROCESS_SIGNAL = "process_signal"
    CONNECTION_RESET = "connection_reset"
    TLS_FAILURE = "tls_failure"
    SUBSCRIPTION_RATE_LIMIT = "subscription_rate_limit"
    MALFORMED_EVENT_STREAM = "malformed_event_stream"
    NONZERO_EXIT = "nonzero_exit"
    POST_SEND = "post_send"


class ModelAttemptConsumption(StrEnum):
    NOT_CONSUMED = "not_consumed"
    CONSUMED = "consumed"
    UNKNOWN = "unknown"


class CostKnowledge(StrEnum):
    ZERO = "zero"
    KNOWN = "known"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CliFault:
    """Scoring-neutral facts about one CLI infrastructure/request failure."""

    kind: CliFaultKind
    code: str
    phase: Literal["pre_send", "post_send"]
    classification: Literal["request_failure", "infrastructure_failure"]
    model_attempt_consumption: ModelAttemptConsumption
    cost_knowledge: CostKnowledge

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("CLI fault code is required")
        if self.phase == "pre_send" and (
            self.model_attempt_consumption is not ModelAttemptConsumption.NOT_CONSUMED
            or self.cost_knowledge is not CostKnowledge.ZERO
        ):
            raise ValueError("pre-send CLI faults consume no model attempt and cost zero")

    @property
    def transport_status(
        self,
    ) -> Literal["pre_send_failure", "deadline", "rate_limited", "transport_fault"]:
        if self.phase == "pre_send":
            return "pre_send_failure"
        if self.kind is CliFaultKind.PROCESS_TIMEOUT:
            return "deadline"
        if self.kind is CliFaultKind.SUBSCRIPTION_RATE_LIMIT:
            return "rate_limited"
        return "transport_fault"

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "code": self.code,
            "phase": self.phase,
            "classification": self.classification,
            "model_attempt_consumption": self.model_attempt_consumption.value,
            "cost_knowledge": self.cost_knowledge.value,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CliFault:
        return cls(
            kind=CliFaultKind(value["kind"]),
            code=str(value["code"]),
            phase=value["phase"],
            classification=value["classification"],
            model_attempt_consumption=ModelAttemptConsumption(
                value["model_attempt_consumption"]
            ),
            cost_knowledge=CostKnowledge(value["cost_knowledge"]),
        )


@dataclass(frozen=True)
class TransportOutcome:
    status: Literal[
        "response",
        "policy_violation",
        "pre_send_failure",
        "deadline",
        "rate_limited",
        "transport_fault",
        "unknown",
    ]
    response: dict[str, Any] | None = None
    failure_code: str | None = None
    retry_after_seconds: float | None = None
    backoff_source: str | None = None
    fault: CliFault | None = None

    def __post_init__(self) -> None:
        if self.status in {"response", "policy_violation"} and self.response is None:
            raise ValueError(f"{self.status} transport outcome requires a response")
        if self.fault is not None and self.status != self.fault.transport_status:
            raise ValueError("CLI fault and transport status disagree")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "response": self.response,
            "failure_code": self.failure_code,
            "retry_after_seconds": self.retry_after_seconds,
            "backoff_source": self.backoff_source,
            "fault": None if self.fault is None else self.fault.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TransportOutcome:
        raw_fault = value.get("fault")
        return cls(
            status=value["status"],
            response=value.get("response"),
            failure_code=value.get("failure_code"),
            retry_after_seconds=value.get("retry_after_seconds"),
            backoff_source=value.get("backoff_source"),
            fault=(
                CliFault.from_dict(raw_fault)
                if isinstance(raw_fault, dict)
                else None
            ),
        )


def cli_pre_send_fault(
    code: str, *, kind: CliFaultKind = CliFaultKind.PRE_SEND
) -> CliFault:
    """Describe a failure proven to occur before a CLI process can send a request."""

    return CliFault(
        kind=kind,
        code=code,
        phase="pre_send",
        classification="request_failure",
        model_attempt_consumption=ModelAttemptConsumption.NOT_CONSUMED,
        cost_knowledge=CostKnowledge.ZERO,
    )


def cli_timeout_fault(code: str) -> CliFault:
    return CliFault(
        kind=CliFaultKind.PROCESS_TIMEOUT,
        code=code,
        phase="post_send",
        classification="infrastructure_failure",
        model_attempt_consumption=ModelAttemptConsumption.UNKNOWN,
        cost_knowledge=CostKnowledge.UNKNOWN,
    )


_CONNECTION_RESET_MARKERS = (
    "connection reset",
    "connection closed",
    "broken pipe",
    "econnreset",
    "socket hang up",
)
_TLS_FAILURE_MARKERS = (
    "certificate verify failed",
    "ssl error",
    "tls error",
    "tls handshake",
)
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "usage limit",
    "subscription limit",
)


def classify_cli_process_fault(
    *,
    return_code: int | None,
    stderr: str,
    stream_malformed: bool,
    subscription_rate_limited: bool = False,
    error_type: str | None = None,
    usage_observed: bool = False,
    cost_observed: bool = False,
) -> CliFault | None:
    """Classify a failed CLI execution without treating its text as model output."""

    diagnostic = f"{error_type or ''} {stderr}".lower()
    if subscription_rate_limited or any(marker in diagnostic for marker in _RATE_LIMIT_MARKERS):
        return CliFault(
            kind=CliFaultKind.SUBSCRIPTION_RATE_LIMIT,
            code="cli_subscription_rate_limit",
            phase="post_send",
            classification="request_failure",
            model_attempt_consumption=ModelAttemptConsumption.NOT_CONSUMED,
            cost_knowledge=CostKnowledge.ZERO,
        )
    if any(marker in diagnostic for marker in _TLS_FAILURE_MARKERS) or (
        error_type is not None and "ssl" in error_type.lower()
    ):
        kind = CliFaultKind.TLS_FAILURE
        code = "cli_tls_failure"
    elif any(marker in diagnostic for marker in _CONNECTION_RESET_MARKERS) or (
        error_type is not None
        and error_type.lower() in {"connectionreseterror", "brokenpipeerror"}
    ):
        kind = CliFaultKind.CONNECTION_RESET
        code = "cli_connection_reset"
    elif return_code is not None and return_code < 0:
        kind = CliFaultKind.PROCESS_SIGNAL
        code = "cli_process_signal"
    elif stream_malformed:
        kind = CliFaultKind.MALFORMED_EVENT_STREAM
        code = "cli_malformed_event_stream"
    elif return_code == 0 and error_type is None:
        return None
    elif return_code not in (None, 0):
        kind = CliFaultKind.NONZERO_EXIT
        code = "cli_nonzero_exit"
    else:
        kind = CliFaultKind.POST_SEND
        code = "cli_post_send_failure"
    return CliFault(
        kind=kind,
        code=code,
        phase="post_send",
        classification="infrastructure_failure",
        model_attempt_consumption=(
            ModelAttemptConsumption.CONSUMED
            if usage_observed
            else ModelAttemptConsumption.UNKNOWN
        ),
        cost_knowledge=(CostKnowledge.KNOWN if cost_observed else CostKnowledge.UNKNOWN),
    )


def cli_fault_outcome(fault: CliFault) -> TransportOutcome:
    return TransportOutcome(
        fault.transport_status,
        failure_code=fault.code,
        fault=fault,
    )


def sandbox_endpoint_allowlist_digest(endpoint: str, *, policy_version: str) -> str:
    """Validate one credential-free provider origin and bind it to a policy version."""

    parts = urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("provider endpoint must be an absolute HTTP(S) URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("provider endpoint must not contain URL userinfo")
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise ValueError("provider endpoint identity must contain only scheme, host, and port")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("provider endpoint has an invalid port") from exc
    hostname = parts.hostname.lower()
    normalized_host = f"[{hostname}]" if ":" in hostname else hostname
    normalized = f"{parts.scheme}://{normalized_host}"
    if port is not None:
        normalized += f":{port}"
    return content_digest({"policy": policy_version, "allowed_origins": [normalized]})


def _plain_mapping(value: dict[str, Any]) -> MappingProxyType[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class SeedRecord:
    seed: int
    partition: Partition
    family: WorkflowFamily
    family_index: int
    logical_id: str
    variant: Literal["base", "twin_a", "twin_b"]
    difficulty_band: DifficultyBand

    def __post_init__(self) -> None:
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("seed must be a non-negative plain int")
        if self.family_index < 0 or not self.logical_id:
            raise ValueError("family_index and logical_id must be valid")

    @property
    def robustness_pair(self) -> bool:
        return self.variant != "base"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "partition": self.partition.value,
            "family": self.family.value,
            "family_index": self.family_index,
            "logical_id": self.logical_id,
            "variant": self.variant,
            "difficulty_band": self.difficulty_band.value,
        }


@dataclass(frozen=True)
class Control:
    control_id: str
    label: str
    role: Literal["choice", "input", "continue", "commit"] = "choice"

    def to_dict(self) -> dict[str, str]:
        return {"control_id": self.control_id, "label": self.label, "role": self.role}


@dataclass(frozen=True)
class Stage:
    stage_id: str
    kind: StageKind
    heading: str
    instruction: str
    facts: tuple[str, ...]
    controls: tuple[Control, ...]
    target_control_id: str
    required_text: str = ""
    critical: bool = False
    dependency_id: str | None = None
    recovery_stage: bool = False

    def __post_init__(self) -> None:
        ids = [control.control_id for control in self.controls]
        if len(ids) != len(set(ids)) or self.target_control_id not in ids:
            raise ValueError(f"stage {self.stage_id!r} has invalid control identity")
        if self.kind is StageKind.TEXT:
            if not self.required_text or len(self.required_text) > 5:
                raise ValueError("text stages require one to five printable characters")
        elif self.required_text:
            raise ValueError("only text stages may carry required_text")

    def public_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "kind": self.kind.value,
            "heading": self.heading,
            "instruction": self.instruction,
            "facts": list(self.facts),
            "controls": [control.to_dict() for control in self.controls],
        }

    def privileged_dict(self) -> dict[str, Any]:
        value = self.public_dict()
        value.update(
            {
                "target_control_id": self.target_control_id,
                "required_text": self.required_text,
                "critical": self.critical,
                "dependency_id": self.dependency_id,
                "recovery_stage": self.recovery_stage,
            }
        )
        return value


@dataclass(frozen=True)
class V5Task:
    seed_record: SeedRecord
    instruction: str
    title: str
    stages: tuple[Stage, ...]
    optimal_low_level_actions: int
    correction_slack: int
    max_episode_steps: int
    expected_result: str
    semantic_digest: str
    task_id: str

    def __post_init__(self) -> None:
        decision_count = len(self.stages)
        if not 8 <= decision_count <= 14:
            raise ValueError("v5 tasks require 8 to 14 semantic decisions")
        if not 18 <= self.optimal_low_level_actions <= 40:
            raise ValueError("v5 optimal action horizon must be in [18, 40]")
        expected_slack = max(6, math.ceil(0.25 * self.optimal_low_level_actions))
        if self.correction_slack != expected_slack:
            raise ValueError("correction slack does not match the frozen formula")
        if self.max_episode_steps != self.optimal_low_level_actions + expected_slack:
            raise ValueError("max_episode_steps does not match the frozen formula")
        typed = [stage.required_text for stage in self.stages if stage.required_text]
        if sum(map(len, typed)) > 12:
            raise ValueError("golden trajectory types more than twelve characters")
        dependencies = {stage.dependency_id for stage in self.stages if stage.dependency_id}
        if len(dependencies) < 2:
            raise ValueError("v5 tasks require at least two cross-step dependencies")

    @property
    def seed(self) -> int:
        return self.seed_record.seed

    @property
    def family(self) -> WorkflowFamily:
        return self.seed_record.family

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TASK_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "generator_version": GENERATOR_VERSION,
            "seed_record": self.seed_record.to_dict(),
            "instruction": self.instruction,
            "title": self.title,
            "stages": [stage.privileged_dict() for stage in self.stages],
            "optimal_low_level_actions": self.optimal_low_level_actions,
            "correction_slack": self.correction_slack,
            "max_episode_steps": self.max_episode_steps,
            "expected_result": self.expected_result,
            "semantic_digest": self.semantic_digest,
            "task_id": self.task_id,
        }

    def public_dict(self) -> dict[str, Any]:
        """Policy-safe task application input, excluding privileged annotations."""

        return {
            "protocol_version": PROTOCOL_VERSION,
            "task_id": self.task_id,
            "instruction": self.instruction,
            "title": self.title,
            "stages": [stage.public_dict() for stage in self.stages],
        }

    def generated_record(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "seed": self.seed,
            "fields": {"workflow_result": self.expected_result},
            "protocol_version": PROTOCOL_VERSION,
            "generator_version": GENERATOR_VERSION,
        }


@dataclass(frozen=True)
class SandboxManifest:
    runtime_digest: str
    network_policy_version: str
    provider_endpoint: str
    endpoint_allowlist_digest: str
    denied_capabilities: tuple[str, ...]

    REQUIRED_DENIALS = frozenset(
        {
            "external_search",
            "browser_dom",
            "shell",
            "shared_storage",
            "inbound_listener",
            "cross_policy_channel",
        }
    )

    def __post_init__(self) -> None:
        if not self.runtime_digest.startswith("sha256:"):
            raise ValueError("sandbox runtime must be content addressed")
        expected_endpoint_digest = sandbox_endpoint_allowlist_digest(
            self.provider_endpoint,
            policy_version=self.network_policy_version,
        )
        if self.endpoint_allowlist_digest != expected_endpoint_digest:
            raise ValueError("sandbox endpoint allowlist digest does not match provider endpoint")
        missing = self.REQUIRED_DENIALS - set(self.denied_capabilities)
        if missing:
            raise ValueError(f"sandbox manifest omits required denials: {sorted(missing)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_digest": self.runtime_digest,
            "network_policy_version": self.network_policy_version,
            "provider_endpoint": self.provider_endpoint,
            "endpoint_allowlist_digest": self.endpoint_allowlist_digest,
            "denied_capabilities": list(self.denied_capabilities),
        }


@dataclass(frozen=True)
class PolicyManifest:
    policy_id: str
    provider: str
    model: str
    exact_snapshot: bool
    harness_digest: str
    dependency_lock_digest: str
    system_prompt_digest: str
    task_renderer_version: str
    response_schema_version: str
    state_reducer_version: str
    parser_version: str
    memory_policy_version: str
    coordinate_adapter: str
    coordinate_adapter_digest: str
    coordinate_input_convention: str
    max_model_attempts_per_action: int
    max_cancellation_requests_per_attempt: int
    max_reconciliation_requests_per_attempt: int
    request_deadline_seconds: float
    cancellation_mode: str
    reconciliation_deadline_seconds: float
    sandbox: SandboxManifest
    code_revision: str
    dirty_worktree_policy: str
    cross_episode_cache: Literal["disabled"] = "disabled"
    inference_parameters: tuple[tuple[str, str], ...] = ()
    context_limit: int = 1
    transport_retry_rule: str = "pre-send-or-confirmed-no-response-only-v1"
    provider_sdk_auto_retries: Literal[False] = False
    proxy_auto_retries: Literal[False] = False

    def __post_init__(self) -> None:
        if self.max_model_attempts_per_action <= 0:
            raise ValueError("max_model_attempts_per_action must be positive")
        if self.request_deadline_seconds <= 0 or self.reconciliation_deadline_seconds < 0:
            raise ValueError("provider deadlines are invalid")
        if self.context_limit <= 0:
            raise ValueError("context_limit must be positive")
        if self.provider_sdk_auto_retries or self.proxy_auto_retries:
            raise ValueError("provider SDK and proxy auto-retries must remain disabled")
        canonical = self.identity_fields()
        expected = "policy-" + sha256_bytes(canonical_json_bytes(canonical))[:20]
        if self.policy_id != expected:
            raise ValueError(f"policy_id must be {expected!r}")

    def identity_fields(self) -> dict[str, Any]:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "provider": self.provider,
            "model": self.model,
            "exact_snapshot": self.exact_snapshot,
            "harness_digest": self.harness_digest,
            "dependency_lock_digest": self.dependency_lock_digest,
            "system_prompt_digest": self.system_prompt_digest,
            "task_renderer_version": self.task_renderer_version,
            "response_schema_version": self.response_schema_version,
            "state_reducer_version": self.state_reducer_version,
            "parser_version": self.parser_version,
            "memory_policy_version": self.memory_policy_version,
            "coordinate_adapter": self.coordinate_adapter,
            "coordinate_adapter_digest": self.coordinate_adapter_digest,
            "coordinate_input_convention": self.coordinate_input_convention,
            "output_convention": f"integer-pixel/{SCREEN_WIDTH}x{SCREEN_HEIGHT}",
            "inference_screen": {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT},
            "action_schema_version": ACTION_SCHEMA_VERSION,
            "key_allowlist_version": KEY_ALLOWLIST_VERSION,
            "max_model_attempts_per_action": self.max_model_attempts_per_action,
            "max_cancellation_requests_per_attempt": self.max_cancellation_requests_per_attempt,
            "max_reconciliation_requests_per_attempt": self.max_reconciliation_requests_per_attempt,
            "request_deadline_seconds": self.request_deadline_seconds,
            "cancellation_mode": self.cancellation_mode,
            "reconciliation_deadline_seconds": self.reconciliation_deadline_seconds,
            "sandbox": self.sandbox.to_dict(),
            "code_revision": self.code_revision,
            "dirty_worktree_policy": self.dirty_worktree_policy,
            "cross_episode_cache": self.cross_episode_cache,
            "inference_parameters": [list(item) for item in self.inference_parameters],
            "context_limit": self.context_limit,
            "transport_retry_rule": self.transport_retry_rule,
            "provider_sdk_auto_retries": self.provider_sdk_auto_retries,
            "proxy_auto_retries": self.proxy_auto_retries,
        }

    def to_dict(self) -> dict[str, Any]:
        value = self.__dict__.copy()
        value["sandbox"] = self.sandbox.to_dict()
        value["inference_parameters"] = [list(item) for item in self.inference_parameters]
        return value

    @classmethod
    def build(cls, **fields: Any) -> PolicyManifest:
        """Build a manifest and derive its policy ID from every identity field."""

        values = {
            "cross_episode_cache": "disabled",
            "inference_parameters": (),
            "context_limit": 1,
            "transport_retry_rule": "pre-send-or-confirmed-no-response-only-v1",
            "provider_sdk_auto_retries": False,
            "proxy_auto_retries": False,
            **fields,
        }
        provisional = cls.__new__(cls)
        for name, value in values.items():
            object.__setattr__(provisional, name, value)
        object.__setattr__(provisional, "policy_id", "")
        identity = provisional.identity_fields()
        policy_id = "policy-" + sha256_bytes(canonical_json_bytes(identity))[:20]
        return cls(policy_id=policy_id, **values)


@dataclass(frozen=True, order=True)
class AttemptIdentity:
    trial_id: str
    step_index: int
    attempt_index: int

    def __post_init__(self) -> None:
        if not self.trial_id or self.step_index < 0 or self.attempt_index < 0:
            raise ValueError("invalid deterministic attempt identity")

    @property
    def key(self) -> str:
        return f"{self.trial_id}/step-{self.step_index:04d}/attempt-{self.attempt_index:02d}"


@dataclass(frozen=True)
class CallCaps:
    environment_action_cap: int
    model_attempt_cap: int
    provider_control_request_cap: int
    provider_wire_request_cap: int

    @classmethod
    def calculate(
        cls,
        *,
        max_episode_steps: tuple[int, ...],
        max_model_attempts_per_action: int,
        max_cancellation_requests_per_attempt: int,
        max_reconciliation_requests_per_attempt: int,
    ) -> CallCaps:
        environment = sum(max_episode_steps)
        attempts = environment * max_model_attempts_per_action
        controls = attempts * (
            max_cancellation_requests_per_attempt + max_reconciliation_requests_per_attempt
        )
        return cls(environment, attempts, controls, attempts + controls)

    def to_dict(self) -> dict[str, int]:
        return {
            "environment_action_cap": self.environment_action_cap,
            "model_attempt_cap": self.model_attempt_cap,
            "provider_control_request_cap": self.provider_control_request_cap,
            "provider_wire_request_cap": self.provider_wire_request_cap,
        }
