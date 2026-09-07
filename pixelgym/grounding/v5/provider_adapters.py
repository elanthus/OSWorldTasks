"""Narrow typed provider adapters for the manifest-driven calibration runner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any

from pixelgym.grounding.v5.calibration_runner import SpendSnapshot
from pixelgym.grounding.v5.claude_code_policy import (
    ClaudeCodePolicy,
    ClaudeCodeTransport,
    ClaudeInvocationJournal,
    build_claude_policy_manifest,
    probe_claude_runtime,
)
from pixelgym.grounding.v5.codex_cli_policy import (
    CODEX_POLICY_BY_SLOT,
    CodexCliInvocationJournal,
    CodexCliPolicy,
    CodexCliTransport,
    SubscriptionExemptLedger,
    build_codex_cli_policy_manifest,
    probe_codex_runtime,
)
from pixelgym.grounding.v5.contracts import CallCaps, PolicyManifest, sha256_bytes
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import (
    GEMINI_STATEFUL,
    GEMINI_STATEFUL_FULL_CALIBRATION,
    GEMINI_STATEFUL_ONE_CALL_SMOKE,
    GLM_STATEFUL_CANDIDATE,
    GLM_STATEFUL_JSON_OBJECT_SMOKE_CANDIDATE,
    GLM_STATEFUL_RELAXED_SCHEMA_CANDIDATE,
    LLAMA_STATEFUL,
    QWEN_STATEFUL,
    QWEN_STATEFUL_RETRY_SUCCESSOR,
    QWEN_STATELESS,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    PanelPolicyConfig,
    SpendLedger,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.plan import CalibrationPlan, TaskAssignment
from pixelgym.grounding.v5.runner import EpisodeResult, StatefulPolicyPackage, V5Runner
from pixelgym.grounding.v5.runner import ProviderTransport as EpisodeTransport

_OPENROUTER_CONFIGS = {
    config.slot: config
    for config in (
        GEMINI_STATEFUL,
        GEMINI_STATEFUL_ONE_CALL_SMOKE,
        GEMINI_STATEFUL_FULL_CALIBRATION,
        QWEN_STATEFUL,
        QWEN_STATEFUL_RETRY_SUCCESSOR,
        LLAMA_STATEFUL,
        GLM_STATEFUL_CANDIDATE,
        GLM_STATEFUL_RELAXED_SCHEMA_CANDIDATE,
        GLM_STATEFUL_JSON_OBJECT_SMOKE_CANDIDATE,
        QWEN_STATELESS,
    )
}


@dataclass(frozen=True)
class CliAdapterSettings:
    invocation_journal: PurePosixPath
    prior_budget_accounted_spend_usd: Decimal
    expected_resolved_model: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, claude: bool) -> CliAdapterSettings:
        allowed = {
            "invocation_journal",
            "prior_budget_accounted_spend_usd",
            *(('expected_resolved_model',) if claude else ()),
        }
        unexpected = set(value) - allowed
        if unexpected:
            raise ValueError(f"unsupported CLI adapter setting: {min(unexpected)}")
        path = _filename(value.get("invocation_journal"), "invocation journal")
        prior = _amount(value.get("prior_budget_accounted_spend_usd", "0"))
        resolved = value.get("expected_resolved_model")
        if resolved is not None and (not claude or not isinstance(resolved, str) or not resolved):
            raise ValueError("expected_resolved_model is invalid for this CLI adapter")
        return cls(path, prior, resolved)


class _BaseAdapter:
    def __init__(self, repository_root: Path, plan: CalibrationPlan) -> None:
        self.repository_root = repository_root
        self.plan = plan
        self._policy_records = {policy.slot: policy for policy in plan.policies}
        self._records: list[dict[str, Any]] = []
        self._closed = False

    def execute(
        self,
        assignment: TaskAssignment,
        *,
        journal: V5AttemptJournal,
        approved_caps: CallCaps,
    ) -> EpisodeResult:
        manifest = self._manifest(assignment.slot)
        expected = self._policy_records[assignment.slot]
        if manifest.to_dict() != expected.policy_manifest:
            raise ValueError(f"runtime policy manifest differs for slot {assignment.slot!r}")
        task = generate_task(assignment.seed)
        if (
            task.task_id != assignment.task_id
            or task.family.value != assignment.family
            or assignment.action_limit > task.max_episode_steps
        ):
            raise ValueError("generated task differs from approved assignment")
        return V5Runner(
            journal=journal,
            manifest=manifest,
            transport=self._transport(assignment.slot, journal),
            policy=self._policy(assignment.slot),
            approved_caps=approved_caps,
        ).run(
            trial_id=_trial_id(assignment),
            task=task,
            action_limit=assignment.action_limit,
        )

    def reconcile(
        self,
        assignment: TaskAssignment,
        *,
        journal: V5AttemptJournal,
        approved_caps: CallCaps,
    ) -> EpisodeResult:
        del approved_caps
        self._bind_resume_journal(journal)
        self._records.append(
            {
                "slot": assignment.slot,
                "task_id": assignment.task_id,
                "status": "infrastructure_failure",
                "resume_disposition": "incomplete_assignment_not_replayed",
            }
        )
        return EpisodeResult(
            trial_id=_trial_id(assignment),
            task_id=assignment.task_id,
            success=False,
            classification="infrastructure_failure",
            environment_actions=0,
            model_attempts=0,
            provider_control_requests=0,
            provider_wire_requests=0,
            final_policy_checkpoint_digest="sha256:" + sha256_bytes(b""),
        )

    def transport_records(self) -> Sequence[Mapping[str, Any]]:
        return tuple(self._records)

    def _manifest(self, slot: str) -> PolicyManifest:
        raise NotImplementedError

    def _transport(self, slot: str, journal: V5AttemptJournal) -> EpisodeTransport:
        raise NotImplementedError

    def _policy(self, slot: str) -> StatefulPolicyPackage:
        raise NotImplementedError

    def _bind_resume_journal(self, journal: V5AttemptJournal) -> None:
        del journal


class OpenRouterHttpAdapter(_BaseAdapter):
    adapter_name = "openrouter_http"
    transport_name = "http"

    def __init__(self, repository_root: Path, plan: CalibrationPlan) -> None:
        super().__init__(repository_root, plan)
        if plan.provider.config:
            raise ValueError("OpenRouter adapter derives all routing from policy manifests")
        unknown_slots = set(self._policy_records) - set(_OPENROUTER_CONFIGS)
        if unknown_slots:
            raise ValueError(f"unsupported OpenRouter policy slot: {min(unknown_slots)}")
        self.ledger = SpendLedger(plan.budgets.maximum_spend_usd, Decimal(0))
        self._transports: dict[str, OpenRouterPanelTransport] = {}

    def _config(self, slot: str) -> PanelPolicyConfig:
        return _OPENROUTER_CONFIGS[slot]

    def _manifest(self, slot: str) -> PolicyManifest:
        return build_panel_policy_manifest(
            self.repository_root,
            config=self._config(slot),
            code_revision=self.plan.code_revision,
        )

    def _transport(self, slot: str, journal: V5AttemptJournal) -> OpenRouterPanelTransport:
        transport = self._transports.get(slot)
        if transport is None:
            transport = OpenRouterPanelTransport(self._config(slot), ledger=self.ledger)
            self._transports[slot] = transport
        transport.bind_spend_journal(journal)
        return transport

    def _policy(self, slot: str) -> OpenRouterPanelPolicy:
        return OpenRouterPanelPolicy(self._config(slot))

    def _bind_resume_journal(self, journal: V5AttemptJournal) -> None:
        self.ledger.bind_journal(journal)

    def spend_snapshot(self) -> SpendSnapshot:
        return SpendSnapshot(
            known_spend_usd=self.ledger.spent_usd,
            unknown_reservation_usd=(
                self.ledger.unknown_reservation_usd
                + self.ledger.in_flight_reservation_usd
            ),
            budget_accounted_spend_usd=self.ledger.budget_accounted_spend_usd,
            blocked=self.ledger.blocked,
        )

    def transport_records(self) -> Sequence[Mapping[str, Any]]:
        provider = [record for transport in self._transports.values() for record in transport.records]
        return (*provider, *super().transport_records())

    def close(self) -> None:
        self._closed = True


class CodexCliAdapter(_BaseAdapter):
    adapter_name = "codex_cli"
    transport_name = "cli_subprocess"

    def __init__(self, repository_root: Path, plan: CalibrationPlan) -> None:
        super().__init__(repository_root, plan)
        self.settings = CliAdapterSettings.from_mapping(plan.provider.config, claude=False)
        unknown_slots = set(self._policy_records) - set(CODEX_POLICY_BY_SLOT)
        if unknown_slots:
            raise ValueError(f"unsupported Codex policy slot: {min(unknown_slots)}")
        self.runtime_identities = {
            slot: probe_codex_runtime(CODEX_POLICY_BY_SLOT[slot]) for slot in self._policy_records
        }
        self.ledger = SubscriptionExemptLedger(
            plan.budgets.maximum_spend_usd,
            self.settings.prior_budget_accounted_spend_usd,
        )
        self._invocation_journal: CodexCliInvocationJournal | None = None
        self._transports: dict[str, CodexCliTransport] = {}

    def _manifest(self, slot: str) -> PolicyManifest:
        config = CODEX_POLICY_BY_SLOT[slot]
        return build_codex_cli_policy_manifest(
            self.repository_root,
            runtime_identity=self.runtime_identities[slot],
            config=config,
            code_revision=self.plan.code_revision,
        )

    def _journal(self, slot: str) -> CodexCliInvocationJournal:
        config = CODEX_POLICY_BY_SLOT[slot]
        if self._invocation_journal is None:
            self._invocation_journal = CodexCliInvocationJournal(
                self.repository_root / self.plan.outputs.directory / self.settings.invocation_journal,
                config,
            )
        elif self._invocation_journal.config != config:
            raise ValueError("one Codex invocation journal cannot mix policy contracts")
        return self._invocation_journal

    def _transport(self, slot: str, journal: V5AttemptJournal) -> CodexCliTransport:
        del journal
        transport = self._transports.get(slot)
        if transport is None:
            transport = CodexCliTransport(
                ledger=self.ledger,
                invocation_journal=self._journal(slot),
                runtime_identity=self.runtime_identities[slot],
                config=CODEX_POLICY_BY_SLOT[slot],
            )
            self._transports[slot] = transport
        return transport

    def _policy(self, slot: str) -> CodexCliPolicy:
        return CodexCliPolicy(CODEX_POLICY_BY_SLOT[slot])

    def spend_snapshot(self) -> SpendSnapshot:
        return SpendSnapshot(
            known_spend_usd=self.ledger.budget_accounted_usd,
            unknown_reservation_usd=Decimal(0),
            budget_accounted_spend_usd=self.ledger.budget_accounted_usd,
            blocked=self.ledger.blocked,
        )

    def transport_records(self) -> Sequence[Mapping[str, Any]]:
        provider = [record for transport in self._transports.values() for record in transport.records]
        return (*provider, *super().transport_records())

    def close(self) -> None:
        for transport in self._transports.values():
            transport.close()
        if self._invocation_journal is not None:
            self._invocation_journal.close()
        self._closed = True


class ClaudeCliAdapter(_BaseAdapter):
    adapter_name = "claude_cli"
    transport_name = "cli_subprocess"

    def __init__(self, repository_root: Path, plan: CalibrationPlan) -> None:
        super().__init__(repository_root, plan)
        self.settings = CliAdapterSettings.from_mapping(plan.provider.config, claude=True)
        if len(self._policy_records) != 1:
            raise ValueError("Claude adapter currently supports one explicit policy slot")
        self.runtime_identity = probe_claude_runtime()
        self.ledger = SubscriptionExemptLedger(
            plan.budgets.maximum_spend_usd,
            self.settings.prior_budget_accounted_spend_usd,
        )
        self._invocation_journal: ClaudeInvocationJournal | None = None
        self._transport_value: ClaudeCodeTransport | None = None

    def _manifest(self, slot: str) -> PolicyManifest:
        if slot not in self._policy_records:
            raise ValueError("Claude assignment names an unknown policy slot")
        return build_claude_policy_manifest(
            self.repository_root,
            runtime_identity=self.runtime_identity,
            code_revision=self.plan.code_revision,
            resolved_model=self.settings.expected_resolved_model,
        )

    def _transport(self, slot: str, journal: V5AttemptJournal) -> ClaudeCodeTransport:
        del slot, journal
        if self._invocation_journal is None:
            self._invocation_journal = ClaudeInvocationJournal(
                self.repository_root / self.plan.outputs.directory / self.settings.invocation_journal
            )
        if self._transport_value is None:
            self._transport_value = ClaudeCodeTransport(
                ledger=self.ledger,
                invocation_journal=self._invocation_journal,
                runtime_identity=self.runtime_identity,
                expected_resolved_model=self.settings.expected_resolved_model,
            )
        return self._transport_value

    def _policy(self, slot: str) -> ClaudeCodePolicy:
        del slot
        return ClaudeCodePolicy()

    def spend_snapshot(self) -> SpendSnapshot:
        return SpendSnapshot(
            known_spend_usd=self.ledger.budget_accounted_usd,
            unknown_reservation_usd=Decimal(0),
            budget_accounted_spend_usd=self.ledger.budget_accounted_usd,
            blocked=self.ledger.blocked,
        )

    def transport_records(self) -> Sequence[Mapping[str, Any]]:
        provider = [] if self._transport_value is None else self._transport_value.records
        return (*provider, *super().transport_records())

    def close(self) -> None:
        if self._transport_value is not None:
            self._transport_value.close()
        if self._invocation_journal is not None:
            self._invocation_journal.close()
        self._closed = True


def build_provider_adapter(repository_root: Path, plan: CalibrationPlan) -> _BaseAdapter:
    if plan.provider.adapter == "openrouter_http":
        return OpenRouterHttpAdapter(repository_root, plan)
    if plan.provider.adapter == "codex_cli":
        return CodexCliAdapter(repository_root, plan)
    if plan.provider.adapter == "claude_cli":
        return ClaudeCliAdapter(repository_root, plan)
    raise ValueError("deterministic_fake adapters are test-injected and cannot be built by the CLI")


def _amount(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("CLI prior spend must be a finite non-negative decimal") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("CLI prior spend must be a finite non-negative decimal")
    return amount


def _filename(value: object, field: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be one output-relative filename")
    path = PurePosixPath(str(value))
    if path.is_absolute() or len(path.parts) != 1 or str(path) in {"", "."}:
        raise ValueError(f"{field} must be one output-relative filename")
    return path


def _trial_id(assignment: TaskAssignment) -> str:
    return f"manifest-{assignment.slot}-{assignment.ordinal:04d}-{assignment.task_id}"
