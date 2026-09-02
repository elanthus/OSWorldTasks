"""Approval-bound Claude Code Sonnet medium smoke and frozen D5.6 run."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.claude_code_policy import (
    ACTION_SCHEMA,
    AUTH_METHOD,
    CLAUDE_CLI_VERSION,
    CONTEXT_LIMIT_TOKENS,
    EXPERIMENT_CHARGE_USD,
    KILL_GRACE_SECONDS,
    MAXIMUM_AGGREGATE_SPEND_USD,
    MODEL,
    MODEL_REASONING_EFFORT,
    PROCESS_TIMEOUT_SECONDS,
    PROVIDER_IDENTITY,
    RUNNER_REQUEST_DEADLINE_SECONDS,
    SUBSCRIPTION_TYPE,
    TERMINATE_GRACE_SECONDS,
    ClaudeCodePolicy,
    ClaudeCodeTransport,
    ClaudeInvocationJournal,
    ClaudeRuntimeIdentity,
    RunningProcess,
    build_claude_policy_manifest,
    command_contract_digest,
    probe_claude_runtime,
    sanitized_command_contract,
)
from pixelgym.grounding.v5.codex_cli_policy import SubscriptionExemptLedger
from pixelgym.grounding.v5.contracts import CallCaps, Partition, content_digest
from pixelgym.grounding.v5.d56_calibration import (
    CALIBRATION_MANIFEST,
    EXPECTED_TASK_COUNT,
    NORMAL_TERMINAL_CLASSIFICATIONS,
    _calibration_manifest,
)
from pixelgym.grounding.v5.d56_codex_cli_calibration import (
    PRIOR_BUDGET_ACCOUNTED_SPEND_USD,
    _assert_plan_publishable,
    _file_digest,
    _git,
    _streaming_file_digest,
)
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.runner import V5Runner

SMOKE_PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-claude-subscription-smoke-plan-v1"
SMOKE_RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-claude-subscription-smoke-result-v1"
FULL_PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-claude-subscription-full-plan-v1"
FULL_RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-claude-subscription-full-result-v1"
SMOKE_SEED = 5002
SMOKE_ACTION_LIMIT = 1
AGGREGATE_SMOKE_PROCESS_CAP = 3


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path.name}")
    return value


def _human_approval_scope() -> dict[str, Any]:
    return {
        "status": "advance_human_authorization_recorded",
        "authorized_at_local_date": "2026-08-30",
        "scope": [
            "one_smoke_for_luna_medium",
            "one_smoke_for_terra_medium",
            "one_smoke_for_sonnet_medium",
            "one_full_frozen_run_per_policy_only_after_its_smoke_succeeds",
        ],
        "aggregate_smoke_process_cap": AGGREGATE_SMOKE_PROCESS_CAP,
        "implemented_smoke_allocation": "one_per_requested_policy_no_retries",
        "sonnet_authentication": "claude_ai_max_subscription_reauthenticated",
        "human_gates_unchanged": True,
    }


def _pricing_record() -> dict[str, Any]:
    return {
        "authentication_mode": AUTH_METHOD,
        "subscription_type": SUBSCRIPTION_TYPE,
        "billing_classification": "Claude_Max_subscription_billed",
        "accounting_method": "claude_max_subscription_experiment_charge_zero_v1",
        "experiment_charge_per_call_usd": str(EXPERIMENT_CHARGE_USD),
        "prior_non_subscription_budget_accounted_spend_usd": str(
            PRIOR_BUDGET_ACCOUNTED_SPEND_USD
        ),
        "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
        "usage_telemetry": "optional_non_billing_diagnostic",
        "reported_cost_telemetry": "informational_non_billing_diagnostic",
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
    revision: str,
    runtime_identity: ClaudeRuntimeIdentity,
    resolved_model: str | None,
) -> dict[str, Any]:
    manifest = build_claude_policy_manifest(
        repository_root,
        code_revision=revision,
        runtime_identity=runtime_identity,
        resolved_model=resolved_model,
    )
    return {
        "slot": "sonnet-medium",
        "evaluated_provider": "Claude Code CLI",
        "provider_identity": PROVIDER_IDENTITY,
        "provider_runtime": "non-interactive claude print stream-json",
        "model": MODEL,
        "model_reasoning_effort": MODEL_REASONING_EFFORT,
        "resolved_model": resolved_model,
        "orchestrator_model_excluded": "gpt-5.6-sol",
        "runtime_identity": runtime_identity.to_dict(),
        "policy_manifest": manifest.to_dict(),
        "policy_manifest_digest": content_digest(manifest.to_dict()),
        "command_contract": list(sanitized_command_contract()),
        "command_contract_digest": command_contract_digest(),
        "response_schema_digest": content_digest(ACTION_SCHEMA),
        "observation_contract": "task_instruction_plus_current_inline_screenshot_only",
        "isolation": {
            "working_directory": "isolated_empty_temporary_directory",
            "session_persistence": "disabled",
            "settings_and_customizations": "disabled",
            "tools": "none",
            "mcp": "none",
            "chrome": "disabled",
            "slash_commands": "disabled",
            "filesystem_image_input": "none_inline_base64_only",
        },
        "limits": {
            "context_limit_tokens": CONTEXT_LIMIT_TOKENS,
            "process_timeout_seconds": PROCESS_TIMEOUT_SECONDS,
            "runner_request_deadline_seconds": RUNNER_REQUEST_DEADLINE_SECONDS,
            "termination_grace_seconds": TERMINATE_GRACE_SECONDS,
            "kill_grace_seconds": KILL_GRACE_SECONDS,
            "runner_cli_request_and_stream_retries": 0,
        },
    }


def _task_order(repository_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    partition = _calibration_manifest(repository_root)
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
    runtime_identity: ClaudeRuntimeIdentity | None = None,
) -> dict[str, Any]:
    runtime = runtime_identity or probe_claude_runtime()
    revision = _git(repository_root, "rev-parse", "HEAD")
    task = generate_task(SMOKE_SEED)
    if task.seed_record.partition is not Partition.DEVELOPMENT:
        raise ValueError("smoke task must be from the development partition")
    plan = {
        "schema_version": SMOKE_PLAN_SCHEMA_VERSION,
        "purpose": "one development smoke action for Sonnet medium; not calibration evidence",
        "execution_state": "advance_human_authorized",
        "provider_calls_made_while_planning": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "human_approval_scope": _human_approval_scope(),
        "policy": _policy_record(
            repository_root,
            revision=revision,
            runtime_identity=runtime,
            resolved_model=None,
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
            "claude_process_invocation_cap": 1,
            "model_attempt_cap": 1,
            "provider_control_request_cap": 0,
            "provider_wire_request_cap": 1,
            "experiment_charge_per_call_usd": "0.00",
            "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
        },
        "price_and_cost_accounting": _pricing_record(),
        "raw_evidence_policy": _raw_evidence_policy(),
        "stop_conditions": [
            "send exactly one Claude Code process invocation and never retry it",
            "stop after the first valid environment action",
            "stop without dispatch on invalid or unparseable action output",
            "stop on any tool, MCP, Chrome, filesystem, or unrecognized stream event",
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


def _validated_successful_smoke_evidence(value: dict[str, Any]) -> dict[str, Any]:
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
        "authentication_mode",
        "subscription_type",
        "resolved_model",
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
        "model": MODEL,
        "model_reasoning_effort": MODEL_REASONING_EFFORT,
        "cli_version": CLAUDE_CLI_VERSION,
        "authentication_mode": AUTH_METHOD,
        "subscription_type": SUBSCRIPTION_TYPE,
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
    resolved = value["resolved_model"]
    if not isinstance(resolved, str) or not resolved.startswith(MODEL):
        raise ValueError("smoke did not bind the requested Sonnet model")
    if value["usage_telemetry_status"] not in {"available", "unavailable"}:
        raise ValueError("smoke usage telemetry state is invalid")
    return dict(value)


def successful_smoke_evidence_from_files(output_directory: Path) -> dict[str, Any]:
    summary_path = output_directory / "summary.json"
    attempt_path = output_directory / "attempts.sqlite"
    invocation_path = output_directory / "claude-code-invocations.sqlite"
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
            "authentication_mode": summary.get("authentication_mode"),
            "subscription_type": summary.get("subscription_type"),
            "resolved_model": summary.get("resolved_model"),
            "policy_violation": summary.get("policy_violation"),
            "incremental_experiment_charge_usd": summary.get(
                "incremental_experiment_charge_usd"
            ),
            "budget_accounted_aggregate_spend_usd": summary.get(
                "budget_accounted_aggregate_spend_usd"
            ),
            "usage_telemetry_status": summary.get("usage_telemetry_status"),
        }
    )


def build_full_plan(
    repository_root: Path,
    *,
    successful_smoke_evidence: dict[str, Any],
    runtime_identity: ClaudeRuntimeIdentity | None = None,
) -> dict[str, Any]:
    smoke = _validated_successful_smoke_evidence(successful_smoke_evidence)
    runtime = runtime_identity or probe_claude_runtime()
    revision = _git(repository_root, "rev-parse", "HEAD")
    task_order, partition = _task_order(repository_root)
    action_cap = sum(record["max_episode_steps"] for record in task_order)
    plan = {
        "schema_version": FULL_PLAN_SCHEMA_VERSION,
        "purpose": "one full frozen fifty-task D5.6 calibration for Sonnet medium",
        "execution_state": "advance_human_authorized_after_successful_smoke",
        "provider_calls_made_while_planning": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "human_approval_scope": _human_approval_scope(),
        "policy": _policy_record(
            repository_root,
            revision=revision,
            runtime_identity=runtime,
            resolved_model=str(smoke["resolved_model"]),
        ),
        "calibration_partition": {
            "manifest_file_sha256": _file_digest(repository_root / CALIBRATION_MANIFEST),
            "manifest_digest": partition["manifest_digest"],
            "assigned_task_count": EXPECTED_TASK_COUNT,
            "task_order_rule": "frozen_manifest_order_no_reordering_or_replacement",
        },
        "task_order": task_order,
        "caps": {
            "assigned_task_cap": EXPECTED_TASK_COUNT,
            "environment_action_cap": action_cap,
            "claude_process_invocation_cap": action_cap,
            "model_attempt_cap": action_cap,
            "provider_control_request_cap": 0,
            "provider_wire_request_cap": action_cap,
            "experiment_charge_per_call_usd": "0.00",
            "maximum_incremental_experiment_charge_usd": "0.00",
            "prior_budget_accounted_spend_usd": str(PRIOR_BUDGET_ACCOUNTED_SPEND_USD),
            "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
        },
        "price_and_cost_accounting": _pricing_record(),
        "successful_smoke_evidence": smoke,
        "raw_evidence_policy": _raw_evidence_policy(),
        "stop_conditions": [
            "run tasks only in frozen fifty-task order",
            "continue after success termination or full step-limit truncation",
            "stop the policy at the first other classification or evidence mismatch",
            "never retry or replay a Claude Code process invocation",
            "stop if the resolved model differs from the successful smoke",
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
    digest: str,
    ledger: SubscriptionExemptLedger,
    attempt_integrity: dict[str, Any],
    invocation_integrity: dict[str, Any],
    call_counts: tuple[int, int],
    transport_records: list[dict[str, Any]],
    execution_error: dict[str, str] | None,
    subprocesses_closed: bool,
) -> dict[str, Any]:
    informational_cost = sum(
        Decimal(str(record["informational_cost_telemetry_usd"]))
        for record in transport_records
        if record.get("informational_cost_telemetry_usd") is not None
    )
    resolved_models = sorted(
        {
            str(record["resolved_model"])
            for record in transport_records
            if record.get("resolved_model") is not None
        }
    )
    violations = sorted(
        {
            str(record["policy_violation"])
            for record in transport_records
            if record.get("policy_violation") != "none"
        }
    )
    return {
        "approved_plan_sha256": digest,
        "code_revision": plan["code_revision"],
        "provider": "Claude Code CLI",
        "provider_identity": PROVIDER_IDENTITY,
        "model": MODEL,
        "model_reasoning_effort": MODEL_REASONING_EFFORT,
        "resolved_model": resolved_models[0] if len(resolved_models) == 1 else None,
        "cli_version": CLAUDE_CLI_VERSION,
        "authentication_mode": AUTH_METHOD,
        "subscription_type": SUBSCRIPTION_TYPE,
        "provider_calls_made": ledger.processes_started,
        "provider_wire_requests": ledger.processes_started,
        "model_attempt_reservations": call_counts[0],
        "provider_control_requests": call_counts[1],
        "incremental_experiment_charge_usd": str(ledger.incremental_experiment_charge_usd),
        "informational_cost_telemetry_usd": str(informational_cost),
        "budget_accounted_aggregate_spend_usd": str(ledger.budget_accounted_usd),
        "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
        "unresolved_invocation_count": len(ledger.unresolved),
        "usage_telemetry_unavailable_count": len(ledger.usage_telemetry_unavailable),
        "policy_violation": "none" if not violations else ",".join(violations),
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
            "subprocesses_closed": subprocesses_closed,
            "temporary_inputs_removed": True,
        },
        "human_gates": {
            "D4.12": "not_evaluated_human_owned",
            "D5.10": "not_evaluated_human_owned",
        },
    }


def _execute(
    repository_root: Path,
    *,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    output_directory: Path,
    runtime_identity: ClaudeRuntimeIdentity,
    smoke: bool,
    process_factory: Callable[..., RunningProcess] | None,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved Claude subscription digest differs from the plan")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before provider invocation")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace output: {output_directory}")
    output_directory.mkdir(parents=True)
    attempt_journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    invocation_journal = ClaudeInvocationJournal(
        output_directory / "claude-code-invocations.sqlite"
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
    resolved_model = None if smoke else str(plan["successful_smoke_evidence"]["resolved_model"])
    transport: ClaudeCodeTransport | None = None
    episode_results: list[dict[str, Any]] = []
    execution_error: dict[str, str] | None = None
    summary: dict[str, Any]
    try:
        manifest = build_claude_policy_manifest(
            repository_root,
            code_revision=plan["code_revision"],
            runtime_identity=runtime_identity,
            resolved_model=resolved_model,
        )
        if manifest.to_dict() != plan["policy"]["policy_manifest"]:
            raise ValueError("runtime policy manifest differs from the plan")
        transport_kwargs: dict[str, Any] = {
            "ledger": ledger,
            "invocation_journal": invocation_journal,
            "runtime_identity": runtime_identity,
            "expected_resolved_model": resolved_model,
        }
        if process_factory is not None:
            transport_kwargs["process_factory"] = process_factory
        transport = ClaudeCodeTransport(**transport_kwargs)
        task_records = [plan["task"]] if smoke else plan["task_order"]
        for ordinal, task_record in enumerate(task_records):
            task = generate_task(int(task_record["seed"]))
            if task.task_id != task_record["task_id"]:
                raise ValueError("generated task differs from the plan")
            trial_id = (
                f"d56-sonnet-medium-smoke-{task.task_id}"
                if smoke
                else f"d56-sonnet-medium-{ordinal:02d}-{task.task_id}"
            )
            result = V5Runner(
                journal=attempt_journal,
                manifest=manifest,
                transport=transport,
                policy=ClaudeCodePolicy(),
                approved_caps=approved_caps,
            ).run(
                trial_id=trial_id,
                task=task,
                action_limit=SMOKE_ACTION_LIMIT if smoke else None,
            )
            episode_results.append(result.to_dict())
            if not smoke and result.classification not in NORMAL_TERMINAL_CLASSIFICATIONS:
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
        subprocesses_closed = transport is None or transport.subprocesses_closed
        attempt_journal.close()
        invocation_journal.close()
        summary = {
            "schema_version": (
                SMOKE_RESULT_SCHEMA_VERSION if smoke else FULL_RESULT_SCHEMA_VERSION
            ),
            "purpose": plan["purpose"],
            **_summary_common(
                plan=plan,
                digest=digest,
                ledger=ledger,
                attempt_integrity=attempt_integrity,
                invocation_integrity=invocation_integrity,
                call_counts=call_counts,
                transport_records=transport_records,
                execution_error=execution_error,
                subprocesses_closed=subprocesses_closed,
            ),
            "assigned_policy_task_pairs": 1 if smoke else EXPECTED_TASK_COUNT,
            "attempted_policy_task_pairs": len(episode_results),
            "successful_policy_task_pairs": sum(
                bool(result["success"]) for result in episode_results
            ),
            "completed_all_assigned_pairs": len(episode_results)
            == (1 if smoke else EXPECTED_TASK_COUNT),
            "classifications": dict(sorted(classifications.items())),
            "episode_results": episode_results,
        }
        if smoke:
            summary["task_id"] = plan["task"]["task_id"]
            summary["episode_result"] = episode_results[0] if episode_results else None
            records = transport_records
            summary["usage_telemetry_status"] = (
                str(records[-1].get("usage_telemetry_status")) if records else None
            )
        else:
            summary["successful_smoke_evidence"] = plan["successful_smoke_evidence"]
        (output_directory / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return summary


def execute_smoke(
    repository_root: Path,
    *,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    output_directory: Path,
    runtime_identity: ClaudeRuntimeIdentity | None = None,
    process_factory: Callable[..., RunningProcess] | None = None,
) -> dict[str, Any]:
    runtime = runtime_identity or probe_claude_runtime()
    if plan != build_smoke_plan(repository_root, runtime_identity=runtime):
        raise ValueError("Claude subscription smoke plan is not canonical")
    return _execute(
        repository_root,
        plan=plan,
        approved_plan_sha256=approved_plan_sha256,
        output_directory=output_directory,
        runtime_identity=runtime,
        smoke=True,
        process_factory=process_factory,
    )


def execute_full(
    repository_root: Path,
    *,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    successful_smoke_evidence: dict[str, Any],
    output_directory: Path,
    runtime_identity: ClaudeRuntimeIdentity | None = None,
    process_factory: Callable[..., RunningProcess] | None = None,
) -> dict[str, Any]:
    smoke = _validated_successful_smoke_evidence(successful_smoke_evidence)
    runtime = runtime_identity or probe_claude_runtime()
    if plan != build_full_plan(
        repository_root,
        successful_smoke_evidence=smoke,
        runtime_identity=runtime,
    ):
        raise ValueError("Claude subscription full plan is not canonical")
    return _execute(
        repository_root,
        plan=plan,
        approved_plan_sha256=approved_plan_sha256,
        output_directory=output_directory,
        runtime_identity=runtime,
        smoke=False,
        process_factory=process_factory,
    )


def assert_publishable_evidence(value: Mapping[str, Any]) -> None:
    validate_credential_free(value)
