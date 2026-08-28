"""Plan and execute the Slot C successor to the frozen B/C/D calibration."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps, content_digest
from pixelgym.grounding.v5.d56_bcd_calibration import _streaming_file_digest
from pixelgym.grounding.v5.d56_bcd_calibration import (
    build_plan as build_bcd_plan,
)
from pixelgym.grounding.v5.d56_calibration import (
    EXPECTED_TASK_COUNT,
    NORMAL_TERMINAL_CLASSIFICATIONS,
    _file_digest,
    _git,
)
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import (
    LLAMA_STATEFUL,
    PANEL_MAXIMUM_SPEND_USD,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.runner import V5Runner

PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-c-calibration-plan-v1"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-c-calibration-result-v1"

FROZEN_BCD_PLAN_SHA256 = (
    "sha256:880fa35de9616a5a46a766ab9babecf495315d4e4ff3d46e5c1eeb49809e68a9"
)
FROZEN_BCD_SUMMARY_SHA256 = (
    "sha256:630f765bc44ce7cb85c91e0ae3b906fcedf4e1374556d6074d84cc2e95b447f4"
)
FROZEN_BCD_JOURNAL_SHA256 = (
    "sha256:78dbb0e3d819c06604c3b664120df35448f6642ac13b3c367ef0bb9f7f8ef7fe"
)
FROZEN_BCD_CODE_REVISION = "e25bfbe384a583ef853682481f56d08659cfe40f"
FROZEN_BCD_ACTUAL_SPEND_USD = Decimal("2.040917557")
FROZEN_BCD_JOURNAL_INTEGRITY = {
    "schema_version": "pixelgym-agent-v5-journal-integrity-v1",
    "object_count": 163,
    "event_count": 171,
    "event_chain_digest": (
        "sha256:fe80c9c9d81756515716e1fbe42135302df730646b6027c0d4597a47892efbab"
    ),
}
FROZEN_BCD_TERMINAL_IDENTITY = AttemptIdentity(
    "d56-bcd-B-qwen-stateful-00-v5-bfe5707f6b44202a0e7f493e", 24, 0
)


def _validated_frozen_bcd_evidence(output_directory: Path) -> dict[str, Any]:
    summary_path = output_directory / "summary.json"
    journal_path = output_directory / "attempts.sqlite"
    if _file_digest(summary_path) != FROZEN_BCD_SUMMARY_SHA256:
        raise ValueError("frozen B/C/D summary digest mismatch")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    expected_fields = {
        "schema_version": "pixelgym-agent-v5-d56-bcd-calibration-result-v1",
        "approved_plan_sha256": FROZEN_BCD_PLAN_SHA256,
        "code_revision": FROZEN_BCD_CODE_REVISION,
        "provider_calls_made": 25,
        "provider_wire_requests": 25,
        "model_attempt_reservations": 25,
        "provider_control_requests": 0,
        "prior_aggregate_spend_usd": "2.032875185",
        "actual_aggregate_spend_usd": str(FROZEN_BCD_ACTUAL_SPEND_USD),
        "calibration_incremental_spend_usd": "0.008042372",
        "remaining_aggregate_spend_usd": "7.959082443",
        "assigned_policy_task_pairs": 150,
        "attempted_policy_task_pairs": 1,
        "successful_policy_task_pairs": 0,
        "classifications": {"infrastructure_failure": 1},
        "journal_integrity": FROZEN_BCD_JOURNAL_INTEGRITY,
    }
    if not isinstance(summary, dict) or any(
        summary.get(key) != value for key, value in expected_fields.items()
    ):
        raise ValueError("frozen B/C/D summary facts mismatch")
    results = summary.get("episode_results")
    if not isinstance(results, list) or results != [
        {
            "classification": "infrastructure_failure",
            "environment_actions": 24,
            "final_policy_checkpoint_digest": (
                "sha256:9ad51c920f5a512662a473ce425d591af2cb4f2ebf31d0f56fa9bb1b2bfbfb6a"
            ),
            "model_attempts": 25,
            "provider_control_requests": 0,
            "provider_wire_requests": 25,
            "slot": "B-qwen-stateful",
            "success": False,
            "task_id": "v5-bfe5707f6b44202a0e7f493e",
            "trial_id": FROZEN_BCD_TERMINAL_IDENTITY.trial_id,
        }
    ]:
        raise ValueError("frozen B/C/D assignment evidence mismatch")
    transport_records = summary.get("transport_records")
    if not isinstance(transport_records, list) or len(transport_records) != 25:
        raise ValueError("frozen B/C/D transport evidence mismatch")
    terminal_transport = transport_records[-1]
    if (
        terminal_transport.get("status") != "unknown"
        or terminal_transport.get("failure_code") != "HTTPError"
        or terminal_transport.get("http_status") != 429
        or terminal_transport.get("provider_error_code") != 429
        or terminal_transport.get("upstream_provider") != "Alibaba"
    ):
        raise ValueError("frozen B/C/D terminal transport mismatch")
    if _streaming_file_digest(journal_path) != FROZEN_BCD_JOURNAL_SHA256:
        raise ValueError("frozen B/C/D journal digest mismatch")
    journal = V5AttemptJournal(journal_path)
    try:
        terminal_event = journal.terminal_attempt(FROZEN_BCD_TERMINAL_IDENTITY)
        if (
            terminal_event is None
            or terminal_event.kind != "unknown_outcome_infrastructure_failure"
            or terminal_event.payload.get("failure_code") != "provider_request_unknown"
            or terminal_event.payload.get("response_digest") is not None
            or terminal_event.payload.get("usage") != {}
        ):
            raise ValueError("frozen B/C/D terminal journal event mismatch")
        if journal.call_counts() != (25, 0):
            raise ValueError("frozen B/C/D journal request counts mismatch")
    finally:
        journal.close()
    return {
        "approved_plan_sha256": FROZEN_BCD_PLAN_SHA256,
        "code_revision": FROZEN_BCD_CODE_REVISION,
        "summary_path": str(summary_path),
        "summary_sha256": FROZEN_BCD_SUMMARY_SHA256,
        "journal_path": str(journal_path),
        "journal_sha256": FROZEN_BCD_JOURNAL_SHA256,
        "actual_aggregate_spend_usd": str(FROZEN_BCD_ACTUAL_SPEND_USD),
        "remaining_aggregate_spend_usd": str(
            PANEL_MAXIMUM_SPEND_USD - FROZEN_BCD_ACTUAL_SPEND_USD
        ),
        "attempted_policy_task_pairs": 1,
        "attempted_slots": ["B-qwen-stateful"],
        "unattempted_successor_slots": ["C-llama-stateful", "D-qwen-stateless"],
        "terminal": {
            "classification": terminal_event.kind,
            "failure_code": terminal_event.payload["failure_code"],
            "http_status": 429,
            "provider_error_code": 429,
            "trial_id": FROZEN_BCD_TERMINAL_IDENTITY.trial_id,
            "step_index": FROZEN_BCD_TERMINAL_IDENTITY.step_index,
            "attempt_index": FROZEN_BCD_TERMINAL_IDENTITY.attempt_index,
            "request_outcome": "unknown",
            "retry_eligible": False,
        },
        "journal_integrity": FROZEN_BCD_JOURNAL_INTEGRITY,
    }


def build_plan(
    repository_root: Path,
    *,
    smoke_output_directory: Path,
    frozen_calibration_output_directory: Path,
    frozen_bcd_output_directory: Path,
) -> dict[str, Any]:
    base_plan = build_bcd_plan(
        repository_root,
        smoke_output_directory=smoke_output_directory,
        frozen_calibration_output_directory=frozen_calibration_output_directory,
    )
    frozen_bcd_evidence = _validated_frozen_bcd_evidence(
        frozen_bcd_output_directory
    )
    prior_spend = Decimal(frozen_bcd_evidence["actual_aggregate_spend_usd"])
    c_policy = next(
        policy for policy in base_plan["policies"] if policy["slot"] == LLAMA_STATEFUL.slot
    )
    action_cap = base_plan["calibration_partition"]["action_cap_per_policy"]
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": (
            "evaluate only the previously unattempted Slot C policy on the frozen fifty-task "
            "D5.6 calibration partition"
        ),
        "provider_calls_made": 0,
        "code_revision": base_plan["code_revision"],
        "requires_clean_tracked_worktree": True,
        "assigned_policy_task_pairs": EXPECTED_TASK_COUNT,
        "calibration_partition": base_plan["calibration_partition"],
        "policies": [c_policy],
        "aggregate_caps": {
            **CallCaps(action_cap, action_cap * 2, 0, action_cap * 2).to_dict(),
            "maximum_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "prior_aggregate_spend_usd": str(prior_spend),
            "remaining_aggregate_spend_usd": str(
                PANEL_MAXIMUM_SPEND_USD - prior_spend
            ),
            "uncapped_theoretical_request_maximum_usd": c_policy["caps"][
                "run_theoretical_maximum_usd"
            ],
            "enforcement": (
                "before each wire request, reserve Slot C's worst-case request cost against "
                "the shared aggregate ledger; stop before a request that cannot fit"
            ),
        },
        "smoke_evidence": base_plan["smoke_evidence"],
        "frozen_panel_predecessor_evidence": base_plan[
            "frozen_predecessor_evidence"
        ],
        "frozen_bcd_predecessor_evidence": frozen_bcd_evidence,
        "task_order": base_plan["task_order"],
        "stop_rules": [
            "run only policy Slot C and tasks in frozen manifest order",
            "do not resume, retry, or replace any predecessor Slot A or Slot B request or assignment",
            "continue after success termination or step-limit truncation so assigned tasks remain in the denominator",
            "retry once on the same route only after a zero-token, zero-cost, empty response with finish_reason error",
            "retain both attempts and stop after a repeated retryable provider error",
            "stop the Slot C run after the first other transport, identity, cost, parse, adapter, invalid-action, or evidence-integrity failure",
            "stop before any request whose per-request theoretical maximum cannot fit under the shared ten-dollar ledger",
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
    frozen_bcd_output_directory: Path,
    output_directory: Path,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved Slot C calibration plan digest does not match the supplied plan")
    if LLAMA_STATEFUL.adapter.name != "native-1024x768":
        raise RuntimeError(
            "native-coordinate Slot C calibration is frozen; use the normalized trial"
        )
    if plan != build_plan(
        repository_root,
        smoke_output_directory=smoke_output_directory,
        frozen_calibration_output_directory=frozen_calibration_output_directory,
        frozen_bcd_output_directory=frozen_bcd_output_directory,
    ):
        raise ValueError("Slot C plan does not match the canonical request configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before calibration provider requests")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace Slot C output: {output_directory}")
    output_directory.mkdir(parents=True)
    journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    prior_spend = Decimal(plan["aggregate_caps"]["prior_aggregate_spend_usd"])
    ledger = SpendLedger(PANEL_MAXIMUM_SPEND_USD, prior_spend)
    aggregate_caps = CallCaps(
        plan["aggregate_caps"]["environment_action_cap"],
        plan["aggregate_caps"]["model_attempt_cap"],
        plan["aggregate_caps"]["provider_control_request_cap"],
        plan["aggregate_caps"]["provider_wire_request_cap"],
    )
    episode_results: list[dict[str, Any]] = []
    transport_records: list[dict[str, Any]] = []
    config = LLAMA_STATEFUL
    try:
        policy_record = plan["policies"][0]
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
                trial_id=f"d56-c-{task_record['ordinal']:02d}-{task.task_id}",
                task=task,
            )
            episode_results.append({"slot": config.slot, **result.to_dict()})
            if result.classification not in NORMAL_TERMINAL_CLASSIFICATIONS:
                break
        transport_records.extend(transport.records)
        classifications = Counter(
            result["classification"] for result in episode_results
        )
        summary = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "approved_plan_sha256": digest,
            "code_revision": plan["code_revision"],
            "provider_calls_made": ledger.wire_requests_sent,
            "provider_wire_requests": ledger.wire_requests_sent,
            "model_attempt_reservations": journal.call_counts()[0],
            "provider_control_requests": journal.call_counts()[1],
            "prior_aggregate_spend_usd": str(prior_spend),
            "actual_aggregate_spend_usd": str(ledger.spent_usd),
            "calibration_incremental_spend_usd": str(ledger.spent_usd - prior_spend),
            "remaining_aggregate_spend_usd": str(
                PANEL_MAXIMUM_SPEND_USD - ledger.spent_usd
            ),
            "maximum_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "assigned_policy_task_pairs": EXPECTED_TASK_COUNT,
            "attempted_policy_task_pairs": len(episode_results),
            "successful_policy_task_pairs": sum(
                result["success"] for result in episode_results
            ),
            "classifications": dict(sorted(classifications.items())),
            "episode_results": episode_results,
            "transport_records": transport_records,
            "frozen_panel_predecessor_evidence": plan[
                "frozen_panel_predecessor_evidence"
            ],
            "frozen_bcd_predecessor_evidence": plan[
                "frozen_bcd_predecessor_evidence"
            ],
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
