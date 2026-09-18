"""No-call D5.9 confirmatory freeze construction and validation."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.admission import validate_task_admission
from pixelgym.grounding.v5.contracts import DifficultyBand, WorkflowFamily, content_digest
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_generator import (
    MEMORY_GENERATOR_VERSION,
    generate_memory_task,
)

D59_CONFIRMATORY_SEEDS = tuple(range(6000, 6192))
HISTORICAL_SPEND_USD = Decimal("23.978227275")
AGGREGATE_PLANNING_CAP_USD = Decimal("120.00")
PRIMARY_PHASE_CAP_USD = Decimal("85.00")
RELIABILITY_PHASE_CAP_USD = AGGREGATE_PLANNING_CAP_USD - HISTORICAL_SPEND_USD - PRIMARY_PHASE_CAP_USD
MAX_MODEL_ATTEMPTS_PER_ACTION = 3
RELIABILITY_REPEATS_PER_ARM = 2

SOURCE_FILES = (
    "pixelgym/grounding/v5/contracts.py",
    "pixelgym/grounding/v5/generator.py",
    "pixelgym/grounding/v5/seeds.py",
    "pixelgym/grounding/v5/memory_generator.py",
    "pixelgym/grounding/v5/schemas/memory-task-v3.schema.json",
    "pixelgym/grounding/v5/memory_backend.py",
    "pixelgym/grounding/v5/screenshot_memory.py",
    "pixelgym/grounding/v5/request_budget.py",
    "pixelgym/grounding/v5/admission.py",
)


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def confirmatory_tasks() -> tuple[Any, ...]:
    return tuple(generate_memory_task(seed) for seed in D59_CONFIRMATORY_SEEDS)


def representative_tasks(tasks: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(task for task in tasks if task.seed_record.variant != "twin_b")


def reliability_tasks(tasks: tuple[Any, ...]) -> tuple[Any, ...]:
    by_family: dict[WorkflowFamily, list[Any]] = defaultdict(list)
    for task in representative_tasks(tasks):
        by_family[task.family].append(task)
    return tuple(
        task
        for family in WorkflowFamily
        for task in sorted(by_family[family], key=lambda value: value.seed)[:2]
    )


def validate_confirmatory_design(tasks: tuple[Any, ...]) -> dict[str, Any]:
    if tuple(task.seed for task in tasks) != D59_CONFIRMATORY_SEEDS:
        raise ValueError("D5.9 tasks do not match the selected seed order")
    if len({task.task_id for task in tasks}) != 192:
        raise ValueError("D5.9 task IDs are not unique")
    family_counts = Counter(task.family.value for task in tasks)
    band_counts = Counter(task.seed_record.difficulty_band.value for task in tasks)
    representatives = representative_tasks(tasks)
    twins: dict[str, list[Any]] = defaultdict(list)
    for task in tasks:
        if task.seed_record.robustness_pair:
            twins[task.seed_record.logical_id].append(task)
    if family_counts != Counter({family.value: 32 for family in WorkflowFamily}):
        raise ValueError("D5.9 family allocation changed")
    if band_counts != Counter(
        {
            DifficultyBand.REGRESSION.value: 42,
            DifficultyBand.FRONTIER.value: 108,
            DifficultyBand.CEILING.value: 42,
        }
    ):
        raise ValueError("D5.9 difficulty-band allocation changed")
    if len(representatives) != 168 or len(twins) != 24:
        raise ValueError("D5.9 logical-task allocation changed")
    if any(
        len(pair) != 2
        or {task.seed_record.variant for task in pair} != {"twin_a", "twin_b"}
        or len({task.semantic_digest for task in pair}) != 1
        for pair in twins.values()
    ):
        raise ValueError("D5.9 robustness twins changed semantics")
    reliability = reliability_tasks(tasks)
    if len(reliability) != 12 or Counter(task.family for task in reliability) != Counter(
        {family: 2 for family in WorkflowFamily}
    ):
        raise ValueError("D5.9 reliability selection changed")
    return {
        "episode_count_per_arm": len(tasks),
        "independent_representative_count": len(representatives),
        "robustness_pair_count": len(twins),
        "family_counts": dict(sorted(family_counts.items())),
        "difficulty_band_counts": dict(sorted(band_counts.items())),
        "reliability_seeds": [task.seed for task in reliability],
    }


def source_binding(root: Path, *, source_revision: str) -> dict[str, Any]:
    files = {name: _sha256(root / name) for name in SOURCE_FILES}
    value = {
        "schema_version": "pixelgym-d59-source-binding-v1",
        "source_revision": source_revision,
        "generator_version": MEMORY_GENERATOR_VERSION,
        "files": files,
    }
    return {**value, "binding_digest": content_digest(value)}


def task_manifest(root: Path, *, source_revision: str) -> dict[str, Any]:
    tasks = confirmatory_tasks()
    allocation = validate_confirmatory_design(tasks)
    records = [
        {
            "seed_record": task.seed_record.to_dict(),
            "task_id": task.task_id,
            "task_digest": content_digest(task.canonical_dict()),
            "semantic_digest": task.semantic_digest,
            "max_episode_steps": task.max_episode_steps,
            "canonical_task": task.canonical_dict(),
        }
        for task in tasks
    ]
    value = {
        "schema_version": "pixelgym-agent-v5-d59-confirmatory-manifest-v1",
        "status": "frozen_no_call_candidate",
        "partition": "confirmatory",
        "generator_version": MEMORY_GENERATOR_VERSION,
        "source_binding": source_binding(root, source_revision=source_revision),
        "allocation": allocation,
        "records": records,
        "provider_calls_made": 0,
    }
    return {**value, "manifest_digest": content_digest(value)}


def admission_evidence() -> dict[str, Any]:
    tasks = confirmatory_tasks()
    records = [
        validate_task_admission(task, backend_factory=MemoryBackend) for task in tasks
    ]
    value = {
        "schema_version": "pixelgym-agent-v5-d59-admission-v1",
        "partition": "confirmatory",
        "task_count": len(records),
        "tasks": records,
        "provider_calls_made": 0,
    }
    return {**value, "evidence_digest": content_digest(records)}


def _job(task: Any, *, mode: str, phase: str, repeat: int = 0) -> dict[str, Any]:
    return {
        "trial_id": f"d59-{phase}-{task.seed}-{mode}-r{repeat}",
        "phase": phase,
        "repeat": repeat,
        "mode": mode,
        "seed": task.seed,
        "seed_record": task.seed_record.to_dict(),
        "task_id": task.task_id,
        "task_digest": content_digest(task.canonical_dict()),
        "action_limit": task.max_episode_steps,
    }


def _caps(jobs: list[dict[str, Any]]) -> dict[str, int]:
    actions = sum(job["action_limit"] for job in jobs)
    attempts = actions * MAX_MODEL_ATTEMPTS_PER_ACTION
    return {
        "environment_action_cap": actions,
        "model_attempt_cap": attempts,
        "provider_control_request_cap": 0,
        "provider_wire_request_cap": attempts,
    }


def execution_plan(
    root: Path,
    *,
    source_revision: str,
    price_snapshot: dict[str, Any],
) -> dict[str, Any]:
    tasks = confirmatory_tasks()
    allocation = validate_confirmatory_design(tasks)
    primary_jobs = [
        _job(task, mode=mode, phase="primary")
        for task in tasks
        for mode in ("history", "stateless")
    ]
    reliability_jobs = [
        _job(task, mode=mode, phase="reliability", repeat=repeat)
        for task in reliability_tasks(tasks)
        for mode in ("history", "stateless")
        for repeat in range(1, RELIABILITY_REPEATS_PER_ARM + 1)
    ]
    prior = json.loads(
        (root / "artifacts/grounding-v5-d58-owner-budget-continuation/execution-plan.json")
        .read_text(encoding="utf-8")
    )
    policies = prior["policy_manifests"]
    if {value["policy_id"] for value in policies.values()} != {
        "policy-f3643833caa8b2e9a526",
        "policy-1c80069711f0cf37e792",
    }:
        raise ValueError("D5.9 candidate policy identities changed")
    endpoint = price_snapshot["endpoints"]
    if len(endpoint) != 1 or endpoint[0]["tag"] != "google-vertex/global":
        raise ValueError("D5.9 price snapshot must bind the selected Vertex route")
    if price_snapshot["model"] != "google/gemini-3.8-flash":
        raise ValueError("D5.9 price snapshot model changed")
    all_jobs = primary_jobs + reliability_jobs
    value = {
        "schema_version": "pixelgym-agent-v5-d59-execution-plan-v1",
        "status": "frozen_candidate_awaiting_exact_paid_execution_approval",
        "execution_enabled": False,
        "paid_execution_authorized": False,
        "approved_model_attempt_cap": 0,
        "approved_provider_wire_request_cap": 0,
        "provider_calls_made": 0,
        "owner_selection_digest": content_digest(
            json.loads(
                (root / "artifacts/grounding-v5-d59-freeze/owner-selection.json")
                .read_text(encoding="utf-8")
            )
        ),
        "source_binding": source_binding(root, source_revision=source_revision),
        "task_manifest_path": "artifacts/grounding-v5-d59-freeze/task-manifest.json",
        "admission_path": "artifacts/grounding-v5-d59-freeze/admission.json",
        "policy_manifests": policies,
        "allocation": allocation,
        "primary_comparison": {
            "conditions": ["history", "stateless"],
            "paired_task_seeds": list(D59_CONFIRMATORY_SEEDS),
            "independent_representative_rule": "all singleton tasks and twin_a from every robustness pair",
            "test": "two-sided exact McNemar",
            "alpha": 0.05,
            "minimum_relevant_absolute_difference": 0.2,
            "target_power": 0.8,
            "bootstrap_seed": 20260911,
            "bootstrap_resamples": 10000,
            "interval_level": 0.95,
        },
        "price_snapshot": price_snapshot,
        "price_snapshot_digest": content_digest(price_snapshot),
        "request_budget": {
            "version": "gemini-png-request-budget-v1",
            "maximum_images_per_request": 32,
            "maximum_workload_input_tokens": 163840,
            "maximum_output_tokens": 4096,
            "maximum_request_reservation_usd": "0.13824000",
            "completion_not_guaranteed_within_dollar_cap": True,
        },
        "budget": {
            "historical_confirmed_spend_usd": str(HISTORICAL_SPEND_USD),
            "aggregate_planning_cap_usd": str(AGGREGATE_PLANNING_CAP_USD),
            "primary_phase_cap_usd": str(PRIMARY_PHASE_CAP_USD),
            "reliability_phase_cap_usd": str(RELIABILITY_PHASE_CAP_USD),
            "unknown_outcome_budget_weight_usd": "0",
            "active_request_bound_counts_against_cap": True,
            "unused_phase_budget_transfer_allowed": False,
        },
        "phase_caps": {
            "primary": _caps(primary_jobs),
            "reliability": _caps(reliability_jobs),
            "aggregate": _caps(all_jobs),
        },
        "runtime": {
            "proposed_aggregate_window_hours": 96,
            "approval_required": True,
            "expired_calibration_window_reused": False,
        },
        "primary_jobs": primary_jobs,
        "reliability_jobs": reliability_jobs,
        "failure_rule": "retain every assigned failure and unrun assignment; no post-result tuning or replay outside the frozen reliability schedule",
    }
    return {**value, "execution_plan_digest": content_digest(value)}
