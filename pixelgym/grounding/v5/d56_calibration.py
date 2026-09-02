"""Plan and execute the exact four-policy, fifty-task D5.6 calibration."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import CallCaps, content_digest, sha256_bytes
from pixelgym.grounding.v5.evidence import repository_relative_path
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import (
    BOUNDED_RETRY_STOP_RULE,
    LLAMA_STATEFUL,
    PANEL,
    PANEL_BY_SLOT,
    PANEL_MAXIMUM_SPEND_USD,
    PRIOR_AGGREGATE_SPEND_USD,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.panel_smoke import PRICE_OBSERVED_AT_UTC
from pixelgym.grounding.v5.planning import call_cap_plan, load_partition_manifests
from pixelgym.grounding.v5.runner import V5Runner

PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-calibration-plan-v2"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-calibration-result-v2"
CALIBRATION_MANIFEST = Path("artifacts/grounding-v5-manifests/calibration-d56.json")
EXPECTED_TASK_COUNT = 50
EXPECTED_PANEL_SLOTS = tuple(config.slot for config in PANEL)
NORMAL_TERMINAL_CLASSIFICATIONS = frozenset(
    {"success_termination", "step_limit_truncation"}
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
    return "sha256:" + sha256_bytes(path.read_bytes())


def _calibration_manifest(repository_root: Path) -> dict[str, Any]:
    path = repository_root / CALIBRATION_MANIFEST
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("D5.6 calibration manifest must be an object")
    unsigned = dict(value)
    claimed = unsigned.pop("manifest_digest", None)
    if claimed != content_digest(unsigned):
        raise ValueError("D5.6 calibration manifest digest mismatch")
    records = value.get("records")
    if value.get("schema_version") != "pixelgym-agent-v5-partition-v4" or not isinstance(
        records, list
    ):
        raise ValueError("D5.6 calibration manifest schema mismatch")
    if len(records) != EXPECTED_TASK_COUNT:
        raise ValueError("D5.6 calibration manifest must contain fifty tasks")
    if any(
        type(record.get("max_episode_steps")) is not int
        or record["max_episode_steps"] <= 0
        for record in records
    ):
        raise ValueError("D5.6 calibration manifest action caps are invalid")
    return value


def _validated_smoke_evidence(repository_root: Path, smoke_output_directory: Path) -> dict[str, Any]:
    summary_path = smoke_output_directory / "summary.json"
    journal_path = smoke_output_directory / "attempts.sqlite"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        raise TypeError("panel-smoke summary must be an object")
    if summary.get("schema_version") != "pixelgym-agent-v5-panel-smoke-result-v2":
        raise ValueError("panel-smoke summary schema mismatch")
    results = summary.get("episode_results")
    if not isinstance(results, list) or len(results) != 4:
        raise ValueError("all four panel smoke assignments must be present")
    if tuple(result.get("slot") for result in results) != EXPECTED_PANEL_SLOTS:
        raise ValueError("panel-smoke slot identities or order mismatch")
    if any(result.get("classification") != "pilot_action_limit" for result in results):
        raise ValueError("all four panel smoke assignments must reach the action limit")
    provider_requests = summary.get("provider_wire_requests")
    if (
        type(provider_requests) is not int
        or not 4 <= provider_requests <= 8
        or summary.get("provider_calls_made") != provider_requests
        or summary.get("model_attempt_reservations") != provider_requests
        or summary.get("provider_control_requests") != 0
    ):
        raise ValueError("panel-smoke request counts mismatch")
    approved_plan = summary.get("approved_plan_sha256")
    if not isinstance(approved_plan, str) or not approved_plan.startswith("sha256:"):
        raise ValueError("panel-smoke approval digest is invalid")
    actual_spend = Decimal(str(summary.get("actual_aggregate_spend_usd")))
    if not actual_spend.is_finite() or not (
        PRIOR_AGGREGATE_SPEND_USD <= actual_spend <= PANEL_MAXIMUM_SPEND_USD
    ):
        raise ValueError("panel-smoke aggregate spend is invalid")
    if not journal_path.is_file():
        raise FileNotFoundError("panel-smoke journal is missing")
    journal = V5AttemptJournal(journal_path)
    try:
        if journal.call_counts() != (provider_requests, 0):
            raise ValueError("panel-smoke journal request counts mismatch")
        events = journal.events()
        retry_events = [
            event for event in events if event.kind == "retryable_provider_response"
        ]
        if len(retry_events) != provider_requests - 4 or any(
            event.payload.get("failure_code") != "zero_completion_error"
            for event in retry_events
        ):
            raise ValueError("panel-smoke retry evidence mismatch")
        journal_integrity = journal.integrity_report()
    finally:
        journal.close()
    if journal_integrity != summary.get("journal_integrity"):
        raise ValueError("panel-smoke journal integrity mismatch")
    return {
        "approved_plan_sha256": approved_plan,
        "summary_path": repository_relative_path(repository_root, summary_path),
        "summary_sha256": _file_digest(summary_path),
        "journal_path": repository_relative_path(repository_root, journal_path),
        "journal_sha256": _file_digest(journal_path),
        "provider_wire_requests": provider_requests,
        "actual_aggregate_spend_usd": str(actual_spend),
        "journal_integrity": journal_integrity,
    }


def build_plan(repository_root: Path, *, smoke_output_directory: Path) -> dict[str, Any]:
    revision = _git(repository_root, "rev-parse", "HEAD")
    partition = _calibration_manifest(repository_root)
    smoke_evidence = _validated_smoke_evidence(repository_root, smoke_output_directory)
    prior_spend = Decimal(smoke_evidence["actual_aggregate_spend_usd"])
    action_cap = sum(record["max_episode_steps"] for record in partition["records"])
    partition_manifests = load_partition_manifests(
        repository_root / "artifacts/grounding-v5-manifests",
        calibration_manifest=repository_root / CALIBRATION_MANIFEST,
    )
    policies: list[dict[str, Any]] = []
    aggregate_theoretical_maximum = Decimal(0)
    for config in PANEL:
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
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": "complete four-policy D5.6 calibration on fifty pilot-unexposed tasks",
        "provider_calls_made": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "calibration_partition": {
            "path": CALIBRATION_MANIFEST.as_posix(),
            "file_sha256": _file_digest(repository_root / CALIBRATION_MANIFEST),
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
                action_cap * len(PANEL),
                action_cap * len(PANEL) * 2,
                0,
                action_cap * len(PANEL) * 2,
            ).to_dict(),
            "maximum_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "prior_aggregate_spend_usd": str(prior_spend),
            "remaining_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD - prior_spend),
            "uncapped_theoretical_request_maximum_usd": str(
                aggregate_theoretical_maximum
            ),
            "enforcement": (
                "before each wire request, reserve that slot's worst-case request cost against "
                "the shared aggregate ledger; stop before a request that cannot fit"
            ),
        },
        "smoke_evidence": smoke_evidence,
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
            "run policy slots sequentially in A, B, C, D order and tasks in frozen manifest order",
            "continue after success termination or step-limit truncation so assigned tasks remain in the denominator",
            BOUNDED_RETRY_STOP_RULE,
            "retain both attempts and stop after a repeated retryable provider error",
            "stop the complete panel after the first other transport, identity, cost, parse, adapter, invalid-action, or evidence-integrity failure",
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
    output_directory: Path,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved D5.6 plan digest does not match the supplied plan")
    if LLAMA_STATEFUL.adapter.name != "native-1024x768":
        raise RuntimeError(
            "native-coordinate D5.6 calibration is frozen; use the normalized Slot C trial"
        )
    if plan != build_plan(
        repository_root, smoke_output_directory=smoke_output_directory
    ):
        raise ValueError("D5.6 plan does not match the canonical request configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before calibration provider requests")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace D5.6 output: {output_directory}")
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
                        f"d56-{config.slot}-{task_record['ordinal']:02d}-{task.task_id}"
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
            "assigned_policy_task_pairs": len(PANEL) * EXPECTED_TASK_COUNT,
            "attempted_policy_task_pairs": len(episode_results),
            "successful_policy_task_pairs": sum(
                result["success"] for result in episode_results
            ),
            "classifications": dict(sorted(classifications.items())),
            "episode_results": episode_results,
            "transport_records": transport_records,
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
