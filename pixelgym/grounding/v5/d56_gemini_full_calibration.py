"""Plan and execute a fifty-task Gemini 3.7 Flash successor calibration."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.d56_bcd_calibration import _streaming_file_digest
from pixelgym.grounding.v5.d56_calibration import (
    CALIBRATION_MANIFEST,
    CONSECUTIVE_FAILURE_LIMIT,
    EXPECTED_TASK_COUNT,
    ConsecutiveFailureBreaker,
    _calibration_manifest,
    _file_digest,
    _git,
)
from pixelgym.grounding.v5.evidence import repository_relative_path
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import (
    GEMINI_STATEFUL_FULL_CALIBRATION,
    PANEL_MAXIMUM_SPEND_USD,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
    bounded_retry_stop_rule,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.planning import call_cap_plan, load_partition_manifests
from pixelgym.grounding.v5.runner import V5Runner

PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-gemini-full-calibration-plan-v1"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-gemini-full-calibration-result-v1"
ENDPOINT_METADATA_OBSERVED_AT_UTC = "2026-08-28T00:32:11Z"
POLICY_GENERATION = "v3"

FROZEN_SMOKE_PLAN_SHA256 = (
    "sha256:6bc241c61122b9fdb69c6298c168fac76c20a5779a5c02e549ff08adaf2bb3eb"
)
FROZEN_SMOKE_SUMMARY_SHA256 = (
    "sha256:97a8f1fa404999f05d248c8988fe1f2ee595ac2c0459d14efbc73e43bc773815"
)
FROZEN_SMOKE_JOURNAL_SHA256 = (
    "sha256:04ce4b27670371bd1d95f2cc055acab3e1c30ff426f98f38c350b676b42109b0"
)
FROZEN_SMOKE_CODE_REVISION = "6380a4ccb6869a21fe21598527efd6e915601551"
FROZEN_SMOKE_ACTUAL_SPEND_USD = Decimal("2.489953207")
FROZEN_SMOKE_JOURNAL_INTEGRITY = {
    "schema_version": "pixelgym-agent-v5-journal-integrity-v1",
    "object_count": 12,
    "event_count": 8,
    "event_chain_digest": (
        "sha256:f8fe491f1d053362098a81c7dd236a95dce3558b41889cc7b97b158db0bd0e75"
    ),
}
FROZEN_PREDECESSOR_SLOT_A_PLAN_SHA256 = (
    "sha256:8144512f2790333874a706681860e6d7c4def4aa98d4d33b21e03c16302a71a6"
)
FROZEN_PREDECESSOR_SLOT_A_SUMMARY_SHA256 = (
    "sha256:db7a80b5c2c623d370678d6c610578721b5551b68a1fd9055c23b54a5a267741"
)


def _validated_smoke_evidence(repository_root: Path, output_directory: Path) -> dict[str, Any]:
    summary_path = output_directory / "summary.json"
    journal_path = output_directory / "attempts.sqlite"
    if _file_digest(summary_path) != FROZEN_SMOKE_SUMMARY_SHA256:
        raise ValueError("frozen Gemini smoke summary digest mismatch")
    if _streaming_file_digest(journal_path) != FROZEN_SMOKE_JOURNAL_SHA256:
        raise ValueError("frozen Gemini smoke journal digest mismatch")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    expected_fields = {
        "schema_version": "pixelgym-agent-v5-d56-gemini-one-call-smoke-result-v1",
        "approved_plan_sha256": FROZEN_SMOKE_PLAN_SHA256,
        "code_revision": FROZEN_SMOKE_CODE_REVISION,
        "provider_calls_made": 1,
        "provider_wire_requests": 1,
        "model_attempt_reservations": 1,
        "provider_control_requests": 0,
        "actual_aggregate_spend_usd": str(FROZEN_SMOKE_ACTUAL_SPEND_USD),
        "smoke_incremental_spend_usd": "0.002613750",
        "remaining_aggregate_spend_usd": "7.510046793",
        "reached_model_response": True,
        "journal_integrity": FROZEN_SMOKE_JOURNAL_INTEGRITY,
        "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
    }
    if not isinstance(summary, dict) or any(
        summary.get(key) != value for key, value in expected_fields.items()
    ):
        raise ValueError("frozen Gemini smoke summary facts mismatch")
    result = summary.get("episode_result")
    if not isinstance(result, dict) or any(
        result.get(key) != value
        for key, value in {
            "classification": "pilot_action_limit",
            "environment_actions": 1,
            "model_attempts": 1,
            "provider_wire_requests": 1,
            "provider_control_requests": 0,
            "slot": "A-gemini-stateful-one-call-smoke",
            "task_id": "v5-64ba7d452b3c8d3e43d1d30e",
        }.items()
    ):
        raise ValueError("frozen Gemini smoke episode evidence mismatch")
    progress = summary.get("semantic_progress")
    if not isinstance(progress, dict) or (
        progress.get("first_transition_completed") is not True
        or progress.get("maximum_stage_index_observed") != 1
        or progress.get("diagnostic_event_counts") != {"correct_transition": 1}
    ):
        raise ValueError("frozen Gemini smoke semantic evidence mismatch")
    transport_records = summary.get("transport_records")
    if not isinstance(transport_records, list) or len(transport_records) != 1:
        raise ValueError("frozen Gemini smoke transport evidence mismatch")
    transport = transport_records[0]
    if any(
        transport.get(key) != value
        for key, value in {
            "status": "response",
            "response_model": "google/gemini-3.7-flash",
            "upstream_provider": "Google",
            "cost_usd": "0.00261375",
        }.items()
    ):
        raise ValueError("frozen Gemini smoke response identity mismatch")
    journal = V5AttemptJournal(journal_path)
    try:
        if journal.call_counts() != (1, 0):
            raise ValueError("frozen Gemini smoke journal request counts mismatch")
        if journal.integrity_report() != FROZEN_SMOKE_JOURNAL_INTEGRITY:
            raise ValueError("frozen Gemini smoke journal integrity mismatch")
    finally:
        journal.close()
    return {
        "approved_plan_sha256": FROZEN_SMOKE_PLAN_SHA256,
        "code_revision": FROZEN_SMOKE_CODE_REVISION,
        "summary_path": repository_relative_path(repository_root, summary_path),
        "summary_sha256": FROZEN_SMOKE_SUMMARY_SHA256,
        "journal_path": repository_relative_path(repository_root, journal_path),
        "journal_sha256": FROZEN_SMOKE_JOURNAL_SHA256,
        "actual_aggregate_spend_usd": str(FROZEN_SMOKE_ACTUAL_SPEND_USD),
        "remaining_aggregate_spend_usd": str(
            PANEL_MAXIMUM_SPEND_USD - FROZEN_SMOKE_ACTUAL_SPEND_USD
        ),
        "model_response_received": True,
        "first_transition_completed": True,
        "journal_integrity": FROZEN_SMOKE_JOURNAL_INTEGRITY,
    }


def build_plan(
    repository_root: Path,
    *,
    smoke_output_directory: Path,
    maximum_spend_usd: Decimal,
) -> dict[str, Any]:
    if not maximum_spend_usd.is_finite() or maximum_spend_usd <= 0:
        raise ValueError("maximum run spend must be positive")
    revision = _git(repository_root, "rev-parse", "HEAD")
    partition = _calibration_manifest(repository_root)
    smoke_evidence = _validated_smoke_evidence(repository_root, smoke_output_directory)
    action_cap = sum(record["max_episode_steps"] for record in partition["records"])
    config = GEMINI_STATEFUL_FULL_CALIBRATION
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
            f"evaluate the Gemini 3.7 Flash {POLICY_GENERATION} policy on all fifty frozen "
            "D5.6 calibration tasks as a distinct successor run"
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
                "router_metadata": "enabled",
            },
            "response_validation": {
                "upstream_response_format": "json_schema",
                "upstream_json_schema_strict": True,
                "local_exact_action_parser": True,
                "invalid_or_unparseable_output_rule": (
                    "retain_fail_the_assignment_and_continue_without_retry"
                ),
            },
            "live_endpoint_record": {
                "source_url": config.price_source,
                "observed_at_utc": ENDPOINT_METADATA_OBSERVED_AT_UTC,
                "provider_name": "Google",
                "requested_tag": "google-vertex/global",
                "matching_tags": [
                    "google-vertex/global",
                    "google-vertex/global/flex",
                    "google-vertex/global/priority",
                ],
                "statuses": [0, 0, 0],
                "supports_response_format": True,
                "supports_structured_outputs": True,
                "reservation_price_basis": "highest-priced matching Vertex endpoint",
                "maximum_prompt_per_token_usd": str(config.prompt_price_per_token_usd),
                "maximum_completion_per_token_usd": str(
                    config.completion_price_per_token_usd
                ),
            },
        },
        "run_continuation": {
            "rule": (
                "record every non-normal terminal classification as a failed assignment "
                "and continue to the next task so all fifty stay in the denominator"
            ),
            "consecutive_failure_limit": CONSECUTIVE_FAILURE_LIMIT,
            "hard_stop_conditions": [
                "run spend ledger blocked",
                "policy or request identity mismatch",
                "a charge above the per-request theoretical maximum",
                "non-retryable HTTP status",
                "evidence-integrity failure",
            ],
            "retryable_send_outcomes": [
                "http_429_rate_limit",
                "transient transport fault (dropped connection, timeout, retryable 5xx, unreadable envelope)",
                "zero_completion_error",
            ],
            "unobservable_charge_rule": (
                "for every send whose charge cannot be observed, hold against this "
                "run's ledger three times the most expensive response the run has "
                "priced so far, capped at the per-request theoretical maximum and "
                "falling back to that maximum before any response has been priced"
            ),
        },
        "caps": {
            **CallCaps(action_cap, attempt_cap, 0, attempt_cap).to_dict(),
            "maximum_run_spend_usd": str(maximum_spend_usd),
            "spend_lineage": (
                "per-run: this run's ledger starts at zero and is bounded only by the "
                "approved maximum_run_spend_usd; no prior run's spend is carried in"
            ),
            "per_request_theoretical_maximum_usd": str(config.request_maximum_usd),
            "uncapped_run_theoretical_maximum_usd": str(theoretical_maximum),
            "enforcement": (
                "before each wire request, reserve the highest matching Vertex endpoint's "
                "worst-case request cost against this run's own ledger; stop before a "
                "request that cannot fit"
            ),
        },
        "successful_smoke_evidence": smoke_evidence,
        "predecessor_relation": {
            "frozen_plan_sha256": FROZEN_PREDECESSOR_SLOT_A_PLAN_SHA256,
            "frozen_summary_sha256": FROZEN_PREDECESSOR_SLOT_A_SUMMARY_SHA256,
            "rule": (
                f"this {POLICY_GENERATION} policy is a distinct successor run; do not "
                "resume, retry, replace, or reinterpret any frozen predecessor Slot A "
                "request or assignment"
            ),
        },
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
            bounded_retry_stop_rule(ledger="this run's ledger"),
            "retain every invalid or unparseable model output, record it as a failed assignment, and continue to the next task",
            "continue after a settled per-task transport or infrastructure failure so the assignment stays in the denominator",
            f"stop after {CONSECUTIVE_FAILURE_LIMIT} consecutive non-normal terminal classifications",
            "stop immediately on an identity, price-guard, non-retryable HTTP, or evidence-integrity failure",
            (
                "stop before any request whose per-request theoretical maximum cannot fit "
                "under this run's approved maximum_run_spend_usd cap of "
                f"{maximum_spend_usd} USD"
            ),
            "do not resume, retry, replace, or reinterpret any frozen predecessor Slot A request or assignment",
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
    output_directory: Path,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved Gemini full calibration digest does not match the plan")
    maximum_spend_usd = Decimal(plan["caps"]["maximum_run_spend_usd"])
    if plan != build_plan(
        repository_root,
        smoke_output_directory=smoke_output_directory,
        maximum_spend_usd=maximum_spend_usd,
    ):
        raise ValueError("Gemini full calibration does not match canonical configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before calibration provider requests")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace Gemini calibration output: {output_directory}")
    output_directory.mkdir(parents=True)
    journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    # The approved plan carries this run's entire budget; nothing is inherited.
    ledger = SpendLedger(maximum_spend_usd, Decimal(0))
    approved_caps = CallCaps(
        plan["caps"]["environment_action_cap"],
        plan["caps"]["model_attempt_cap"],
        plan["caps"]["provider_control_request_cap"],
        plan["caps"]["provider_wire_request_cap"],
    )
    config = GEMINI_STATEFUL_FULL_CALIBRATION
    manifest = build_panel_policy_manifest(
        repository_root, config=config, code_revision=plan["code_revision"]
    )
    transport: OpenRouterPanelTransport | None = None
    breaker = ConsecutiveFailureBreaker(CONSECUTIVE_FAILURE_LIMIT)
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
                trial_id=(
                    f"d56-gemini-{POLICY_GENERATION}-"
                    f"{task_record['ordinal']:02d}-{task.task_id}"
                ),
                task=task,
            )
            episode_results.append({"slot": config.slot, **result.to_dict()})
            if breaker.record(result.classification):
                break
            if ledger.blocked:
                breaker.trip("run_spend_ledger_blocked")
                break
    except Exception as exc:
        execution_error = {"type": type(exc).__name__}
        raise
    finally:
        classifications = Counter(
            result["classification"] for result in episode_results
        )
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
            "run_spend_usd": str(ledger.spent_usd),
            "budget_accounted_run_spend_usd": str(ledger.budget_accounted_spend_usd),
            "unknown_charge_reservation_usd": str(ledger.unknown_reservation_usd),
            "unknown_charge_outcomes": ledger.unknown_charge_outcomes,
            "run_spend_ledger_blocked": ledger.blocked,
            "run_continuation": breaker.to_dict(),
            "remaining_run_spend_usd": str(
                maximum_spend_usd - ledger.budget_accounted_spend_usd
            ),
            "maximum_run_spend_usd": str(maximum_spend_usd),
            "assigned_policy_task_pairs": EXPECTED_TASK_COUNT,
            "attempted_policy_task_pairs": len(episode_results),
            "successful_policy_task_pairs": sum(
                result["success"] for result in episode_results
            ),
            "completed_all_assigned_pairs": len(episode_results) == EXPECTED_TASK_COUNT,
            "classifications": dict(sorted(classifications.items())),
            "episode_results": episode_results,
            "execution_error": execution_error,
            "transport_records": transport_records,
            "successful_smoke_evidence": plan["successful_smoke_evidence"],
            "predecessor_relation": plan["predecessor_relation"],
            "journal_integrity": integrity,
            "publication_status": "restricted_raw_responses_in_local_journal",
            "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
        }
        (output_directory / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return summary
