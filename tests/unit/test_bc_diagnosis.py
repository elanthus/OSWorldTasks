"""Fast tests for the BC v2 diagnosis helpers and its report generator (no torch, no training)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from scripts import diagnose_bc_v2 as diagnose
from scripts import generate_bc_diagnosis_report as report
from scripts import probe_bc_v2 as probe

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("position", "bucket"),
    [
        (0, "0"),
        (1, "1"),
        (2, "2-3"),
        (3, "2-3"),
        (7, "4-7"),
        (15, "8-15"),
        (16, "16+"),
        (90, "16+"),
    ],
)
def test_position_buckets(position: int, bucket: str) -> None:
    assert diagnose.position_bucket(position) == bucket


def test_agreement_counts_partition_the_frames() -> None:
    policy = np.array([True, True, False, False, True])
    baseline = np.array([True, False, True, False, False])
    counts = diagnose.agreement(policy, baseline)
    assert counts == {
        "frames": 5,
        "policy_correct": 3,
        "baseline_correct": 2,
        "both_correct": 1,
        "policy_only": 2,
        "baseline_only": 1,
        "neither": 1,
    }


def test_widget_name_includes_the_option() -> None:
    from pixelgym.tasks.vendor_form.ui import WidgetId

    assert diagnose.widget_name((WidgetId.PAYMENT_TERMS, 2)) == "payment_terms[2]"
    assert diagnose.widget_name((WidgetId.TAX_ID, None)) == "tax_id"
    assert diagnose.widget_name(None) == "empty space"


def test_committed_report_matches_regeneration(tmp_path: Path) -> None:
    folder = tmp_path / report.FOLDER
    shutil.copytree(ROOT / report.FOLDER, folder)
    (folder / "REPORT.md").unlink()
    assert report.main(["--repository-root", str(tmp_path)]) == 0
    assert (folder / "REPORT.md").read_text(encoding="utf-8") == (
        ROOT / report.FOLDER / "REPORT.md"
    ).read_text(encoding="utf-8")


def test_report_numbers_come_from_the_json() -> None:
    diagnosis = json.loads((ROOT / report.FOLDER / "diagnosis.json").read_text(encoding="utf-8"))
    run = diagnosis["runs"]["standard-n256-seed0-steps6000"]
    overall = run["keys"]["overall"]
    text = (ROOT / report.FOLDER / "REPORT.md").read_text(encoding="utf-8")
    row = report.agreement_row("`standard-n256-seed0-steps6000`", overall)
    assert row in text
    assert overall["policy_only"] + overall["both_correct"] == overall["policy_correct"]
    assert sum(run["predictions_reproduced_mismatches"].values()) == 0


def test_detail_runs_are_the_largest_demo_count_in_each_split() -> None:
    diagnosis = json.loads((ROOT / report.FOLDER / "diagnosis.json").read_text(encoding="utf-8"))
    assert report.detail_runs(diagnosis, "standard") == [
        "standard-n256-seed0-steps6000",
        "standard-n256-seed1-steps6000",
    ]


@pytest.mark.parametrize(
    ("step", "max_total", "expected"),
    [(6000, 30000, 500), (6000, 6001, 1), (6000, 6000, 0), (29800, 30000, 200)],
)
def test_convergence_block_stops_at_max_total_steps(
    step: int, max_total: int, expected: int
) -> None:
    assert probe.updates_in_block(step, 500, max_total) == expected


def test_convergence_timing_is_described_by_what_was_measured() -> None:
    stored = {"train_seconds": 1799.5}
    assert report._convergence_seconds(stored) == (
        "1799.5 s from the start of training to the end of test scoring "
        "(training time alone was not recorded)"
    )
    measured = {"train_seconds": 1900.0, "training_loop_seconds": 1600.2}
    assert report._convergence_seconds(measured) == "training ran 1600.2 s"
