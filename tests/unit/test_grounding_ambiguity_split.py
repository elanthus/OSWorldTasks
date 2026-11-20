"""Tests for the request-card ambiguity split of the frozen v1 raw-coordinate misses."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from scripts import generate_grounding_ambiguity_split as split
from scripts import generate_grounding_clustered_analysis as clustered

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    return split.analyze(split.load_examples(ROOT), split.load_review(ROOT))


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        ([148.0, 92.0], split.REQUEST_CARD_CLASS),  # left panel, inside the row's y-range
        ([511.9, 79.0], split.REQUEST_CARD_CLASS),  # boundaries: x < 512, y0 inclusive
        ([512.0, 92.0], split.OTHER_CLASS),  # right panel
        ([148.0, 109.0], split.OTHER_CLASS),  # y1 exclusive
        ([148.0, 78.9], split.OTHER_CLASS),  # above the row
        (None, split.OTHER_CLASS),  # unparsed response
    ],
)
def test_classify_uses_left_panel_and_the_targets_own_rows(
    point: list[float] | None, expected: str
) -> None:
    assert split.classify(point, [536, 79, 1000, 109]) == expected


def test_split_counts_on_the_frozen_evidence(result: dict[str, Any]) -> None:
    counts = result["counts"]
    assert counts["raw_misses"] == 44
    assert counts["request_card_same_row"] == 22 and counts["other_misses"] == 22
    assert counts["left_panel_other_row"] == 0
    assert result["request_card_same_row_by_target"] == {
        "company_name": 9,
        "contact_email": 3,
        "contact_phone": 10,
    }
    assert counts["forgiving_raw_correct"] == 78 and counts["marks_correct"] == 100
    assert result["forgiving"]["delta_percentage_points"] == pytest.approx(22.0)


def test_strict_block_reproduces_the_frozen_clustered_supplement(result: dict[str, Any]) -> None:
    frozen = json.loads(
        (ROOT / "artifacts/grounding-clustered-analysis-v1.json").read_text(encoding="utf-8")
    )
    strict = result["strict"]
    assert strict["delta_percentage_points"] == frozen["delta_percentage_points"]
    for key in (
        "example_level_bootstrap_95_ci_percentage_points",
        "target_clustered_bootstrap_95_ci_percentage_points",
    ):
        assert strict[key] == frozen[key]


def test_every_request_card_miss_was_reviewed_as_wrong_semantic_element(
    result: dict[str, Any],
) -> None:
    assert result["manual_review_categories"]["request_card_same_row"] == {
        "wrong semantic element": 22
    }


def test_committed_artifacts_match_regeneration(tmp_path: Path) -> None:
    assert split.main(["--repository-root", str(ROOT), "--output-dir", str(tmp_path)]) == 0
    for output in (split.JSON_OUTPUT, split.MD_OUTPUT):
        name = Path(output).name
        assert (tmp_path / name).read_text(encoding="utf-8") == (ROOT / output).read_text(
            encoding="utf-8"
        )


def test_markdown_numbers_come_from_json(result: dict[str, Any]) -> None:
    text = split.render_markdown(result)
    assert "| Forgiving request-card same-row | 78/100 | 100/100 | +22.0 pp |" in text
    assert "Of 44 raw-coordinate misses on 100 examples, 22 land" in text


def _copy_inputs(tmp_path: Path) -> Path:
    for relative in (clustered.RESULTS_PATH, clustered.PREDICTIONS_PATH, split.REVIEW_PATH):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    return tmp_path


def test_rejects_a_missing_bbox(tmp_path: Path) -> None:
    root = _copy_inputs(tmp_path)
    results_path = root / clustered.RESULTS_PATH
    results = json.loads(results_path.read_text(encoding="utf-8"))
    del results["per_example"][0]["bbox"]
    results_path.write_text(json.dumps(results), encoding="utf-8")
    with pytest.raises(split.AmbiguitySplitError, match="bbox"):
        split.load_examples(root)


def test_rejects_results_that_disagree_with_predictions(tmp_path: Path) -> None:
    root = _copy_inputs(tmp_path)
    results_path = root / clustered.RESULTS_PATH
    results = json.loads(results_path.read_text(encoding="utf-8"))
    row = results["per_example"][0]
    row["raw"]["correct"] = not row["raw"]["correct"]
    results_path.write_text(json.dumps(results), encoding="utf-8")
    with pytest.raises(clustered.ClusteredAnalysisError, match="disagree"):
        split.load_examples(root)
