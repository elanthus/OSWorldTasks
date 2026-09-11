"""No-call plans for separately approved Slot C successors."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pixelgym.grounding.v5.contracts import Partition, content_digest
from pixelgym.grounding.v5.panel_policy import (
    LLAMA_STATEFUL_VERTEX_DIAGNOSTIC,
    LLAMA_STATEFUL_VERTEX_SMOKE,
    MISTRAL_STATEFUL_CALIBRATION,
    MISTRAL_STATEFUL_CALIBRATION_RETRY,
    MISTRAL_STATEFUL_SMOKE,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.plan import CalibrationPlan, load_plan
from pixelgym.grounding.v5.planning import _validated_records


def build_slot_c_plan(
    root: Path,
    *,
    code_revision: str,
    phase: Literal["smoke", "diagnostic", "calibration"],
    maximum_spend_usd: str,
    output_directory: str,
    candidate: Literal["vertex", "mistral"] = "vertex",
    generation: Literal["v1", "v2"] = "v1",
) -> CalibrationPlan:
    """Allocate probes or Mistral calibration after verifying reviewed smoke evidence."""

    calibration = phase == "calibration" and candidate == "mistral"
    if generation not in {"v1", "v2"} or (generation == "v2" and not calibration):
        raise ValueError("v2 is available only for Mistral calibration")
    if phase not in {"smoke", "diagnostic"} and not calibration:
        raise ValueError("only smoke and diagnostic phases are supported; calibration is blocked")
    smoke = phase == "smoke"
    config = LLAMA_STATEFUL_VERTEX_SMOKE if smoke else LLAMA_STATEFUL_VERTEX_DIAGNOSTIC
    label = "Llama Scout Google Vertex"
    if candidate == "mistral":
        if not smoke and not calibration:
            raise ValueError("Mistral supports only smoke and reviewed calibration")
        config = MISTRAL_STATEFUL_CALIBRATION if calibration else MISTRAL_STATEFUL_SMOKE
        if generation == "v2":
            config = MISTRAL_STATEFUL_CALIBRATION_RETRY
        label = "Mistral Small 4 Mistral"
    elif candidate != "vertex":
        raise ValueError("unknown Slot C candidate")
    review_digest = verify_mistral_smoke_review(root) if calibration else None
    partition = Partition.CALIBRATION if calibration else Partition.DEVELOPMENT
    filename = "calibration-d56.json" if calibration else "development.json"
    manifest_path = Path("artifacts/grounding-v5-manifests/v2") / filename
    manifest = json.loads((root / manifest_path).read_text(encoding="utf-8"))
    records = _validated_records(manifest, partition=partition)
    families = sorted({record["seed_record"]["family"] for record in records})
    groups = [[r for r in records if r["seed_record"]["family"] == f] for f in families]
    ordered = tuple(
        group[index]
        for index in range(max(map(len, groups)))
        for group in groups
        if index < len(group)
    )
    records = records if calibration else ordered[:10 if smoke else 1]
    if len(records) != (50 if calibration else 10 if smoke else 1):
        raise ValueError("unexpected Slot C partition size")
    assignments = [
        {
            "ordinal": index,
            "slot": config.slot,
            "seed": record["seed_record"]["seed"],
            "task_id": record["task_id"],
            "family": record["seed_record"]["family"],
            "action_limit": record["max_episode_steps"] if calibration else 2 if smoke else 1,
        }
        for index, record in enumerate(records)
    ]
    action_cap = sum(item["action_limit"] for item in assignments)
    attempt_cap = action_cap * config.max_model_attempts_per_action
    policy = build_panel_policy_manifest(root, config=config, code_revision=code_revision).to_dict()
    return CalibrationPlan.from_dict(
        {
            "schema_version": "pixelgym-agent-v5-runner-plan-v1",
            "purpose": f"fresh Slot C {label} {phase}; no predecessor pooling",
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
                *(
                    [f"calibration prerequisite: reviewed Mistral smoke evidence {review_digest}",
                     "calibration approval does not authorize confirmatory calls"]
                    if calibration else []
                ),
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


def verify_mistral_smoke_review(root: Path) -> str:
    """Verify the versioned technical review against its immutable local source files.

    The receipt records a completed journal audit, not a user-supplied success flag.
    A public clone without the restricted source journal cannot prepare a paid run.
    """

    review = json.loads((root / "plans/slot-c-mistral-smoke-review.json").read_text())
    if review["summary_validation"] != "valid" or not review["original_files_unchanged"]:
        raise ValueError("Mistral smoke review is not valid")
    paths = review["source_file_sha256"]
    for relative, expected in paths.items():
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("smoke review source escapes repository")
        if "sha256:" + sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"reviewed smoke source hash mismatch: {relative}")
    plan_path = "artifacts/grounding-v5-slot-c-mistral-smoke-plan.json"
    smoke = load_plan(root / plan_path)
    summary_path = (smoke.outputs.directory / smoke.outputs.summary).as_posix()
    journal_path = (smoke.outputs.directory / smoke.outputs.journal).as_posix()
    if set(paths) != {plan_path, summary_path, journal_path}:
        raise ValueError("smoke review must bind plan, summary, and journal")
    summary = json.loads((root / summary_path).read_text())
    if (
        review["approved_plan_sha256"] != smoke.digest
        or summary["approved_plan_sha256"] != smoke.digest
        or summary["code_revision"] != smoke.code_revision
        or review["source_code_revision"] != smoke.code_revision
        or len(smoke.policies) != 1
        or smoke.policies[0].slot != MISTRAL_STATEFUL_SMOKE.slot
        or len(smoke.assignments) != 10
        or {a.action_limit for a in smoke.assignments} != {2}
        or summary["classifications"] != {"pilot_action_limit": 10}
        or summary["completed_all_assigned_pairs"] is not True
        or summary["execution_error"] is not None
        or summary["provider_accounting"] != {
            "provider_calls_made": 20, "unknown_charge_outcomes": 0,
        }
        or summary["spend"]["blocked"]
        or summary["spend"]["unknown_reservation_usd"] != "0"
        or summary["cleanup"] != {"journal_closed": True, "provider_adapter_closed": True}
        or review["reconstructed_requests_matching_journal"] != 20
        or review["validated_responses"] != 20
        or review["episodes_with_verified_history_carryover"] != 10
    ):
        raise ValueError("Mistral smoke does not establish complete compatibility")
    old = dict(smoke.policies[0].policy_manifest)
    current = build_panel_policy_manifest(
        root, config=MISTRAL_STATEFUL_SMOKE, code_revision=smoke.code_revision,
    ).to_dict()
    # Preparation/registration changes require a new revision and runtime digest.
    # Every other declared policy field must retain the smoke's tested contract.
    for value in (old, current):
        value.pop("policy_id")
        value["sandbox"] = dict(value["sandbox"])
        value["sandbox"].pop("runtime_digest")
    if old != current:
        raise ValueError("Mistral policy contract differs from reviewed smoke")
    return content_digest(review)
