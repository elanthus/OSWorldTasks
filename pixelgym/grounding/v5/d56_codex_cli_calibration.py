"""Plan the Codex CLI successor and execute only its approval-bound smoke task."""

from __future__ import annotations

import hashlib
import json
import sqlite3
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
    LUNA_EXPERIMENT_CHARGE_USD,
    MODEL,
    MODEL_CONTEXT_WINDOW_TOKENS,
    MODEL_PROVIDER_ID,
    MODEL_REASONING_EFFORT,
    PRICE_OBSERVED_AT_UTC,
    PRICE_SOURCE,
    PROCESS_TIMEOUT_SECONDS,
    REQUEST_MAXIMUM_INFORMATIONAL_LIST_PRICE_EQUIVALENT_USD,
    ROLLOUT_BUDGET_TOKENS,
    RUNNER_REQUEST_DEADLINE_SECONDS,
    SUBSCRIPTION_SOURCE,
    TERMINATE_GRACE_SECONDS,
    TRANSPORT_RETRY_RULE,
    CodexCliInvocationJournal,
    CodexCliPolicy,
    CodexCliTransport,
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
    CALIBRATION_MANIFEST,
    EXPECTED_TASK_COUNT,
    _calibration_manifest,
)
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.runner import V5Runner
from pixelgym.serialization import canonical_json_bytes

SMOKE_PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-codex-cli-luna-smoke-plan-v3"
CALIBRATION_PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-codex-cli-luna-calibration-plan-v2"
SMOKE_RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-codex-cli-luna-smoke-result-v3"
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

FAILED_LUNA_SMOKE_PLAN_CONTENT_SHA256 = (
    "sha256:1bea6849d3dd619ab481ee21b957975fedac9302119806aaadc90b21ce6be433"
)
FAILED_LUNA_SMOKE_PLAN_FILE_SHA256 = (
    "sha256:f75293b1370d60ac84e1afeaeddf97b01f9fa4193b1b295fd3c1aadb614b6dd3"
)
FAILED_LUNA_SMOKE_SUMMARY_FILE_SHA256 = (
    "sha256:6bff9bfb09f36556133dc1e33ff2c69ce846ad3856b7af89edc2ef5e5f045ebb"
)
FAILED_LUNA_SMOKE_ATTEMPT_JOURNAL_FILE_SHA256 = (
    "sha256:412350c8211ea077b5bc763aa802ee9553f8496583d26a2b553b062feabbc98e"
)
FAILED_LUNA_SMOKE_ATTEMPT_EVENT_CHAIN_SHA256 = (
    "sha256:c29c9f7b6e6eadbfca55356bfa4f34367a1b26674f32fac37f050a610cb99530"
)
FAILED_LUNA_SMOKE_INVOCATION_JOURNAL_FILE_SHA256 = (
    "sha256:1487aa1603b84ee6aa54bf228b9bea02e05b58ae03c767579f6aac9bf6d26894"
)
FAILED_LUNA_SMOKE_RAW_STDOUT_SHA256 = (
    "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)
FAILED_LUNA_SMOKE_RAW_STDERR_SHA256 = (
    "sha256:74cb00300ed4a4c23ba979d30d34218cd356e4bfb55b81f10a3dc832d52c56c7"
)
FAILED_LUNA_SMOKE_OUTCOME_SHA256 = (
    "sha256:5927d5c61ff3b0f495857990feb3d3e70488c5836126061e30f5ee99817e1d66"
)
FAILED_LUNA_SMOKE_CODE_REVISION = "f3cf861edda5b75d30145739de12e1764923d6b4"

RETRY_PREDECESSOR_PLAN_CONTENT_SHA256 = (
    "sha256:ec88a91aa1573d9b33aeb840c9deb56147c94c47456169546a9c7c834a6bfc7b"
)
RETRY_PREDECESSOR_PLAN_FILE_SHA256 = (
    "sha256:e56dd925fe7fbe0c01e9d2c7c97f1e11ded132fc94504d206ddb36082661e507"
)
RETRY_PREDECESSOR_SUMMARY_FILE_SHA256 = (
    "sha256:fd7c3c8ab525b1a5e924e6aa7fa1fddd77e203fb805a82d3992515766ecee401"
)
RETRY_PREDECESSOR_ATTEMPT_JOURNAL_FILE_SHA256 = (
    "sha256:320ea82608e78915906104977fda370f8cc6a7e1db9efe090d9228c4270c3adb"
)
RETRY_PREDECESSOR_ATTEMPT_EVENT_CHAIN_SHA256 = (
    "sha256:8d8ddd96aefbb48e24086ca84ca831f1657b39d760220015618c8628e66e740b"
)
RETRY_PREDECESSOR_INVOCATION_JOURNAL_FILE_SHA256 = (
    "sha256:64818ef9ecac914537fc085721e69fd419fd1930f23a91c0d170c32e0afeefd6"
)
RETRY_PREDECESSOR_RAW_STDOUT_SHA256 = (
    "sha256:11fa917940f8157b9f0d28678298426b51e538925fa79fcf530d59f284dc75eb"
)
RETRY_PREDECESSOR_RAW_STDERR_SHA256 = (
    "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)
RETRY_PREDECESSOR_OUTCOME_SHA256 = (
    "sha256:044d2c18c06845cc4689e3d5175284194bab16b4783ab259d7ed4283668a9fbd"
)
RETRY_PREDECESSOR_ERROR_MESSAGE_SHA256 = (
    "sha256:098e801ebc95c9c7312a945849442846324dcf639365a297313248993822711b"
)
RETRY_PREDECESSOR_CODE_REVISION = "af82c6fbb27be82a0ee8ccccb8a3a2f20bc0c5fa"

_PLAN_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-plan.json")
_SUMMARY_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-run/summary.json")
_JOURNAL_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-run/attempts.sqlite")
_AUDIT_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-integrity-audit.json")
_RELATION_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-publication-relation.json")
_PUBLISHABLE_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-publishable.json")
_REPORT_PATH = Path("artifacts/grounding-v5-d56-qwen-full-calibration-report.md")
_FAILED_LUNA_SMOKE_PLAN_PATH = Path(
    "artifacts/grounding-v5-d56-codex-cli-luna-one-call-smoke-plan.json"
)
_FAILED_LUNA_SMOKE_RUN_PATH = Path("artifacts/grounding-v5-d56-codex-cli-luna-one-call-smoke-run")
_RETRY_PREDECESSOR_PLAN_PATH = Path(
    "artifacts/grounding-v5-d56-codex-cli-luna-one-call-smoke-plan-v2.json"
)
_RETRY_PREDECESSOR_RUN_PATH = Path(
    "artifacts/grounding-v5-d56-codex-cli-luna-one-call-smoke-run-v2"
)


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


def validated_failed_luna_smoke_evidence(repository_root: Path) -> dict[str, Any]:
    """Verify the consumed first Luna smoke without rewriting its historical result."""

    plan_path = repository_root / _FAILED_LUNA_SMOKE_PLAN_PATH
    run_path = repository_root / _FAILED_LUNA_SMOKE_RUN_PATH
    summary_path = run_path / "summary.json"
    attempt_path = run_path / "attempts.sqlite"
    invocation_path = run_path / "codex-cli-invocations.sqlite"
    expected_files = (
        (plan_path, FAILED_LUNA_SMOKE_PLAN_FILE_SHA256, False),
        (summary_path, FAILED_LUNA_SMOKE_SUMMARY_FILE_SHA256, False),
        (attempt_path, FAILED_LUNA_SMOKE_ATTEMPT_JOURNAL_FILE_SHA256, True),
        (invocation_path, FAILED_LUNA_SMOKE_INVOCATION_JOURNAL_FILE_SHA256, True),
    )
    for path, expected, streaming in expected_files:
        actual = _streaming_file_digest(path) if streaming else _file_digest(path)
        if actual != expected:
            raise ValueError(f"frozen failed Luna smoke digest mismatch: {path.name}")
    plan = _load_json_object(plan_path)
    if content_digest(plan) != FAILED_LUNA_SMOKE_PLAN_CONTENT_SHA256:
        raise ValueError("frozen failed Luna smoke canonical plan digest mismatch")
    summary = _load_json_object(summary_path)
    expected_summary = {
        "approved_plan_sha256": FAILED_LUNA_SMOKE_PLAN_CONTENT_SHA256,
        "code_revision": FAILED_LUNA_SMOKE_CODE_REVISION,
        "provider_calls_made": 1,
        "provider_wire_requests": 1,
        "model_attempt_reservations": 1,
        "incremental_cost_equivalent_usd": "0.97920000",
        "budget_accounted_aggregate_spend_usd": "5.757364718",
        "unresolved_reservation_count": 1,
        "task_id": "v5-64ba7d452b3c8d3e43d1d30e",
        "policy_violation": (
            "completed_turn_usage_count_mismatch,cost_accounting_failure,"
            "final_agent_message_count_mismatch,nonzero_exit"
        ),
    }
    if any(summary.get(key) != expected for key, expected in expected_summary.items()):
        raise ValueError("frozen failed Luna smoke summary facts mismatch")
    episode = summary.get("episode_result")
    if not isinstance(episode, dict) or any(
        episode.get(key) != expected
        for key, expected in {
            "classification": "infrastructure_failure",
            "environment_actions": 0,
            "model_attempts": 1,
            "provider_wire_requests": 1,
            "success": False,
        }.items()
    ):
        raise ValueError("frozen failed Luna smoke episode facts mismatch")
    attempt_integrity = summary.get("attempt_journal_integrity")
    if not isinstance(attempt_integrity, dict) or (
        attempt_integrity.get("event_chain_digest") != FAILED_LUNA_SMOKE_ATTEMPT_EVENT_CHAIN_SHA256
    ):
        raise ValueError("frozen failed Luna smoke attempt event chain mismatch")
    invocation_integrity = summary.get("invocation_journal_integrity")
    records = (
        invocation_integrity.get("records") if isinstance(invocation_integrity, dict) else None
    )
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError("frozen failed Luna invocation integrity record is invalid")
    invocation_record = records[0]
    expected_invocation = {
        "exit_code": 1,
        "status": "policy_violation",
        "raw_stdout_sha256": FAILED_LUNA_SMOKE_RAW_STDOUT_SHA256,
        "raw_stderr_sha256": FAILED_LUNA_SMOKE_RAW_STDERR_SHA256,
        "outcome_sha256": FAILED_LUNA_SMOKE_OUTCOME_SHA256,
    }
    if any(invocation_record.get(key) != expected for key, expected in expected_invocation.items()):
        raise ValueError("frozen failed Luna invocation integrity facts mismatch")
    return {
        "status": "consumed_immutable_infrastructure_failure",
        "approved_plan_content_sha256": FAILED_LUNA_SMOKE_PLAN_CONTENT_SHA256,
        "approved_plan_file_sha256": FAILED_LUNA_SMOKE_PLAN_FILE_SHA256,
        "summary_file_sha256": FAILED_LUNA_SMOKE_SUMMARY_FILE_SHA256,
        "attempt_journal_file_sha256": FAILED_LUNA_SMOKE_ATTEMPT_JOURNAL_FILE_SHA256,
        "attempt_journal_event_chain_sha256": FAILED_LUNA_SMOKE_ATTEMPT_EVENT_CHAIN_SHA256,
        "invocation_journal_file_sha256": FAILED_LUNA_SMOKE_INVOCATION_JOURNAL_FILE_SHA256,
        "raw_stdout_sha256": FAILED_LUNA_SMOKE_RAW_STDOUT_SHA256,
        "raw_stderr_sha256": FAILED_LUNA_SMOKE_RAW_STDERR_SHA256,
        "outcome_sha256": FAILED_LUNA_SMOKE_OUTCOME_SHA256,
        "code_revision": FAILED_LUNA_SMOKE_CODE_REVISION,
        "task_id": expected_summary["task_id"],
        "provider_calls_made": 1,
        "environment_actions": 0,
        "classification": "infrastructure_failure",
        "root_cause": "strict_config_rejected_unknown_tools_view_image_key_before_inference",
        "replacement_fix": "remove_unknown_config_key_and_disable_view_image_feature",
        "historical_result_accounting": {
            "accounting_method": "superseded_conservative_list_price_reservation_v1",
            "incremental_cost_equivalent_usd": "0.97920000",
            "budget_accounted_aggregate_spend_usd": "5.757364718",
            "unresolved_reservation_count": 1,
        },
        "reuse_rule": "do_not_resume_overwrite_or_reuse_plan_or_run_directory",
    }


def validated_retry_predecessor_evidence(repository_root: Path) -> dict[str, Any]:
    """Verify the consumed v2 Luna smoke before planning one fresh invocation."""

    plan_path = repository_root / _RETRY_PREDECESSOR_PLAN_PATH
    run_path = repository_root / _RETRY_PREDECESSOR_RUN_PATH
    summary_path = run_path / "summary.json"
    attempt_path = run_path / "attempts.sqlite"
    invocation_path = run_path / "codex-cli-invocations.sqlite"
    expected_files = (
        (plan_path, RETRY_PREDECESSOR_PLAN_FILE_SHA256, False),
        (summary_path, RETRY_PREDECESSOR_SUMMARY_FILE_SHA256, False),
        (attempt_path, RETRY_PREDECESSOR_ATTEMPT_JOURNAL_FILE_SHA256, True),
        (invocation_path, RETRY_PREDECESSOR_INVOCATION_JOURNAL_FILE_SHA256, True),
    )
    for path, expected, streaming in expected_files:
        actual = _streaming_file_digest(path) if streaming else _file_digest(path)
        if actual != expected:
            raise ValueError(f"frozen retry predecessor digest mismatch: {path.name}")
    plan = _load_json_object(plan_path)
    if content_digest(plan) != RETRY_PREDECESSOR_PLAN_CONTENT_SHA256:
        raise ValueError("frozen retry predecessor canonical plan digest mismatch")
    summary = _load_json_object(summary_path)
    expected_summary = {
        "approved_plan_sha256": RETRY_PREDECESSOR_PLAN_CONTENT_SHA256,
        "code_revision": RETRY_PREDECESSOR_CODE_REVISION,
        "provider_calls_made": 1,
        "provider_wire_requests": 1,
        "model_attempt_reservations": 1,
        "incremental_luna_experiment_charge_usd": "0.00",
        "informational_list_price_equivalent_usd": "0.00426390",
        "budget_accounted_aggregate_spend_usd": "4.778164718",
        "unresolved_invocation_count": 0,
        "usage_telemetry_status": "available",
        "task_id": "v5-64ba7d452b3c8d3e43d1d30e",
        "policy_violation": "unauthorized_item:error",
    }
    if any(summary.get(key) != expected for key, expected in expected_summary.items()):
        raise ValueError("frozen retry predecessor summary facts mismatch")
    episode = summary.get("episode_result")
    if not isinstance(episode, dict) or any(
        episode.get(key) != expected
        for key, expected in {
            "classification": "invalid_output",
            "environment_actions": 0,
            "model_attempts": 1,
            "provider_wire_requests": 1,
            "success": False,
        }.items()
    ):
        raise ValueError("frozen retry predecessor episode facts mismatch")
    attempt_integrity = summary.get("attempt_journal_integrity")
    if not isinstance(attempt_integrity, dict) or (
        attempt_integrity.get("event_chain_digest") != RETRY_PREDECESSOR_ATTEMPT_EVENT_CHAIN_SHA256
    ):
        raise ValueError("frozen retry predecessor attempt event chain mismatch")
    invocation_integrity = summary.get("invocation_journal_integrity")
    records = (
        invocation_integrity.get("records") if isinstance(invocation_integrity, dict) else None
    )
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError("frozen retry predecessor invocation integrity record is invalid")
    expected_invocation = {
        "exit_code": 0,
        "status": "policy_violation",
        "raw_stdout_sha256": RETRY_PREDECESSOR_RAW_STDOUT_SHA256,
        "raw_stderr_sha256": RETRY_PREDECESSOR_RAW_STDERR_SHA256,
        "outcome_sha256": RETRY_PREDECESSOR_OUTCOME_SHA256,
    }
    if any(records[0].get(key) != expected for key, expected in expected_invocation.items()):
        raise ValueError("frozen retry predecessor invocation integrity facts mismatch")
    with sqlite3.connect(f"file:{invocation_path}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT raw_stdout FROM invocations").fetchone()
    if row is None or not isinstance(row[0], bytes):
        raise ValueError("frozen retry predecessor raw stream is unavailable")
    events = [json.loads(line) for line in row[0].decode("utf-8").splitlines() if line]
    event_shapes = [
        (
            event.get("type"),
            event.get("item", {}).get("type") if isinstance(event.get("item"), dict) else None,
        )
        for event in events
        if isinstance(event, dict)
    ]
    if event_shapes != [
        ("thread.started", None),
        ("item.completed", "error"),
        ("turn.started", None),
        ("item.completed", "agent_message"),
        ("turn.completed", None),
    ]:
        raise ValueError("frozen retry predecessor event sequence mismatch")
    error_item = events[1].get("item")
    if not isinstance(error_item, dict) or not isinstance(error_item.get("message"), str):
        raise TypeError("frozen retry predecessor error item is invalid")
    error_message_digest = (
        "sha256:" + hashlib.sha256(error_item["message"].encode("utf-8")).hexdigest()
    )
    if error_message_digest != RETRY_PREDECESSOR_ERROR_MESSAGE_SHA256:
        raise ValueError("frozen retry predecessor error message digest mismatch")
    return {
        "status": "consumed_immutable_invalid_output",
        "approved_plan_content_sha256": RETRY_PREDECESSOR_PLAN_CONTENT_SHA256,
        "approved_plan_file_sha256": RETRY_PREDECESSOR_PLAN_FILE_SHA256,
        "summary_file_sha256": RETRY_PREDECESSOR_SUMMARY_FILE_SHA256,
        "attempt_journal_file_sha256": RETRY_PREDECESSOR_ATTEMPT_JOURNAL_FILE_SHA256,
        "attempt_journal_event_chain_sha256": RETRY_PREDECESSOR_ATTEMPT_EVENT_CHAIN_SHA256,
        "invocation_journal_file_sha256": RETRY_PREDECESSOR_INVOCATION_JOURNAL_FILE_SHA256,
        "raw_stdout_sha256": RETRY_PREDECESSOR_RAW_STDOUT_SHA256,
        "raw_stderr_sha256": RETRY_PREDECESSOR_RAW_STDERR_SHA256,
        "outcome_sha256": RETRY_PREDECESSOR_OUTCOME_SHA256,
        "error_message_sha256": RETRY_PREDECESSOR_ERROR_MESSAGE_SHA256,
        "code_revision": RETRY_PREDECESSOR_CODE_REVISION,
        "task_id": expected_summary["task_id"],
        "provider_calls_made": 1,
        "environment_actions": 0,
        "classification": "invalid_output",
        "policy_violation": "unauthorized_item:error",
        "event_sequence": [
            "thread.started",
            "item.completed:error",
            "turn.started",
            "item.completed:agent_message",
            "turn.completed",
        ],
        "experiment_charge_usd": "0.00",
        "usage_telemetry_status": "available",
        "diagnosis": "unresolved_pre_turn_cli_error_item_with_later_agent_message",
        "reuse_rule": "do_not_resume_overwrite_or_reuse_plan_or_run_directory",
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
        "subscription_source_url": SUBSCRIPTION_SOURCE,
        "informational_list_price_source_url": PRICE_SOURCE,
        "observed_at_utc": PRICE_OBSERVED_AT_UTC,
        "authentication_mode": AUTH_MODE,
        "billing_classification": "ChatGPT_subscription_billed",
        "human_approved_experiment_rule": (
            "treat_gpt_5_6_luna_calls_as_zero_dollars_and_exclude_them_from_the_usage_cap"
        ),
        "accounting_method": "luna_chatgpt_subscription_experiment_charge_zero_v1",
        "currency": "USD",
        "luna_experiment_charge_per_call_usd": str(LUNA_EXPERIMENT_CHARGE_USD),
        "missing_or_invalid_usage_telemetry_rule": (
            "record_unavailable_nonblocking_and_preserve_action_validation"
        ),
        "usage_telemetry_purpose": "optional_non_billing_diagnostic_only",
        "standard_long_context_input_rate_usd_per_unit": "0.00000040",
        "standard_long_context_cached_input_rate_usd_per_unit": "0.00000004",
        "standard_long_context_cache_write_rate_usd_per_unit": "0.00000050",
        "standard_long_context_output_rate_usd_per_unit": "0.00000180",
        "conservative_input_rate_usd_per_unit": str(CONSERVATIVE_INPUT_PER_TOKEN_USD),
        "conservative_output_rate_usd_per_unit": str(CONSERVATIVE_OUTPUT_PER_TOKEN_USD),
        "reasoning_output_double_counted": True,
        "usage_telemetry_source": "codex_jsonl_turn.completed.usage_when_present",
        "per_invocation_maximum_informational_list_price_equivalent_usd": str(
            REQUEST_MAXIMUM_INFORMATIONAL_LIST_PRICE_EQUIVALENT_USD
        ),
    }


def _failure_classifications() -> dict[str, str]:
    return {
        "success_termination": "normal_terminal_continue_in_calibration",
        "step_limit_truncation": "normal_terminal_continue_in_calibration",
        "invalid_output": "retain_and_stop_without_retry",
        "request_failure": "retain_and_stop_without_retry",
        "infrastructure_failure": "retain_raw_evidence_and_stop",
        "tool_or_unauthorized_observation": "invalid_output_retain_and_stop",
        "timeout_or_interruption": "terminate_process_group_retain_and_stop",
        "missing_or_invalid_usage_telemetry": "record_nonblocking_for_subscription_billed_luna",
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
    failed_luna_smoke = validated_failed_luna_smoke_evidence(repository_root)
    retry_predecessor = validated_retry_predecessor_evidence(repository_root)
    revision = _git(repository_root, "rev-parse", "HEAD")
    task = generate_task(SMOKE_SEED)
    if task.seed_record.partition is not Partition.DEVELOPMENT:
        raise ValueError("Codex CLI smoke may use a development task only")
    aggregate_upper_bound = PRIOR_BUDGET_ACCOUNTED_SPEND_USD + LUNA_EXPERIMENT_CHARGE_USD
    plan = {
        "schema_version": SMOKE_PLAN_SCHEMA_VERSION,
        "purpose": (
            "one development-only Codex CLI action validating image input, isolation, "
            "schema-constrained action output, journaling, and subscription-exempt Luna "
            "accounting; one fresh human-requested invocation after two immutable failed "
            "smokes; not calibration evidence"
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
            "prior_non_luna_budget_accounted_spend_usd": str(PRIOR_BUDGET_ACCOUNTED_SPEND_USD),
            "maximum_remaining_non_luna_incremental_exposure_usd": str(
                MAXIMUM_REMAINING_INCREMENTAL_EXPOSURE_USD
            ),
            "luna_experiment_charge_per_call_usd": str(LUNA_EXPERIMENT_CHARGE_USD),
            "maximum_luna_incremental_experiment_charge_usd": str(LUNA_EXPERIMENT_CHARGE_USD),
            "per_invocation_maximum_informational_list_price_equivalent_usd": str(
                REQUEST_MAXIMUM_INFORMATIONAL_LIST_PRICE_EQUIVALENT_USD
            ),
            "aggregate_theoretical_upper_bound_usd": str(aggregate_upper_bound),
            "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
        },
        "price_and_cost_accounting": _pricing_record(),
        "failure_classifications": _failure_classifications(),
        "predecessor_evidence": predecessor,
        "failed_luna_smoke_evidence": failed_luna_smoke,
        "retry_predecessor_evidence": retry_predecessor,
        "retry_context": {
            "human_requested_fresh_attempt": True,
            "transport_retry": False,
            "luna_smoke_process_ordinal": 3,
            "fresh_process_invocation_cap": 1,
            "parser_policy_changed": False,
            "unresolved_error_item_will_remain_fail_closed": True,
        },
        "human_accounting_override": {
            "approved_scope": "gpt-5.6-luna_calls_in_this_experiment",
            "effective_experiment_charge_usd": str(LUNA_EXPERIMENT_CHARGE_USD),
            "rationale": "subscription_billed",
            "historical_raw_result_rewritten": False,
            "superseded_failed_smoke_reservation_excluded_from_current_aggregate": True,
            "service_subscription_limits_unchanged": True,
        },
        "raw_evidence_policy": _raw_evidence_policy(),
        "stop_conditions": [
            "send at most one fresh Codex CLI model invocation and never retry it",
            "stop after the first valid environment action",
            "stop without dispatch on invalid or unparseable action output",
            "stop without dispatch on any JSONL tool, web, filesystem, MCP, plugin, skill, connector, or subagent event",
            "terminate the whole process group on timeout or interruption and retain restricted raw evidence",
            "stop if CLI version, ChatGPT authentication mode, model catalog identity, model, reasoning effort, command contract, or predecessor evidence differs",
            "record JSONL usage as optional non-billing telemetry when present; missing or invalid usage telemetry does not invalidate an otherwise valid action",
            "stop if the ChatGPT authentication-mode preflight fails or the process exits nonzero",
            "do not resume, overwrite, or reuse the consumed Qwen/OpenRouter plan or journals",
            "do not resume, overwrite, or reuse the consumed first Luna smoke plan or run directory",
            "do not resume, overwrite, or reuse the consumed v2 Luna smoke plan or run directory",
        ],
        "approval_required": {
            "owner": "human",
            "scope": "this_exact_one_task_smoke_plan_only",
            "exact_plan_sha256": "sha256 of canonical plan bytes",
            "earlier_qwen_or_openrouter_approvals_apply": False,
            "earlier_luna_smoke_approval_applies": False,
            "v2_luna_smoke_approval_applies": False,
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
        "incremental_luna_experiment_charge_usd",
        "usage_telemetry_status",
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
            "incremental_luna_experiment_charge_usd": str(LUNA_EXPERIMENT_CHARGE_USD),
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
    if value.get("usage_telemetry_status") not in {
        "available",
        "unavailable",
        "invalid_or_ambiguous",
    }:
        raise ValueError("smoke evidence usage telemetry status is invalid")
    accounted = Decimal(str(value["budget_accounted_aggregate_spend_usd"]))
    if accounted != PRIOR_BUDGET_ACCOUNTED_SPEND_USD:
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
    failed_luna_smoke = validated_failed_luna_smoke_evidence(repository_root)
    retry_predecessor = validated_retry_predecessor_evidence(repository_root)
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
            "maximum_remaining_non_luna_incremental_exposure_usd": str(
                MAXIMUM_AGGREGATE_SPEND_USD - prior
            ),
            "luna_experiment_charge_per_call_usd": str(LUNA_EXPERIMENT_CHARGE_USD),
            "maximum_luna_incremental_experiment_charge_usd": str(LUNA_EXPERIMENT_CHARGE_USD),
            "per_invocation_maximum_informational_list_price_equivalent_usd": str(
                REQUEST_MAXIMUM_INFORMATIONAL_LIST_PRICE_EQUIVALENT_USD
            ),
            "uncapped_run_informational_list_price_equivalent_ceiling_usd": str(
                REQUEST_MAXIMUM_INFORMATIONAL_LIST_PRICE_EQUIVALENT_USD * action_cap
            ),
            "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
            "enforcement": (
                "charge every Luna invocation exactly zero experiment dollars; enforce "
                "task, action, process, model-attempt, and logical-wire caps independently"
            ),
        },
        "price_and_cost_accounting": _pricing_record(),
        "failure_classifications": _failure_classifications(),
        "predecessor_evidence": predecessor,
        "failed_luna_smoke_evidence": failed_luna_smoke,
        "retry_predecessor_evidence": retry_predecessor,
        "successful_smoke_evidence": smoke,
        "raw_evidence_policy": _raw_evidence_policy(),
        "stop_conditions": [
            "run tasks only in the frozen fifty-task order",
            "continue after success termination or full step-limit truncation",
            "stop at the first invalid output, policy violation, request failure, infrastructure failure, timeout, interruption, identity mismatch, or evidence mismatch",
            "never retry or replay a Codex CLI process invocation",
            "record JSONL usage as optional non-billing telemetry when present; missing or invalid usage telemetry is nonblocking",
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
    ledger = SubscriptionExemptLedger(
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
        usage_telemetry_status = (
            str(transport_records[-1].get("usage_telemetry_status")) if transport_records else None
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
            "prior_non_luna_budget_accounted_spend_usd": str(PRIOR_BUDGET_ACCOUNTED_SPEND_USD),
            "incremental_luna_experiment_charge_usd": str(ledger.incremental_experiment_charge_usd),
            "informational_list_price_equivalent_usd": str(
                ledger.incremental_informational_list_price_equivalent_usd
            ),
            "budget_accounted_aggregate_spend_usd": str(ledger.budget_accounted_usd),
            "remaining_non_luna_aggregate_exposure_usd": str(
                MAXIMUM_AGGREGATE_SPEND_USD - ledger.budget_accounted_usd
            ),
            "maximum_aggregate_spend_usd": str(MAXIMUM_AGGREGATE_SPEND_USD),
            "unresolved_invocation_count": len(ledger.unresolved),
            "usage_telemetry_unavailable_count": len(ledger.usage_telemetry_unavailable),
            "usage_telemetry_status": usage_telemetry_status,
            "cost_accounting_method": "luna_chatgpt_subscription_experiment_charge_zero_v1",
            "task_id": plan["task"]["task_id"],
            "episode_result": result_record,
            "execution_error": execution_error,
            "policy_violation": policy_violation,
            "transport_records": transport_records,
            "attempt_journal_integrity": attempt_integrity,
            "invocation_journal_integrity": invocation_integrity,
            "predecessor_evidence": plan["predecessor_evidence"],
            "failed_luna_smoke_evidence": plan["failed_luna_smoke_evidence"],
            "retry_predecessor_evidence": plan["retry_predecessor_evidence"],
            "retry_context": plan["retry_context"],
            "human_accounting_override": plan["human_accounting_override"],
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
            "incremental_luna_experiment_charge_usd": summary.get(
                "incremental_luna_experiment_charge_usd"
            ),
            "usage_telemetry_status": summary.get("usage_telemetry_status"),
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
