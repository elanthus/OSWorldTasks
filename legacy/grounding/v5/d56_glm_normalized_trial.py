"""Plan and execute one GLM normalized-coordinate candidate trial."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from legacy.grounding.v5.d56_bcd_calibration import _streaming_file_digest
from legacy.grounding.v5.d56_calibration import _file_digest, _git
from legacy.grounding.v5.d56_spend import (
    campaign_spend_fields,
    combine_spend_disclosures,
    ledger_spend_disclosure,
    legacy_campaign_spend_disclosure,
    legacy_summary_spend_disclosure,
    phase_spend_fields,
)
from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps, Partition, content_digest
from pixelgym.grounding.v5.diagnostics import maximum_stage_index as summarize_maximum_stage_index
from pixelgym.grounding.v5.evidence import repository_relative_path
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import (
    BOUNDED_RETRY_STOP_RULE,
    GLM_STATEFUL_CANDIDATE,
    PANEL_MAXIMUM_SPEND_USD,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.runner import V5Runner

PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-glm-normalized-trial-plan-v2"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-glm-normalized-trial-result-v2"
TRIAL_SEED = 5010
PRICE_OBSERVED_AT_UTC = "2026-08-27T20:19:30Z"
EXECUTION_FROZEN = True

FROZEN_LLAMA_TRIAL_PLAN_SHA256 = (
    "sha256:1e87da05960de2b1ae44926f2d83b34c7e47e6a2871e1e22d293859036ff7d15"
)
FROZEN_LLAMA_TRIAL_SUMMARY_SHA256 = (
    "sha256:7e3c8778a7e35fd48ef81db9fb56a70fdd45c8f71f7736e3b1e961076cd1fd66"
)
FROZEN_LLAMA_TRIAL_JOURNAL_SHA256 = (
    "sha256:1742f25baca698807f33407d1b046083f4731828796ce8b707f6374b71ec648e"
)
FROZEN_LLAMA_TRIAL_CODE_REVISION = "b34d7d3ba7163c09b23dc60d06bc8978a49690f2"
FROZEN_LLAMA_TRIAL_ACTUAL_SPEND_USD = Decimal("2.487339457")
FROZEN_LLAMA_TRIAL_JOURNAL_INTEGRITY = {
    "schema_version": "pixelgym-agent-v5-journal-integrity-v1",
    "object_count": 123,
    "event_count": 136,
    "event_chain_digest": (
        "sha256:b68efcdfe838eeb04141e64bb9735af015fa5e8b11aa3e06340e07ff8de24db9"
    ),
}
FROZEN_LLAMA_TRIAL_TERMINAL_IDENTITY = AttemptIdentity(
    "d56-c-normalized-trial-v5-bf8d93604c1ba0da75b32ca8", 19, 0
)


def _validated_frozen_llama_trial_evidence(repository_root: Path, output_directory: Path) -> dict[str, Any]:
    summary_path = output_directory / "summary.json"
    journal_path = output_directory / "attempts.sqlite"
    if _file_digest(summary_path) != FROZEN_LLAMA_TRIAL_SUMMARY_SHA256:
        raise ValueError("frozen normalized Llama trial summary digest mismatch")
    if _streaming_file_digest(journal_path) != FROZEN_LLAMA_TRIAL_JOURNAL_SHA256:
        raise ValueError("frozen normalized Llama trial journal digest mismatch")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    expected_fields = {
        "schema_version": "pixelgym-agent-v5-d56-c-normalized-trial-result-v1",
        "purpose": (
            "one complete development-only Slot C episode to test the normalized coordinate "
            "adapter; not calibration evidence"
        ),
        "approved_plan_sha256": FROZEN_LLAMA_TRIAL_PLAN_SHA256,
        "code_revision": FROZEN_LLAMA_TRIAL_CODE_REVISION,
        "provider_calls_made": 20,
        "provider_wire_requests": 20,
        "model_attempt_reservations": 20,
        "provider_control_requests": 0,
        "prior_aggregate_spend_usd": "2.481019457",
        "actual_aggregate_spend_usd": str(FROZEN_LLAMA_TRIAL_ACTUAL_SPEND_USD),
        "trial_incremental_spend_usd": "0.006320000",
        "remaining_aggregate_spend_usd": "7.512660543",
        "maximum_aggregate_spend_usd": "10.00",
        "assigned_policy_task_pairs": 1,
        "attempted_policy_task_pairs": 1,
        "successful_policy_task_pairs": 0,
        "semantic_progress": {
            "first_transition_completed": True,
            "maximum_stage_index_observed": 1,
            "diagnostic_event_counts": {
                "correct_transition": 1,
                "text_input_focused": 18,
            },
        },
        "execution_error": None,
        "journal_integrity": FROZEN_LLAMA_TRIAL_JOURNAL_INTEGRITY,
        "cleanup": {
            "journal_closed": True,
            "policy_and_environments_closed": True,
        },
    }
    if not isinstance(summary, dict) or any(
        summary.get(key) != value for key, value in expected_fields.items()
    ):
        raise ValueError("frozen normalized Llama trial summary facts mismatch")
    episode_result = summary.get("episode_result")
    if episode_result != {
        "classification": "infrastructure_failure",
        "environment_actions": 19,
        "final_policy_checkpoint_digest": (
            "sha256:d16e535fe2ad504b0227fb97128a33ff533765806de449ed5626762d37ed9fb6"
        ),
        "model_attempts": 20,
        "provider_control_requests": 0,
        "provider_wire_requests": 20,
        "slot": "C-llama-stateful",
        "success": False,
        "task_id": "v5-bf8d93604c1ba0da75b32ca8",
        "trial_id": FROZEN_LLAMA_TRIAL_TERMINAL_IDENTITY.trial_id,
    }:
        raise ValueError("frozen normalized Llama trial assignment mismatch")
    native_evidence = summary.get("frozen_native_c_evidence")
    if not isinstance(native_evidence, dict) or (
        native_evidence.get("actual_aggregate_spend_usd") != "2.481019457"
        or native_evidence.get("summary_sha256")
        != "sha256:73cfd90ae6594a4d826520be3aa971ba049554c5d69196111edb672a78f0da0f"
        or native_evidence.get("journal_sha256")
        != "sha256:d67a8c9197e3d2d54f62fd28002e7c9e054c0e5cdb3872b1042ff76537272cf6"
    ):
        raise ValueError("frozen normalized Llama trial predecessor mismatch")
    transport_records = summary.get("transport_records")
    if not isinstance(transport_records, list) or len(transport_records) != 20:
        raise ValueError("frozen normalized Llama trial transport evidence mismatch")
    if any(
        record.get("status") != "response"
        or record.get("upstream_provider") != "DeepInfra"
        or record.get("response_model") != "meta-llama/llama-4-scout"
        for record in transport_records[:-1]
    ):
        raise ValueError("frozen normalized Llama trial response route mismatch")
    terminal_transport = transport_records[-1]
    if (
        terminal_transport.get("status") != "unknown"
        or terminal_transport.get("failure_code") != "HTTPError"
        or terminal_transport.get("http_status") != 429
        or terminal_transport.get("provider_error_code") != 429
        or terminal_transport.get("upstream_provider") != "DeepInfra"
    ):
        raise ValueError("frozen normalized Llama trial terminal transport mismatch")
    journal = V5AttemptJournal(journal_path)
    try:
        terminal_event = journal.terminal_attempt(FROZEN_LLAMA_TRIAL_TERMINAL_IDENTITY)
        if (
            terminal_event is None
            or terminal_event.kind != "unknown_outcome_infrastructure_failure"
            or terminal_event.payload.get("failure_code") != "provider_request_unknown"
            or terminal_event.payload.get("response_digest") is not None
            or terminal_event.payload.get("usage") != {}
        ):
            raise ValueError("frozen normalized Llama trial terminal journal mismatch")
        if journal.call_counts() != (20, 0):
            raise ValueError("frozen normalized Llama trial journal counts mismatch")
    finally:
        journal.close()
    phase_spend = legacy_summary_spend_disclosure(summary)
    campaign_spend = legacy_campaign_spend_disclosure(summary)
    return {
        "approved_plan_sha256": FROZEN_LLAMA_TRIAL_PLAN_SHA256,
        "code_revision": FROZEN_LLAMA_TRIAL_CODE_REVISION,
        "summary_path": repository_relative_path(repository_root, summary_path),
        "summary_sha256": FROZEN_LLAMA_TRIAL_SUMMARY_SHA256,
        "journal_path": repository_relative_path(repository_root, journal_path),
        "journal_sha256": FROZEN_LLAMA_TRIAL_JOURNAL_SHA256,
        "actual_aggregate_spend_usd": str(FROZEN_LLAMA_TRIAL_ACTUAL_SPEND_USD),
        "phase_spend": phase_spend,
        "campaign_spend": campaign_spend,
        "remaining_aggregate_spend_usd": str(
            PANEL_MAXIMUM_SPEND_USD - FROZEN_LLAMA_TRIAL_ACTUAL_SPEND_USD
        ),
        "attempted_policy_task_pairs": 1,
        "successful_policy_task_pairs": 0,
        "semantic_progress": summary["semantic_progress"],
        "terminal": {
            "classification": terminal_event.kind,
            "failure_code": terminal_event.payload["failure_code"],
            "http_status": 429,
            "provider_error_code": 429,
            "trial_id": FROZEN_LLAMA_TRIAL_TERMINAL_IDENTITY.trial_id,
            "step_index": FROZEN_LLAMA_TRIAL_TERMINAL_IDENTITY.step_index,
            "attempt_index": FROZEN_LLAMA_TRIAL_TERMINAL_IDENTITY.attempt_index,
            "request_outcome": "unknown",
            "retry_eligible": False,
        },
        "journal_integrity": FROZEN_LLAMA_TRIAL_JOURNAL_INTEGRITY,
    }


def build_plan(
    repository_root: Path,
    *,
    frozen_llama_trial_output_directory: Path,
) -> dict[str, Any]:
    revision = _git(repository_root, "rev-parse", "HEAD")
    frozen_llama_evidence = _validated_frozen_llama_trial_evidence(
        repository_root, frozen_llama_trial_output_directory
    )
    prior_campaign_spend = frozen_llama_evidence["campaign_spend"]
    task = generate_task(TRIAL_SEED)
    if task.seed_record.partition is not Partition.DEVELOPMENT:
        raise ValueError("GLM candidate trial may use a development task only")
    config = GLM_STATEFUL_CANDIDATE
    manifest = build_panel_policy_manifest(repository_root, config=config, code_revision=revision)
    action_cap = task.max_episode_steps
    attempt_cap = action_cap * manifest.max_model_attempts_per_action
    theoretical_maximum = config.request_maximum_usd * attempt_cap
    if theoretical_maximum > PANEL_MAXIMUM_SPEND_USD:
        raise ValueError("GLM candidate trial theoretical maximum exceeds its run cap")
    policy_record = {
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
        "model_capabilities": {
            "input_modalities": ["text", "image", "video"],
            "output_modalities": ["text"],
            "structured_response_parameter": True,
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
    }
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": (
            "one complete development-only GLM candidate episode using the normalized coordinate "
            "adapter; comparative diagnostic evidence, not calibration evidence"
        ),
        "provider_calls_made": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "assigned_policy_task_pairs": 1,
        "policy": policy_record,
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
            "maximum_run_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "remaining_run_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "prior_campaign_spend": prior_campaign_spend,
            "spend_lineage": (
                "per-run enforcement: this phase starts at zero; predecessor spend is "
                "carried only as campaign disclosure"
            ),
            "trial_theoretical_maximum_usd": str(theoretical_maximum),
            "run_theoretical_upper_bound_usd": str(theoretical_maximum),
            "enforcement": (
                "before each wire request, reserve the GLM candidate's worst-case request cost "
                "against this phase's ledger; stop before a request that cannot fit"
            ),
        },
        "frozen_normalized_llama_trial_evidence": frozen_llama_evidence,
        "stop_rules": [
            "run exactly one complete development-partition episode and no calibration or confirmatory task",
            "do not resume, retry, replace, or reinterpret any frozen Llama request or assignment",
            BOUNDED_RETRY_STOP_RULE,
            "retain both attempts and stop after a repeated retryable provider error",
            "stop after the first other transport, identity, cost, parse, adapter, invalid-action, or evidence-integrity failure",
            "stop before any request whose per-request theoretical maximum cannot fit under this phase's ten-dollar ledger",
            "do not retry a parse, action, unknown-outcome, or other provider failure",
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
    frozen_llama_trial_output_directory: Path,
    output_directory: Path,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved GLM candidate trial digest does not match the plan")
    if EXECUTION_FROZEN:
        raise ValueError("strict-schema GLM trial is frozen; use the relaxed-schema successor")
    if plan != build_plan(
        repository_root,
        frozen_llama_trial_output_directory=frozen_llama_trial_output_directory,
    ):
        raise ValueError("GLM candidate trial does not match canonical configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before provider requests")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace GLM candidate output: {output_directory}")
    output_directory.mkdir(parents=True)
    journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    maximum_spend = Decimal(plan["caps"]["maximum_run_spend_usd"])
    ledger = SpendLedger(maximum_spend, Decimal(0))
    approved_caps = CallCaps(
        plan["caps"]["environment_action_cap"],
        plan["caps"]["model_attempt_cap"],
        plan["caps"]["provider_control_request_cap"],
        plan["caps"]["provider_wire_request_cap"],
    )
    transport: OpenRouterPanelTransport | None = None
    result_record: dict[str, Any] | None = None
    trial_id = f"d56-glm-normalized-trial-{plan['task']['task_id']}"
    execution_error: dict[str, str] | None = None
    try:
        config = GLM_STATEFUL_CANDIDATE
        manifest = build_panel_policy_manifest(
            repository_root, config=config, code_revision=plan["code_revision"]
        )
        if manifest.to_dict() != plan["policy"]["policy_manifest"]:
            raise ValueError("runtime policy manifest differs from approved GLM trial plan")
        task = generate_task(plan["task"]["seed"])
        if task.task_id != plan["task"]["task_id"]:
            raise ValueError("generated development task differs from approved GLM trial plan")
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
        diagnostic_counts = Counter(str(diagnostic.get("event")) for diagnostic in diagnostics)
        maximum_stage_index = summarize_maximum_stage_index(diagnostics)
        transport_records = [] if transport is None else list(transport.records)
        integrity = journal.integrity_report()
        call_counts = journal.call_counts()
        journal.close()
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
            "trial_incremental_spend_usd": phase_spend["known_spend_usd"],
            "remaining_run_spend_usd": str(
                maximum_spend - Decimal(phase_spend["budget_accounted_spend_usd"])
            ),
            "maximum_run_spend_usd": str(maximum_spend),
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
            "frozen_normalized_llama_trial_evidence": plan[
                "frozen_normalized_llama_trial_evidence"
            ],
            "journal_integrity": integrity,
            "publication_status": "restricted_raw_responses_in_local_journal",
            "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
        }
        (output_directory / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return summary
