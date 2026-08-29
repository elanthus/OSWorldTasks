"""Plan the Codex CLI successor and execute only its approval-bound smoke task."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.codex_cli_policy import (
    ACTION_SCHEMA,
    AUTH_MODE,
    CODEX_CLI_VERSION,
    CONSERVATIVE_INPUT_PER_TOKEN_USD,
    CONSERVATIVE_OUTPUT_PER_TOKEN_USD,
    KILL_GRACE_SECONDS,
    MODEL,
    MODEL_CONTEXT_WINDOW_TOKENS,
    MODEL_PROVIDER_ID,
    MODEL_REASONING_EFFORT,
    PRICE_OBSERVED_AT_UTC,
    PRICE_SOURCE,
    PROCESS_TIMEOUT_SECONDS,
    REQUEST_MAXIMUM_COST_EQUIVALENT_USD,
    ROLLOUT_BUDGET_TOKENS,
    RUNNER_REQUEST_DEADLINE_SECONDS,
    TERMINATE_GRACE_SECONDS,
    TRANSPORT_RETRY_RULE,
    CodexCliInvocationJournal,
    CodexCliPolicy,
    CodexCliTransport,
    CodexRuntimeIdentity,
    CostEquivalentLedger,
    RunningProcess,
    build_codex_cli_policy_manifest,
    command_contract_digest,
    probe_codex_runtime,
    sanitized_command_contract,
)
from pixelgym.grounding.v5.contracts import CallCaps, Partition, content_digest
from pixelgym.grounding.v5.d56_calibration import (
    CALIBRATION_MANIFEST,
    EXPECTED_TASK_COUNT,
    _calibration_manifest,
)
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.runner import V5Runner
from pixelgym.serialization import canonical_json_bytes

SMOKE_PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-codex-cli-luna-smoke-plan-v1"
CALIBRATION_PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-codex-cli-luna-calibration-plan-v1"
SMOKE_RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-codex-cli-luna-smoke-result-v1"
MAXIMUM_AGGREGATE_SPEND_USD = Decimal("10.00")
PRIOR_BUDGET_ACCOUNTED_SPEND_USD = Decimal("4.778164718")
MAXIMUM_REMAINING_INCREMENTAL_EXPOSURE_USD = Decimal("5.221835282")
SMOKE_SEED = 5002
SMOKE_ACTION_LIMIT = 1

PREDECESSOR_PLAN_CONTENT_SHA256 = (
    "sha256:fc1f41d00df8c847d55765ea6af6b4c688463a893281f995f3eee3b770c43f7c"
)
PREDECESSOR_PLAN_FILE_SHA256 = (
    "sha256:80f8b7ea4d9809405faf98c9b4ef01193f5456a9d58db243488dd2fe4229133e"
)
PREDECESSOR_SUMMARY_FILE_SHA256 = (
    "sha256:2fb447acaca1c934a0469fa56d67849f9f5458de21edd506b0e6a63660b67b69"
)
PREDECESSOR_JOURNAL_FILE_SHA256 = (
    "sha256:81e9137cb5c5074c3a0fd02f8f20ef7c2368ede9dbf95f1a92a1805a61818671"
)
PREDECESSOR_JOURNAL_EVENT_CHAIN_SHA256 = (
    "sha256:6117a1454d0cbe834a2b6a0f46cc0be8536ae44650b687a30ad1360c5ff0999d"
)
PREDECESSOR_AUDIT_FILE_SHA256 = (
    "sha256:faf1cfb20422fe87456ba2373d48f6e321da50a1ba1b0ceabbc2c9561ceb0afd"
)
PREDECESSOR_RELATION_FILE_SHA256 = (
    "sha256:7ccc779f0b2204b99296e5fd4f5190646bd523cc7a447ed4236ca22807abc312"
)
PREDECESSOR_PUBLISHABLE_FILE_SHA256 = (
    "sha256:952e105d4d6f092b2f5a98b98b3ff6e2f784c407ee99f8b7c540ae000134fde6"
)
PREDECESSOR_REPORT_FILE_SHA256 = (
    "sha256:3a380f9f44cae54b6116f426ecbcf2d6bb1cbd038116237e3f80530f4d0169a5"
)
PREDECESSOR_CODE_REVISION = "0e74a791559b5e6636c12e2fa4fd480ab3a8c8fd"

_PLAN_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-plan.json")
_SUMMARY_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-run/summary.json")
_JOURNAL_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-run/attempts.sqlite")
_AUDIT_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-integrity-audit.json")
_RELATION_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-publication-relation.json")
_PUBLISHABLE_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-publishable.json")
_REPORT_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-report.md")


def _git(repository_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository_root,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _streaming_file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object: {path.name}")
    return value


def validated_predecessor_evidence(repository_root: Path) -> dict[str, Any]:
    """Verify the consumed Qwen plan and frozen negative evidence without modifying it."""

    expected_files = (
        (_PLAN_PATH, PREDECESSOR_PLAN_FILE_SHA256, False),
        (_SUMMARY_PATH, PREDECESSOR_SUMMARY_FILE_SHA256, False),
        (_JOURNAL_PATH, PREDECESSOR_JOURNAL_FILE_SHA256, True),
        (_AUDIT_PATH, PREDECESSOR_AUDIT_FILE_SHA256, False),
        (_RELATION_PATH, PREDECESSOR_RELATION_FILE_SHA256, False),
        (_PUBLISHABLE_PATH, PREDECESSOR_PUBLISHABLE_FILE_SHA256, False),
        (_REPORT_PATH, PREDECESSOR_REPORT_FILE_SHA256, False),
    )
    for relative, expected, streaming in expected_files:
        path = repository_root / relative
        actual = _streaming_file_digest(path) if streaming else _file_digest(path)
        if actual != expected:
            raise ValueError(f"frozen predecessor digest mismatch: {relative.name}")
    predecessor_plan = _load_json_object(repository_root / _PLAN_PATH)
    if content_digest(predecessor_plan) != PREDECESSOR_PLAN_CONTENT_SHA256:
        raise ValueError("frozen predecessor canonical plan digest mismatch")
    summary = _load_json_object(repository_root / _SUMMARY_PATH)
    expected_summary = {
        "approved_plan_sha256": PREDECESSOR_PLAN_CONTENT_SHA256,
        "code_revision": PREDECESSOR_CODE_REVISION,
        "assigned_policy_task_pairs": 50,
        "attempted_policy_task_pairs": 13,
        "successful_policy_task_pairs": 0,
        "completed_all_assigned_pairs": False,
        "provider_calls_made": 350,
        "provider_wire_requests": 350,
        "budget_accounted_aggregate_spend_usd": str(PRIOR_BUDGET_ACCOUNTED_SPEND_USD),
        "remaining_aggregate_spend_usd": str(MAXIMUM_REMAINING_INCREMENTAL_EXPOSURE_USD),
        "classifications": {"invalid_output": 1, "step_limit_truncation": 12},
    }
    if any(summary.get(key) != expected for key, expected in expected_summary.items()):
        raise ValueError("frozen predecessor summary facts mismatch")
    integrity = summary.get("journal_integrity")
    if not isinstance(integrity, dict) or (
        integrity.get("event_chain_digest") != PREDECESSOR_JOURNAL_EVENT_CHAIN_SHA256
    ):
        raise ValueError("frozen predecessor journal event chain mismatch")
    audit = _load_json_object(repository_root / _AUDIT_PATH)
    if audit.get("provider_calls_made") != 0 or audit.get("result") != {
        "checks_failed": 0,
        "checks_verified": 8,
        "milestone_gate_verdict": "not_evaluated_human_owned",
        "provider_calls_made": 0,
    }:
        raise ValueError("frozen predecessor integrity audit is not authoritative")
    relation = _load_json_object(repository_root / _RELATION_PATH)
    if relation.get("authoritative") != {
        "approved_plan_content_sha256": PREDECESSOR_PLAN_CONTENT_SHA256,
        "integrity_audit_file_sha256": PREDECESSOR_AUDIT_FILE_SHA256,
        "journal_event_chain_sha256": PREDECESSOR_JOURNAL_EVENT_CHAIN_SHA256,
        "restricted_attempt_journal_file_sha256": PREDECESSOR_JOURNAL_FILE_SHA256,
        "run_summary_file_sha256": PREDECESSOR_SUMMARY_FILE_SHA256,
    }:
        raise ValueError("frozen predecessor publication relation is not authoritative")
    return {
        "status": "consumed_immutable_incomplete_negative_evidence",
        "approved_plan_content_sha256": PREDECESSOR_PLAN_CONTENT_SHA256,
        "approved_plan_file_sha256": PREDECESSOR_PLAN_FILE_SHA256,
        "summary_file_sha256": PREDECESSOR_SUMMARY_FILE_SHA256,
        "restricted_journal_file_sha256": PREDECESSOR_JOURNAL_FILE_SHA256,
        "journal_event_chain_sha256": PREDECESSOR_JOURNAL_EVENT_CHAIN_SHA256,
        "integrity_audit_file_sha256": PREDECESSOR_AUDIT_FILE_SHA256,
        "publication_relation_file_sha256": PREDECESSOR_RELATION_FILE_SHA256,
        "publishable_derivative_file_sha256": PREDECESSOR_PUBLISHABLE_FILE_SHA256,
        "report_file_sha256": PREDECESSOR_REPORT_FILE_SHA256,
        "code_revision": PREDECESSOR_CODE_REVISION,
        "assigned_tasks": 50,
        "attempted_tasks": 13,
        "successful_tasks": 0,
        "unattempted_tasks": 37,
        "classifications": {"invalid_output": 1, "step_limit_truncation": 12},
        "budget_accounted_aggregate_spend_usd": str(PRIOR_BUDGET_ACCOUNTED_SPEND_USD),
        "maximum_remaining_incremental_exposure_usd": str(
            MAXIMUM_REMAINING_INCREMENTAL_EXPOSURE_USD
        ),
        "reuse_rule": "do_not_resume_overwrite_reuse_or_reinterpret",
    }


def _policy_record(
    repository_root: Path,
    *,
    revision: str,
    runtime_identity: CodexRuntimeIdentity,
) -> dict[str, Any]:
    manifest = build_codex_cli_policy_manifest(
        repository_root,
        code_revision=revision,
        runtime_identity=runtime_identity,
    )
    return {
        "evaluated_provider": "Codex CLI",
        "provider_runtime": PROVIDER_RUNTIME,
        "model_provider_profile": MODEL_PROVIDER_ID,
        "model": MODEL,
        "model_reasoning_effort": MODEL_REASONING_EFFORT,
        "orchestrator_model_excluded": "gpt-5.6-sol",
        "runtime_identity": runtime_identity.to_dict(),
        "policy_manifest": manifest.to_dict(),
        "policy_manifest_digest": content_digest(manifest.to_dict()),
        "command_contract": list(sanitized_command_contract()),
        "command_contract_digest": command_contract_digest(),
        "response_schema_digest": content_digest(ACTION_SCHEMA),
        "observation_contract": {
            "application_state_observation": "current_screenshot_only",
            "allowed_non_observation_input": "task_instruction",
            "statefulness": "none",
            "forbidden_inputs": [
                "accessibility_tree",
                "dom",
                "evaluator_state",
                "expected_answer",
                "filesystem_observations",
                "prior_provider_responses",
                "privileged_task_metadata",
                "repository_context",
                "target_bounding_box",
            ],
        },
        "isolation": {
            "working_directory": "isolated_empty_temporary_directory",
            "session_persistence": "ephemeral",
            "sandbox": "read-only",
            "shell_tool": "disabled",
            "web_search": "disabled",
            "user_config": "ignored_except_authentication",
            "user_and_project_rules": "ignored",
            "plugins": "disabled",
            "mcp": "unconfigured_and_unavailable",
            "skills": "disabled",
            "connectors": "disabled",
            "filesystem_and_repository_tools": "disabled",
            "environment_inheritance_for_tools": "none",
        },
        "inference_limits_and_timeouts": {
            "model_context_window_tokens": MODEL_CONTEXT_WINDOW_TOKENS,
            "rollout_budget_tokens": ROLLOUT_BUDGET_TOKENS,
            "final_output": "one_schema_constrained_action_object",
            "process_timeout_seconds": PROCESS_TIMEOUT_SECONDS,
            "runner_request_deadline_seconds": RUNNER_REQUEST_DEADLINE_SECONDS,
            "termination_grace_seconds": TERMINATE_GRACE_SECONDS,
            "kill_grace_seconds": KILL_GRACE_SECONDS,
        },
        "retry_and_backoff": {
            "runner_retries": 0,
            "cli_request_retries": 0,
            "cli_stream_retries": 0,
            "websocket_transport": "disabled",
            "backoff": "none",
            "transport_retry_rule": TRANSPORT_RETRY_RULE,
            "codex_401_auth_recovery": (
                "Codex CLI 0.150.1 may internally recover and replay after an HTTP 401; "
                "this pre-inference authentication path is outside the configurable request "
                "and stream retry counters"
            ),
            "physical_wire_observability": (
                "Codex JSONL does not expose encrypted internal HTTP exchange counts"
            ),
        },
    }


PROVIDER_RUNTIME = "non-interactive codex exec"


def _pricing_record() -> dict[str, Any]:
    return {
        "source_url": PRICE_SOURCE,
        "observed_at_utc": PRICE_OBSERVED_AT_UTC,
        "authentication_mode": AUTH_MODE,
        "billing_classification": "ChatGPT/Codex subscription usage_not_metered_API_spend",
        "accounting_method": "conservative_standard_list_price_equivalent_v1",
        "currency": "USD",
        "standard_long_context_input_rate_usd_per_unit": "0.00000040",
        "standard_long_context_cached_input_rate_usd_per_unit": "0.00000004",
        "standard_long_context_cache_write_rate_usd_per_unit": "0.00000050",
        "standard_long_context_output_rate_usd_per_unit": "0.00000180",
        "conservative_input_rate_usd_per_unit": str(CONSERVATIVE_INPUT_PER_TOKEN_USD),
        "conservative_output_rate_usd_per_unit": str(CONSERVATIVE_OUTPUT_PER_TOKEN_USD),
        "reasoning_output_double_counted": True,
        "authoritative_usage_source": "codex_jsonl_turn.completed.usage",
        "per_invocation_unresolved_reservation_usd": str(REQUEST_MAXIMUM_COST_EQUIVALENT_USD),
        "unknown_or_invalid_usage_rule": (
            "retain_full_reservation_block_all_further_calls_and_fail_closed"
        ),
    }


def _failure_classifications() -> dict[str, str]:
    return {
        "success_termination": "normal_terminal_continue_in_calibration",
        "step_limit_truncation": "normal_terminal_continue_in_calibration",
        "invalid_output": "retain_and_stop_without_retry",
        "request_failure": "retain_and_stop_without_retry",
        "infrastructure_failure": "retain_unresolved_reservation_and_stop",
        "tool_or_unauthorized_observation": "invalid_output_retain_and_stop",
        "timeout_or_interruption": "terminate_process_group_retain_and_stop",
        "missing_or_invalid_usage": "retain_full_reservation_and_stop",
    }


def _raw_evidence_policy() -> dict[str, Any]:
    return {
        "raw_cli_jsonl": "restricted_local_invocation_journal_only",
        "raw_cli_stderr": "restricted_local_invocation_journal_only",
        "screenshots": "restricted_local_attempt_journal_only",
        "provider_response_content": "restricted_local_journals_only",
        "publication": ("only_response_content_free_derivatives_after_separate_integrity_audit"),
        "forbidden_plan_content": [
            "absolute_host_paths",
            "credentials",
            "private_provider_identifiers",
            "raw_responses",
            "screenshots",
        ],
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
        raise ValueError("calibration manifest task count differs from the frozen contract")
    return records, partition


def _assert_plan_publishable(plan: dict[str, Any], repository_root: Path) -> None:
    validate_plan = canonical_json_bytes(plan)
    validate_credential_free(plan)
    repository_text = str(repository_root.resolve()).encode("utf-8")
    if repository_text in validate_plan:
        raise ValueError("plan contains an absolute repository path")
    forbidden_fields = {
        "raw_response",
        "raw_stderr",
        "raw_stdout",
        "screenshot_path",
        "thread_id",
        "upstream_request_id",
    }

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if forbidden_fields & set(value):
                raise ValueError("plan contains a forbidden private or raw field")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and Path(value).is_absolute():
            raise ValueError("plan contains an absolute host path")

    visit(plan)


def build_smoke_plan(
    repository_root: Path,
    *,
    runtime_identity: CodexRuntimeIdentity | None = None,
) -> dict[str, Any]:
    runtime = runtime_identity or probe_codex_runtime()
    predecessor = validated_predecessor_evidence(repository_root)
    revision = _git(repository_root, "rev-parse", "HEAD")
    task = generate_task(SMOKE_SEED)
    if task.seed_record.partition is not Partition.DEVELOPMENT:
        raise ValueError("Codex CLI smoke may use a development task only")
    aggregate_upper_bound = PRIOR_BUDGET_ACCOUNTED_SPEND_USD + REQUEST_MAXIMUM_COST_EQUIVALENT_USD
    if aggregate_upper_bound > MAXIMUM_AGGREGATE_SPEND_USD:
        raise ValueError("Codex CLI smoke reservation exceeds the aggregate cap")
    plan = {
        "schema_version": SMOKE_PLAN_SCHEMA_VERSION,
        "purpose": (
            "one development-only Codex CLI action validating image input, isolation, "
            "schema-constrained action output, journaling, and conservative cost accounting; "
            "not calibration evidence"
        ),
        "execution_state": "awaiting_exact_human_approval",
        "provider_calls_made_while_planning": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "policy": _policy_record(
            repository_root,
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
            "model_completed_turn_cap": 1,
            "model_attempt_cap": 1,
            "provider_control_request_cap": 0,
            "provider_wire_request_cap": 1,
            "provider_wire_request_cap_semantics": (
                "one logical adapter transport send; packet-level ChatGPT authentication "
                "exchanges are not observable"
            ),
            "prior_budget_accounted_spend_usd": str(PRIOR_BUDGET_ACCOUNTED_SPEND_USD),
            "maximum_remaining_incremental_exposure_usd": str(
                MAXIMUM_REMAINING_INCREMENTAL_EXPOSURE_USD
            ),
            "per_invocation_unresolved_reservation_usd": str(REQUEST_MAXIMUM_COST_EQUIVALENT_USD),
            "aggregate_theoretical_upper_bound_usd": str(aggregate_upper_bound),
            "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
        },
        "price_and_cost_accounting": _pricing_record(),
        "failure_classifications": _failure_classifications(),
        "predecessor_evidence": predecessor,
        "raw_evidence_policy": _raw_evidence_policy(),
        "stop_conditions": [
            "send at most one Codex CLI model invocation and never retry",
            "stop after the first valid environment action",
            "stop without dispatch on invalid or unparseable action output",
            "stop without dispatch on any JSONL tool, web, filesystem, MCP, plugin, skill, connector, or subagent event",
            "terminate the whole process group on timeout or interruption and retain the maximum unresolved reservation",
            "stop if CLI version, ChatGPT authentication mode, model catalog identity, model, reasoning effort, command contract, or predecessor evidence differs",
            "stop before process start if the maximum unresolved reservation cannot fit under the aggregate cap",
            "stop and block further calls if authoritative JSONL usage is missing or invalid",
            "stop if the ChatGPT authentication-mode preflight fails; any ultimate 401 or missing usage retains the full unresolved reservation",
            "do not resume, overwrite, or reuse the consumed Qwen/OpenRouter plan or journals",
        ],
        "approval_required": {
            "owner": "human",
            "scope": "this_exact_one_task_smoke_plan_only",
            "exact_plan_sha256": "sha256 of canonical plan bytes",
            "earlier_qwen_or_openrouter_approvals_apply": False,
        },
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
        "budget_accounted_aggregate_spend_usd",
        "task_id",
        "classification",
        "provider_calls_made",
        "environment_actions",
        "model",
        "model_reasoning_effort",
        "cli_version",
        "policy_violation",
    }
    if set(value) != required:
        raise ValueError("successful smoke evidence fields do not match the frozen contract")
    if any(
        value.get(key) != expected
        for key, expected in {
            "classification": "pilot_action_limit",
            "provider_calls_made": 1,
            "environment_actions": 1,
            "model": MODEL,
            "model_reasoning_effort": MODEL_REASONING_EFFORT,
            "cli_version": CODEX_CLI_VERSION,
            "policy_violation": "none",
        }.items()
    ):
        raise ValueError("smoke evidence does not record one successful bounded action")
    for key in (
        "approved_smoke_plan_sha256",
        "summary_file_sha256",
        "attempt_journal_file_sha256",
        "invocation_journal_file_sha256",
    ):
        if not isinstance(value[key], str) or not value[key].startswith("sha256:"):
            raise ValueError("smoke evidence digest is invalid")
    accounted = Decimal(str(value["budget_accounted_aggregate_spend_usd"]))
    if not PRIOR_BUDGET_ACCOUNTED_SPEND_USD <= accounted <= MAXIMUM_AGGREGATE_SPEND_USD:
        raise ValueError("smoke evidence aggregate accounting is invalid")
    return dict(value)


def build_successor_calibration_plan(
    repository_root: Path,
    *,
    successful_smoke_evidence: dict[str, Any],
    runtime_identity: CodexRuntimeIdentity | None = None,
) -> dict[str, Any]:
    """Build the larger candidate only after a separate successful smoke record exists."""

    runtime = runtime_identity or probe_codex_runtime()
    predecessor = validated_predecessor_evidence(repository_root)
    smoke = _validated_successful_smoke_evidence(successful_smoke_evidence)
    revision = _git(repository_root, "rev-parse", "HEAD")
    task_order, partition = _task_order(repository_root)
    action_cap = sum(record["max_episode_steps"] for record in task_order)
    prior = Decimal(str(smoke["budget_accounted_aggregate_spend_usd"]))
    plan = {
        "schema_version": CALIBRATION_PLAN_SCHEMA_VERSION,
        "purpose": (
            "Codex CLI plus GPT-5.6-Luna low-reasoning successor calibration over the "
            "frozen fifty-task D5.6 order; the consumed Qwen result remains negative evidence"
        ),
        "execution_state": "awaiting_separate_exact_human_approval",
        "provider_calls_made_while_planning": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "policy": _policy_record(
            repository_root,
            revision=revision,
            runtime_identity=runtime,
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
            "codex_process_invocation_cap": action_cap,
            "model_completed_turn_cap": action_cap,
            "model_attempt_cap": action_cap,
            "provider_control_request_cap": 0,
            "provider_wire_request_cap": action_cap,
            "provider_wire_request_cap_semantics": (
                "logical adapter transport sends; packet-level ChatGPT authentication "
                "exchanges are not observable"
            ),
            "prior_budget_accounted_spend_usd": str(prior),
            "maximum_remaining_incremental_exposure_usd": str(MAXIMUM_AGGREGATE_SPEND_USD - prior),
            "per_invocation_unresolved_reservation_usd": str(REQUEST_MAXIMUM_COST_EQUIVALENT_USD),
            "uncapped_run_theoretical_maximum_usd": str(
                REQUEST_MAXIMUM_COST_EQUIVALENT_USD * action_cap
            ),
            "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
            "enforcement": (
                "reserve one full-context conservative cost equivalent before each fresh "
                "CLI process; release only to authoritative completed-turn usage; stop "
                "before any reservation that cannot fit"
            ),
        },
        "price_and_cost_accounting": _pricing_record(),
        "failure_classifications": _failure_classifications(),
        "predecessor_evidence": predecessor,
        "successful_smoke_evidence": smoke,
        "raw_evidence_policy": _raw_evidence_policy(),
        "stop_conditions": [
            "run tasks only in the frozen fifty-task order",
            "continue after success termination or full step-limit truncation",
            "stop at the first invalid output, policy violation, request failure, infrastructure failure, timeout, interruption, identity mismatch, evidence mismatch, or cost-accounting failure",
            "never retry or replay a Codex CLI process invocation",
            "stop before any next invocation whose unresolved reservation cannot fit under the aggregate cap",
            "retain the maximum reservation and block all further calls after unknown or missing usage",
            "do not resume, overwrite, or reuse the consumed Qwen/OpenRouter plan or journals",
            "do not expose confirmatory tasks",
        ],
        "approval_required": {
            "owner": "human",
            "scope": "this_exact_successor_calibration_plan_only",
            "exact_plan_sha256": "sha256 of canonical plan bytes",
            "smoke_approval_applies": False,
        },
        "human_gates": {
            "D4.12": "not_evaluated_human_owned",
            "D5.10": "not_evaluated_human_owned",
        },
    }
    _assert_plan_publishable(plan, repository_root)
    return plan


def plan_digest(plan: dict[str, Any]) -> str:
    return content_digest(plan)


def plan_file_bytes(plan: dict[str, Any]) -> bytes:
    return (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode("utf-8")


def execute_smoke(
    repository_root: Path,
    *,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    output_directory: Path,
    runtime_identity: CodexRuntimeIdentity | None = None,
    process_factory: Callable[..., RunningProcess] | None = None,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved Codex CLI smoke digest does not match the plan")
    runtime = runtime_identity or probe_codex_runtime()
    if plan != build_smoke_plan(repository_root, runtime_identity=runtime):
        raise ValueError("Codex CLI smoke plan does not match the canonical configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before a Codex CLI model invocation")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace Codex CLI smoke output: {output_directory}")
    output_directory.mkdir(parents=True)
    attempt_journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    invocation_journal = CodexCliInvocationJournal(
        output_directory / "codex-cli-invocations.sqlite"
    )
    ledger = CostEquivalentLedger(
        MAXIMUM_AGGREGATE_SPEND_USD,
        PRIOR_BUDGET_ACCOUNTED_SPEND_USD,
    )
    transport: CodexCliTransport | None = None
    result_record: dict[str, Any] | None = None
    execution_error: dict[str, str] | None = None
    trial_id = f"d56-codex-cli-luna-smoke-{plan['task']['task_id']}"
    summary: dict[str, Any]
    try:
        manifest = build_codex_cli_policy_manifest(
            repository_root,
            code_revision=plan["code_revision"],
            runtime_identity=runtime,
        )
        if manifest.to_dict() != plan["policy"]["policy_manifest"]:
            raise ValueError("runtime policy manifest differs from the approved smoke plan")
        task = generate_task(int(plan["task"]["seed"]))
        if task.task_id != plan["task"]["task_id"]:
            raise ValueError("generated task differs from the approved smoke plan")
        transport_kwargs: dict[str, Any] = {
            "ledger": ledger,
            "invocation_journal": invocation_journal,
            "runtime_identity": runtime,
        }
        if process_factory is not None:
            transport_kwargs["process_factory"] = process_factory
        transport = CodexCliTransport(**transport_kwargs)
        result = V5Runner(
            journal=attempt_journal,
            manifest=manifest,
            transport=transport,
            policy=CodexCliPolicy(),
            approved_caps=CallCaps(1, 1, 0, 1),
        ).run(
            trial_id=trial_id,
            task=task,
            action_limit=SMOKE_ACTION_LIMIT,
        )
        result_record = result.to_dict()
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
        policy_violation = (
            str(transport_records[-1].get("policy_violation")) if transport_records else None
        )
        summary = {
            "schema_version": SMOKE_RESULT_SCHEMA_VERSION,
            "purpose": plan["purpose"],
            "approved_plan_sha256": digest,
            "code_revision": plan["code_revision"],
            "provider": "Codex CLI",
            "provider_runtime": PROVIDER_RUNTIME,
            "model": MODEL,
            "model_reasoning_effort": MODEL_REASONING_EFFORT,
            "cli_version": CODEX_CLI_VERSION,
            "authentication_mode": AUTH_MODE,
            "provider_calls_made": ledger.processes_started,
            "provider_wire_requests": ledger.processes_started,
            "model_attempt_reservations": call_counts[0],
            "provider_control_requests": call_counts[1],
            "prior_budget_accounted_spend_usd": str(PRIOR_BUDGET_ACCOUNTED_SPEND_USD),
            "incremental_cost_equivalent_usd": str(ledger.incremental_cost_equivalent_usd),
            "budget_accounted_aggregate_spend_usd": str(ledger.budget_accounted_usd),
            "remaining_aggregate_exposure_usd": str(
                MAXIMUM_AGGREGATE_SPEND_USD - ledger.budget_accounted_usd
            ),
            "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
            "unresolved_reservation_count": len(ledger.unresolved),
            "cost_accounting_method": (
                "conservative_standard_list_price_equivalent_v1_not_billed_spend"
            ),
            "task_id": plan["task"]["task_id"],
            "episode_result": result_record,
            "execution_error": execution_error,
            "policy_violation": policy_violation,
            "transport_records": transport_records,
            "attempt_journal_integrity": attempt_integrity,
            "invocation_journal_integrity": invocation_integrity,
            "predecessor_evidence": plan["predecessor_evidence"],
            "publication_status": "restricted_raw_evidence_local_only",
            "cleanup": {
                "attempt_journal_closed": True,
                "invocation_journal_closed": True,
                "policy_and_environment_closed": True,
                "subprocesses_closed": True,
                "temporary_inputs_removed": True,
            },
            "human_gates": {
                "D4.12": "not_evaluated_human_owned",
                "D5.10": "not_evaluated_human_owned",
            },
        }
        (output_directory / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return summary


def successful_smoke_evidence_from_files(output_directory: Path) -> dict[str, Any]:
    """Build a response-content-free record for a later, separately approved plan."""

    summary_path = output_directory / "summary.json"
    attempt_path = output_directory / "attempts.sqlite"
    invocation_path = output_directory / "codex-cli-invocations.sqlite"
    summary = _load_json_object(summary_path)
    episode = summary.get("episode_result")
    if not isinstance(episode, Mapping):
        raise TypeError("smoke summary does not contain an episode result")
    return _validated_successful_smoke_evidence(
        {
            "approved_smoke_plan_sha256": summary.get("approved_plan_sha256"),
            "summary_file_sha256": _file_digest(summary_path),
            "attempt_journal_file_sha256": _streaming_file_digest(attempt_path),
            "invocation_journal_file_sha256": _streaming_file_digest(invocation_path),
            "budget_accounted_aggregate_spend_usd": summary.get(
                "budget_accounted_aggregate_spend_usd"
            ),
            "task_id": summary.get("task_id"),
            "classification": episode.get("classification"),
            "provider_calls_made": summary.get("provider_calls_made"),
            "environment_actions": episode.get("environment_actions"),
            "model": summary.get("model"),
            "model_reasoning_effort": summary.get("model_reasoning_effort"),
            "cli_version": summary.get("cli_version"),
            "policy_violation": summary.get("policy_violation"),
        }
    )
