"""Corrected pilot analysis and admission-report denominators."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import prepare_grounding_v5_memory as admission
from scripts.prepare_grounding_v5_review_corrections import (
    corrected_admission_summary,
    corrected_pilot_analysis,
)

ROOT = Path(__file__).resolve().parents[2]


def test_pilot_correction_separates_analysis_control_and_wire_counts() -> None:
    summary = json.loads(
        (ROOT / "artifacts/grounding-v5-d58-calibration-pilot/summary.json").read_text()
    )
    result = corrected_pilot_analysis(summary)
    assert "provider_calls" not in result
    assert result["provider_calls_by_analysis"] == result["provider_control_requests"] == 0
    assert result["wire_requests_sent"] == 20
    assert result["remaining_aggregate_ceiling_usd"] == "4.80991250"
    summary["provider_control_requests"] = 3
    summary["spend"]["wire_requests_sent"] = 23
    changed = corrected_pilot_analysis(summary)
    assert changed["provider_control_requests"] == 3 and changed["wire_requests_sent"] == 23


def test_report_denominators_come_from_stored_task_and_choice_rows() -> None:
    value = {
        "generator_version": "fixture",
        "tasks": [{"seed": 1}, {"seed": 2}, {"seed": 3}, {"seed": 101}],
        "counterfactuals": [{"base_seed": seed} for seed in (1, 2, 3)],
        "baselines": {"fixture-rule": [{"choices": [1, 2]}, {"choices": [1]}]},
        "summary": {
            "admitted_task_count": 4,
            "counterfactual_pair_count": 3,
            "memory_target_positions": {"0": 3, "1": 2, "2": 1},
            "baselines": {"fixture-rule": {"first_attempt_correct": 2, "successes": 1}},
        },
    }
    value["summary"] = corrected_admission_summary(value)
    report = admission.render_report(value)
    assert "3 base development tasks (6 choices)" in report
    assert "| fixture-rule | 2 / 3 | 1 / 2 |" in report
