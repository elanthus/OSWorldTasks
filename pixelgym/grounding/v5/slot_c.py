"""No-call plans for the separately approved Slot C Google Vertex successor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pixelgym.grounding.v5.contracts import Partition, content_digest
from pixelgym.grounding.v5.panel_policy import (
    LLAMA_STATEFUL_VERTEX_DIAGNOSTIC,
    LLAMA_STATEFUL_VERTEX_SMOKE,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.plan import CalibrationPlan
from pixelgym.grounding.v5.planning import _validated_records


def build_slot_c_plan(
    root: Path,
    *,
    code_revision: str,
    phase: Literal["smoke", "diagnostic"],
    maximum_spend_usd: str,
    output_directory: str,
) -> CalibrationPlan:
    """Allocate development probes; calibration awaits successful smoke review."""

    if phase not in {"smoke", "diagnostic"}:
        raise ValueError("only smoke and diagnostic phases are supported; calibration is blocked")
    smoke = phase == "smoke"
    config = LLAMA_STATEFUL_VERTEX_SMOKE if smoke else LLAMA_STATEFUL_VERTEX_DIAGNOSTIC
    partition = Partition.DEVELOPMENT
    filename = "development.json"
    manifest_path = Path("artifacts/grounding-v5-manifests/v2") / filename
    manifest = json.loads((root / manifest_path).read_text(encoding="utf-8"))
    records = _validated_records(manifest, partition=partition)
    families = sorted({record["seed_record"]["family"] for record in records})
    groups = [[r for r in records if r["seed_record"]["family"] == f] for f in families]
    records = tuple(
        group[index]
        for index in range(max(map(len, groups)))
        for group in groups
        if index < len(group)
    )[:10 if smoke else 1]
    if len(records) != (10 if smoke else 1):
        raise ValueError("unexpected Slot C partition size")
    assignments = [
        {
            "ordinal": index,
            "slot": config.slot,
            "seed": record["seed_record"]["seed"],
            "task_id": record["task_id"],
            "family": record["seed_record"]["family"],
            "action_limit": 2 if smoke else 1,
        }
        for index, record in enumerate(records)
    ]
    action_cap = sum(item["action_limit"] for item in assignments)
    attempt_cap = action_cap * config.max_model_attempts_per_action
    policy = build_panel_policy_manifest(root, config=config, code_revision=code_revision).to_dict()
    return CalibrationPlan.from_dict(
        {
            "schema_version": "pixelgym-agent-v5-runner-plan-v1",
            "purpose": f"fresh Slot C Llama Scout Google Vertex {phase}; no predecessor pooling",
            "code_revision": code_revision,
            "policy_panel": [
                {
                    "slot": config.slot,
                    "policy_manifest": policy,
                    "policy_manifest_digest": content_digest(policy),
                }
            ],
            "task_allocation": {
                "manifest_path": manifest_path.as_posix(),
                "manifest_digest": manifest["manifest_digest"],
                "assignments": assignments,
            },
            "provider": {"adapter": "openrouter_http", "transport": "http", "config": {}},
            "budgets": {
                "environment_action_cap": action_cap,
                "model_attempt_cap": attempt_cap,
                "provider_control_request_cap": 0,
                "provider_wire_request_cap": attempt_cap,
                "maximum_spend_usd": maximum_spend_usd,
                "per_request_theoretical_maximum_usd": str(config.request_maximum_usd),
                "unknown_reservation_rule": "retain every unknown reservation",
            },
            "retry_breaker": {
                "max_bounded_retries_per_action": config.bounded_retry_budget,
                "consecutive_failure_limit": 3,
                "continue_classifications": [
                    "success_termination",
                    "step_limit_truncation",
                    "pilot_action_limit",
                    "invalid_output",
                ],
                "hard_stop_classifications": [
                    "infrastructure_failure",
                    "request_failure",
                    "policy_violation",
                ],
            },
            "stop_conditions": [
                "stop before any send exceeding the fresh run's approved spend or call cap",
                "retain every failure and unknown-charge reservation; never reuse old approval",
                "stop on an infrastructure failure, request failure, or policy violation",
                "retain invalid output without retry; stop after three consecutive failures",
                "smoke approval does not authorize calibration or confirmatory calls",
                "diagnostic approval authorizes one development request only, not another smoke",
            ],
            "outputs": {
                "directory": output_directory,
                "journal": "attempts.sqlite",
                "summary": "summary.json",
                "resume_mode": "forbid",
            },
            "requires_clean_tracked_worktree": True,
            "provider_calls_made_while_planning": 0,
            "approval_required": {
                "owner": "human",
                "exact_plan_sha256": "sha256 of canonical plan bytes",
            },
        }
    )
