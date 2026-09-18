"""No-call D5.9 sample, cap, and immutable artifact checks."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from pixelgym.grounding.v5.d59_freeze import (
    D59_CONFIRMATORY_SEEDS,
    confirmatory_tasks,
    execution_plan,
    reliability_tasks,
    validate_confirmatory_design,
)
from pixelgym.grounding.v5.memory_generator import MEMORY_GENERATOR_VERSION, seed_record

ROOT = Path(__file__).parents[2]


def test_d59_selected_seed_allocation_is_balanced_and_versioned() -> None:
    tasks = confirmatory_tasks()
    summary = validate_confirmatory_design(tasks)
    assert MEMORY_GENERATOR_VERSION == "pixelgym-agent-v5-generator-memory-v3"
    assert D59_CONFIRMATORY_SEEDS == tuple(range(6000, 6192))
    assert summary == {
        "episode_count_per_arm": 192,
        "independent_representative_count": 168,
        "robustness_pair_count": 24,
        "family_counts": {
            "conditional_precedence": 32,
            "deferred_join": 32,
            "evidence_aggregation": 32,
            "review_and_commit": 32,
            "revision_after_reveal": 32,
            "visible_error_recovery": 32,
        },
        "difficulty_band_counts": {
            "ceiling_probe": 42,
            "frontier": 108,
            "regression_canary": 42,
        },
        "reliability_seeds": [
            6000,
            6002,
            6016,
            6018,
            6032,
            6034,
            6048,
            6050,
            6064,
            6066,
            6080,
            6082,
        ],
    }
    assert [task.seed for task in reliability_tasks(tasks)] == summary["reliability_seeds"]
    assert seed_record(6144).family_index == 24
    assert seed_record(6191).family_index == 31


def test_d59_execution_plan_is_exact_but_non_executable() -> None:
    price = json.loads(
        (ROOT / "artifacts/grounding-v5-d59-freeze/price-recheck.json").read_text()
    )
    manifest = json.loads(
        (ROOT / "artifacts/grounding-v5-d59-freeze/task-manifest.json").read_text()
    )
    admission = json.loads(
        (ROOT / "artifacts/grounding-v5-d59-freeze/admission.json").read_text()
    )
    source_revision = manifest["source_binding"]["source_revision"]
    plan = execution_plan(
        ROOT,
        source_revision=source_revision,
        price_snapshot=price,
        task_manifest_value=manifest,
        admission_value=admission,
    )
    assert not plan["execution_enabled"]
    assert not plan["paid_execution_authorized"]
    assert plan["approved_model_attempt_cap"] == 0
    assert plan["approved_provider_wire_request_cap"] == 0
    assert len(plan["primary_jobs"]) == 384
    assert len(plan["reliability_jobs"]) == 48
    assert plan["phase_caps"]["aggregate"]["environment_action_cap"] == sum(
        job["action_limit"] for job in plan["primary_jobs"] + plan["reliability_jobs"]
    )
    assert plan["phase_caps"]["aggregate"]["model_attempt_cap"] == 3 * plan[
        "phase_caps"
    ]["aggregate"]["environment_action_cap"]
    budget = plan["budget"]
    assert (
        Decimal(budget["historical_confirmed_spend_usd"])
        + Decimal(budget["primary_phase_cap_usd"])
        + Decimal(budget["reliability_phase_cap_usd"])
        == Decimal(budget["aggregate_planning_cap_usd"])
        == Decimal("120.00")
    )
    assert plan["provider_calls_made"] == 0
    assert plan["task_manifest_binding"]["manifest_digest"] == manifest["manifest_digest"]
    assert plan["admission_binding"]["evidence_digest"] == admission["evidence_digest"]

    doubled = deepcopy(price)
    doubled["endpoints"][0]["pricing"]["prompt"] = "0.00000150"
    doubled["endpoints"][0]["pricing"]["image"] = "0.00000150"
    rebound = execution_plan(
        ROOT,
        source_revision=source_revision,
        price_snapshot=doubled,
        task_manifest_value=manifest,
        admission_value=admission,
    )
    assert Decimal(rebound["request_budget"]["maximum_request_reservation_usd"]) > Decimal(
        plan["request_budget"]["maximum_request_reservation_usd"]
    )


def test_d59_checked_in_artifacts_verify_without_provider_access() -> None:
    execution = json.loads(
        (ROOT / "artifacts/grounding-v5-d59-freeze/execution-plan.json").read_text()
    )
    manifest = json.loads(
        (ROOT / "artifacts/grounding-v5-d59-freeze/task-manifest.json").read_text()
    )
    admission = json.loads(
        (ROOT / "artifacts/grounding-v5-d59-freeze/admission.json").read_text()
    )
    assert execution["source_binding"] == manifest["source_binding"]
    assert manifest["manifest_digest"]
    assert admission["task_count"] == 192
    assert execution["provider_calls_made"] == manifest["provider_calls_made"] == 0
    assert admission["provider_calls_made"] == 0
