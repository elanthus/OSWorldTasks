"""The Haiku D5.8 successor uses only predeclared independent representatives.

The committed analysis records ``source_file_digests`` over its evidence inputs
and the prepare script itself. Reproduction reads those files from an archive of
the revision that wrote the analysis, not the live checkout, so later edits or
removal of the script do not break byte verification of the frozen analysis.
"""

import copy
import json
from pathlib import Path

import pytest

from scripts import prepare_grounding_v5_d58_haiku_successor as successor
from scripts.prepare_grounding_v5_d58_haiku_successor import (
    OUTPUT,
    SNAPSHOT,
    build_analysis,
    render_report,
    representative_outcomes,
)
from tests.support.recorded_revision import archive_recorded_root, artifact_revision

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = "scripts/prepare_grounding_v5_d58_haiku_successor.py"
INPUTS = (
    "artifacts/grounding-v5-haiku-cli-replication/snapshot.json",
    "artifacts/grounding-v5-haiku-cli-replication/sources.json",
    "artifacts/grounding-v5-d58-owner-budget-continuation/analysis.json",
    "artifacts/grounding-v5-d58-design/audit.py",
    "artifacts/grounding-v5-d58-final-design/power.py",
)


@pytest.fixture
def recorded_root(tmp_path, monkeypatch):
    """Script and inputs as they were when the analysis was written.

    ``build_analysis`` reads module-level paths and hashes its own ``__file__``;
    point those attributes into the archive. ``OUTPUT`` stays live because it
    is the checked-in artifact under verification.
    """
    root = archive_recorded_root(
        tmp_path,
        monkeypatch,
        artifact_revision(OUTPUT.relative_to(ROOT) / "analysis.json"),
        (SCRIPT, *INPUTS),
    )
    monkeypatch.setattr(successor, "ROOT", root)
    monkeypatch.setattr(successor, "SNAPSHOT", root / INPUTS[0])
    monkeypatch.setattr(successor, "SOURCES", root / INPUTS[1])
    monkeypatch.setattr(successor, "REPRESENTATIVES", root / INPUTS[2])
    monkeypatch.setattr(successor, "__file__", str(root / SCRIPT))
    return root


def test_committed_successor_reproduces_from_response_free_evidence(recorded_root):
    analysis = build_analysis()
    assert json.loads((OUTPUT / "analysis.json").read_text(encoding="utf-8")) == analysis
    assert (OUTPUT / "report.md").read_text(encoding="utf-8") == render_report(analysis)
    assert analysis["provider_calls_made"] == 0
    assert analysis["confirmatory_tasks_generated"] == 0
    assert analysis["owner_selection"] is None
    assert analysis["paid_or_subscription_execution_authorized"] is False


def test_script_mutation_at_recorded_revision_breaks_reproduction(recorded_root):
    with (recorded_root / SCRIPT).open("a", encoding="utf-8") as handle:
        handle.write("# mutated\n")
    committed = json.loads((OUTPUT / "analysis.json").read_text(encoding="utf-8"))
    assert committed != build_analysis()


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
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
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
