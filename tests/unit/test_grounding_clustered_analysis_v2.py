"""Tests for the v2 clustered supplement, which restates v1's allocation and changes no number."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import generate_grounding_clustered_analysis as v1
from scripts import generate_grounding_clustered_analysis_v2 as v2

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def result() -> dict:
    return v2.build(ROOT)


def test_every_number_equals_the_checked_in_v1(result: dict) -> None:
    frozen = json.loads((ROOT / v1.JSON_OUTPUT).read_text(encoding="utf-8"))
    changed = {key for key in set(result) | set(frozen) if result.get(key) != frozen.get(key)}
    assert changed == {"allocation", "correction", "inputs", "schema_version"}
    assert result["correction"]["numbers_unchanged"] is True


def test_allocation_is_read_from_the_dataset(result: dict) -> None:
    alloc = result["allocation"]
    assert alloc["example_count"] == 100
    assert alloc["task_seed_count"] == 20 and len(alloc["screen_states"]) == 5
    assert alloc["examples_per_task_seed"] == [5]
    assert alloc["examples_per_screen_state"] == [20]
    assert alloc["examples_per_target"] == alloc["task_seeds_per_target"] == [10]
    assert alloc["screen_states_per_target"] == [1]


def test_markdown_differs_from_v1_only_in_the_allocation_and_notice(result: dict) -> None:
    old = (ROOT / v1.MD_OUTPUT).read_text(encoding="utf-8").splitlines()
    new = v2.render_markdown(result).splitlines()
    assert new[2].startswith("**Correction.**")
    assert "`scripts/generate_grounding_clustered_analysis_v2.py`" in new[4]
    changed = [(a, b) for a, b in zip(old[4:], new[6:], strict=True) if a != b]
    old_line = (
        "100 paired examples (10 target controls x 10 seeds). Raw 56/100, marks 100/100, "
        "paired difference +44.0 pp."
    )
    new_line = (
        "100 paired examples (10 target controls x 10 task seeds each, every target in one "
        "screen state; 20 task seeds x 5 screen states in all). Raw 56/100, marks 100/100, "
        "paired difference +44.0 pp."
    )
    assert changed == [(old_line, new_line)]


def test_rejects_a_dataset_that_disagrees_with_the_results(tmp_path: Path) -> None:
    rows = v1.load_rows(ROOT)
    lines = (ROOT / v2.DATASET_PATH).read_text(encoding="utf-8").splitlines()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / v2.DATASET_PATH).write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(v2.CorrectionError, match="different examples"):
        v2.allocation(tmp_path, rows)


def test_committed_artifacts_match_regeneration(tmp_path: Path) -> None:
    assert v2.main(["--repository-root", str(ROOT), "--output-dir", str(tmp_path)]) == 0
    for output in (v2.JSON_OUTPUT, v2.MD_OUTPUT):
        name = Path(output).name
        assert (tmp_path / name).read_text(encoding="utf-8") == (ROOT / output).read_text(
            encoding="utf-8"
        )
