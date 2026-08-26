"""Plan and execute the ten-task full-episode Qwen3-VL calibration expansion."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.calibration_pilot import _load_calibration_records
from pixelgym.grounding.v5.contracts import CallCaps, content_digest, sha256_bytes
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.openrouter_policy import (
    COMPLETION_PRICE_PER_TOKEN_USD,
    MAX_OUTPUT_TOKENS,
    MAX_PROMPT_TOKENS,
    MODEL,
    PRICE_SOURCE,
    PROMPT_PRICE_PER_TOKEN_USD,
    REQUEST_MAXIMUM_USD,
    UPSTREAM_PROVIDER,
    OpenRouterV5Transport,
    QwenV5StatefulPolicy,
    build_policy_manifest,
)
from pixelgym.grounding.v5.runner import V5Runner

PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-expanded-calibration-plan-v1"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-expanded-calibration-result-v1"
PILOT_PLAN = Path("artifacts/grounding-v5-qwen3-vl-8b-calibration-pilot-plan.json")
PILOT_SUMMARY = Path(
    "artifacts/grounding-v5-qwen3-vl-8b-calibration-pilot-run/summary.json"
)
PILOT_JOURNAL = Path(
    "artifacts/grounding-v5-qwen3-vl-8b-calibration-pilot-run/attempts.sqlite"
)
APPROVED_PILOT_PLAN_DIGEST = (
    "sha256:fe6e9b03fd5b4c13d417596d1712073e2d375de03711a3aeca6e04cf2f55fd7a"
)
PRICE_OBSERVED_AT_UTC = "2026-08-26T06:49:29Z"
MAXIMUM_SPEND_USD = Decimal("5.00")
PRIOR_AGGREGATE_SPEND_USD = Decimal("0.004228237")
EXPECTED_PILOT_WIRE_REQUESTS = 20
EXPECTED_TASK_COUNT = 10
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


def _completed_pilot_evidence(repository_root: Path) -> dict[str, Any]:
    pilot_plan_path = repository_root / PILOT_PLAN
    pilot_summary_path = repository_root / PILOT_SUMMARY
    pilot_journal_path = repository_root / PILOT_JOURNAL
    plan = json.loads(pilot_plan_path.read_text(encoding="utf-8"))
    summary = json.loads(pilot_summary_path.read_text(encoding="utf-8"))
    if content_digest(plan) != APPROVED_PILOT_PLAN_DIGEST:
        raise ValueError("completed pilot plan digest mismatch")
    if summary.get("approved_plan_sha256") != APPROVED_PILOT_PLAN_DIGEST:
        raise ValueError("completed pilot summary approval mismatch")
    if summary.get("provider_wire_requests") != EXPECTED_PILOT_WIRE_REQUESTS:
        raise ValueError("completed pilot wire-request count mismatch")
    if Decimal(str(summary.get("actual_aggregate_spend_usd"))) != (
        PRIOR_AGGREGATE_SPEND_USD
    ):
        raise ValueError("completed pilot aggregate spend mismatch")
    journal = V5AttemptJournal(pilot_journal_path)
    try:
        journal_integrity = journal.integrity_report()
    finally:
        journal.close()
    if journal_integrity != summary.get("journal_integrity"):
        raise ValueError("completed pilot journal integrity mismatch")
    return {
        "approved_plan_sha256": APPROVED_PILOT_PLAN_DIGEST,
        "plan_path": PILOT_PLAN.as_posix(),
        "plan_sha256": _file_digest(pilot_plan_path),
        "summary_path": PILOT_SUMMARY.as_posix(),
        "summary_sha256": _file_digest(pilot_summary_path),
        "journal_path": PILOT_JOURNAL.as_posix(),
        "journal_sha256": _file_digest(pilot_journal_path),
        "provider_wire_requests": EXPECTED_PILOT_WIRE_REQUESTS,
        "actual_aggregate_spend_usd": str(PRIOR_AGGREGATE_SPEND_USD),
        "journal_integrity": journal_integrity,
    }


def build_plan(repository_root: Path) -> dict[str, Any]:
    revision = _git(repository_root, "rev-parse", "HEAD")
    manifest = build_policy_manifest(repository_root, code_revision=revision)
    selected_records = _load_calibration_records(repository_root)
    tasks: list[dict[str, Any]] = []
    for ordinal, record in enumerate(selected_records):
        task = generate_task(record["seed_record"]["seed"])
        if task.task_id != record["task_id"]:
            raise ValueError("selected calibration task identity mismatch")
        tasks.append(
            {
                "ordinal": ordinal,
                "seed": task.seed,
                "task_id": task.task_id,
                "family": task.seed_record.family.value,
                "max_episode_steps": task.max_episode_steps,
            }
        )
    if len(tasks) != EXPECTED_TASK_COUNT:
        raise ValueError("expanded calibration allocation must contain ten tasks")
    action_cap = sum(record["max_episode_steps"] for record in tasks)
    theoretical_run = REQUEST_MAXIMUM_USD * action_cap
    aggregate_upper_bound = PRIOR_AGGREGATE_SPEND_USD + theoretical_run
    if aggregate_upper_bound > MAXIMUM_SPEND_USD:
        raise ValueError("expanded calibration can exceed the approved aggregate spend cap")
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": (
            "ten-task full-episode expanded single-policy calibration run; not the full D5.6 "
            "four-policy calibration"
        ),
        "provider_calls_made": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "policy_manifest": manifest.to_dict(),
        "policy_manifest_digest": content_digest(manifest.to_dict()),
        "provider": {
            "name": "openrouter",
            "upstream_provider": UPSTREAM_PROVIDER,
            "only": [UPSTREAM_PROVIDER],
            "allow_fallbacks": False,
            "automatic_retries": False,
            "data_collection": "deny",
        },
        "model": MODEL,
        "price_record": {
            "source_url": PRICE_SOURCE,
            "observed_at_utc": PRICE_OBSERVED_AT_UTC,
            "currency": "USD",
            "prompt_per_token": str(PROMPT_PRICE_PER_TOKEN_USD),
            "completion_per_token": str(COMPLETION_PRICE_PER_TOKEN_USD),
            "max_prompt_tokens": MAX_PROMPT_TOKENS,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        },
        "allocation_rule": (
            "the same ten frozen calibration tasks as the approved two-action pilot, rerun from "
            "reset through each task's complete frozen action horizon"
        ),
        "tasks": tasks,
        "caps": {
            **CallCaps(action_cap, action_cap, 0, action_cap).to_dict(),
            "maximum_spend_usd": str(MAXIMUM_SPEND_USD),
            "prior_aggregate_spend_usd": str(PRIOR_AGGREGATE_SPEND_USD),
            "per_request_theoretical_maximum_usd": str(REQUEST_MAXIMUM_USD),
            "run_theoretical_maximum_usd": str(theoretical_run),
            "aggregate_upper_bound_usd": str(aggregate_upper_bound),
            "aggregate_headroom_usd": str(MAXIMUM_SPEND_USD - aggregate_upper_bound),
        },
        "completed_pilot_evidence": _completed_pilot_evidence(repository_root),
        "stop_rules": [
            "stop after the approved model-attempt reservation cap",
            "continue after a scored success or step-limit truncation so every assigned task remains in the denominator",
            "stop after the first transport, identity, cost, parse, invalid-action, or evidence-integrity failure",
            "do not retry or replace a failed or incomplete task",
            "do not expose confirmatory tasks",
        ],
        "approval_required": {
            "owner": "human",
            "exact_plan_sha256": "sha256 of canonical plan bytes",
        },
    }


def plan_digest(plan: dict[str, Any]) -> str:
    return content_digest(plan)


def execute_expanded_run(
    repository_root: Path,
    *,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    output_directory: Path,
    transport: OpenRouterV5Transport | None = None,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved expanded-run plan digest does not match the supplied plan")
    if plan != build_plan(repository_root):
        raise ValueError("expanded-run plan does not match the canonical request configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before calibration provider requests")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace expanded-run output: {output_directory}")
    manifest = build_policy_manifest(repository_root, code_revision=plan["code_revision"])
    if manifest.to_dict() != plan["policy_manifest"]:
        raise ValueError("runtime policy manifest differs from the approved plan")
    output_directory.mkdir(parents=True)
    journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    provider = transport or OpenRouterV5Transport(
        maximum_spend_usd=MAXIMUM_SPEND_USD,
        prior_spend_usd=PRIOR_AGGREGATE_SPEND_USD,
    )
    caps_value = plan["caps"]
    caps = CallCaps(
        caps_value["environment_action_cap"],
        caps_value["model_attempt_cap"],
        caps_value["provider_control_request_cap"],
        caps_value["provider_wire_request_cap"],
    )
    episode_results: list[dict[str, Any]] = []
    try:
        for task_record in plan["tasks"]:
            task = generate_task(task_record["seed"])
            if task.task_id != task_record["task_id"]:
                raise ValueError("generated calibration task differs from the approved plan")
            result = V5Runner(
                journal=journal,
                manifest=manifest,
                transport=provider,
                policy=QwenV5StatefulPolicy(),
                approved_caps=caps,
            ).run(
                trial_id=f"qwen3-vl-expanded-{task_record['ordinal']:02d}-{task.task_id}",
                task=task,
            )
            episode_results.append(result.to_dict())
            if result.classification not in NORMAL_TERMINAL_CLASSIFICATIONS:
                break
        classifications = Counter(
            result["classification"] for result in episode_results
        )
        summary = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "approved_plan_sha256": digest,
            "code_revision": plan["code_revision"],
            "provider_calls_made": getattr(
                provider, "wire_requests_sent", len(provider.records)
            ),
            "provider_wire_requests": getattr(
                provider, "wire_requests_sent", len(provider.records)
            ),
            "model_attempt_reservations": journal.call_counts()[0],
            "provider_control_requests": journal.call_counts()[1],
            "prior_aggregate_spend_usd": str(PRIOR_AGGREGATE_SPEND_USD),
            "actual_aggregate_spend_usd": str(provider.spent_usd),
            "run_incremental_spend_usd": str(
                provider.spent_usd - PRIOR_AGGREGATE_SPEND_USD
            ),
            "remaining_approved_spend_usd": str(
                MAXIMUM_SPEND_USD - provider.spent_usd
            ),
            "maximum_spend_usd": str(MAXIMUM_SPEND_USD),
            "model": MODEL,
            "upstream_provider": UPSTREAM_PROVIDER,
            "assigned_tasks": len(plan["tasks"]),
            "attempted_tasks": len(episode_results),
            "successful_tasks": sum(result["success"] for result in episode_results),
            "classifications": dict(sorted(classifications.items())),
            "episode_results": episode_results,
            "transport_records": provider.records,
            "journal_integrity": journal.integrity_report(),
            "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
        }
    finally:
        journal.close()
    (output_directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary
