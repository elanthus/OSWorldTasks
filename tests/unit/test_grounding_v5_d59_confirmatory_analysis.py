from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.stats import exact_mcnemar_p_value, wilson_interval
from scripts import analyze_grounding_v5_d59 as analysis

ROOT = Path(__file__).resolve().parents[2]
FAMILIES = ("fam_a", "fam_b")


def _plan() -> dict[str, Any]:
    # fam_a: seeds 1 (twin_a) and 2 (twin_b) form one cluster, seed 3 a singleton.
    # fam_b: seeds 4 and 5 are singletons. Reliability repeats seed 1.
    records = {
        1: ("fam_a", "L-a0", "twin_a"),
        2: ("fam_a", "L-a0", "twin_b"),
        3: ("fam_a", "L-a1", "base"),
        4: ("fam_b", "L-b0", "base"),
        5: ("fam_b", "L-b1", "base"),
    }

    def job(seed: int, mode: str, phase: str, repeat: int) -> dict[str, Any]:
        family, logical_id, variant = records[seed]
        return {
            "seed": seed,
            "mode": mode,
            "phase": phase,
            "repeat": repeat,
            "trial_id": f"t-{phase}-{seed}-{mode}-r{repeat}",
            "seed_record": {
                "seed": seed,
                "family": family,
                "logical_id": logical_id,
                "variant": variant,
            },
        }

    return {
        "execution_plan_digest": "sha256:test",
        "allocation": {"reliability_seeds": [1]},
        "primary_comparison": {
            "conditions": ["history", "stateless"],
            "alpha": 0.05,
            "minimum_relevant_absolute_difference": 0.2,
            "target_power": 0.8,
            "test": "two-sided exact McNemar",
            "bootstrap_seed": 20260911,
            "bootstrap_resamples": 200,
            "interval_level": 0.95,
            "paired_task_seeds": [1, 2, 3, 4, 5],
        },
        "primary_jobs": [
            job(seed, mode, "primary", 0) for seed in records for mode in ("history", "stateless")
        ],
        "reliability_jobs": [
            job(1, mode, "reliability", repeat)
            for mode in ("history", "stateless")
            for repeat in (1, 2)
        ],
    }


OUTCOMES = {
    # (phase, mode, seed, repeat): classification
    ("primary", "history", 1, 0): "success_termination",
    ("primary", "history", 2, 0): "success_termination",
    ("primary", "history", 3, 0): "invalid_output",
    ("primary", "history", 4, 0): "success_termination",
    ("primary", "history", 5, 0): "infrastructure_failure",
    ("primary", "stateless", 1, 0): "step_limit_truncation",
    ("primary", "stateless", 2, 0): "success_termination",
    ("primary", "stateless", 3, 0): "success_termination",
    ("primary", "stateless", 4, 0): "step_limit_truncation",
    ("primary", "stateless", 5, 0): "invalid_output",
    ("reliability", "history", 1, 1): "success_termination",
    ("reliability", "history", 1, 2): "invalid_output",
    ("reliability", "stateless", 1, 1): "step_limit_truncation",
    ("reliability", "stateless", 1, 2): "step_limit_truncation",
}


def _projection() -> dict[str, Any]:
    results = []
    for (phase, mode, seed, repeat), classification in OUTCOMES.items():
        results.append(
            {
                "trial_id": f"t-{phase}-{seed}-{mode}-r{repeat}",
                "phase": phase,
                "mode": mode,
                "seed": seed,
                "repeat": repeat,
                "classification": classification,
                "success": classification == "success_termination",
                "model_attempts": 2,
                "provider_wire_requests": 2,
                "provider_control_requests": 0,
                "environment_actions": 1,
            }
        )
    return {
        "results": results,
        "unresolved_invocations": 3,
        "stop_reason": "completed_all_assignments",
        "assigned": len(results),
        "completed": len(results),
    }


def _texts() -> tuple[str, str]:
    plan_text = "\n".join(
        line for path, line in analysis.PREREGISTRATION_QUOTES if path == analysis.ANALYSIS_PLAN
    )
    design_text = "\n".join(
        line for path, line in analysis.PREREGISTRATION_QUOTES if path == analysis.DESIGN_DOC
    )
    return plan_text, design_text


def _analyze(projection: dict[str, Any] | None = None, plan: dict[str, Any] | None = None):
    plan_text, design_text = _texts()
    return analysis.analyze(
        projection or _projection(),
        plan or _plan(),
        projection_sha256="sha256:p",
        plan_sha256="sha256:e",
        analysis_plan_text=plan_text,
        design_doc_text=design_text,
    )


def test_wilson_matches_hand_computed_case() -> None:
    # 5/10, z = 1.96: center 0.5, half = 1.96*sqrt(0.025 + 0.009604)/1.38416
    z = 1.959963984540054
    half = z * math.sqrt(0.25 / 10 + z * z / 400) / (1 + z * z / 10)
    low, high = wilson_interval(5, 10)
    assert low == pytest.approx(0.5 - half)
    assert high == pytest.approx(0.5 + half)
    assert (low, high) == pytest.approx((0.2366, 0.7634), abs=1e-4)
    assert wilson_interval(0, 4)[0] == 0.0


def test_mcnemar_matches_hand_computed_cases() -> None:
    # (0, 5): 2 * 1/32 = 0.0625; (1, 4): 2 * 6/32 = 0.375; (3, 3): capped at 1.
    assert exact_mcnemar_p_value(0, 5) == 0.0625
    assert exact_mcnemar_p_value(1, 4) == 0.375
    assert exact_mcnemar_p_value(3, 3) == 1.0


def test_paired_table_on_synthetic_projection() -> None:
    result = _analyze()
    table = result["confirmatory"]["paired_success_table"]
    assert table == {
        "pairs": 5,
        "both_success": 1,  # seed 2
        "history_only_success": 2,  # seeds 1, 4
        "stateless_only_success": 1,  # seed 3
        "both_failure": 1,  # seed 5
        "exact_mcnemar_two_sided_p": exact_mcnemar_p_value(2, 1),
    }
    assert result["confirmatory"]["p_value_below_alpha"] is False
    boot = result["confirmatory"]["paired_absolute_difference"]
    assert boot["estimate"] == pytest.approx((3 - 2) / 5)
    assert (boot["strata"], boot["clusters"], boot["rows"]) == (2, 4, 5)
    subset = result["independent_representative_subset"]["paired_success_table"]
    assert subset["pairs"] == 4  # twin_b seed 2 excluded


def test_reliability_is_never_pooled_into_primary() -> None:
    result = _analyze()
    history = result["success_rates"]["policies"]["history"]["overall"]
    assert (history["successes"], history["denominator"]) == (3, 5)
    rel = result["reliability"]["policies"]
    assert rel["history"]["two_of_three_success"] == 1
    assert rel["stateless"]["all_three_failure"] == 1
    assert rel["history"]["reliability_phase_only"]["denominator"] == 2


def test_every_classification_stays_in_the_denominator() -> None:
    result = _analyze()
    profile = result["secondary"]["termination_profile"]["primary"]
    for mode in ("history", "stateless"):
        counts = profile[mode]
        assert counts["denominator"] == 5
        assert sum(counts[name] for name in analysis.CLASSIFICATIONS) == 5
        assert result["success_rates"]["policies"][mode]["overall"]["denominator"] == 5
    assert profile["history"]["infrastructure_failure"] == 1
    assert profile["history"]["invalid_output"] == 1
    assert profile["stateless"]["step_limit_truncation"] == 2
    fam_b = result["success_rates"]["policies"]["history"]["per_family"]["fam_b"]
    assert (fam_b["successes"], fam_b["denominator"]) == (1, 2)


def test_family_join_fails_when_seed_missing_from_freeze_mapping() -> None:
    projection = _projection()
    projection["results"].append({**projection["results"][0], "seed": 99, "trial_id": "t-x"})
    with pytest.raises(analysis.AnalysisError, match="seed 99 has no family"):
        _analyze(projection)


def test_join_fails_on_missing_or_duplicate_assignments() -> None:
    projection = _projection()
    projection["results"].pop()
    with pytest.raises(analysis.AnalysisError, match="missing"):
        _analyze(projection)
    projection = _projection()
    projection["results"].append(dict(projection["results"][0]))
    with pytest.raises(analysis.AnalysisError, match="duplicate"):
        _analyze(projection)


def test_join_fails_when_cluster_spans_families() -> None:
    plan = _plan()
    for job in plan["primary_jobs"]:
        if job["seed"] == 2:
            job["seed_record"]["family"] = "fam_b"
    with pytest.raises(analysis.AnalysisError, match="more than one family|spans families"):
        _analyze(plan=plan)


def test_join_fails_on_success_classification_disagreement() -> None:
    projection = _projection()
    projection["results"][0]["success"] = False
    with pytest.raises(analysis.AnalysisError, match="disagrees"):
        _analyze(projection)


def test_missing_preregistration_line_fails() -> None:
    with pytest.raises(analysis.AnalysisError, match="pre-registration line not found"):
        analysis.analyze(
            _projection(),
            _plan(),
            projection_sha256="p",
            plan_sha256="e",
            analysis_plan_text="",
            design_doc_text="",
        )


def test_non_preregistered_sections_are_labelled_exploratory() -> None:
    result = _analyze()
    for key in ("success_rates", "independent_representative_subset", "secondary"):
        assert result[key]["preregistered"] is False
        assert result[key]["label"] == "exploratory"
    assert result["confirmatory"]["preregistered"] is True
    assert result["reliability"]["preregistered"] is True
    md = analysis.render(result)
    assert "## Success rates, primary phase (exploratory)" in md
    assert "## Termination profile (exploratory)" in md


def test_render_has_disclosure_and_no_verdict_language() -> None:
    md = analysis.render(_analyze())
    expected = (
        "The stop rule was amended after repeated infrastructure failures from Anthropic to allow "
        "for retries in the case of intermittent network issues. Each failed assignment remains in "
        "the results. No prior assignment was replayed."
    )
    assert analysis.DISCLOSURE_TEXT == expected
    assert _analyze()["disclosure"]["text"] == expected
    section = md.split("\n## Disclosure\n", 1)[1]
    assert section == f"\n{expected}\n"
    assert "D5.10 is the owner's review." in md
    lowered = md.lower()
    for word in ("significant", "confirmed", "pass", "fail "):
        assert word not in lowered
    for name, _reason in analysis.NOT_COMPUTABLE:
        assert f"- {name}:" in md


def test_build_is_deterministic_and_matches_committed_artifacts() -> None:
    first = analysis.build(ROOT)
    second = analysis.build(ROOT)
    assert first == second
    assert (ROOT / analysis.OUTPUT_JSON).read_text(encoding="utf-8") == first[0]
    assert (ROOT / analysis.OUTPUT_MD).read_text(encoding="utf-8") == first[1]
    data = json.loads(first[0])
    assert data["confirmatory"]["paired_success_table"]["pairs"] == 192
    assert data["disclosure"]["unresolved_invocations"] == 3
