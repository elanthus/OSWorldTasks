"""Plan and execute the relaxed-schema successor to the frozen GLM trial."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps, Partition, content_digest
from pixelgym.grounding.v5.d56_bcd_calibration import _streaming_file_digest
from pixelgym.grounding.v5.d56_calibration import _file_digest, _git
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import (
    GLM_STATEFUL_RELAXED_SCHEMA_CANDIDATE,
    PANEL_MAXIMUM_SPEND_USD,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.runner import V5Runner

PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-glm-relaxed-trial-plan-v1"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-glm-relaxed-trial-result-v1"
TRIAL_SEED = 5010
PRICE_OBSERVED_AT_UTC = "2026-08-27T23:11:32Z"

FROZEN_STRICT_GLM_PLAN_SHA256 = (
    "sha256:e05780e1ba5487b024c8b3a76ff00ceae3d5cda7c28cb051d59777044e5f9f62"
)
FROZEN_STRICT_GLM_SUMMARY_SHA256 = (
    "sha256:884650f5a35b7767eb195de549857235d9aa0092d03ab9e7528627d66f8f823d"
)
FROZEN_STRICT_GLM_JOURNAL_SHA256 = (
    "sha256:639592ffa11973550702eafabd69e7f8e6b94f973d6c30f42933ef60524f7294"
)
FROZEN_STRICT_GLM_CODE_REVISION = "dd9709e4db535316246cfd618e0f1abdc13a3d8c"
FROZEN_STRICT_GLM_ACTUAL_SPEND_USD = Decimal("2.487339457")
FROZEN_STRICT_GLM_JOURNAL_INTEGRITY = {
    "schema_version": "pixelgym-agent-v5-journal-integrity-v1",
    "object_count": 5,
    "event_count": 3,
    "event_chain_digest": (
        "sha256:c14ddbf6c1e6f9d5dc67854d33e3eeb855f565a931552a4a413e53fced245de1"
    ),
}
FROZEN_STRICT_GLM_TERMINAL_IDENTITY = AttemptIdentity(
    "d56-glm-normalized-trial-v5-bf8d93604c1ba0da75b32ca8", 0, 0
)


def _validated_frozen_strict_glm_evidence(output_directory: Path) -> dict[str, Any]:
    summary_path = output_directory / "summary.json"
    journal_path = output_directory / "attempts.sqlite"
    if _file_digest(summary_path) != FROZEN_STRICT_GLM_SUMMARY_SHA256:
        raise ValueError("frozen strict GLM summary digest mismatch")
    if _streaming_file_digest(journal_path) != FROZEN_STRICT_GLM_JOURNAL_SHA256:
        raise ValueError("frozen strict GLM journal digest mismatch")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    expected_fields = {
        "schema_version": "pixelgym-agent-v5-d56-glm-normalized-trial-result-v1",
        "purpose": (
            "one complete development-only GLM candidate episode using the normalized "
            "coordinate adapter; comparative diagnostic evidence, not calibration evidence"
        ),
        "approved_plan_sha256": FROZEN_STRICT_GLM_PLAN_SHA256,
        "code_revision": FROZEN_STRICT_GLM_CODE_REVISION,
        "provider_calls_made": 1,
        "provider_wire_requests": 1,
        "model_attempt_reservations": 1,
        "provider_control_requests": 0,
        "prior_aggregate_spend_usd": "2.487339457",
        "actual_aggregate_spend_usd": str(FROZEN_STRICT_GLM_ACTUAL_SPEND_USD),
        "trial_incremental_spend_usd": "0E-9",
        "remaining_aggregate_spend_usd": "7.512660543",
        "maximum_aggregate_spend_usd": "10.00",
        "assigned_policy_task_pairs": 1,
        "attempted_policy_task_pairs": 1,
        "successful_policy_task_pairs": 0,
        "semantic_progress": {
            "first_transition_completed": False,
            "maximum_stage_index_observed": 0,
            "diagnostic_event_counts": {},
        },
        "execution_error": None,
        "journal_integrity": FROZEN_STRICT_GLM_JOURNAL_INTEGRITY,
        "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
    }
    if not isinstance(summary, dict) or any(
        summary.get(key) != value for key, value in expected_fields.items()
    ):
        raise ValueError("frozen strict GLM summary facts mismatch")
    if summary.get("episode_result") != {
        "classification": "infrastructure_failure",
        "environment_actions": 0,
        "final_policy_checkpoint_digest": (
            "sha256:63e9da03831c87ca45e2c7b7bbc730fa262decdf49943ad3b6032b212e7348c1"
        ),
        "model_attempts": 1,
        "provider_control_requests": 0,
        "provider_wire_requests": 1,
        "slot": "C-glm-stateful-candidate",
        "success": False,
        "task_id": "v5-bf8d93604c1ba0da75b32ca8",
        "trial_id": FROZEN_STRICT_GLM_TERMINAL_IDENTITY.trial_id,
    }:
        raise ValueError("frozen strict GLM assignment mismatch")
    transport_records = summary.get("transport_records")
    if not isinstance(transport_records, list) or len(transport_records) != 1:
        raise ValueError("frozen strict GLM transport evidence mismatch")
    terminal_transport = transport_records[0]
    expected_transport = {
        "status": "unknown",
        "failure_code": "HTTPError",
        "http_status": 404,
        "provider_error_code": 404,
        "error_body_bytes_read": 201,
        "error_body_prefix_digest": (
            "sha256:680fd4832f47938dd9f75c25939ba46a92474efe69b643b0bfafbe087c195042"
        ),
        "error_body_truncated": False,
    }
    if any(terminal_transport.get(key) != value for key, value in expected_transport.items()):
        raise ValueError("frozen strict GLM terminal transport mismatch")
    journal = V5AttemptJournal(journal_path)
    try:
        terminal_event = journal.terminal_attempt(FROZEN_STRICT_GLM_TERMINAL_IDENTITY)
        if (
            terminal_event is None
            or terminal_event.kind != "unknown_outcome_infrastructure_failure"
            or terminal_event.payload.get("failure_code") != "provider_request_unknown"
            or terminal_event.payload.get("response_digest") is not None
            or terminal_event.payload.get("usage") != {}
        ):
            raise ValueError("frozen strict GLM terminal journal mismatch")
        if journal.call_counts() != (1, 0):
            raise ValueError("frozen strict GLM journal counts mismatch")
    finally:
        journal.close()
    return {
        "approved_plan_sha256": FROZEN_STRICT_GLM_PLAN_SHA256,
        "code_revision": FROZEN_STRICT_GLM_CODE_REVISION,
        "summary_path": str(summary_path),
        "summary_sha256": FROZEN_STRICT_GLM_SUMMARY_SHA256,
        "journal_path": str(journal_path),
        "journal_sha256": FROZEN_STRICT_GLM_JOURNAL_SHA256,
        "actual_aggregate_spend_usd": str(FROZEN_STRICT_GLM_ACTUAL_SPEND_USD),
        "remaining_aggregate_spend_usd": str(
            PANEL_MAXIMUM_SPEND_USD - FROZEN_STRICT_GLM_ACTUAL_SPEND_USD
        ),
        "attempted_policy_task_pairs": 1,
        "successful_policy_task_pairs": 0,
        "semantic_progress": summary["semantic_progress"],
        "terminal": {
            "classification": terminal_event.kind,
            "failure_code": terminal_event.payload["failure_code"],
            "http_status": 404,
            "provider_error_code": 404,
            "trial_id": FROZEN_STRICT_GLM_TERMINAL_IDENTITY.trial_id,
            "step_index": FROZEN_STRICT_GLM_TERMINAL_IDENTITY.step_index,
            "attempt_index": FROZEN_STRICT_GLM_TERMINAL_IDENTITY.attempt_index,
            "request_outcome": "unknown",
            "retry_eligible": False,
        },
        "journal_integrity": FROZEN_STRICT_GLM_JOURNAL_INTEGRITY,
    }


def build_plan(
    repository_root: Path,
    *,
    frozen_strict_glm_trial_output_directory: Path,
) -> dict[str, Any]:
    revision = _git(repository_root, "rev-parse", "HEAD")
    frozen_evidence = _validated_frozen_strict_glm_evidence(
        frozen_strict_glm_trial_output_directory
    )
    prior_spend = Decimal(frozen_evidence["actual_aggregate_spend_usd"])
    task = generate_task(TRIAL_SEED)
    if task.seed_record.partition is not Partition.DEVELOPMENT:
        raise ValueError("relaxed-schema GLM trial may use a development task only")
    config = GLM_STATEFUL_RELAXED_SCHEMA_CANDIDATE
    manifest = build_panel_policy_manifest(repository_root, config=config, code_revision=revision)
    action_cap = task.max_episode_steps
    attempt_cap = action_cap * manifest.max_model_attempts_per_action
    theoretical_maximum = config.request_maximum_usd * attempt_cap
    aggregate_upper_bound = prior_spend + theoretical_maximum
    if aggregate_upper_bound > PANEL_MAXIMUM_SPEND_USD:
        raise ValueError("relaxed-schema GLM trial theoretical maximum exceeds shared cap")
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": (
            "one complete development-only GLM candidate episode with upstream strict JSON "
            "schema disabled and the local exact-action parser unchanged; comparative diagnostic "
            "evidence, not calibration evidence"
        ),
        "provider_calls_made": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "assigned_policy_task_pairs": 1,
        "policy": {
            "slot": config.slot,
            "policy_manifest": manifest.to_dict(),
            "policy_manifest_digest": content_digest(manifest.to_dict()),
            "provider": {
                "name": "openrouter",
                "upstream_provider": config.response_provider,
                "only": [config.provider_route],
                "quantizations": list(config.quantizations),
                "allow_fallbacks": False,
                "automatic_retries": False,
                "data_collection": "deny",
                "require_parameters": True,
            },
            "response_validation": {
                "upstream_json_schema_strict": False,
                "local_exact_action_parser": True,
                "invalid_or_unparseable_output_rule": "retain_and_fail_closed_without_retry",
            },
            "model_capabilities": {
                "input_modalities": ["text", "image", "video"],
                "output_modalities": ["text"],
                "response_format_parameter": True,
                "strict_structured_outputs": False,
                "seed_parameter": True,
            },
            "price_record": {
                "source_url": config.price_source,
                "observed_at_utc": PRICE_OBSERVED_AT_UTC,
                "currency": "USD",
                "prompt_per_token": str(config.prompt_price_per_token_usd),
                "completion_per_token": str(config.completion_price_per_token_usd),
                "image_input_billing": "provider input tokens at prompt_per_token",
                "max_prompt_tokens": 126_976,
                "max_output_tokens": 4_096,
                "per_request_theoretical_maximum_usd": str(config.request_maximum_usd),
                "unknown_usage_or_price_rule": "fail_closed",
            },
        },
        "task": {
            "partition": task.seed_record.partition.value,
            "seed": task.seed,
            "task_id": task.task_id,
            "family": task.seed_record.family.value,
            "variant": task.seed_record.variant,
            "max_episode_steps": task.max_episode_steps,
        },
        "caps": {
            **CallCaps(action_cap, attempt_cap, 0, attempt_cap).to_dict(),
            "maximum_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "prior_aggregate_spend_usd": str(prior_spend),
            "remaining_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD - prior_spend),
            "trial_theoretical_maximum_usd": str(theoretical_maximum),
            "aggregate_theoretical_upper_bound_usd": str(aggregate_upper_bound),
            "enforcement": (
                "before each wire request, reserve the relaxed-schema GLM candidate's worst-case "
                "request cost against the shared aggregate ledger; stop before a request that "
                "cannot fit"
            ),
        },
        "frozen_strict_glm_trial_evidence": frozen_evidence,
        "stop_rules": [
            "run exactly one complete development-partition episode and no calibration or confirmatory task",
            "do not resume, retry, replace, or reinterpret the frozen strict-schema GLM request or assignment",
            "retry once on the same Novita route only after a canonical zero-token, zero-cost, empty response with finish_reason error",
            "retain both attempts and stop after a repeated retryable provider error",
            "stop after the first other transport, identity, cost, parse, adapter, invalid-action, or evidence-integrity failure",
            "stop before any request whose per-request theoretical maximum cannot fit under the shared ten-dollar ledger",
            "retain and do not retry any invalid or unparseable model output",
        ],
        "approval_required": {
            "owner": "human",
            "exact_plan_sha256": "sha256 of canonical plan bytes",
        },
    }


def plan_digest(plan: dict[str, Any]) -> str:
    return content_digest(plan)


def execute_trial(
    repository_root: Path,
    *,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    frozen_strict_glm_trial_output_directory: Path,
    output_directory: Path,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved relaxed-schema GLM trial digest does not match the plan")
    if plan != build_plan(
        repository_root,
        frozen_strict_glm_trial_output_directory=frozen_strict_glm_trial_output_directory,
    ):
        raise ValueError("relaxed-schema GLM trial does not match canonical configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before provider requests")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace relaxed-schema GLM output: {output_directory}")
    output_directory.mkdir(parents=True)
    journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    prior_spend = Decimal(plan["caps"]["prior_aggregate_spend_usd"])
    ledger = SpendLedger(PANEL_MAXIMUM_SPEND_USD, prior_spend)
    approved_caps = CallCaps(
        plan["caps"]["environment_action_cap"],
        plan["caps"]["model_attempt_cap"],
        plan["caps"]["provider_control_request_cap"],
        plan["caps"]["provider_wire_request_cap"],
    )
    transport: OpenRouterPanelTransport | None = None
    result_record: dict[str, Any] | None = None
    trial_id = f"d56-glm-relaxed-trial-{plan['task']['task_id']}"
    execution_error: dict[str, str] | None = None
    try:
        config = GLM_STATEFUL_RELAXED_SCHEMA_CANDIDATE
        manifest = build_panel_policy_manifest(
            repository_root, config=config, code_revision=plan["code_revision"]
        )
        if manifest.to_dict() != plan["policy"]["policy_manifest"]:
            raise ValueError("runtime policy manifest differs from approved relaxed GLM plan")
        task = generate_task(plan["task"]["seed"])
        if task.task_id != plan["task"]["task_id"]:
            raise ValueError("generated task differs from approved relaxed GLM plan")
        transport = OpenRouterPanelTransport(config, ledger=ledger)
        result = V5Runner(
            journal=journal,
            manifest=manifest,
            transport=transport,
            policy=OpenRouterPanelPolicy(config),
            approved_caps=approved_caps,
        ).run(trial_id=trial_id, task=task)
        result_record = {"slot": config.slot, **result.to_dict()}
    except Exception as exc:
        execution_error = {"type": type(exc).__name__}
        raise
    finally:
        events = journal.events(trial_id)
        diagnostics = [
            event.payload["diagnostic"]
            for event in events
            if event.kind == "dispatch_committed"
            and isinstance(event.payload.get("diagnostic"), dict)
        ]
        diagnostic_counts = Counter(str(item.get("event")) for item in diagnostics)
        maximum_stage_index = max((int(item["stage_index"]) for item in diagnostics), default=0)
        transport_records = [] if transport is None else list(transport.records)
        integrity = journal.integrity_report()
        call_counts = journal.call_counts()
        journal.close()
        summary = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "purpose": plan["purpose"],
            "approved_plan_sha256": digest,
            "code_revision": plan["code_revision"],
            "provider_calls_made": ledger.wire_requests_sent,
            "provider_wire_requests": ledger.wire_requests_sent,
            "model_attempt_reservations": call_counts[0],
            "provider_control_requests": call_counts[1],
            "prior_aggregate_spend_usd": str(prior_spend),
            "actual_aggregate_spend_usd": str(ledger.spent_usd),
            "trial_incremental_spend_usd": str(ledger.spent_usd - prior_spend),
            "remaining_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD - ledger.spent_usd),
            "maximum_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "assigned_policy_task_pairs": 1,
            "attempted_policy_task_pairs": int(result_record is not None),
            "successful_policy_task_pairs": int(
                result_record is not None and result_record["success"]
            ),
            "episode_result": result_record,
            "semantic_progress": {
                "first_transition_completed": maximum_stage_index >= 1,
                "maximum_stage_index_observed": maximum_stage_index,
                "diagnostic_event_counts": dict(sorted(diagnostic_counts.items())),
            },
            "execution_error": execution_error,
            "transport_records": transport_records,
            "frozen_strict_glm_trial_evidence": plan["frozen_strict_glm_trial_evidence"],
            "journal_integrity": integrity,
            "publication_status": "restricted_raw_responses_in_local_journal",
            "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
        }
        (output_directory / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return summary
