"""Plan and execute the B/C/D successor to the frozen D5.6 calibration."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps, content_digest
from pixelgym.grounding.v5.d56_calibration import (
    CURRENT_CALIBRATION_MANIFEST,
    EXPECTED_TASK_COUNT,
    NORMAL_TERMINAL_CLASSIFICATIONS,
    _current_calibration_manifest,
    _file_digest,
    _git,
    _validated_smoke_evidence,
)
from pixelgym.grounding.v5.d56_spend import (
    campaign_spend_fields,
    combine_spend_disclosures,
    ledger_spend_disclosure,
    legacy_campaign_spend_disclosure,
    legacy_summary_spend_disclosure,
    phase_spend_fields,
)
from pixelgym.grounding.v5.evidence import repository_relative_path
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import (
    BOUNDED_RETRY_STOP_RULE,
    LLAMA_STATEFUL,
    PANEL,
    PANEL_BY_SLOT,
    PANEL_MAXIMUM_SPEND_USD,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.panel_smoke import PRICE_OBSERVED_AT_UTC
from pixelgym.grounding.v5.planning import call_cap_plan, load_partition_manifests
from pixelgym.grounding.v5.runner import V5Runner

PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-bcd-calibration-plan-v2"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-bcd-calibration-result-v2"
SUCCESSOR_PANEL = PANEL[1:]
EXPECTED_SUCCESSOR_SLOTS = tuple(config.slot for config in SUCCESSOR_PANEL)

FROZEN_PLAN_SHA256 = (
    "sha256:8144512f2790333874a706681860e6d7c4def4aa98d4d33b21e03c16302a71a6"
)
FROZEN_SUMMARY_SHA256 = (
    "sha256:db7a80b5c2c623d370678d6c610578721b5551b68a1fd9055c23b54a5a267741"
)
FROZEN_JOURNAL_SHA256 = (
    "sha256:2e98191a2169627d2d5fc6b30200b9578b035ad4013499a57b5845d2f7532812"
)
FROZEN_CODE_REVISION = "a9b35eac1540aa13693b28d37e2bf3397e32b842"
FROZEN_ACTUAL_SPEND_USD = Decimal("2.032875185")
FROZEN_TERMINAL_IDENTITY = AttemptIdentity(
    "d56-A-gemini-stateful-32-v5-48860ad9b285908aa000a26b", 14, 0
)
FROZEN_JOURNAL_INTEGRITY = {
    "schema_version": "pixelgym-agent-v5-journal-integrity-v1",
    "object_count": 5791,
    "event_count": 6076,
    "event_chain_digest": (
        "sha256:316451e8cb73a6c2dafff0a9662f571d4c63084cef88c88036dcf55c929a2f54"
    ),
}


def _streaming_file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _validated_frozen_calibration_evidence(
    repository_root: Path,
    output_directory: Path,
) -> dict[str, Any]:
    summary_path = output_directory / "summary.json"
    journal_path = output_directory / "attempts.sqlite"
    if _file_digest(summary_path) != FROZEN_SUMMARY_SHA256:
        raise ValueError("frozen D5.6 summary digest mismatch")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    expected_fields = {
        "schema_version": "pixelgym-agent-v5-d56-calibration-result-v2",
        "approved_plan_sha256": FROZEN_PLAN_SHA256,
        "code_revision": FROZEN_CODE_REVISION,
        "provider_calls_made": 864,
        "provider_wire_requests": 864,
        "model_attempt_reservations": 864,
        "provider_control_requests": 0,
        "prior_aggregate_spend_usd": "0.372661310",
        "actual_aggregate_spend_usd": str(FROZEN_ACTUAL_SPEND_USD),
        "calibration_incremental_spend_usd": "1.660213875",
        "remaining_aggregate_spend_usd": "7.967124815",
        "assigned_policy_task_pairs": 200,
        "attempted_policy_task_pairs": 33,
        "successful_policy_task_pairs": 20,
        "classifications": {
            "infrastructure_failure": 1,
            "step_limit_truncation": 12,
            "success_termination": 20,
        },
        "journal_integrity": FROZEN_JOURNAL_INTEGRITY,
    }
    if not isinstance(summary, dict) or any(
        summary.get(key) != value for key, value in expected_fields.items()
    ):
        raise ValueError("frozen D5.6 summary facts mismatch")
    results = summary.get("episode_results")
    if (
        not isinstance(results, list)
        or len(results) != 33
        or any(result.get("slot") != "A-gemini-stateful" for result in results)
    ):
        raise ValueError("frozen D5.6 assignment evidence mismatch")
    terminal_result = results[-1]
    if terminal_result != {
        "classification": "infrastructure_failure",
        "environment_actions": 14,
        "final_policy_checkpoint_digest": (
            "sha256:677ece2bb2f88aa36136e3bd472ec6c3622f5ccbd3815b271888d1f4e137c188"
        ),
        "model_attempts": 15,
        "provider_control_requests": 0,
        "provider_wire_requests": 15,
        "slot": "A-gemini-stateful",
        "success": False,
        "task_id": "v5-48860ad9b285908aa000a26b",
        "trial_id": FROZEN_TERMINAL_IDENTITY.trial_id,
    }:
        raise ValueError("frozen D5.6 terminal assignment mismatch")
    if _streaming_file_digest(journal_path) != FROZEN_JOURNAL_SHA256:
        raise ValueError("frozen D5.6 journal digest mismatch")
    journal = V5AttemptJournal(journal_path)
    try:
        terminal_event = journal.terminal_attempt(FROZEN_TERMINAL_IDENTITY)
        if (
            terminal_event is None
            or terminal_event.kind != "unknown_outcome_infrastructure_failure"
            or terminal_event.payload.get("failure_code") != "runner_request_deadline"
            or terminal_event.payload.get("response_digest") is not None
            or terminal_event.payload.get("usage") != {}
        ):
            raise ValueError("frozen D5.6 terminal journal event mismatch")
        if journal.call_counts() != (864, 0):
            raise ValueError("frozen D5.6 journal request counts mismatch")
    finally:
        journal.close()
    phase_spend = legacy_summary_spend_disclosure(summary)
    campaign_spend = legacy_campaign_spend_disclosure(summary)
    return {
        "approved_plan_sha256": FROZEN_PLAN_SHA256,
        "code_revision": FROZEN_CODE_REVISION,
        "summary_path": repository_relative_path(repository_root, summary_path),
        "summary_sha256": FROZEN_SUMMARY_SHA256,
        "journal_path": repository_relative_path(repository_root, journal_path),
        "journal_sha256": FROZEN_JOURNAL_SHA256,
        "actual_aggregate_spend_usd": str(FROZEN_ACTUAL_SPEND_USD),
        "phase_spend": phase_spend,
        "campaign_spend": campaign_spend,
        "remaining_aggregate_spend_usd": str(
            PANEL_MAXIMUM_SPEND_USD - FROZEN_ACTUAL_SPEND_USD
        ),
        "attempted_policy_task_pairs": 33,
        "attempted_slots": ["A-gemini-stateful"],
        "unattempted_successor_slots": list(EXPECTED_SUCCESSOR_SLOTS),
        "terminal": {
            "classification": terminal_event.kind,
            "failure_code": terminal_event.payload["failure_code"],
            "trial_id": FROZEN_TERMINAL_IDENTITY.trial_id,
            "step_index": FROZEN_TERMINAL_IDENTITY.step_index,
            "attempt_index": FROZEN_TERMINAL_IDENTITY.attempt_index,
            "request_outcome": "unknown",
            "retry_eligible": False,
        },
        "journal_integrity": FROZEN_JOURNAL_INTEGRITY,
    }


def build_plan(
    repository_root: Path,
    *,
    smoke_output_directory: Path,
    frozen_calibration_output_directory: Path,
) -> dict[str, Any]:
    revision = _git(repository_root, "rev-parse", "HEAD")
    partition = _current_calibration_manifest(repository_root)
    smoke_evidence = _validated_smoke_evidence(repository_root, smoke_output_directory)
    frozen_evidence = _validated_frozen_calibration_evidence(
        repository_root, frozen_calibration_output_directory
    )
    prior_campaign_spend = frozen_evidence["campaign_spend"]
    action_cap = sum(record["max_episode_steps"] for record in partition["records"])
    partition_manifests = load_partition_manifests(
        repository_root / CURRENT_CALIBRATION_MANIFEST.parent,
        calibration_manifest=repository_root / CURRENT_CALIBRATION_MANIFEST,
    )
    policies: list[dict[str, Any]] = []
    aggregate_theoretical_maximum = Decimal(0)
    for config in SUCCESSOR_PANEL:
        manifest = build_panel_policy_manifest(
            repository_root, config=config, code_revision=revision
        )
        phase_call_cap_plan = call_cap_plan(
            manifest,
            partition_manifests=partition_manifests,
            approved_calibration_manifest_digest=partition["manifest_digest"],
        )
        run_theoretical_maximum = (
            config.request_maximum_usd
            * action_cap
            * manifest.max_model_attempts_per_action
        )
        aggregate_theoretical_maximum += run_theoretical_maximum
        policies.append(
            {
                "slot": config.slot,
                "policy_manifest": manifest.to_dict(),
                "policy_manifest_digest": content_digest(manifest.to_dict()),
                "phase_call_cap_plan": phase_call_cap_plan,
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
                "price_record": {
                    "source_url": config.price_source,
                    "observed_at_utc": PRICE_OBSERVED_AT_UTC,
                    "currency": "USD",
                    "prompt_per_token": str(config.prompt_price_per_token_usd),
                    "completion_per_token": str(config.completion_price_per_token_usd),
                    "image_input_billing": "provider input tokens at prompt_per_token",
                    "max_prompt_tokens": 126_976,
                    "max_output_tokens": 4_096,
                    "unknown_usage_or_price_rule": "fail_closed",
                },
                "caps": {
                    **phase_call_cap_plan["phases"]["calibration"],
                    "per_request_theoretical_maximum_usd": str(
                        config.request_maximum_usd
                    ),
                    "run_theoretical_maximum_usd": str(run_theoretical_maximum),
                },
            }
        )
    successor_count = len(SUCCESSOR_PANEL)
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": (
            "evaluate only the three unattempted B/C/D policy slots on the frozen fifty-task "
            "D5.6 calibration partition"
        ),
        "provider_calls_made": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "assigned_policy_task_pairs": successor_count * EXPECTED_TASK_COUNT,
        "calibration_partition": {
            "path": CURRENT_CALIBRATION_MANIFEST.as_posix(),
            "file_sha256": _file_digest(
                repository_root / CURRENT_CALIBRATION_MANIFEST
            ),
            "manifest_digest": partition["manifest_digest"],
            "source_manifest_digest": partition["derivation"]["source_manifest_digest"],
            "pilot_plan_digest": partition["derivation"]["pilot_plan_digest"],
            "episode_count": EXPECTED_TASK_COUNT,
            "action_cap_per_policy": action_cap,
            "exclusion_rule": partition["derivation"]["exclusion_rule"],
            "excluded_seeds": partition["derivation"]["excluded_seeds"],
            "replacement_seeds": partition["derivation"]["replacement_seeds"],
            "consumed_calibration_plan_digest": partition["derivation"][
                "consumed_calibration_plan_digest"
            ],
            "consumed_calibration_summary_sha256": partition["derivation"][
                "consumed_calibration_summary_sha256"
            ],
        },
        "policies": policies,
        "aggregate_caps": {
            **CallCaps(
                action_cap * successor_count,
                action_cap * successor_count * 2,
                0,
                action_cap * successor_count * 2,
            ).to_dict(),
            "maximum_run_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "remaining_run_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "prior_campaign_spend": prior_campaign_spend,
            "spend_lineage": (
                "per-run enforcement: this phase starts at zero; predecessor spend is "
                "carried only as campaign disclosure"
            ),
            "uncapped_theoretical_request_maximum_usd": str(
                aggregate_theoretical_maximum
            ),
            "enforcement": (
                "before each wire request, reserve that slot's worst-case request cost against "
                "this phase's ledger; stop before a request that cannot fit"
            ),
        },
        "smoke_evidence": smoke_evidence,
        "frozen_predecessor_evidence": frozen_evidence,
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
            "run only policy slots B, C, and D sequentially in that order and tasks in frozen manifest order",
            "do not resume, retry, or replace any predecessor Slot A request or assignment",
            "continue after success termination or step-limit truncation so assigned tasks remain in the denominator",
            BOUNDED_RETRY_STOP_RULE,
            "retain both attempts and stop after a repeated retryable provider error",
            "stop the B/C/D run after the first other transport, identity, cost, parse, adapter, invalid-action, or evidence-integrity failure",
            "stop before any request whose per-request theoretical maximum cannot fit under this phase's ten-dollar ledger",
            "do not retry a parse, action, unknown-outcome, or other provider failure; do not replace or reorder an assignment",
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
    frozen_calibration_output_directory: Path,
    output_directory: Path,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved B/C/D calibration plan digest does not match the supplied plan")
    if LLAMA_STATEFUL.adapter.name != "native-1024x768":
        raise RuntimeError(
            "native-coordinate B/C/D calibration is frozen; use the normalized Slot C trial"
        )
    if plan != build_plan(
        repository_root,
        smoke_output_directory=smoke_output_directory,
        frozen_calibration_output_directory=frozen_calibration_output_directory,
    ):
        raise ValueError("B/C/D plan does not match the canonical request configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before calibration provider requests")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace B/C/D output: {output_directory}")
    output_directory.mkdir(parents=True)
    journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    maximum_spend = Decimal(plan["aggregate_caps"]["maximum_run_spend_usd"])
    ledger = SpendLedger(maximum_spend, Decimal(0))
    aggregate_caps = CallCaps(
        plan["aggregate_caps"]["environment_action_cap"],
        plan["aggregate_caps"]["model_attempt_cap"],
        plan["aggregate_caps"]["provider_control_request_cap"],
        plan["aggregate_caps"]["provider_wire_request_cap"],
    )
    episode_results: list[dict[str, Any]] = []
    transport_records: list[dict[str, Any]] = []
    freeze_run = False
    try:
        for policy_record in plan["policies"]:
            config = PANEL_BY_SLOT[policy_record["slot"]]
            manifest = build_panel_policy_manifest(
                repository_root, config=config, code_revision=plan["code_revision"]
            )
            if manifest.to_dict() != policy_record["policy_manifest"]:
                raise ValueError("runtime policy manifest differs from the approved plan")
            transport = OpenRouterPanelTransport(config, ledger=ledger)
            for task_record in plan["task_order"]:
                task = generate_task(task_record["seed"])
                if task.task_id != task_record["task_id"]:
                    raise ValueError("generated calibration task differs from the approved plan")
                result = V5Runner(
                    journal=journal,
                    manifest=manifest,
                    transport=transport,
                    policy=OpenRouterPanelPolicy(config),
                    approved_caps=aggregate_caps,
                ).run(
                    trial_id=(
                        f"d56-bcd-{config.slot}-{task_record['ordinal']:02d}-{task.task_id}"
                    ),
                    task=task,
                )
                episode_results.append({"slot": config.slot, **result.to_dict()})
                if result.classification not in NORMAL_TERMINAL_CLASSIFICATIONS:
                    freeze_run = True
                    break
            transport_records.extend(transport.records)
            if freeze_run:
                break
        classifications = Counter(
            result["classification"] for result in episode_results
        )
        phase_spend = ledger_spend_disclosure(ledger)
        prior_campaign_spend = plan["aggregate_caps"]["prior_campaign_spend"]
        campaign_spend = combine_spend_disclosures(
            (prior_campaign_spend, phase_spend)
        )
        summary = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "approved_plan_sha256": digest,
            "code_revision": plan["code_revision"],
            "provider_calls_made": ledger.wire_requests_sent,
            "provider_wire_requests": ledger.wire_requests_sent,
            "model_attempt_reservations": journal.call_counts()[0],
            "provider_control_requests": journal.call_counts()[1],
            "unknown_charge_outcomes": ledger.unknown_charge_outcomes,
            **phase_spend_fields(phase_spend),
            "prior_campaign_spend": prior_campaign_spend,
            **campaign_spend_fields(campaign_spend),
            "actual_aggregate_spend_usd": campaign_spend["known_spend_usd"],
            "calibration_incremental_spend_usd": phase_spend["known_spend_usd"],
            "remaining_run_spend_usd": str(
                maximum_spend - Decimal(phase_spend["budget_accounted_spend_usd"])
            ),
            "maximum_run_spend_usd": str(maximum_spend),
            "assigned_policy_task_pairs": len(SUCCESSOR_PANEL) * EXPECTED_TASK_COUNT,
            "attempted_policy_task_pairs": len(episode_results),
            "successful_policy_task_pairs": sum(
                result["success"] for result in episode_results
            ),
            "classifications": dict(sorted(classifications.items())),
            "episode_results": episode_results,
            "transport_records": transport_records,
            "frozen_predecessor_evidence": plan["frozen_predecessor_evidence"],
            "journal_integrity": journal.integrity_report(),
            "publication_status": "restricted_raw_responses_in_local_journal",
            "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
        }
    finally:
        journal.close()
    (output_directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary
