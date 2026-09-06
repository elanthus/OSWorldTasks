"""Approval-bound Luna/Terra medium smoke and frozen D5.6 calibration runs."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.codex_cli_policy import (
    ACTION_SCHEMA,
    ALLOWED_DISABLED_CODE_MODE_DIAGNOSTIC_SHA256,
    AUTH_MODE,
    CODEX_CLI_VERSION,
    CODEX_POLICY_BY_SLOT,
    KILL_GRACE_SECONDS,
    LUNA_EXPERIMENT_CHARGE_USD,
    PROCESS_TIMEOUT_SECONDS,
    PROVIDER_IDENTITY,
    REQUEST_MAXIMUM_INFORMATIONAL_LIST_PRICE_EQUIVALENT_USD,
    ROLLOUT_BUDGET_TOKENS,
    RUNNER_REQUEST_DEADLINE_SECONDS,
    TERMINATE_GRACE_SECONDS,
    CodexCliInvocationJournal,
    CodexCliPolicy,
    CodexCliTransport,
    CodexPolicyConfig,
    CodexRuntimeIdentity,
    RunningProcess,
    SubscriptionExemptLedger,
    build_codex_cli_policy_manifest,
    command_contract_digest,
    probe_codex_runtime,
    sanitized_command_contract,
)
from pixelgym.grounding.v5.contracts import CallCaps, Partition, content_digest
from pixelgym.grounding.v5.d56_calibration import (
    CURRENT_CALIBRATION_MANIFEST,
    EXPECTED_TASK_COUNT,
    NORMAL_TERMINAL_CLASSIFICATIONS,
    _current_calibration_manifest,
)
from pixelgym.grounding.v5.d56_codex_cli_calibration import (
    MAXIMUM_AGGREGATE_SPEND_USD,
    MAXIMUM_REMAINING_INCREMENTAL_EXPOSURE_USD,
    PRIOR_BUDGET_ACCOUNTED_SPEND_USD,
    _assert_plan_publishable,
    _file_digest,
    _git,
    _streaming_file_digest,
    validated_failed_luna_smoke_evidence,
    validated_predecessor_evidence,
    validated_retry_predecessor_evidence,
    validated_third_smoke_evidence,
)
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.runner import V5Runner
from pixelgym.serialization import canonical_json_bytes

SMOKE_PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-codex-subscription-smoke-plan-v1"
SMOKE_RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-codex-subscription-smoke-result-v1"
FULL_PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-codex-subscription-full-plan-v1"
FULL_RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-codex-subscription-full-result-v1"
SMOKE_SEED = 5002
SMOKE_ACTION_LIMIT = 1
AUTHORIZED_POLICY_SLOTS = frozenset({"luna-medium", "terra-medium"})
SUPERSEDED_LUNA_LOW_V4_PLAN_PATH = Path(
    "artifacts/grounding-v5-d56-codex-cli-luna-one-call-smoke-plan-v4.json"
)
SUPERSEDED_LUNA_LOW_V4_PLAN_CONTENT_SHA256 = (
    "sha256:e8e584b40207103f6e53548171e76b194d575aa196ccbc4a2ce7bd48098ebe4c"
)
SUPERSEDED_LUNA_LOW_V4_PLAN_FILE_SHA256 = (
    "sha256:81c9f8521f895f258a5d29de7e4ba378834553f04c96a66b6b3070c52d295531"
)


def policy_for_slot(slot: str) -> CodexPolicyConfig:
    if slot not in AUTHORIZED_POLICY_SLOTS:
        raise ValueError("policy slot is outside the unattended campaign authorization")
    return CODEX_POLICY_BY_SLOT[slot]


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path.name}")
    return value


def _superseded_luna_low_v4_plan(repository_root: Path) -> dict[str, Any]:
    path = repository_root / SUPERSEDED_LUNA_LOW_V4_PLAN_PATH
    if _file_digest(path) != SUPERSEDED_LUNA_LOW_V4_PLAN_FILE_SHA256:
        raise ValueError("superseded Luna-low v4 plan file digest mismatch")
    plan = _load_json_object(path)
    if content_digest(plan) != SUPERSEDED_LUNA_LOW_V4_PLAN_CONTENT_SHA256:
        raise ValueError("superseded Luna-low v4 canonical plan digest mismatch")
    if (repository_root / "artifacts/grounding-v5-d56-codex-cli-luna-one-call-smoke-run-v4").exists():
        raise ValueError("superseded Luna-low v4 plan unexpectedly has a run directory")
    return {
        "status": "superseded_unconsumed_no_provider_calls",
        "plan_content_sha256": SUPERSEDED_LUNA_LOW_V4_PLAN_CONTENT_SHA256,
        "plan_file_sha256": SUPERSEDED_LUNA_LOW_V4_PLAN_FILE_SHA256,
        "reason": "owner_requested_medium_reasoning_campaign_before_execution",
        "reuse_rule": "do_not_execute_or_reinterpret",
    }


def _historical_evidence(repository_root: Path) -> dict[str, Any]:
    return {
        "qwen_predecessor": validated_predecessor_evidence(repository_root),
        "luna_low_smoke_v1": validated_failed_luna_smoke_evidence(repository_root),
        "luna_low_smoke_v2": validated_retry_predecessor_evidence(repository_root),
        "luna_low_smoke_v3": validated_third_smoke_evidence(repository_root),
        "luna_low_smoke_v4": _superseded_luna_low_v4_plan(repository_root),
    }


def _human_approval_scope() -> dict[str, Any]:
    return {
        "status": "advance_human_authorization_recorded",
        "authorized_at_local_date": "2026-08-30",
        "scope": [
            "one_smoke_for_luna_medium",
            "one_smoke_for_terra_medium",
            "one_full_frozen_run_per_policy_only_after_its_smoke_succeeds",
        ],
        "aggregate_smoke_process_cap": 3,
        "implemented_smoke_allocation": "one_per_requested_policy_no_retries",
        "sonnet_status": "separate_policy_blocked_until_claude_cli_authentication_exists",
        "human_gates_unchanged": True,
    }


def _pricing_record() -> dict[str, Any]:
    return {
        "authentication_mode": AUTH_MODE,
        "billing_classification": "ChatGPT_subscription_billed",
        "accounting_method": "codex_chatgpt_subscription_experiment_charge_zero_v1",
        "experiment_charge_per_call_usd": str(LUNA_EXPERIMENT_CHARGE_USD),
        "prior_non_subscription_budget_accounted_spend_usd": str(
            PRIOR_BUDGET_ACCOUNTED_SPEND_USD
        ),
        "maximum_remaining_non_subscription_exposure_usd": str(
            MAXIMUM_REMAINING_INCREMENTAL_EXPOSURE_USD
        ),
        "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
        "usage_telemetry": "optional_non_billing_diagnostic",
        "missing_usage_rule": "record_nonblocking_for_subscription_authenticated_calls",
        "service_subscription_limits": "unchanged_and_not_programmatically_observable",
    }


def _raw_evidence_policy() -> dict[str, Any]:
    return {
        "raw_cli_jsonl": "restricted_local_invocation_journal_only",
        "raw_cli_stderr": "restricted_local_invocation_journal_only",
        "screenshots": "restricted_local_attempt_journal_only",
        "provider_response_content": "restricted_local_journals_only",
        "publication": "response_content_free_derivatives_only_after_integrity_audit",
    }


def _policy_record(
    repository_root: Path,
    *,
    config: CodexPolicyConfig,
    revision: str,
    runtime_identity: CodexRuntimeIdentity,
) -> dict[str, Any]:
    manifest = build_codex_cli_policy_manifest(
        repository_root,
        code_revision=revision,
        runtime_identity=runtime_identity,
        config=config,
    )
    return {
        "slot": config.slot,
        "evaluated_provider": "Codex CLI",
        "provider_identity": PROVIDER_IDENTITY,
        "provider_runtime": "non-interactive codex exec",
        "model": config.model,
        "model_reasoning_effort": config.model_reasoning_effort,
        "orchestrator_model_excluded": "gpt-5.6-sol",
        "runtime_identity": runtime_identity.to_dict(),
        "policy_manifest": manifest.to_dict(),
        "policy_manifest_digest": content_digest(manifest.to_dict()),
        "command_contract": list(sanitized_command_contract(config)),
        "command_contract_digest": command_contract_digest(config),
        "response_schema_digest": content_digest(ACTION_SCHEMA),
        "observation_contract": "task_instruction_plus_current_screenshot_only",
        "isolation": {
            "working_directory": "isolated_empty_temporary_directory",
            "session_persistence": "ephemeral",
            "sandbox": "read-only",
            "shell_web_plugins_mcp_skills_connectors": "disabled_or_unavailable",
            "user_and_project_rules": "ignored",
        },
        "jsonl_diagnostic_policy": {
            "allowed_item_type": "error",
            "allowed_message_sha256": ALLOWED_DISABLED_CODE_MODE_DIAGNOSTIC_SHA256,
            "allowed_occurrence_cap": 1,
            "all_other_error_items": "fail_closed_policy_violation",
        },
        "limits": {
            "context_window_tokens": config.model_context_window_tokens,
            "rollout_budget_tokens": ROLLOUT_BUDGET_TOKENS,
            "process_timeout_seconds": PROCESS_TIMEOUT_SECONDS,
            "runner_request_deadline_seconds": RUNNER_REQUEST_DEADLINE_SECONDS,
            "termination_grace_seconds": TERMINATE_GRACE_SECONDS,
            "kill_grace_seconds": KILL_GRACE_SECONDS,
            "runner_cli_request_and_stream_retries": 0,
        },
    }


def _task_order(repository_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    partition = _current_calibration_manifest(repository_root)
    records = [
        {
            "ordinal": ordinal,
            "seed": record["seed_record"]["seed"],
            "task_id": record["task_id"],
            "family": record["seed_record"]["family"],
            "variant": record["seed_record"]["variant"],
            "max_episode_steps": record["max_episode_steps"],
        }
        for ordinal, record in enumerate(partition["records"])
    ]
    if len(records) != EXPECTED_TASK_COUNT:
        raise ValueError("frozen calibration task count differs from the contract")
    return records, partition


def plan_digest(plan: dict[str, Any]) -> str:
    return content_digest(plan)


def plan_file_bytes(plan: dict[str, Any]) -> bytes:
    return (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_smoke_plan(
    repository_root: Path,
    *,
    config: CodexPolicyConfig,
    runtime_identity: CodexRuntimeIdentity | None = None,
) -> dict[str, Any]:
    config = policy_for_slot(config.slot)
    runtime = runtime_identity or probe_codex_runtime(config)
    revision = _git(repository_root, "rev-parse", "HEAD")
    task = generate_task(SMOKE_SEED)
    if task.seed_record.partition is not Partition.DEVELOPMENT:
        raise ValueError("smoke task must be from the development partition")
    plan = {
        "schema_version": SMOKE_PLAN_SCHEMA_VERSION,
        "purpose": f"one development smoke action for {config.slot}; not calibration evidence",
        "execution_state": "advance_human_authorized",
        "provider_calls_made_while_planning": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "human_approval_scope": _human_approval_scope(),
        "policy": _policy_record(
            repository_root,
            config=config,
            revision=revision,
            runtime_identity=runtime,
        ),
        "task": {
            "partition": task.seed_record.partition.value,
            "seed": task.seed,
            "task_id": task.task_id,
            "family": task.seed_record.family.value,
            "variant": task.seed_record.variant,
            "action_limit": SMOKE_ACTION_LIMIT,
        },
        "caps": {
            "assigned_task_cap": 1,
            "environment_action_cap": 1,
            "codex_process_invocation_cap": 1,
            "model_attempt_cap": 1,
            "provider_control_request_cap": 0,
            "provider_wire_request_cap": 1,
            "experiment_charge_per_call_usd": str(LUNA_EXPERIMENT_CHARGE_USD),
            "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
        },
        "price_and_cost_accounting": _pricing_record(),
        "historical_evidence": _historical_evidence(repository_root),
        "raw_evidence_policy": _raw_evidence_policy(),
        "stop_conditions": [
            "send exactly one Codex CLI process invocation and never retry it",
            "stop after the first valid environment action",
            "stop without dispatch on invalid or unparseable action output",
            "stop on any unmatched error, tool, web, filesystem, plugin, MCP, skill, connector, or subagent event",
            "terminate the process group on timeout or interruption",
            "stop on runtime identity, command contract, auth mode, or evidence mismatch",
        ],
        "human_gates": {
            "D4.12": "not_evaluated_human_owned",
            "D5.10": "not_evaluated_human_owned",
        },
    }
    _assert_plan_publishable(plan, repository_root)
    return plan


def _validated_successful_smoke_evidence(
    value: dict[str, Any], *, config: CodexPolicyConfig
) -> dict[str, Any]:
    required = {
        "approved_smoke_plan_sha256",
        "summary_file_sha256",
        "attempt_journal_file_sha256",
        "invocation_journal_file_sha256",
        "task_id",
        "classification",
        "provider_calls_made",
        "environment_actions",
        "model",
        "model_reasoning_effort",
        "cli_version",
        "policy_violation",
        "incremental_experiment_charge_usd",
        "budget_accounted_aggregate_spend_usd",
        "usage_telemetry_status",
    }
    if set(value) != required:
        raise ValueError("successful smoke evidence fields differ from the frozen contract")
    expected = {
        "classification": "pilot_action_limit",
        "provider_calls_made": 1,
        "environment_actions": 1,
        "model": config.model,
        "model_reasoning_effort": config.model_reasoning_effort,
        "cli_version": CODEX_CLI_VERSION,
        "policy_violation": "none",
        "incremental_experiment_charge_usd": "0.00",
        "budget_accounted_aggregate_spend_usd": str(PRIOR_BUDGET_ACCOUNTED_SPEND_USD),
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise ValueError("smoke evidence does not record one successful bounded policy action")
    for key in (
        "approved_smoke_plan_sha256",
        "summary_file_sha256",
        "attempt_journal_file_sha256",
        "invocation_journal_file_sha256",
    ):
        if not isinstance(value[key], str) or not value[key].startswith("sha256:"):
            raise ValueError("smoke evidence digest is invalid")
    if value["usage_telemetry_status"] not in {
        "available",
        "unavailable",
        "invalid_or_ambiguous",
    }:
        raise ValueError("smoke usage telemetry state is invalid")
    return dict(value)


def successful_smoke_evidence_from_files(
    output_directory: Path, *, config: CodexPolicyConfig
) -> dict[str, Any]:
    summary_path = output_directory / "summary.json"
    attempt_path = output_directory / "attempts.sqlite"
    invocation_path = output_directory / "codex-cli-invocations.sqlite"
    summary = _load_json_object(summary_path)
    episode = summary.get("episode_result")
    if not isinstance(episode, Mapping):
        raise TypeError("smoke summary has no episode result")
    return _validated_successful_smoke_evidence(
        {
            "approved_smoke_plan_sha256": summary.get("approved_plan_sha256"),
            "summary_file_sha256": _file_digest(summary_path),
            "attempt_journal_file_sha256": _streaming_file_digest(attempt_path),
            "invocation_journal_file_sha256": _streaming_file_digest(invocation_path),
            "task_id": summary.get("task_id"),
            "classification": episode.get("classification"),
            "provider_calls_made": summary.get("provider_calls_made"),
            "environment_actions": episode.get("environment_actions"),
            "model": summary.get("model"),
            "model_reasoning_effort": summary.get("model_reasoning_effort"),
            "cli_version": summary.get("cli_version"),
            "policy_violation": summary.get("policy_violation"),
            "incremental_experiment_charge_usd": summary.get(
                "incremental_experiment_charge_usd"
            ),
            "budget_accounted_aggregate_spend_usd": summary.get(
                "budget_accounted_aggregate_spend_usd"
            ),
            "usage_telemetry_status": summary.get("usage_telemetry_status"),
        },
        config=config,
    )


def build_full_plan(
    repository_root: Path,
    *,
    config: CodexPolicyConfig,
    successful_smoke_evidence: dict[str, Any],
    runtime_identity: CodexRuntimeIdentity | None = None,
) -> dict[str, Any]:
    config = policy_for_slot(config.slot)
    smoke = _validated_successful_smoke_evidence(successful_smoke_evidence, config=config)
    runtime = runtime_identity or probe_codex_runtime(config)
    revision = _git(repository_root, "rev-parse", "HEAD")
    task_order, partition = _task_order(repository_root)
    action_cap = sum(record["max_episode_steps"] for record in task_order)
    plan = {
        "schema_version": FULL_PLAN_SCHEMA_VERSION,
        "purpose": f"one full frozen fifty-task D5.6 calibration for {config.slot}",
        "execution_state": "advance_human_authorized_after_successful_smoke",
        "provider_calls_made_while_planning": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "human_approval_scope": _human_approval_scope(),
        "policy": _policy_record(
            repository_root,
            config=config,
            revision=revision,
            runtime_identity=runtime,
        ),
        "calibration_partition": {
            "manifest_file_sha256": _file_digest(
                repository_root / CURRENT_CALIBRATION_MANIFEST
            ),
            "manifest_digest": partition["manifest_digest"],
            "assigned_task_count": EXPECTED_TASK_COUNT,
            "task_order_rule": "frozen_manifest_order_no_reordering_or_replacement",
        },
        "task_order": task_order,
        "caps": {
            "assigned_task_cap": EXPECTED_TASK_COUNT,
            "environment_action_cap": action_cap,
            "codex_process_invocation_cap": action_cap,
            "model_attempt_cap": action_cap,
            "provider_control_request_cap": 0,
            "provider_wire_request_cap": action_cap,
            "experiment_charge_per_call_usd": "0.00",
            "maximum_incremental_experiment_charge_usd": "0.00",
            "per_invocation_maximum_informational_list_price_equivalent_usd": str(
                REQUEST_MAXIMUM_INFORMATIONAL_LIST_PRICE_EQUIVALENT_USD
            ),
            "uncapped_run_informational_list_price_equivalent_ceiling_usd": str(
                REQUEST_MAXIMUM_INFORMATIONAL_LIST_PRICE_EQUIVALENT_USD * action_cap
            ),
            "prior_budget_accounted_spend_usd": str(PRIOR_BUDGET_ACCOUNTED_SPEND_USD),
            "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
        },
        "price_and_cost_accounting": _pricing_record(),
        "successful_smoke_evidence": smoke,
        "historical_evidence": _historical_evidence(repository_root),
        "raw_evidence_policy": _raw_evidence_policy(),
        "stop_conditions": [
            "run tasks only in frozen fifty-task order",
            "continue after success termination or full step-limit truncation",
            "stop the policy at the first other classification or evidence mismatch",
            "never retry or replay a Codex CLI process invocation",
            "do not expose confirmatory tasks",
        ],
        "human_gates": {
            "D4.12": "not_evaluated_human_owned",
            "D5.10": "not_evaluated_human_owned",
        },
    }
    _assert_plan_publishable(plan, repository_root)
    return plan


def _summary_common(
    *,
    plan: dict[str, Any],
    config: CodexPolicyConfig,
    digest: str,
    ledger: SubscriptionExemptLedger,
    attempt_integrity: dict[str, Any],
    invocation_integrity: dict[str, Any],
    call_counts: tuple[int, int],
    transport_records: list[dict[str, Any]],
    execution_error: dict[str, str] | None,
) -> dict[str, Any]:
    return {
        "approved_plan_sha256": digest,
        "code_revision": plan["code_revision"],
        "provider": "Codex CLI",
        "provider_identity": PROVIDER_IDENTITY,
        "model": config.model,
        "model_reasoning_effort": config.model_reasoning_effort,
        "cli_version": CODEX_CLI_VERSION,
        "authentication_mode": AUTH_MODE,
        "provider_calls_made": ledger.processes_started,
        "provider_wire_requests": ledger.processes_started,
        "model_attempt_reservations": call_counts[0],
        "provider_control_requests": call_counts[1],
        "incremental_experiment_charge_usd": str(ledger.incremental_experiment_charge_usd),
        "informational_list_price_equivalent_usd": str(
            ledger.incremental_informational_list_price_equivalent_usd
        ),
        "budget_accounted_aggregate_spend_usd": str(ledger.budget_accounted_usd),
        "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
        "unresolved_invocation_count": len(ledger.unresolved),
        "usage_telemetry_unavailable_count": len(ledger.usage_telemetry_unavailable),
        "transport_records": transport_records,
        "attempt_journal_integrity": attempt_integrity,
        "invocation_journal_integrity": invocation_integrity,
        "execution_error": execution_error,
        "human_approval_scope": plan["human_approval_scope"],
        "publication_status": "restricted_raw_evidence_local_only",
        "cleanup": {
            "attempt_journal_closed": True,
            "invocation_journal_closed": True,
            "policy_and_environments_closed": True,
            "subprocesses_closed": True,
            "temporary_inputs_removed": True,
        },
        "human_gates": {
            "D4.12": "not_evaluated_human_owned",
            "D5.10": "not_evaluated_human_owned",
        },
    }


def execute_smoke(
    repository_root: Path,
    *,
    config: CodexPolicyConfig,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    output_directory: Path,
    runtime_identity: CodexRuntimeIdentity | None = None,
    process_factory: Callable[..., RunningProcess] | None = None,
) -> dict[str, Any]:
    config = policy_for_slot(config.slot)
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved Codex subscription smoke digest differs from the plan")
    runtime = runtime_identity or probe_codex_runtime(config)
    if plan != build_smoke_plan(repository_root, config=config, runtime_identity=runtime):
        raise ValueError("Codex subscription smoke plan is not canonical")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before provider invocation")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace smoke output: {output_directory}")
    output_directory.mkdir(parents=True)
    attempt_journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    invocation_journal = CodexCliInvocationJournal(
        output_directory / "codex-cli-invocations.sqlite",
        config,
    )
    ledger = SubscriptionExemptLedger(
        MAXIMUM_AGGREGATE_SPEND_USD,
        PRIOR_BUDGET_ACCOUNTED_SPEND_USD,
    )
    transport: CodexCliTransport | None = None
    episode_result: dict[str, Any] | None = None
    execution_error: dict[str, str] | None = None
    summary: dict[str, Any]
    try:
        manifest = build_codex_cli_policy_manifest(
            repository_root,
            code_revision=plan["code_revision"],
            runtime_identity=runtime,
            config=config,
        )
        if manifest.to_dict() != plan["policy"]["policy_manifest"]:
            raise ValueError("runtime policy manifest differs from the smoke plan")
        task = generate_task(int(plan["task"]["seed"]))
        transport_kwargs: dict[str, Any] = {
            "ledger": ledger,
            "invocation_journal": invocation_journal,
            "runtime_identity": runtime,
            "config": config,
        }
        if process_factory is not None:
            transport_kwargs["process_factory"] = process_factory
        transport = CodexCliTransport(**transport_kwargs)
        result = V5Runner(
            journal=attempt_journal,
            manifest=manifest,
            transport=transport,
            policy=CodexCliPolicy(config),
            approved_caps=CallCaps(1, 1, 0, 1),
        ).run(
            trial_id=f"d56-{config.slot}-smoke-{task.task_id}",
            task=task,
            action_limit=SMOKE_ACTION_LIMIT,
        )
        episode_result = result.to_dict()
    except BaseException as exc:
        execution_error = {"type": type(exc).__name__}
        raise
    finally:
        if transport is not None:
            transport.close()
        attempt_integrity = attempt_journal.integrity_report()
        invocation_integrity = invocation_journal.integrity_report()
        call_counts = attempt_journal.call_counts()
        transport_records = [] if transport is None else list(transport.records)
        attempt_journal.close()
        invocation_journal.close()
        summary = {
            "schema_version": SMOKE_RESULT_SCHEMA_VERSION,
            "purpose": plan["purpose"],
            **_summary_common(
                plan=plan,
                config=config,
                digest=digest,
                ledger=ledger,
                attempt_integrity=attempt_integrity,
                invocation_integrity=invocation_integrity,
                call_counts=call_counts,
                transport_records=transport_records,
                execution_error=execution_error,
            ),
            "task_id": plan["task"]["task_id"],
            "episode_result": episode_result,
            "policy_violation": (
                str(transport_records[-1].get("policy_violation"))
                if transport_records
                else None
            ),
            "usage_telemetry_status": (
                str(transport_records[-1].get("usage_telemetry_status"))
                if transport_records
                else None
            ),
        }
        (output_directory / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return summary


def execute_full(
    repository_root: Path,
    *,
    config: CodexPolicyConfig,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    successful_smoke_evidence: dict[str, Any],
    output_directory: Path,
    runtime_identity: CodexRuntimeIdentity | None = None,
    process_factory: Callable[..., RunningProcess] | None = None,
) -> dict[str, Any]:
    config = policy_for_slot(config.slot)
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved Codex subscription full-run digest differs from the plan")
    runtime = runtime_identity or probe_codex_runtime(config)
    if plan != build_full_plan(
        repository_root,
        config=config,
        successful_smoke_evidence=successful_smoke_evidence,
        runtime_identity=runtime,
    ):
        raise ValueError("Codex subscription full plan is not canonical")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before provider invocation")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace full output: {output_directory}")
    output_directory.mkdir(parents=True)
    attempt_journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    invocation_journal = CodexCliInvocationJournal(
        output_directory / "codex-cli-invocations.sqlite",
        config,
    )
    ledger = SubscriptionExemptLedger(
        MAXIMUM_AGGREGATE_SPEND_USD,
        PRIOR_BUDGET_ACCOUNTED_SPEND_USD,
    )
    approved_caps = CallCaps(
        int(plan["caps"]["environment_action_cap"]),
        int(plan["caps"]["model_attempt_cap"]),
        int(plan["caps"]["provider_control_request_cap"]),
        int(plan["caps"]["provider_wire_request_cap"]),
    )
    transport: CodexCliTransport | None = None
    episode_results: list[dict[str, Any]] = []
    execution_error: dict[str, str] | None = None
    summary: dict[str, Any]
    try:
        manifest = build_codex_cli_policy_manifest(
            repository_root,
            code_revision=plan["code_revision"],
            runtime_identity=runtime,
            config=config,
        )
        if manifest.to_dict() != plan["policy"]["policy_manifest"]:
            raise ValueError("runtime policy manifest differs from the full plan")
        transport_kwargs: dict[str, Any] = {
            "ledger": ledger,
            "invocation_journal": invocation_journal,
            "runtime_identity": runtime,
            "config": config,
        }
        if process_factory is not None:
            transport_kwargs["process_factory"] = process_factory
        transport = CodexCliTransport(**transport_kwargs)
        for task_record in plan["task_order"]:
            task = generate_task(int(task_record["seed"]))
            if task.task_id != task_record["task_id"]:
                raise ValueError("generated calibration task differs from the plan")
            result = V5Runner(
                journal=attempt_journal,
                manifest=manifest,
                transport=transport,
                policy=CodexCliPolicy(config),
                approved_caps=approved_caps,
            ).run(
                trial_id=f"d56-{config.slot}-{task_record['ordinal']:02d}-{task.task_id}",
                task=task,
            )
            episode_results.append(result.to_dict())
            if result.classification not in NORMAL_TERMINAL_CLASSIFICATIONS:
                break
    except BaseException as exc:
        execution_error = {"type": type(exc).__name__}
        raise
    finally:
        if transport is not None:
            transport.close()
        classifications = Counter(result["classification"] for result in episode_results)
        attempt_integrity = attempt_journal.integrity_report()
        invocation_integrity = invocation_journal.integrity_report()
        call_counts = attempt_journal.call_counts()
        transport_records = [] if transport is None else list(transport.records)
        attempt_journal.close()
        invocation_journal.close()
        summary = {
            "schema_version": FULL_RESULT_SCHEMA_VERSION,
            "purpose": plan["purpose"],
            **_summary_common(
                plan=plan,
                config=config,
                digest=digest,
                ledger=ledger,
                attempt_integrity=attempt_integrity,
                invocation_integrity=invocation_integrity,
                call_counts=call_counts,
                transport_records=transport_records,
                execution_error=execution_error,
            ),
            "assigned_policy_task_pairs": EXPECTED_TASK_COUNT,
            "attempted_policy_task_pairs": len(episode_results),
            "successful_policy_task_pairs": sum(
                bool(result["success"]) for result in episode_results
            ),
            "completed_all_assigned_pairs": len(episode_results) == EXPECTED_TASK_COUNT,
            "classifications": dict(sorted(classifications.items())),
            "episode_results": episode_results,
            "successful_smoke_evidence": plan["successful_smoke_evidence"],
        }
        (output_directory / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return summary


def evidence_digest(value: Mapping[str, Any]) -> str:
    """Stable digest helper for response-content-free evidence records."""

    return "sha256:" + hashlib.sha256(canonical_json_bytes(dict(value))).hexdigest()


def assert_publishable_evidence(value: Mapping[str, Any]) -> None:
    validate_credential_free(value)
