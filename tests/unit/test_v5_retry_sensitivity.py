"""Tests for the post hoc v5 retry-sensitivity supplement."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import generate_v5_retry_sensitivity as retry
from scripts import verify_v5_retry_sensitivity

ROOT = Path(__file__).resolve().parents[2]
Inputs = dict[str, tuple[dict[str, Any], dict[str, Any]]]


@pytest.fixture(scope="module")
def inputs() -> Inputs:
    return retry.load_inputs(ROOT)


@pytest.fixture(scope="module")
def result(inputs: Inputs) -> dict[str, Any]:
    return retry.analyze(inputs)


def _row(trial_id: str, classification: str, attempts: int, actions: int) -> dict[str, Any]:
    return {
        "classification": classification,
        "environment_actions": actions,
        "model_attempts": attempts,
        "trial_id": trial_id,
    }


@pytest.mark.parametrize(
    ("classification", "attempts", "actions", "expected"),
    [
        ("success_termination", 21, 21, 0),
        ("success_termination", 24, 21, 3),
        ("step_limit_truncation", 29, 28, 1),
        ("invalid_output", 12, 11, 0),
        ("invalid_output", 14, 12, 1),
        ("infrastructure_failure", 19, 17, 1),
    ],
)
def test_inference_rule(classification: str, attempts: int, actions: int, expected: int) -> None:
    assert retry.inferred_retries(_row("t", classification, attempts, actions)) == expected


def test_inference_rejects_fewer_attempts_than_actions() -> None:
    with pytest.raises(retry.RetrySensitivityError, match="fewer attempts"):
        retry.inferred_retries(_row("t", "invalid_output", 11, 11))


def test_retried_attempts_per_campaign(result: dict[str, Any]) -> None:
    totals = {name: c["total_retried_attempts"] for name, c in result["campaigns"].items()}
    assert totals == {"historical": 19, "replication": 1, "parser_sensitivity": 5}
    sources = {name: c["retry_source"] for name, c in result["campaigns"].items()}
    assert sources == {
        "historical": "inferred",
        "replication": "recorded",
        "parser_sensitivity": "recorded",
    }


def test_every_retry_is_in_the_history_arm(result: dict[str, Any]) -> None:
    for campaign in result["campaigns"].values():
        assert {e["mode"] for e in campaign["retried_episodes"]} <= {"history"}


def test_both_results_are_reported(result: dict[str, Any]) -> None:
    primary = {
        name: (
            c["results_by_phase_and_arm"]["primary"]["history"]["successes_as_recorded"],
            c["results_by_phase_and_arm"]["primary"]["history"][
                "successes_with_retried_episodes_as_failures"
            ],
            c["results_by_phase_and_arm"]["primary"]["stateless"]["successes_as_recorded"],
        )
        for name, c in result["campaigns"].items()
    }
    assert primary == {
        "historical": (106, 96, 15),
        "replication": (62, 61, 20),
        "parser_sensitivity": (132, 130, 20),
    }
    diff = result["campaigns"]["historical"]["primary_difference_history_minus_stateless"]
    assert diff["as_recorded"]["points"] == 47.4
    assert diff["with_retried_episodes_as_failures"]["points"] == 42.2


def test_rule_in_force_comes_from_the_bound_plan(result: dict[str, Any]) -> None:
    rules = {
        name: c["retry_rule_in_force"]["transport_retry_rule"]
        for name, c in result["campaigns"].items()
    }
    assert rules["historical"] == "claude-one-stopped-timeout-or-connection-retry-v1"
    assert rules["replication"] == rules["parser_sensitivity"]


def test_rejects_inference_that_disagrees_with_recorded_retries(inputs: Inputs) -> None:
    broken = copy.deepcopy(inputs)
    report, _plan = broken["replication"]
    target = next(
        r for r in report["results"] if r["trial_id"] == "d59-rep-primary-6180-history-r0"
    )
    target["model_attempts"] += 1
    with pytest.raises(retry.RetrySensitivityError, match="replication: inferred retries"):
        retry.analyze(broken)


def test_rejects_missing_retry_records_without_disclosed_journal_loss(inputs: Inputs) -> None:
    broken = copy.deepcopy(inputs)
    del broken["parser_sensitivity"][0]["unresolved_timeouts"]
    with pytest.raises(retry.RetrySensitivityError, match="no disclosed journal loss"):
        retry.analyze(broken)


def test_rejects_a_report_bound_to_another_plan(inputs: Inputs) -> None:
    broken = copy.deepcopy(inputs)
    broken["historical"][0]["execution_plan_digest"] = "sha256:" + "0" * 64
    with pytest.raises(retry.RetrySensitivityError, match="does not bind"):
        retry.analyze(broken)


def test_rejects_arms_with_different_retry_rules(inputs: Inputs) -> None:
    broken = copy.deepcopy(inputs)
    broken["replication"][1]["policy_manifests"]["stateless"]["transport_retry_rule"] = "other"
    with pytest.raises(retry.RetrySensitivityError, match="share one retry rule"):
        retry.analyze(broken)


def test_committed_artifacts_match_regeneration(tmp_path: Path) -> None:
    assert retry.main(["--repository-root", str(ROOT), "--output-dir", str(tmp_path)]) == 0
    for output in (retry.JSON_OUTPUT, retry.MD_OUTPUT):
        name = Path(output).name
        assert (tmp_path / name).read_text(encoding="utf-8") == (ROOT / output).read_text(
            encoding="utf-8"
        )


def test_verifier_passes_and_fails_without_writing(tmp_path: Path) -> None:
    assert verify_v5_retry_sensitivity.main(["--repository-root", str(ROOT)]) == 0
    for _name, report, plan in retry.CAMPAIGNS:
        for source in (report, plan):
            (tmp_path / source).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / source).write_bytes((ROOT / source).read_bytes())
    for output in (retry.JSON_OUTPUT, retry.MD_OUTPUT):
        (tmp_path / output).write_bytes((ROOT / output).read_bytes())
    md = tmp_path / retry.MD_OUTPUT
    md.write_text(md.read_text(encoding="utf-8").replace("96/192", "97/192"), encoding="utf-8")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert verify_v5_retry_sensitivity.main(["--repository-root", str(tmp_path)]) == 1
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


def test_markdown_is_labelled_post_hoc_and_numbers_come_from_json(result: dict[str, Any]) -> None:
    text = retry.render_markdown(result)
    assert text.startswith("# v5 retried attempts and their effect on each arm (post hoc)")
    assert "**Post hoc.**" in text
    assert "| historical | history | 106/192 | 15 (10) | 96/192 |" in text
    assert json.loads(retry.serialize(result)) == result
    assert result["post_hoc"] is True
