"""Plan and execute one GLM Novita FP8 JSON-mode smoke request."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps, Partition, content_digest
from pixelgym.grounding.v5.d56_bcd_calibration import _streaming_file_digest
from pixelgym.grounding.v5.d56_calibration import _file_digest, _git
from pixelgym.grounding.v5.d56_spend import (
    campaign_spend_fields,
    combine_spend_disclosures,
    ledger_spend_disclosure,
    legacy_campaign_spend_disclosure,
    legacy_summary_spend_disclosure,
    phase_spend_fields,
)
from pixelgym.grounding.v5.diagnostics import maximum_stage_index as summarize_maximum_stage_index
from pixelgym.grounding.v5.evidence import repository_relative_path
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import (
    GLM_STATEFUL_JSON_OBJECT_SMOKE_CANDIDATE,
    PANEL_MAXIMUM_SPEND_USD,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.runner import V5Runner

PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-glm-json-object-smoke-plan-v2"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-glm-json-object-smoke-result-v2"
SMOKE_SEED = 5010
ENDPOINT_METADATA_OBSERVED_AT_UTC = "2026-08-28T00:08:42Z"

FROZEN_RELAXED_GLM_PLAN_SHA256 = (
    "sha256:fcdf25ff8627a5305ab5e02830daf6c63d45ad071c327ff14af5e6cceca7ebd9"
)
FROZEN_RELAXED_GLM_SUMMARY_SHA256 = (
    "sha256:3045e479de15ff9ca887950d8e9f612f1e593ff6bea7203dc23282466736df6b"
)
FROZEN_RELAXED_GLM_JOURNAL_SHA256 = (
    "sha256:1077c5015396b02be32fb3453acca66aa507f236b5824e80638a6efe1242c244"
)
FROZEN_RELAXED_GLM_CODE_REVISION = "98bfc434553f9c9eb1714c164dd0014e1f3a0f83"
FROZEN_RELAXED_GLM_ACTUAL_SPEND_USD = Decimal("2.487339457")
FROZEN_RELAXED_GLM_JOURNAL_INTEGRITY = {
    "schema_version": "pixelgym-agent-v5-journal-integrity-v1",
    "object_count": 5,
    "event_count": 3,
    "event_chain_digest": (
        "sha256:75edc10fd82795bfc2068a78dae97e6bc07d9339976f6925344c51a367ef72a3"
    ),
}
FROZEN_RELAXED_GLM_TERMINAL_IDENTITY = AttemptIdentity(
    "d56-glm-relaxed-trial-v5-bf8d93604c1ba0da75b32ca8", 0, 0
)


def _validated_frozen_relaxed_glm_evidence(repository_root: Path, output_directory: Path) -> dict[str, Any]:
    summary_path = output_directory / "summary.json"
    journal_path = output_directory / "attempts.sqlite"
    if _file_digest(summary_path) != FROZEN_RELAXED_GLM_SUMMARY_SHA256:
        raise ValueError("frozen relaxed GLM summary digest mismatch")
    if _streaming_file_digest(journal_path) != FROZEN_RELAXED_GLM_JOURNAL_SHA256:
        raise ValueError("frozen relaxed GLM journal digest mismatch")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    expected_fields = {
        "schema_version": "pixelgym-agent-v5-d56-glm-relaxed-trial-result-v1",
        "approved_plan_sha256": FROZEN_RELAXED_GLM_PLAN_SHA256,
        "code_revision": FROZEN_RELAXED_GLM_CODE_REVISION,
        "provider_calls_made": 1,
        "provider_wire_requests": 1,
        "model_attempt_reservations": 1,
        "provider_control_requests": 0,
        "actual_aggregate_spend_usd": str(FROZEN_RELAXED_GLM_ACTUAL_SPEND_USD),
        "trial_incremental_spend_usd": "0E-9",
        "remaining_aggregate_spend_usd": "7.512660543",
        "successful_policy_task_pairs": 0,
        "journal_integrity": FROZEN_RELAXED_GLM_JOURNAL_INTEGRITY,
        "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
    }
    if not isinstance(summary, dict) or any(
        summary.get(key) != value for key, value in expected_fields.items()
    ):
        raise ValueError("frozen relaxed GLM summary facts mismatch")
    transport_records = summary.get("transport_records")
    if not isinstance(transport_records, list) or len(transport_records) != 1:
        raise ValueError("frozen relaxed GLM transport evidence mismatch")
    terminal_transport = transport_records[0]
    if any(
        terminal_transport.get(key) != value
        for key, value in {
            "status": "unknown",
            "failure_code": "HTTPError",
            "http_status": 404,
            "provider_error_code": 404,
            "error_body_bytes_read": 201,
            "error_body_prefix_digest": (
                "sha256:680fd4832f47938dd9f75c25939ba46a92474efe69b643b0bfafbe087c195042"
            ),
            "error_body_truncated": False,
        }.items()
    ):
        raise ValueError("frozen relaxed GLM terminal transport mismatch")
    journal = V5AttemptJournal(journal_path)
    try:
        terminal_event = journal.terminal_attempt(FROZEN_RELAXED_GLM_TERMINAL_IDENTITY)
        if (
            terminal_event is None
            or terminal_event.kind != "unknown_outcome_infrastructure_failure"
            or terminal_event.payload.get("failure_code") != "provider_request_unknown"
            or terminal_event.payload.get("response_digest") is not None
            or terminal_event.payload.get("usage") != {}
            or journal.call_counts() != (1, 0)
        ):
            raise ValueError("frozen relaxed GLM terminal journal mismatch")
    finally:
        journal.close()
    phase_spend = legacy_summary_spend_disclosure(summary)
    campaign_spend = legacy_campaign_spend_disclosure(summary)
    return {
        "approved_plan_sha256": FROZEN_RELAXED_GLM_PLAN_SHA256,
        "code_revision": FROZEN_RELAXED_GLM_CODE_REVISION,
        "summary_path": repository_relative_path(repository_root, summary_path),
        "summary_sha256": FROZEN_RELAXED_GLM_SUMMARY_SHA256,
        "journal_path": repository_relative_path(repository_root, journal_path),
        "journal_sha256": FROZEN_RELAXED_GLM_JOURNAL_SHA256,
        "actual_aggregate_spend_usd": str(FROZEN_RELAXED_GLM_ACTUAL_SPEND_USD),
        "phase_spend": phase_spend,
        "campaign_spend": campaign_spend,
        "terminal": {
            "classification": terminal_event.kind,
            "failure_code": terminal_event.payload["failure_code"],
            "http_status": 404,
            "provider_error_code": 404,
            "request_outcome": "unknown",
            "retry_eligible": False,
        },
        "journal_integrity": FROZEN_RELAXED_GLM_JOURNAL_INTEGRITY,
    }


def build_plan(
    repository_root: Path, *, frozen_relaxed_glm_trial_output_directory: Path
) -> dict[str, Any]:
    revision = _git(repository_root, "rev-parse", "HEAD")
    frozen_evidence = _validated_frozen_relaxed_glm_evidence(
        repository_root, frozen_relaxed_glm_trial_output_directory
    )
    prior_campaign_spend = frozen_evidence["campaign_spend"]
    task = generate_task(SMOKE_SEED)
    if task.seed_record.partition is not Partition.DEVELOPMENT:
        raise ValueError("GLM JSON-object smoke may use a development task only")
    config = GLM_STATEFUL_JSON_OBJECT_SMOKE_CANDIDATE
    manifest = build_panel_policy_manifest(repository_root, config=config, code_revision=revision)
    theoretical_maximum = config.request_maximum_usd
    if theoretical_maximum > PANEL_MAXIMUM_SPEND_USD:
        raise ValueError("GLM JSON-object smoke theoretical maximum exceeds its run cap")
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": (
            "one development-only request testing exact Novita FP8 routing with OpenRouter "
            "JSON mode and the unchanged local exact-action parser; not calibration evidence"
        ),
        "provider_calls_made": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
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
                "router_metadata": "enabled",
            },
            "response_validation": {
                "upstream_response_format": "json_object",
                "upstream_json_schema": False,
                "local_exact_action_parser": True,
                "invalid_or_unparseable_output_rule": "retain_and_fail_closed_without_retry",
            },
            "live_endpoint_record": {
                "source_url": config.price_source,
                "observed_at_utc": ENDPOINT_METADATA_OBSERVED_AT_UTC,
                "provider_name": "Novita",
                "tag": "novita/fp8",
                "quantization": "fp8",
                "status": 0,
                "supports_response_format": True,
                "supports_structured_outputs": False,
                "prompt_per_token_usd": str(config.prompt_price_per_token_usd),
                "completion_per_token_usd": str(config.completion_price_per_token_usd),
            },
        },
        "task": {
            "partition": task.seed_record.partition.value,
            "seed": task.seed,
            "task_id": task.task_id,
            "family": task.seed_record.family.value,
            "variant": task.seed_record.variant,
            "action_limit": 1,
        },
        "caps": {
            "environment_action_cap": 1,
            "model_attempt_cap": 1,
            "provider_control_request_cap": 0,
            "provider_wire_request_cap": 1,
            "prior_campaign_spend": prior_campaign_spend,
            "per_request_theoretical_maximum_usd": str(theoretical_maximum),
            "maximum_run_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "remaining_run_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "run_theoretical_upper_bound_usd": str(theoretical_maximum),
            "spend_lineage": (
                "per-run enforcement: this phase starts at zero; predecessor spend is "
                "carried only as campaign disclosure"
            ),
        },
        "frozen_relaxed_glm_trial_evidence": frozen_evidence,
        "stop_rules": [
            "send exactly one request on one development task and do not retry",
            "allow only the novita/fp8 endpoint and no fallback provider",
            "stop after the first transport, identity, cost, parse, adapter, invalid-action, or evidence-integrity failure",
            "retain and do not retry any invalid or unparseable model output",
            "stop before the request if its theoretical maximum cannot fit under this phase's ten-dollar ledger",
        ],
        "approval_required": {
            "owner": "human",
            "exact_plan_sha256": "sha256 of canonical plan bytes",
        },
    }


def plan_digest(plan: dict[str, Any]) -> str:
    return content_digest(plan)


def execute_smoke(
    repository_root: Path,
    *,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    frozen_relaxed_glm_trial_output_directory: Path,
    output_directory: Path,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved GLM JSON-object smoke digest does not match the plan")
    if plan != build_plan(
        repository_root,
        frozen_relaxed_glm_trial_output_directory=frozen_relaxed_glm_trial_output_directory,
    ):
        raise ValueError("GLM JSON-object smoke does not match canonical configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before provider requests")
    if output_directory.exists():
        raise FileExistsError(
            f"refusing to replace GLM JSON-object smoke output: {output_directory}"
        )
    output_directory.mkdir(parents=True)
    journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    maximum_spend = Decimal(plan["caps"]["maximum_run_spend_usd"])
    ledger = SpendLedger(maximum_spend, Decimal(0))
    approved_caps = CallCaps(1, 1, 0, 1)
    transport: OpenRouterPanelTransport | None = None
    result_record: dict[str, Any] | None = None
    execution_error: dict[str, str] | None = None
    trial_id = f"d56-glm-json-object-smoke-{plan['task']['task_id']}"
    try:
        config = GLM_STATEFUL_JSON_OBJECT_SMOKE_CANDIDATE
        manifest = build_panel_policy_manifest(
            repository_root, config=config, code_revision=plan["code_revision"]
        )
        if manifest.to_dict() != plan["policy"]["policy_manifest"]:
            raise ValueError("runtime policy manifest differs from approved GLM smoke plan")
        task = generate_task(plan["task"]["seed"])
        if task.task_id != plan["task"]["task_id"]:
            raise ValueError("generated task differs from approved GLM smoke plan")
        transport = OpenRouterPanelTransport(config, ledger=ledger)
        result = V5Runner(
            journal=journal,
            manifest=manifest,
            transport=transport,
            policy=OpenRouterPanelPolicy(config),
            approved_caps=approved_caps,
        ).run(trial_id=trial_id, task=task, action_limit=1)
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
        maximum_stage_index = summarize_maximum_stage_index(diagnostics)
        transport_records = [] if transport is None else list(transport.records)
        integrity = journal.integrity_report()
        call_counts = journal.call_counts()
        journal.close()
        smoke_reached_model_response = any(
            event.kind == "canonical_response_persisted" for event in events
        )
        phase_spend = ledger_spend_disclosure(ledger)
        prior_campaign_spend = plan["caps"]["prior_campaign_spend"]
        campaign_spend = combine_spend_disclosures(
            (prior_campaign_spend, phase_spend)
        )
        summary = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "purpose": plan["purpose"],
            "approved_plan_sha256": digest,
            "code_revision": plan["code_revision"],
            "provider_calls_made": ledger.wire_requests_sent,
            "provider_wire_requests": ledger.wire_requests_sent,
            "model_attempt_reservations": call_counts[0],
            "provider_control_requests": call_counts[1],
            "unknown_charge_outcomes": ledger.unknown_charge_outcomes,
            **phase_spend_fields(phase_spend),
            "prior_campaign_spend": prior_campaign_spend,
            **campaign_spend_fields(campaign_spend),
            "actual_aggregate_spend_usd": campaign_spend["known_spend_usd"],
            "smoke_incremental_spend_usd": phase_spend["known_spend_usd"],
            "remaining_run_spend_usd": str(
                maximum_spend - Decimal(phase_spend["budget_accounted_spend_usd"])
            ),
            "maximum_run_spend_usd": str(maximum_spend),
            "reached_model_response": smoke_reached_model_response,
            "episode_result": result_record,
            "semantic_progress": {
                "first_transition_completed": maximum_stage_index >= 1,
                "maximum_stage_index_observed": maximum_stage_index,
                "diagnostic_event_counts": dict(sorted(diagnostic_counts.items())),
            },
            "execution_error": execution_error,
            "transport_records": transport_records,
            "frozen_relaxed_glm_trial_evidence": plan["frozen_relaxed_glm_trial_evidence"],
            "journal_integrity": integrity,
            "publication_status": "restricted_raw_responses_in_local_journal",
            "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
        }
        (output_directory / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return summary
