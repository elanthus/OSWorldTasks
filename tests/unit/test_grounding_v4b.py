"""Fast contract tests for the frozen v4b multi-step pilot."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pixelgym.actions import ActionType
from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.calibration_v4b import validate_v4b_capture
from pixelgym.grounding.evaluation import ResponseCache
from pixelgym.grounding.providers import MockProvider
from pixelgym.grounding.v4b_backend import V4BReplayBackend
from pixelgym.grounding.v4b_evaluation import (
    _load_inputs,
    _parse_action,
    _state_for_observation,
    _target_center,
    planned_v4b_calls,
    run_v4b_evaluation,
    summarize_v4b_evaluation,
)
from pixelgym.grounding.v4b_protocol import (
    V4B_CALL_CAP,
    V4B_EPISODES,
    V4B_MAX_ACTIONS,
    validate_v4b_protocol,
)
from pixelgym.serialization import load_jsonl

REPOSITORY_ROOT = Path(__file__).parents[2]


def _click(x: int, y: int) -> dict[str, int]:
    return {"action_type": int(ActionType.CLICK), "x": x, "y": y, "key": 0}


def test_v4b_protocol_freezes_ten_three_step_episodes_and_eighty_call_cap() -> None:
    summary = validate_v4b_protocol()
    assert summary == {
        "episode_count": 10,
        "family_counts": {
            "triage_route_confirm": 4,
            "reconcile_inspect_resolve": 3,
            "diagnose_repair_resubmit": 3,
        },
        "call_cap": V4B_CALL_CAP,
    }
    assert all(len(episode["stages"]) == 3 for episode in V4B_EPISODES)
    assert 10 * 2 * V4B_MAX_ACTIONS == 80


def test_v4b_capture_validator_rejects_duplicate_state() -> None:
    states = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4b-pilot-states.jsonl")
    candidates = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4b-pilot-candidates.jsonl")
    overlays = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4b-pilot-overlays.jsonl")
    states[-1] = states[0]
    with pytest.raises(ValueError, match="incomplete or contains duplicates"):
        validate_v4b_capture(states, candidates, overlays)


def test_v4b_evaluation_fails_closed_on_unknown_screenshot_pixels() -> None:
    with pytest.raises(ValueError, match="unknown v4b screenshot pixel hash"):
        _state_for_observation({"state_by_pixels": {}}, np.zeros((768, 1024, 3), dtype=np.uint8))


def test_v4b_candidate_and_overlay_artifacts_contain_no_target_identity() -> None:
    rows = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4b-pilot-candidates.jsonl")
    rows += load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4b-pilot-overlays.jsonl")
    assert all("target" not in row and "target_id" not in row for row in rows)


def test_v4b_evaluation_does_not_import_capture_instrumentation() -> None:
    source = REPOSITORY_ROOT / "pixelgym/grounding/v4b_evaluation.py"
    tree = ast.parse(source.read_text())
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "pixelgym.grounding.calibration_v4b" not in modules


def test_v4b_capture_app_has_no_runtime_network_calls() -> None:
    app_source = (REPOSITORY_ROOT / "pixelgym/grounding/v4b_app/static/app.js").read_text()
    assert "fetch(" not in app_source
    assert "XMLHttpRequest" not in app_source


@pytest.mark.parametrize(
    "raw,error",
    [
        ('{"action_type":0,"x":1,"y":1,"key":0}', "action_type=CLICK"),
        ('{"action_type":1,"x":1024,"y":1,"key":0}', "outside"),
        ('{"action_type":1,"x":1,"y":1}', "exactly"),
        ("not json", "invalid JSON"),
    ],
)
def test_v4b_parser_rejects_invalid_actions(raw: str, error: str) -> None:
    action, message = _parse_action(raw)
    assert action is None
    assert message is not None and error in message


def test_v4b_environment_has_no_premature_reward_and_visible_recovery() -> None:
    inputs = _load_inputs(REPOSITORY_ROOT)
    backend = V4BReplayBackend(REPOSITORY_ROOT)
    env = PixelGuiEnv(backend, instruction="Complete workflow", max_episode_steps=4)
    try:
        env.reset(seed=40)
        observation, reward, terminated, truncated, _ = env.step(_click(5, 740))
        state = _state_for_observation(inputs, observation)
        assert (state["stage"], state["recovery"]) == (0, True)
        assert (reward, terminated, truncated) == (0.0, False, False)
        for stage in range(3):
            observation, reward, terminated, truncated, _ = env.step(
                _click(*_target_center(inputs, 40, stage, stage == 0))
            )
            if stage < 2:
                assert (reward, terminated, truncated) == (0.0, False, False)
        assert (reward, terminated, truncated) == (1.0, True, False)
        with pytest.raises(RuntimeError, match="after the episode already ended"):
            env.step(_click(0, 0))
    finally:
        env.close()


def test_v4b_free_plan_reports_exact_upper_bound_without_provider_calls(tmp_path: Path) -> None:
    provider = MockProvider()
    plan = planned_v4b_calls(
        repository_root=REPOSITORY_ROOT,
        provider=provider,
        cache=ResponseCache(tmp_path / "cache"),
    )
    assert plan["upper_bound_calls"] == 80
    assert plan["cached_reachable_state_requests"] == 0
    assert plan["proposal_covered_states"] == plan["proposal_total_states"] == 60
    assert provider.call_count == 0


def test_v4b_runner_enforces_cap_before_first_uncached_call(tmp_path: Path) -> None:
    provider = MockProvider()
    with pytest.raises(RuntimeError, match="approved cap of 0"):
        run_v4b_evaluation(
            repository_root=REPOSITORY_ROOT,
            provider=provider,
            predictions_path=tmp_path / "predictions.jsonl",
            conditions_path=tmp_path / "conditions.jsonl",
            max_new_calls=0,
            cache_directory=tmp_path / "cache",
        )
    assert provider.call_count == 0


def _write_summary_fixture(
    root: Path, *, raw_success: int, marks_success: int, failure: str | None = None
) -> tuple[Path, Path]:
    artifacts = root / "artifacts"
    artifacts.mkdir()
    (artifacts / "grounding-v4b-pilot-capture.json").write_text(
        json.dumps({"proposal_covered_state_count": 60, "actionable_state_count": 60})
    )
    predictions: list[dict[str, Any]] = []
    conditions = []
    for seed in range(40, 50):
        for condition in ("raw", "marks"):
            success_count = raw_success if condition == "raw" else marks_success
            predictions.append(
                {
                    "model": "gpt-5.6-luna",
                    "parameters": {"reasoning_effort": "low", "temperature": None},
                }
            )
            conditions.append(
                {
                    "seed": seed,
                    "condition": condition,
                    "success": seed - 40 < success_count,
                    "checkpoint_count": 3 if seed - 40 < success_count else 2,
                    "action_count": 3,
                    "new_calls": 3,
                    "cache_hits": 0,
                    "failures": [failure] if failure and seed == 40 and condition == "raw" else [],
                }
            )
    predictions_path = artifacts / "predictions.jsonl"
    conditions_path = artifacts / "conditions.jsonl"
    predictions_path.write_text("".join(json.dumps(row) + "\n" for row in predictions))
    conditions_path.write_text("".join(json.dumps(row) + "\n" for row in conditions))
    return predictions_path, conditions_path


@pytest.mark.parametrize(
    "raw,marks,failure,route",
    [
        (6, 7, None, "freeze_v4b_request_ceiling_approval"),
        (9, 8, None, "design_longer_horizon_successor"),
        (4, 8, None, "floor_transport_audit"),
        (8, 8, "parse_failure", "floor_transport_audit"),
        (8, 8, None, "human_review"),
    ],
)
def test_v4b_offline_routing(
    tmp_path: Path, raw: int, marks: int, failure: str | None, route: str
) -> None:
    predictions, conditions = _write_summary_fixture(
        tmp_path, raw_success=raw, marks_success=marks, failure=failure
    )
    results = summarize_v4b_evaluation(
        repository_root=tmp_path,
        predictions_path=predictions,
        conditions_path=conditions,
    )
    assert results["routing"]["decision"] == route
