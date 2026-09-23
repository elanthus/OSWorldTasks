"""The Haiku D5.8 successor uses only predeclared independent representatives."""

import copy
import json

import pytest

from scripts.prepare_grounding_v5_d58_haiku_successor import (
    OUTPUT,
    SNAPSHOT,
    build_analysis,
    render_report,
    representative_outcomes,
)


def test_committed_successor_reproduces_from_response_free_evidence():
    analysis = build_analysis()
    assert json.loads((OUTPUT / "analysis.json").read_text()) == analysis
    assert (OUTPUT / "report.md").read_text() == render_report(analysis)
    assert analysis["provider_calls_made"] == 0
    assert analysis["confirmatory_tasks_generated"] == 0
    assert analysis["owner_selection"] is None
    assert analysis["paid_or_subscription_execution_authorized"] is False


def test_independent_outcomes_and_power_are_derived_not_all_pair_counts():
    analysis = build_analysis()
    representatives = analysis["calibration"]["independent_representatives"]
    assert representatives["pairs"] == 44
    assert representatives["outcomes"] == {
        "both_success": 0,
        "history_only": 25,
        "stateless_only": 5,
        "neither_success": 14,
    }
    assert representatives["discordant"] == 30
    assert analysis["calibration"]["all_pairs"] == 50
    assert analysis["carry_forward_candidate"] == {
        "independent_pairs": 168,
        "episodes_per_arm_with_robustness_twins": 192,
        "meets_power_at_observed_discordance": True,
        "meets_power_at_upper_sensitivity": True,
        "status": "analytical candidate; owner selection not recorded",
    }


@pytest.mark.parametrize("damage", ["duplicate", "missing", "classification", "success"])
def test_representative_outcomes_rejects_invalid_evidence(damage):
    snapshot = json.loads(SNAPSHOT.read_text())
    seeds = [row["seed"] for row in build_analysis()["calibration"]["independent_representatives"]["rows"]]
    changed = copy.deepcopy(snapshot)
    if damage == "duplicate":
        changed["results"].append(copy.deepcopy(changed["results"][0]))
    elif damage == "missing":
        changed["results"] = [row for row in changed["results"] if row["seed"] != seeds[0]]
    elif damage == "classification":
        next(row for row in changed["results"] if row["seed"] == seeds[0])["classification"] = "request_failure"
    else:
        row = next(row for row in changed["results"] if row["seed"] == seeds[0])
        row["success"] = not row["success"]
    with pytest.raises(ValueError):
        representative_outcomes(changed, seeds)
