"""Plan and execute a Qwen3-VL successor after the frozen 429 outcome."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.d56_bcd_calibration import _streaming_file_digest
from pixelgym.grounding.v5.d56_c_calibration import _validated_frozen_bcd_evidence
from pixelgym.grounding.v5.d56_calibration import (
    CALIBRATION_MANIFEST,
    EXPECTED_TASK_COUNT,
    NORMAL_TERMINAL_CLASSIFICATIONS,
    _calibration_manifest,
    _file_digest,
    _git,
    _validated_smoke_evidence,
)
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import (
    BOUNDED_RETRY_STOP_RULE,
    PANEL_MAXIMUM_SPEND_USD,
    QWEN_STATEFUL_RETRY_SUCCESSOR,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.planning import call_cap_plan, load_partition_manifests
from pixelgym.grounding.v5.runner import V5Runner

PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-qwen-full-calibration-plan-v1"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-qwen-full-calibration-result-v1"

INTEGRITY_AUDIT_PATH = Path(
    "artifacts/grounding-v5-d56-gemini-full-calibration-integrity-audit.json"
)
PUBLICATION_RELATION_PATH = Path(
    "artifacts/grounding-v5-d56-gemini-full-calibration-publication-relation.json"
)
FROZEN_GEMINI_PLAN_SHA256 = (
    "sha256:68469a8057cbfa4e380f40e7bb27f2264c49b638aa5951ff7ac93866e8dc1e38"
)
FROZEN_GEMINI_SUMMARY_SHA256 = (
    "sha256:69e542daef2084c2cc8329453a56132699469dd3e712cc13a7885d1b2811d29c"
)
FROZEN_GEMINI_JOURNAL_SHA256 = (
    "sha256:99d6520bc24ab6dc2515d70d4977aed19602dfa6fa87f0e4102d61c583557c82"
)
FROZEN_GEMINI_AUDIT_SHA256 = (
    "sha256:26c6ba1fa0d02d1a35bc4a6b987303995869230ed0d50711118274a2225ce6ac"
)
FROZEN_GEMINI_RELATION_SHA256 = (
    "sha256:7e45a0db2654affb93aa25197ee38600a03bb0b2010e625c6116637d0259a793"
)
FROZEN_GEMINI_EVENT_CHAIN_SHA256 = (
    "sha256:9c21971b4ac2878aef796a9a6fd8afa878a173f0f7095aeea986d51960458134"
)
FROZEN_GEMINI_CODE_REVISION = "4d319ac850c870b9eee55772fb35b95746c673c9"
FROZEN_GEMINI_ACTUAL_SPEND_USD = Decimal("4.552765957")
FROZEN_GEMINI_UNKNOWN_REQUEST_RESERVATION_USD = Decimal("0.099532800")
CONSERVATIVE_PRIOR_SPEND_USD = (
    FROZEN_GEMINI_ACTUAL_SPEND_USD + FROZEN_GEMINI_UNKNOWN_REQUEST_RESERVATION_USD
)
ENDPOINT_METADATA_OBSERVED_AT_UTC = "2026-08-28T13:34:08Z"


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object: {path}")
    return value


def _validated_latest_spend_evidence(
    repository_root: Path,
    *,
    frozen_gemini_output_directory: Path,
) -> dict[str, Any]:
    """Bind the latest audited aggregate spend without opening the raw journal.

    The Gemini calibration this binds to has been withdrawn and its evidence removed from
    the repository, so this function — and therefore ``build_plan`` — cannot run until that
    slot is re-run and a new predecessor is approved. The frozen digests below are retained
    deliberately: they still document which evidence the completed Qwen run was bound to.
    """

    audit_path = repository_root / INTEGRITY_AUDIT_PATH
    relation_path = repository_root / PUBLICATION_RELATION_PATH
    summary_path = frozen_gemini_output_directory / "summary.json"
    journal_path = frozen_gemini_output_directory / "attempts.sqlite"
    if _file_digest(audit_path) != FROZEN_GEMINI_AUDIT_SHA256:
        raise ValueError("frozen Gemini integrity-audit digest mismatch")
    if _file_digest(relation_path) != FROZEN_GEMINI_RELATION_SHA256:
        raise ValueError("frozen Gemini publication-relation digest mismatch")
    if _file_digest(summary_path) != FROZEN_GEMINI_SUMMARY_SHA256:
        raise ValueError("frozen Gemini summary digest mismatch")
    if _streaming_file_digest(journal_path) != FROZEN_GEMINI_JOURNAL_SHA256:
        raise ValueError("frozen Gemini restricted-journal digest mismatch")

    relation = _load_json_object(relation_path)
    if relation.get("authoritative") != {
        "approved_plan_content_sha256": FROZEN_GEMINI_PLAN_SHA256,
        "integrity_audit_file_sha256": FROZEN_GEMINI_AUDIT_SHA256,
        "journal_event_chain_sha256": FROZEN_GEMINI_EVENT_CHAIN_SHA256,
        "restricted_attempt_journal_file_sha256": FROZEN_GEMINI_JOURNAL_SHA256,
        "run_summary_file_sha256": FROZEN_GEMINI_SUMMARY_SHA256,
    }:
        raise ValueError("frozen Gemini publication relation is not authoritative")

    audit = _load_json_object(audit_path)
    if (
        audit.get("provider_calls_made") != 0
        or audit.get("result")
        != {
            "checks_failed": 0,
            "checks_verified": 8,
            "milestone_gate_verdict": "not_evaluated_human_owned",
            "provider_calls_made": 0,
        }
        or any(check.get("verified") is not True for check in audit.get("checks", []))
    ):
        raise ValueError("frozen Gemini integrity audit is not fully verified")
    spend_check = next(
        (
            check
            for check in audit["checks"]
            if check.get("name") == "provider_identity_calls_unknown_outcome_and_spend"
        ),
        None,
    )
    if not isinstance(spend_check, dict) or any(
        spend_check.get(key) != value
        for key, value in {
            "actual_aggregate_spend_usd": str(FROZEN_GEMINI_ACTUAL_SPEND_USD),
            "remaining_aggregate_spend_usd": "5.447234043",
            "provider_wire_requests": 1068,
            "unknown_provider_outcomes": 1,
            "verified": True,
        }.items()
    ):
        raise ValueError("frozen Gemini spend audit facts mismatch")

    summary = _load_json_object(summary_path)
    expected_summary = {
        "schema_version": "pixelgym-agent-v5-d56-gemini-full-calibration-result-v1",
        "approved_plan_sha256": FROZEN_GEMINI_PLAN_SHA256,
        "code_revision": FROZEN_GEMINI_CODE_REVISION,
        "provider_calls_made": 1068,
        "provider_wire_requests": 1068,
        "prior_aggregate_spend_usd": "2.488646332",
        "calibration_incremental_spend_usd": "2.064119625",
        "actual_aggregate_spend_usd": str(FROZEN_GEMINI_ACTUAL_SPEND_USD),
        "remaining_aggregate_spend_usd": "5.447234043",
        "assigned_policy_task_pairs": 50,
        "attempted_policy_task_pairs": 41,
        "successful_policy_task_pairs": 29,
        "completed_all_assigned_pairs": False,
        "classifications": {
            "infrastructure_failure": 1,
            "step_limit_truncation": 11,
            "success_termination": 29,
        },
    }
    if any(summary.get(key) != value for key, value in expected_summary.items()):
        raise ValueError("frozen Gemini summary facts mismatch")
    integrity = summary.get("journal_integrity")
    if not isinstance(integrity, dict) or (
        integrity.get("event_chain_digest") != FROZEN_GEMINI_EVENT_CHAIN_SHA256
    ):
        raise ValueError("frozen Gemini journal integrity mismatch")
    return {
        "approved_plan_sha256": FROZEN_GEMINI_PLAN_SHA256,
        "code_revision": FROZEN_GEMINI_CODE_REVISION,
        "summary_path": summary_path.resolve().relative_to(repository_root.resolve()).as_posix(),
        "summary_sha256": FROZEN_GEMINI_SUMMARY_SHA256,
        "journal_path": journal_path.resolve().relative_to(repository_root.resolve()).as_posix(),
        "journal_sha256": FROZEN_GEMINI_JOURNAL_SHA256,
        "journal_event_chain_sha256": FROZEN_GEMINI_EVENT_CHAIN_SHA256,
        "integrity_audit_path": INTEGRITY_AUDIT_PATH.as_posix(),
        "integrity_audit_sha256": FROZEN_GEMINI_AUDIT_SHA256,
        "publication_relation_path": PUBLICATION_RELATION_PATH.as_posix(),
        "publication_relation_sha256": FROZEN_GEMINI_RELATION_SHA256,
        "actual_aggregate_spend_usd": str(FROZEN_GEMINI_ACTUAL_SPEND_USD),
        "remaining_aggregate_spend_usd": "5.447234043",
        "unknown_provider_outcomes": 1,
        "unknown_request_reservation_usd": str(FROZEN_GEMINI_UNKNOWN_REQUEST_RESERVATION_USD),
        "conservative_aggregate_spend_usd": str(CONSERVATIVE_PRIOR_SPEND_USD),
        "journal_integrity": integrity,
    }


def build_plan(
    repository_root: Path,
    *,
    smoke_output_directory: Path,
    frozen_bcd_output_directory: Path,
    frozen_gemini_output_directory: Path,
) -> dict[str, Any]:
    revision = _git(repository_root, "rev-parse", "HEAD")
    partition = _calibration_manifest(repository_root)
    smoke_evidence = _validated_smoke_evidence(repository_root, smoke_output_directory)
    qwen_predecessor = _validated_frozen_bcd_evidence(
        repository_root, frozen_bcd_output_directory
    )
    latest_spend = _validated_latest_spend_evidence(
        repository_root,
        frozen_gemini_output_directory=frozen_gemini_output_directory,
    )
    known_prior_spend = Decimal(latest_spend["actual_aggregate_spend_usd"])
    prior_spend = Decimal(latest_spend["conservative_aggregate_spend_usd"])
    if Decimal(qwen_predecessor["actual_aggregate_spend_usd"]) > known_prior_spend:
        raise ValueError("Qwen predecessor spend exceeds the latest audited aggregate")
    action_cap = sum(record["max_episode_steps"] for record in partition["records"])
    config = QWEN_STATEFUL_RETRY_SUCCESSOR
    manifest = build_panel_policy_manifest(repository_root, config=config, code_revision=revision)
    partition_manifests = load_partition_manifests(
        repository_root / "artifacts/grounding-v5-manifests",
        calibration_manifest=repository_root / CALIBRATION_MANIFEST,
    )
    phase_call_cap_plan = call_cap_plan(
        manifest,
        partition_manifests=partition_manifests,
        approved_calibration_manifest_digest=partition["manifest_digest"],
    )
    attempt_cap = action_cap * manifest.max_model_attempts_per_action
    theoretical_maximum = config.request_maximum_usd * attempt_cap
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": (
            "evaluate the Qwen3-VL stateful retry successor on all fifty frozen D5.6 "
            "calibration tasks as a distinct run"
        ),
        "provider_calls_made": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "assigned_policy_task_pairs": EXPECTED_TASK_COUNT,
        "calibration_partition": {
            "path": CALIBRATION_MANIFEST.as_posix(),
            "file_sha256": _file_digest(repository_root / CALIBRATION_MANIFEST),
            "manifest_digest": partition["manifest_digest"],
            "source_manifest_digest": partition["derivation"]["source_manifest_digest"],
            "pilot_plan_digest": partition["derivation"]["pilot_plan_digest"],
            "episode_count": EXPECTED_TASK_COUNT,
            "action_cap": action_cap,
            "exclusion_rule": partition["derivation"]["exclusion_rule"],
            "excluded_seeds": partition["derivation"]["excluded_seeds"],
            "replacement_seeds": partition["derivation"]["replacement_seeds"],
        },
        "policy": {
            "slot": config.slot,
            "policy_manifest": manifest.to_dict(),
            "policy_manifest_digest": content_digest(manifest.to_dict()),
            "phase_call_cap_plan": phase_call_cap_plan,
            "provider": {
                "name": "openrouter",
                "upstream_provider": config.response_provider,
                "only": [config.provider_route],
                "allow_fallbacks": False,
                "automatic_retries": False,
                "data_collection": "deny",
                "require_parameters": True,
            },
            "price_record": {
                "source_url": config.price_source,
                "observed_at_utc": ENDPOINT_METADATA_OBSERVED_AT_UTC,
                "currency": "USD",
                "prompt_per_token": str(config.prompt_price_per_token_usd),
                "completion_per_token": str(config.completion_price_per_token_usd),
                "image_input_billing": "provider input tokens at prompt_per_token",
                "max_prompt_tokens": 126_976,
                "max_output_tokens": 4_096,
                "endpoint_context_length": 131_072,
                "endpoint_tag": config.provider_route,
                "endpoint_status": 0,
                "supports_response_format": True,
                "supports_structured_outputs": True,
                "unknown_usage_or_price_rule": "fail_closed",
            },
        },
        "caps": {
            **CallCaps(action_cap, attempt_cap, 0, attempt_cap).to_dict(),
            "maximum_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "known_prior_aggregate_spend_usd": str(known_prior_spend),
            "unknown_prior_charge_reservation_usd": str(
                FROZEN_GEMINI_UNKNOWN_REQUEST_RESERVATION_USD
            ),
            "prior_aggregate_spend_usd": str(prior_spend),
            "remaining_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD - prior_spend),
            "per_request_theoretical_maximum_usd": str(config.request_maximum_usd),
            "uncapped_run_theoretical_maximum_usd": str(theoretical_maximum),
            "enforcement": (
                "before each wire request, reserve the pinned Alibaba endpoint's worst-case "
                "request cost against the shared aggregate ledger; stop before a request "
                "that cannot fit"
            ),
        },
        "successful_smoke_evidence": smoke_evidence,
        "frozen_qwen_429_predecessor": qwen_predecessor,
        "latest_audited_spend_evidence": latest_spend,
        "task_order": [
            {
                "ordinal": ordinal,
                "seed": record["seed_record"]["seed"],
                "task_id": record["task_id"],
                "family": record["seed_record"]["family"],
                "max_episode_steps": record["max_episode_steps"],
            }
            for ordinal, record in enumerate(partition["records"])
        ],
        "stop_rules": [
            "run all fifty tasks in frozen manifest order",
            "continue after success termination or step-limit truncation so assigned tasks remain in the denominator",
            BOUNDED_RETRY_STOP_RULE,
            "retain every invalid or unparseable model output and fail closed",
            "stop after the first other transport, identity, cost, parse, adapter, invalid-action, or evidence-integrity failure",
            "stop before any request whose per-request theoretical maximum cannot fit under the shared ten-dollar ledger",
            "do not resume or replay the frozen Qwen 429 request; this is a distinct successor run",
            "do not resume, retry, replace, or reinterpret any frozen Gemini request or assignment",
            "do not expose confirmatory tasks",
        ],
        "approval_required": {
            "owner": "human",
            "exact_plan_sha256": "sha256 of canonical plan bytes",
        },
    }


def plan_digest(plan: dict[str, Any]) -> str:
    return content_digest(plan)


def execute_calibration(
    repository_root: Path,
    *,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    smoke_output_directory: Path,
    frozen_bcd_output_directory: Path,
    frozen_gemini_output_directory: Path,
    output_directory: Path,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved Qwen full calibration digest does not match the plan")
    if plan != build_plan(
        repository_root,
        smoke_output_directory=smoke_output_directory,
        frozen_bcd_output_directory=frozen_bcd_output_directory,
        frozen_gemini_output_directory=frozen_gemini_output_directory,
    ):
        raise ValueError("Qwen full calibration does not match canonical configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before calibration provider requests")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace Qwen calibration output: {output_directory}")
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
    config = QWEN_STATEFUL_RETRY_SUCCESSOR
    manifest = build_panel_policy_manifest(
        repository_root, config=config, code_revision=plan["code_revision"]
    )
    transport: OpenRouterPanelTransport | None = None
    episode_results: list[dict[str, Any]] = []
    execution_error: dict[str, str] | None = None
    try:
        if manifest.to_dict() != plan["policy"]["policy_manifest"]:
            raise ValueError("runtime policy manifest differs from approved calibration plan")
        transport = OpenRouterPanelTransport(config, ledger=ledger)
        for task_record in plan["task_order"]:
            task = generate_task(task_record["seed"])
            if task.task_id != task_record["task_id"]:
                raise ValueError("generated calibration task differs from approved plan")
            result = V5Runner(
                journal=journal,
                manifest=manifest,
                transport=transport,
                policy=OpenRouterPanelPolicy(config),
                approved_caps=approved_caps,
            ).run(
                trial_id=f"d56-qwen-v2-{task_record['ordinal']:02d}-{task.task_id}",
                task=task,
            )
            episode_results.append({"slot": config.slot, **result.to_dict()})
            if result.classification not in NORMAL_TERMINAL_CLASSIFICATIONS:
                break
    except Exception as exc:
        execution_error = {"type": type(exc).__name__}
        raise
    finally:
        classifications = Counter(result["classification"] for result in episode_results)
        transport_records = [] if transport is None else list(transport.records)
        integrity = journal.integrity_report()
        call_counts = journal.call_counts()
        journal.close()
        known_prior_spend = Decimal(plan["caps"]["known_prior_aggregate_spend_usd"])
        calibration_incremental_spend = ledger.spent_usd - prior_spend
        known_actual_aggregate_spend = known_prior_spend + calibration_incremental_spend
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
            "known_prior_aggregate_spend_usd": str(known_prior_spend),
            "unknown_prior_charge_reservation_usd": plan["caps"][
                "unknown_prior_charge_reservation_usd"
            ],
            "actual_aggregate_spend_usd": str(known_actual_aggregate_spend),
            "budget_accounted_aggregate_spend_usd": str(ledger.spent_usd),
            "calibration_incremental_spend_usd": str(calibration_incremental_spend),
            "remaining_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD - ledger.spent_usd),
            "maximum_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "assigned_policy_task_pairs": EXPECTED_TASK_COUNT,
            "attempted_policy_task_pairs": len(episode_results),
            "successful_policy_task_pairs": sum(result["success"] for result in episode_results),
            "completed_all_assigned_pairs": len(episode_results) == EXPECTED_TASK_COUNT,
            "classifications": dict(sorted(classifications.items())),
            "episode_results": episode_results,
            "execution_error": execution_error,
            "transport_records": transport_records,
            "successful_smoke_evidence": plan["successful_smoke_evidence"],
            "frozen_qwen_429_predecessor": plan["frozen_qwen_429_predecessor"],
            "latest_audited_spend_evidence": plan["latest_audited_spend_evidence"],
            "journal_integrity": integrity,
            "publication_status": "restricted_raw_responses_in_local_journal",
            "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
        }
        (output_directory / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return summary
