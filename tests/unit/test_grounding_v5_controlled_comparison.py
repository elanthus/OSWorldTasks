"""Matched-arm controls and honest denominators, without any provider calls."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.controlled_comparison import (
    CONTROLLED_PAIR,
    SMOKE_PAIR,
    build_comparison_plan,
    summarize_comparison,
    validate_controlled_pair,
)
from pixelgym.grounding.v5.panel_policy import OpenRouterPanelPolicy
from pixelgym.grounding.v5.plan import CalibrationPlan
from pixelgym.grounding.v5.provider_adapters import OpenRouterHttpAdapter
from pixelgym.grounding.v5.runner import PolicyVisibleResult

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def plan() -> CalibrationPlan:
    return build_comparison_plan(
        ROOT,
        code_revision="a" * 40,
        phase="calibration",
        maximum_spend_usd="10",
        output_directory="artifacts/comparison-test-unused",
    )


def test_plan_matches_all_fifty_tasks_and_balances_arm_order(plan: CalibrationPlan) -> None:
    assert validate_controlled_pair(plan) == tuple(c.slot for c in CONTROLLED_PAIR)
    assert len(plan.assignments) == 100
    assert plan.budgets.caps.environment_action_cap == 2862
    assert plan.budgets.caps.model_attempt_cap == 11448
    assert plan.budgets.caps.provider_control_request_cap == 0
    for index in range(50):
        pair = plan.assignments[index * 2 : index * 2 + 2]
        assert pair[0].task_id == pair[1].task_id
        assert pair[0].slot == CONTROLLED_PAIR[index % 2].slot
    adapter = OpenRouterHttpAdapter(ROOT, plan)
    adapter.close()


def test_smoke_is_ten_development_tasks_twenty_calls_and_no_retries() -> None:
    plan = build_comparison_plan(
        ROOT,
        code_revision="a" * 40,
        phase="smoke",
        maximum_spend_usd="0.34",
        output_directory="artifacts/comparison-smoke-test-unused",
    )
    assert len({a.task_id for a in plan.assignments}) == 10
    assert len({a.family for a in plan.assignments}) == 6
    assert all(a.action_limit == 1 for a in plan.assignments)
    assert plan.budgets.caps.model_attempt_cap == 20
    assert plan.budgets.caps.provider_wire_request_cap == 20
    assert all(c.bounded_retry_budget == 0 for c in SMOKE_PAIR)
    assert "development.json" in str(plan.manifest_path)
    assert plan.raw["provider_calls_made_while_planning"] == 0
    OpenRouterHttpAdapter(ROOT, plan).close()
    report = summarize_comparison(plan, _summary(plan))
    assert report["complete_assigned_denominator"]
    assert report["success_rate_difference"] is None
    assert not report["full_episode_comparison"]


@pytest.mark.parametrize(
    "field",
    [
        "system_prompt_digest",
        "provider",
        "model",
        "parser_version",
        "coordinate_adapter",
        "inference_parameters",
        "sandbox",
        "transport_retry_rule",
        "max_model_attempts_per_action",
        "code_revision",
    ],
)
def test_pair_rejects_uncontrolled_manifest_changes(plan: CalibrationPlan, field: str) -> None:
    changed = copy.deepcopy(plan.policies[1].policy_manifest)
    changed[field] = "changed"
    tampered = replace(
        plan,
        policies=(
            plan.policies[0],
            replace(
                plan.policies[1],
                policy_manifest=changed,
                policy_manifest_digest=content_digest(changed),
            ),
        ),
    )
    with pytest.raises(ValueError, match="uncontrolled"):
        validate_controlled_pair(tampered)


@pytest.mark.parametrize("change", ["missing", "order", "action_limit", "same_memory"])
def test_pair_rejects_assignment_and_intervention_mismatches(
    plan: CalibrationPlan, change: str
) -> None:
    assignments = list(plan.assignments)
    if change == "missing":
        assignments.pop()
    elif change == "order":
        assignments[1], assignments[2] = assignments[2], assignments[1]
    elif change == "action_limit":
        assignments[1] = replace(assignments[1], action_limit=1)
    else:
        plan = replace(plan, policies=(plan.policies[0], plan.policies[0]))
    with pytest.raises(ValueError):
        validate_controlled_pair(replace(plan, assignments=tuple(assignments)))


def test_memory_is_the_only_request_intervention_and_reset_clears_it() -> None:
    stateful, stateless = [OpenRouterPanelPolicy(c) for c in CONTROLLED_PAIR]
    states = [p.reset("Read the visible reference, then enter it.") for p in (stateful, stateless)]
    # A deterministic black RGB frame exercises the actual screenshot encoder.
    screenshot = bytes(1024 * 768 * 3)
    assert stateful.build_request(states[0], screenshot) == stateless.build_request(
        states[1], screenshot
    )
    response = b'{"content":"action","model":"example","usage":{}}'
    action = {"action_type": 0, "x": 0, "y": 0, "key": 0}
    result = PolicyVisibleResult(
        screenshot_digest="sha256:" + "0" * 64,
        reward=0.0,
        terminated=False,
        truncated=False,
        step_index=1,
    )
    for index, policy in enumerate((stateful, stateless)):
        state = policy.reduce_state(states[index], response)
        state = policy.post_parse_state(state, action)
        state = policy.post_dispatch_state(state, action, result)
        request = policy.build_request(state, screenshot)
        if index == 0:
            assert '"action_type":0' in request["messages"][1]["content"][0]["text"]
            assert policy.reset("New task") == b'{"history":[],"instruction":"New task"}'
        else:
            assert state == states[index]
            assert "Visible-action history: []" in request["messages"][1]["content"][0]["text"]
            contaminated = json.loads(state)
            contaminated["history"] = [{"secret": "old action"}]
            assert policy.build_request(json.dumps(contaminated).encode(), screenshot) == request


def _summary(plan: CalibrationPlan, *, count: int | None = None) -> dict:
    rows = []
    for assignment in plan.assignments[:count]:
        success = assignment.slot == CONTROLLED_PAIR[0].slot
        rows.append(
            {
                "slot": assignment.slot,
                "task_id": assignment.task_id,
                "trial_id": f"manifest-{assignment.slot}-{assignment.ordinal:04d}-{assignment.task_id}",
                "success": success,
                "classification": "success_termination" if success else "invalid_output",
            }
        )
    return {
        "approved_plan_sha256": plan.digest,
        "code_revision": plan.code_revision,
        "episode_results": rows,
    }


def test_report_keeps_invalid_outputs_and_negative_results(plan: CalibrationPlan) -> None:
    summary = _summary(plan)
    report = summarize_comparison(plan, summary)
    assert report["complete_assigned_denominator"]
    assert report["success_rate_difference"] == 1.0
    assert report["paired_observed_outcomes"]["first_only"] == 50
    assert report["classifications_by_slot"][CONTROLLED_PAIR[1].slot] == {"invalid_output": 50}
    for row in summary["episode_results"]:
        row["success"] = not row["success"]
        row["classification"] = "success_termination" if row["success"] else "step_limit_truncation"
    assert summarize_comparison(plan, summary)["success_rate_difference"] == -1.0


@pytest.mark.parametrize("count", [0, 1, 3, 99])
def test_partial_report_discloses_missing_assignments_and_withholds_difference(
    plan: CalibrationPlan, count: int
) -> None:
    report = summarize_comparison(plan, _summary(plan, count=count))
    assert not report["complete_assigned_denominator"]
    assert report["success_rate_difference"] is None
    assert report["missing_assignments"] == 100 - count
    assert report["observed_pairs"] == count // 2


@pytest.mark.parametrize("fault", ["digest", "duplicate", "task", "trial", "success", "unknown"])
def test_report_rejects_misbound_or_inconsistent_evidence(
    plan: CalibrationPlan, fault: str
) -> None:
    summary = _summary(plan)
    row = summary["episode_results"][0]
    if fault == "digest":
        summary["approved_plan_sha256"] = "sha256:" + "b" * 64
    elif fault == "duplicate":
        summary["episode_results"].append(row)
    elif fault == "task":
        row["task_id"] = "unassigned"
    elif fault == "trial":
        row["trial_id"] = "other"
    elif fault == "success":
        row["success"] = "false"
    else:
        row["success"] = False
        row["classification"] = "invented"
    with pytest.raises(ValueError):
        summarize_comparison(plan, summary)
