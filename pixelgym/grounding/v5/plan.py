"""Versioned plan contract for manifest-driven grounding calibration runs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import jsonschema

from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.evidence import validate_credential_free

RUNNER_PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-runner-plan-v1"
RUNNER_RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-runner-result-v1"
_SCHEMA_PATH = Path(__file__).with_name("schemas") / "runner-plan.schema.json"

ProviderAdapterName = Literal[
    "openrouter_http", "codex_cli", "claude_cli", "deterministic_fake"
]
TransportName = Literal["http", "cli_subprocess", "deterministic_fake"]
ResumeMode = Literal["forbid", "reconcile_existing"]


@dataclass(frozen=True)
class PolicySlot:
    slot: str
    policy_manifest: dict[str, Any]
    policy_manifest_digest: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PolicySlot:
        manifest = cast(dict[str, Any], value["policy_manifest"])
        digest = str(value["policy_manifest_digest"])
        if content_digest(manifest) != digest:
            raise ValueError(f"policy slot {value['slot']!r} manifest digest mismatch")
        return cls(str(value["slot"]), manifest, digest)


@dataclass(frozen=True)
class TaskAssignment:
    ordinal: int
    slot: str
    seed: int
    task_id: str
    family: str
    action_limit: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TaskAssignment:
        return cls(
            ordinal=int(value["ordinal"]),
            slot=str(value["slot"]),
            seed=int(value["seed"]),
            task_id=str(value["task_id"]),
            family=str(value["family"]),
            action_limit=int(value["action_limit"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class ProviderPlan:
    adapter: ProviderAdapterName
    transport: TransportName
    config: dict[str, Any]


@dataclass(frozen=True)
class BudgetPlan:
    caps: CallCaps
    maximum_spend_usd: Decimal
    per_request_theoretical_maximum_usd: Decimal
    unknown_reservation_rule: str


@dataclass(frozen=True)
class RetryBreakerPlan:
    max_bounded_retries_per_action: int
    consecutive_failure_limit: int
    continue_classifications: tuple[str, ...]
    hard_stop_classifications: tuple[str, ...]


@dataclass(frozen=True)
class OutputPlan:
    directory: PurePosixPath
    journal: PurePosixPath
    summary: PurePosixPath
    resume_mode: ResumeMode


@dataclass(frozen=True)
class CalibrationPlan:
    raw: dict[str, Any]
    purpose: str
    code_revision: str
    policies: tuple[PolicySlot, ...]
    manifest_path: PurePosixPath
    manifest_digest: str
    assignments: tuple[TaskAssignment, ...]
    provider: ProviderPlan
    budgets: BudgetPlan
    retry_breaker: RetryBreakerPlan
    stop_conditions: tuple[str, ...]
    outputs: OutputPlan

    @property
    def digest(self) -> str:
        return content_digest(self.raw)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CalibrationPlan:
        raw = dict(value)
        _validate_schema(raw)
        validate_credential_free(raw)
        policy_values = cast(Sequence[Mapping[str, Any]], raw["policy_panel"])
        policies = tuple(PolicySlot.from_dict(item) for item in policy_values)
        slots = [policy.slot for policy in policies]
        if len(slots) != len(set(slots)):
            raise ValueError("policy panel slots must be unique")
        allocation = cast(Mapping[str, Any], raw["task_allocation"])
        assignment_values = cast(Sequence[Mapping[str, Any]], allocation["assignments"])
        assignments = tuple(TaskAssignment.from_dict(item) for item in assignment_values)
        if tuple(item.ordinal for item in assignments) != tuple(range(len(assignments))):
            raise ValueError("task assignment ordinals must be contiguous and ordered")
        if any(item.slot not in slots for item in assignments):
            raise ValueError("task assignment names a slot outside the policy panel")
        identities = [(item.slot, item.task_id) for item in assignments]
        if len(identities) != len(set(identities)):
            raise ValueError("policy/task assignments must be unique")
        provider_value = cast(Mapping[str, Any], raw["provider"])
        provider = ProviderPlan(
            cast(ProviderAdapterName, provider_value["adapter"]),
            cast(TransportName, provider_value["transport"]),
            dict(cast(Mapping[str, Any], provider_value["config"])),
        )
        _validate_transport_pair(provider)
        budget_value = cast(Mapping[str, Any], raw["budgets"])
        budgets = BudgetPlan(
            caps=CallCaps(
                int(budget_value["environment_action_cap"]),
                int(budget_value["model_attempt_cap"]),
                int(budget_value["provider_control_request_cap"]),
                int(budget_value["provider_wire_request_cap"]),
            ),
            maximum_spend_usd=_amount(budget_value["maximum_spend_usd"], positive=True),
            per_request_theoretical_maximum_usd=_amount(
                budget_value["per_request_theoretical_maximum_usd"], positive=False
            ),
            unknown_reservation_rule=str(budget_value["unknown_reservation_rule"]),
        )
        _validate_caps(policies, assignments, budgets.caps)
        breaker_value = cast(Mapping[str, Any], raw["retry_breaker"])
        breaker = RetryBreakerPlan(
            max_bounded_retries_per_action=int(
                breaker_value["max_bounded_retries_per_action"]
            ),
            consecutive_failure_limit=int(breaker_value["consecutive_failure_limit"]),
            continue_classifications=tuple(
                cast(Sequence[str], breaker_value["continue_classifications"])
            ),
            hard_stop_classifications=tuple(
                cast(Sequence[str], breaker_value["hard_stop_classifications"])
            ),
        )
        if set(breaker.continue_classifications) & set(breaker.hard_stop_classifications):
            raise ValueError("continue and hard-stop classifications must be disjoint")
        output_value = cast(Mapping[str, Any], raw["outputs"])
        outputs = OutputPlan(
            _relative_path(output_value["directory"], "output directory"),
            _relative_path(output_value["journal"], "journal"),
            _relative_path(output_value["summary"], "summary"),
            cast(ResumeMode, output_value["resume_mode"]),
        )
        if outputs.journal.parent != PurePosixPath("."):
            raise ValueError("journal must be a filename relative to the output directory")
        if outputs.summary.parent != PurePosixPath("."):
            raise ValueError("summary must be a filename relative to the output directory")
        return cls(
            raw=raw,
            purpose=str(raw["purpose"]),
            code_revision=str(raw["code_revision"]),
            policies=policies,
            manifest_path=_relative_path(allocation["manifest_path"], "task manifest"),
            manifest_digest=str(allocation["manifest_digest"]),
            assignments=assignments,
            provider=provider,
            budgets=budgets,
            retry_breaker=breaker,
            stop_conditions=tuple(cast(Sequence[str], raw["stop_conditions"])),
            outputs=outputs,
        )


@dataclass(frozen=True)
class LegacyPlanProjection:
    canonical_plan_sha256: str
    task_assignment: tuple[tuple[Any, ...], ...]
    stop_rules: tuple[str, ...]


def load_plan(path: Path) -> CalibrationPlan:
    return CalibrationPlan.from_dict(_load_json_object(path))


def legacy_plan_projection(value: Mapping[str, Any]) -> LegacyPlanProjection:
    """Project frozen D5.6 schemas without rewriting or reinterpreting their bytes."""

    task = value.get("task")
    if isinstance(task, dict):
        records: Sequence[Mapping[str, Any]] = [task]
        limit_key = "action_limit"
    else:
        raw_records = value.get("task_order")
        if not isinstance(raw_records, list) or not raw_records:
            raise ValueError("legacy plan has no recognized task assignment")
        records = cast(Sequence[Mapping[str, Any]], raw_records)
        limit_key = "max_episode_steps"
    raw_rules = value.get("stop_conditions", value.get("stop_rules"))
    if not isinstance(raw_rules, list) or not all(isinstance(rule, str) for rule in raw_rules):
        raise ValueError("legacy plan has no recognized stop rules")
    return LegacyPlanProjection(
        canonical_plan_sha256=content_digest(dict(value)),
        task_assignment=tuple(
            (
                record["seed"],
                record["task_id"],
                record[limit_key],
                record["family"],
            )
            for record in records
        ),
        stop_rules=tuple(raw_rules),
    )


def legacy_summary_classifications(value: Mapping[str, Any]) -> dict[str, int]:
    raw = value.get("classifications")
    if isinstance(raw, dict):
        return {str(key): int(count) for key, count in raw.items()}
    episode = value.get("episode_result")
    if not isinstance(episode, dict) or not isinstance(episode.get("classification"), str):
        raise TypeError("legacy summary has no recognized classifications")
    return {episode["classification"]: 1}


def _validate_schema(value: dict[str, Any]) -> None:
    schema = _load_json_object(_SCHEMA_PATH)
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(
        value
    )


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object: {path}")
    return value


def _amount(value: object, *, positive: bool) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("spend amounts must be finite decimals") from exc
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise ValueError("spend amounts must be finite and within the declared range")
    return amount


def _relative_path(value: object, field: str) -> PurePosixPath:
    path = PurePosixPath(str(value))
    if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        raise ValueError(f"{field} must be a non-empty repository-relative path")
    return path


def _validate_transport_pair(provider: ProviderPlan) -> None:
    expected: dict[ProviderAdapterName, TransportName] = {
        "openrouter_http": "http",
        "codex_cli": "cli_subprocess",
        "claude_cli": "cli_subprocess",
        "deterministic_fake": "deterministic_fake",
    }
    if provider.transport != expected[provider.adapter]:
        raise ValueError("provider adapter and transport kind disagree")


def _validate_caps(
    policies: Sequence[PolicySlot],
    assignments: Sequence[TaskAssignment],
    caps: CallCaps,
) -> None:
    by_slot = {policy.slot: policy.policy_manifest for policy in policies}
    environment = sum(item.action_limit for item in assignments)
    attempts = 0
    controls = 0
    for item in assignments:
        manifest = by_slot[item.slot]
        model_attempts = manifest.get("max_model_attempts_per_action")
        cancellations = manifest.get("max_cancellation_requests_per_attempt")
        reconciliations = manifest.get("max_reconciliation_requests_per_attempt")
        if any(type(value) is not int or value < 0 for value in (model_attempts, cancellations, reconciliations)):
            raise ValueError(f"policy slot {item.slot!r} has invalid call-cap fields")
        assert isinstance(model_attempts, int)
        assert isinstance(cancellations, int)
        assert isinstance(reconciliations, int)
        if model_attempts == 0:
            raise ValueError(f"policy slot {item.slot!r} must allow a model attempt")
        assignment_attempts = item.action_limit * model_attempts
        attempts += assignment_attempts
        controls += assignment_attempts * (cancellations + reconciliations)
    expected = CallCaps(environment, attempts, controls, attempts + controls)
    if caps != expected:
        raise ValueError(f"declared call caps differ from assignment maximum: {expected.to_dict()}")
