"""Tests for the v5 stateless-arm chance-level supplement."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import generate_v5_stateless_chance_ceiling as ceiling

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def inputs() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    return ceiling.load_inputs(ROOT)


@pytest.fixture(scope="module")
def result(inputs: tuple[dict[str, Any], dict[str, dict[str, Any]]]) -> dict[str, Any]:
    return ceiling.analyze(*inputs)


def test_uniform_guess_level_is_one_ninth_of_192(result: dict[str, Any]) -> None:
    assert result["episodes_per_arm"] == 192
    assert result["uniform_guess"]["success_probability"] == "1/9"
    assert result["uniform_guess"]["expected_successes"] == pytest.approx(192 / 9)


def test_observed_stateless_successes(result: dict[str, Any]) -> None:
    observed = {name: c["stateless_successes"] for name, c in result["campaigns"].items()}
    assert observed == {"historical": 15, "replication": 20, "parser_sensitivity": 20}
    stateful = {name: c["history_successes"] for name, c in result["campaigns"].items()}
    assert stateful == {"historical": 106, "replication": 62, "parser_sensitivity": 132}


def test_best_fixed_position_uses_the_frozen_answer_distribution(result: dict[str, Any]) -> None:
    joint = result["joint_answer_positions"]
    assert sum(joint.values()) == 192
    assert result["best_fixed_position"] == {
        "positions": [1, 2],
        "successes_if_otherwise_perfect": max(joint.values()),
    }
    assert max(joint.values()) == 26


def test_no_stateless_success_without_both_first_choices_correct(result: dict[str, Any]) -> None:
    assert result["campaigns"]["historical"]["first_attempts"] is None  # not recorded
    for name in ("replication", "parser_sensitivity"):
        first = result["campaigns"][name]["first_attempts"]
        assert first["successes_without_both_first_attempts_correct"] == 0
        assert (
            first["both_first_attempts_correct"] >= result["campaigns"][name]["stateless_successes"]
        )


def test_consumer_stage_answers_are_positions_of_the_target(
    inputs: tuple[dict[str, Any], dict[str, dict[str, Any]]],
) -> None:
    answers = ceiling.consumer_answers(inputs[0])
    assert len(answers) == 192 and all(0 <= p <= 2 for pair in answers.values() for p in pair)
    record = inputs[0]["records"][0]
    stage = record["canonical_task"]["stages"][5]
    ids = [control["control_id"] for control in stage["controls"]]
    assert answers[record["seed_record"]["seed"]][0] == ids.index(stage["target_control_id"])


def test_rejects_a_consumer_stage_that_shows_the_answer(
    inputs: tuple[dict[str, Any], dict[str, dict[str, Any]]],
) -> None:
    manifest = copy.deepcopy(inputs[0])
    manifest["records"][0]["canonical_task"]["stages"][5]["facts"] = ["Request reference: G73."]
    with pytest.raises(ceiling.ChanceCeilingError, match="extra facts"):
        ceiling.consumer_answers(manifest)


def test_rejects_a_campaign_missing_a_seed(
    inputs: tuple[dict[str, Any], dict[str, dict[str, Any]]],
) -> None:
    manifest, reports = inputs
    broken = copy.deepcopy(reports)
    rows = broken["replication"]["results"]
    index = next(
        i for i, r in enumerate(rows) if r["mode"] == "stateless" and r["phase"] == "primary"
    )
    del rows[index]
    with pytest.raises(ceiling.ChanceCeilingError, match="replication/stateless"):
        ceiling.analyze(manifest, broken)


def test_committed_artifacts_match_regeneration(tmp_path: Path) -> None:
    assert ceiling.main(["--repository-root", str(ROOT), "--output-dir", str(tmp_path)]) == 0
    for output in (ceiling.JSON_OUTPUT, ceiling.MD_OUTPUT):
        name = Path(output).name
        assert (tmp_path / name).read_text(encoding="utf-8") == (ROOT / output).read_text(
            encoding="utf-8"
        )


def test_markdown_numbers_come_from_json(result: dict[str, Any]) -> None:
    text = ceiling.render_markdown(result)
    assert "probability 1/9, 21.3 of 192 episodes expected" in text
    assert "| historical | 106/192 | 15/192 |" in text
    assert json.loads(ceiling.serialize(result)) == result
