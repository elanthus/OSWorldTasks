"""No-call planning and denominator-preserving diagnostics for a matched memory pair."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from pixelgym.grounding.v5.contracts import Partition, content_digest
from pixelgym.grounding.v5.metrics import paired_success_table
from pixelgym.grounding.v5.panel_policy import (
    QWEN_STATEFUL_RETRY_SUCCESSOR,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.plan import CalibrationPlan
from pixelgym.grounding.v5.planning import _validated_records

QWEN_CONTROLLED_STATEFUL = replace(
    QWEN_STATEFUL_RETRY_SUCCESSOR,
    slot="qwen-controlled-stateful-v1",
    controlled_history_prompt=True,
)
QWEN_CONTROLLED_STATELESS = replace(
    QWEN_CONTROLLED_STATEFUL, slot="qwen-controlled-stateless-v1", stateful=False
)
CONTROLLED_PAIR = (QWEN_CONTROLLED_STATEFUL, QWEN_CONTROLLED_STATELESS)
CONTROLLED_PAIR_V2 = tuple(
    replace(config, slot=config.slot.replace("-v1", "-v2"), rate_limit_backoff_base_seconds=15.0)
    for config in CONTROLLED_PAIR
)
SMOKE_PAIR = tuple(
    replace(
        config,
        slot=config.slot + "-smoke",
        max_model_attempts_per_action=1,
        max_rate_limit_retries_per_action=0,
        max_bounded_retries_per_action=0,
    )
    for config in CONTROLLED_PAIR
)
SMOKE_PAIR_V2 = tuple(
    replace(config, slot=config.slot.replace("-v1", "-v2"), rate_limit_backoff_base_seconds=15.0)
    for config in SMOKE_PAIR
)


def validate_controlled_pair(plan: CalibrationPlan) -> tuple[str, str]:
    """Reject every manifest difference except the declared memory intervention."""

    if len(plan.policies) != 2:
        raise ValueError("controlled comparison requires exactly two policy slots")
    stateful, stateless = plan.policies
    permitted = {"policy_id", "memory_policy_version", "state_reducer_version"}
    first = stateful.policy_manifest
    second = stateless.policy_manifest
    differences = {key for key in first.keys() | second.keys() if first.get(key) != second.get(key)}
    if differences != permitted:
        raise ValueError(f"uncontrolled policy differences: {sorted(differences)}")
    for manifest, config in zip((first, second), CONTROLLED_PAIR, strict=True):
        if (
            manifest.get("memory_policy_version") != config.memory_policy_version
            or manifest.get("state_reducer_version") != config.state_reducer_version
            or manifest.get("code_revision") != plan.code_revision
        ):
            raise ValueError("controlled memory direction or code revision mismatch")
    allocated = []
    for policy in plan.policies:
        allocated.append(
            [
                (item.seed, item.task_id, item.family, item.action_limit)
                for item in plan.assignments
                if item.slot == policy.slot
            ]
        )
    if not allocated[0] or allocated[0] != allocated[1]:
        raise ValueError("controlled arms require identical ordered task assignments and caps")
    return stateful.slot, stateless.slot


def build_comparison_plan(
    root: Path,
    *,
    code_revision: str,
    phase: Literal["smoke", "calibration"],
    maximum_spend_usd: str,
    output_directory: str,
    generation: Literal["v1", "v2"] = "v1",
) -> CalibrationPlan:
    """Freeze ten development probes or fifty full calibration episodes per arm."""

    if phase not in {"smoke", "calibration"}:
        raise ValueError("only smoke and calibration phases are supported")
    if generation not in {"v1", "v2"}:
        raise ValueError("unsupported controlled comparison generation")
    partition = Partition.DEVELOPMENT if phase == "smoke" else Partition.CALIBRATION
    filename = "development.json" if phase == "smoke" else "calibration-d56.json"
    manifest_path = Path("artifacts/grounding-v5-manifests/v2") / filename
    manifest = json.loads((root / manifest_path).read_text(encoding="utf-8"))
    records = _validated_records(manifest, partition=partition)
    if phase == "smoke":
        # Round-robin the families so the ten probes cover all six families.
        families = sorted({record["seed_record"]["family"] for record in records})
        groups = [[r for r in records if r["seed_record"]["family"] == f] for f in families]
        records = tuple(
            group[index]
            for index in range(max(map(len, groups)))
            for group in groups
            if index < len(group)
        )[:10]
    if len(records) != (10 if phase == "smoke" else 50):
        raise ValueError("unexpected comparison partition size")
    if generation == "v1":
        configs = SMOKE_PAIR if phase == "smoke" else CONTROLLED_PAIR
    else:
        configs = SMOKE_PAIR_V2 if phase == "smoke" else CONTROLLED_PAIR_V2
    policies = []
    for config in configs:
        policy = build_panel_policy_manifest(root, config=config, code_revision=code_revision)
        policies.append(
            {
                "slot": config.slot,
                "policy_manifest": policy.to_dict(),
                "policy_manifest_digest": content_digest(policy.to_dict()),
            }
        )
    assignments: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        # Adjacent pairs, alternating which arm goes first, reduce order confounding.
        for config in configs if index % 2 == 0 else configs[::-1]:
            assignments.append(
                {
                    "ordinal": len(assignments),
                    "slot": config.slot,
                    "seed": record["seed_record"]["seed"],
                    "task_id": record["task_id"],
                    "family": record["seed_record"]["family"],
                    "action_limit": 1 if phase == "smoke" else record["max_episode_steps"],
                }
            )
    action_cap = sum(item["action_limit"] for item in assignments)
    attempt_cap = action_cap * configs[0].max_model_attempts_per_action
    plan = CalibrationPlan.from_dict(
        {
            "schema_version": "pixelgym-agent-v5-runner-plan-v1",
            "purpose": f"controlled Qwen visible-action-history comparison: {phase}",
            "code_revision": code_revision,
            "policy_panel": policies,
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
                "per_request_theoretical_maximum_usd": str(configs[0].request_maximum_usd),
                "unknown_reservation_rule": "retain every unknown reservation",
            },
            "retry_breaker": {
                "max_bounded_retries_per_action": configs[0].bounded_retry_budget,
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
    validate_controlled_pair(plan)
    return plan


def summarize_comparison(plan: CalibrationPlan, summary: dict[str, Any]) -> dict[str, Any]:
    """Describe stored runner outcomes; missing assignments never become observed failures.

    This is a summary-level derivative, not an independent journal-integrity audit.
    No significance test is calculated: robustness twins are not independent observations.
    """

    first, second = validate_controlled_pair(plan)
    if summary.get("approved_plan_sha256") != plan.digest:
        raise ValueError("comparison summary does not bind the approved plan")
    if summary.get("code_revision") != plan.code_revision:
        raise ValueError("comparison summary code revision mismatch")
    expected = {(a.slot, a.task_id): a for a in plan.assignments}
    rows = summary.get("episode_results")
    if not isinstance(rows, list):
        raise TypeError("comparison summary requires stored episode results")
    observed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["slot"], row["task_id"])
        if key not in expected or key in observed:
            raise ValueError("unknown or duplicated comparison assignment")
        assignment = expected[key]
        if row.get("trial_id") != (
            f"manifest-{assignment.slot}-{assignment.ordinal:04d}-{assignment.task_id}"
        ):
            raise ValueError("comparison trial identity mismatch")
        if type(row.get("success")) is not bool or row["success"] != (
            row.get("classification") == "success_termination"
        ):
            raise ValueError("comparison success and classification disagree")
        if row["classification"] not in (
            *plan.retry_breaker.continue_classifications,
            *plan.retry_breaker.hard_stop_classifications,
        ):
            raise ValueError("comparison classification outside the approved plan")
        observed[key] = row
    tasks = [a.task_id for a in plan.assignments if a.slot == first]
    items = []
    for task in tasks:
        assignment = expected[first, task]
        outcomes = {slot: observed.get((slot, task)) for slot in (first, second)}
        items.append(
            {
                "task_id": task,
                "family": assignment.family,
                "outcomes": {
                    slot: None
                    if row is None
                    else {"success": row["success"], "classification": row["classification"]}
                    for slot, row in outcomes.items()
                },
            }
        )
    complete = len(observed) == len(expected)
    full_episodes = all(item.action_limit > 1 for item in plan.assignments)
    paired = [task for task in tasks if all((slot, task) in observed for slot in (first, second))]
    table = (
        paired_success_table(
            {task: observed[first, task]["success"] for task in paired},
            {task: observed[second, task]["success"] for task in paired},
        )
        if paired
        else None
    )
    return {
        "schema_version": "pixelgym-controlled-comparison-summary-v1",
        "plan_sha256": plan.digest,
        "source_summary_sha256": content_digest(summary),
        "evidence_scope": "stored summary only; restricted attempt journal not independently audited",
        "complete_assigned_denominator": complete,
        "full_episode_comparison": full_episodes,
        "assigned_pairs": len(tasks),
        "observed_pairs": len(paired),
        "missing_assignments": len(expected) - len(observed),
        "paired_observed_outcomes": table,
        "success_rate_difference": (
            (table["first_only"] - table["second_only"]) / len(tasks)
            if complete and full_episodes and table is not None
            else None
        ),
        "classifications_by_slot": {
            slot: dict(
                sorted(
                    Counter(
                        row["classification"] for (s, _), row in observed.items() if s == slot
                    ).items()
                )
            )
            for slot in (first, second)
        },
        "items": items,
        "limitations": [
            "calibration diagnostics only; no benchmark or human-gate verdict",
            "intervention is visible-action history, not retained screenshot or semantic memory",
            "provider alias is not an immutable model snapshot",
            "observed-pair counts in an incomplete run are descriptive and may be selected",
        ],
    }
