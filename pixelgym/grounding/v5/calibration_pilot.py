"""Plan and execute the bounded ten-task Qwen3-VL v5 calibration pilot."""

from __future__ import annotations

import json
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import (
    CallCaps,
    Partition,
    WorkflowFamily,
    content_digest,
    sha256_bytes,
)
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

PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-calibration-pilot-plan-v1"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-calibration-pilot-result-v1"
CALIBRATION_MANIFEST = Path("artifacts/grounding-v5-manifests/calibration.json")
CORRECTED_SMOKE_PLAN = Path(
    "artifacts/grounding-v5-openrouter-smoke-plan-qwen3-vl-8b-instruct-normalized-v2.json"
)
CORRECTED_SMOKE_RESULT = Path(
    "artifacts/grounding-v5-openrouter-smoke-result-qwen3-vl-8b-instruct-normalized-v2.json"
)
PRICE_OBSERVED_AT_UTC = "2026-08-26T06:49:29Z"
MAXIMUM_SPEND_USD = Decimal("5.00")
PRIOR_DIAGNOSTIC_SPEND_USD = Decimal("0.000281307")
TASK_COUNT = 10
ACTIONS_PER_TASK = 2
MODEL_ATTEMPT_CAP = TASK_COUNT * ACTIONS_PER_TASK


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


def _load_calibration_records(repository_root: Path) -> tuple[dict[str, Any], ...]:
    value = json.loads((repository_root / CALIBRATION_MANIFEST).read_text(encoding="utf-8"))
    unsigned = dict(value)
    claimed = unsigned.pop("manifest_digest", None)
    if claimed != content_digest(unsigned):
        raise ValueError("calibration partition manifest digest mismatch")
    if value.get("partition") != Partition.CALIBRATION.value:
        raise ValueError("calibration partition identity mismatch")
    records = value.get("records")
    if not isinstance(records, list):
        raise TypeError("calibration partition records must be a list")
    selected: list[dict[str, Any]] = []
    for allocation_index in range(2):
        for family_index, family in enumerate(WorkflowFamily):
            if allocation_index == 1 and family_index >= 4:
                continue
            family_records = [
                record
                for record in records
                if record["seed_record"]["family"] == family.value
            ]
            selected.append(family_records[allocation_index])
    if len(selected) != TASK_COUNT:
        raise ValueError("calibration pilot allocation must contain ten tasks")
    return tuple(selected)


def build_plan(repository_root: Path) -> dict[str, Any]:
    revision = _git(repository_root, "rev-parse", "HEAD")
    manifest = build_policy_manifest(repository_root, code_revision=revision)
    records = _load_calibration_records(repository_root)
    theoretical_pilot = REQUEST_MAXIMUM_USD * MODEL_ATTEMPT_CAP
    aggregate_upper_bound = PRIOR_DIAGNOSTIC_SPEND_USD + theoretical_pilot
    if aggregate_upper_bound > MAXIMUM_SPEND_USD:
        raise ValueError("calibration pilot can exceed the approved aggregate spend cap")
    partition = json.loads(
        (repository_root / CALIBRATION_MANIFEST).read_text(encoding="utf-8")
    )
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": (
            "ten-task, two-action-per-task stateful calibration pilot; not the full D5.6 "
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
        "calibration_partition_manifest_digest": partition["manifest_digest"],
        "allocation_rule": (
            "one task per workflow family in enum order, then a second task for the first four "
            "families; preserve manifest order within family"
        ),
        "tasks": [
            {
                "ordinal": index,
                "seed": record["seed_record"]["seed"],
                "task_id": record["task_id"],
                "family": record["seed_record"]["family"],
                "actions_per_task": ACTIONS_PER_TASK,
            }
            for index, record in enumerate(records)
        ],
        "caps": {
            **CallCaps(
                environment_action_cap=MODEL_ATTEMPT_CAP,
                model_attempt_cap=MODEL_ATTEMPT_CAP,
                provider_control_request_cap=0,
                provider_wire_request_cap=MODEL_ATTEMPT_CAP,
            ).to_dict(),
            "maximum_spend_usd": str(MAXIMUM_SPEND_USD),
            "prior_diagnostic_spend_usd": str(PRIOR_DIAGNOSTIC_SPEND_USD),
            "per_request_theoretical_maximum_usd": str(REQUEST_MAXIMUM_USD),
            "pilot_theoretical_maximum_usd": str(theoretical_pilot),
            "aggregate_upper_bound_usd": str(aggregate_upper_bound),
        },
        "smoke_evidence": {
            "plan_path": CORRECTED_SMOKE_PLAN.as_posix(),
            "plan_sha256": _file_digest(repository_root / CORRECTED_SMOKE_PLAN),
            "result_path": CORRECTED_SMOKE_RESULT.as_posix(),
            "result_sha256": _file_digest(repository_root / CORRECTED_SMOKE_RESULT),
        },
        "stop_rules": [
            "stop after twenty model-attempt reservations",
            "stop after the first transport, identity, cost, parse, action, or evidence failure",
            "do not retry or replace a failed or incomplete task",
            "do not expose confirmatory tasks",
        ],
        "approval_required": {
            "owner": "human",
            "exact_plan_sha256": "sha256 of canonical plan bytes",
        },
    }
    return plan


def plan_digest(plan: dict[str, Any]) -> str:
    return content_digest(plan)


def execute_pilot(
    repository_root: Path,
    *,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    output_directory: Path,
    transport: OpenRouterV5Transport | None = None,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved pilot plan digest does not match the supplied plan")
    if plan != build_plan(repository_root):
        raise ValueError("pilot plan does not match the canonical request configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before calibration provider requests")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace pilot output directory: {output_directory}")
    output_directory.mkdir(parents=True)
    journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    manifest_value = plan["policy_manifest"]
    manifest = build_policy_manifest(repository_root, code_revision=plan["code_revision"])
    if manifest.to_dict() != manifest_value:
        raise ValueError("runtime policy manifest differs from the approved plan")
    provider = transport or OpenRouterV5Transport(
        maximum_spend_usd=MAXIMUM_SPEND_USD,
        prior_spend_usd=PRIOR_DIAGNOSTIC_SPEND_USD,
    )
    caps = CallCaps(MODEL_ATTEMPT_CAP, MODEL_ATTEMPT_CAP, 0, MODEL_ATTEMPT_CAP)
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
                trial_id=f"qwen3-vl-pilot-{task_record['ordinal']:02d}-{task.task_id}",
                task=task,
                action_limit=ACTIONS_PER_TASK,
            )
            episode_results.append(result.to_dict())
            if result.classification != "pilot_action_limit":
                break
        summary = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "approved_plan_sha256": digest,
            "provider_calls_made": getattr(
                provider, "wire_requests_sent", len(provider.records)
            ),
            "provider_wire_requests": getattr(
                provider, "wire_requests_sent", len(provider.records)
            ),
            "model_attempt_reservations": journal.call_counts()[0],
            "provider_control_requests": journal.call_counts()[1],
            "actual_aggregate_spend_usd": str(provider.spent_usd),
            "pilot_incremental_spend_usd": str(
                provider.spent_usd - PRIOR_DIAGNOSTIC_SPEND_USD
            ),
            "maximum_spend_usd": str(MAXIMUM_SPEND_USD),
            "model": MODEL,
            "upstream_provider": UPSTREAM_PROVIDER,
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
