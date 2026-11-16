"""Tests for v2 of the v5 stateless chance-level supplement (v1 plus fixed label-order rules)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import generate_v5_stateless_chance_ceiling as v1
from scripts import generate_v5_stateless_chance_ceiling_v2 as v2

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def inputs() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    return v1.load_inputs(ROOT)


@pytest.fixture(scope="module")
def result(inputs: tuple[dict[str, Any], dict[str, dict[str, Any]]]) -> dict[str, Any]:
    return v2.analyze(*inputs)


def test_v1_values_are_unchanged(result: dict[str, Any]) -> None:
    committed_v1 = json.loads((ROOT / v1.JSON_OUTPUT).read_text(encoding="utf-8"))
    assert result["v1"] == committed_v1


def test_best_label_rule_pair_is_30_and_position_only_is_26(result: dict[str, Any]) -> None:
    rules = result["fixed_rule_pairs"]
    assert rules["pair_count"] == 36
    assert rules["best_pair_successes_if_otherwise_perfect"] == 30
    assert rules["best_position_only_successes"] == 26
    assert "label_first+label_middle" in rules["best_pairs_using_a_label_rule"]
    assert "in-sample" in rules["selection"]


@pytest.mark.parametrize(
    ("rule", "labels", "expected"),
    [
        ("position_2", ["Match row D48", "Match row A17", "Match row G73"], 2),
        ("label_first", ["Match row D48", "Match row A17", "Match row G73"], 1),
        ("label_middle", ["Match row D48", "Match row A17", "Match row G73"], 0),
        ("label_last", ["Match row D48", "Match row A17", "Match row G73"], 2),
    ],
)
def test_pick(rule: str, labels: list[str], expected: int) -> None:
    assert v2.pick(rule, labels) == expected


def test_rejects_duplicate_labels_on_a_consumer_stage(
    inputs: tuple[dict[str, Any], dict[str, dict[str, Any]]],
) -> None:
    manifest = copy.deepcopy(inputs[0])
    stage = next(
        s
        for s in manifest["records"][0]["canonical_task"]["stages"]
        if s.get("critical") and s.get("dependency_id") == "request_ref"
    )
    stage["controls"][1]["label"] = stage["controls"][0]["label"]
    with pytest.raises(v1.ChanceCeilingError, match="not distinct"):
        v2.fixed_rule_pairs(manifest)


def test_committed_artifacts_match_regeneration(tmp_path: Path) -> None:
    assert v2.main(["--repository-root", str(ROOT), "--output-dir", str(tmp_path)]) == 0
    for output in (v2.JSON_OUTPUT, v2.MD_OUTPUT):
        name = Path(output).name
        assert (tmp_path / name).read_text(encoding="utf-8") == (ROOT / output).read_text(
            encoding="utf-8"
        )


def test_markdown_keeps_v1_sections_and_states_selection(result: dict[str, Any]) -> None:
    text = v2.render_markdown(result)
    assert "| historical | 106/192 | 15/192 |" in text
    assert "at most 30 of 192 times; position-only pairs reach 26 of 192" in text
    assert "optimistic in-sample reference level" in text
    assert text.index("added in v2") < text.index("## Scope")
