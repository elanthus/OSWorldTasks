"""Fast contract tests for the frozen v4c longer-horizon pilot."""

from __future__ import annotations

import ast
import copy
import json
import re
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pytest

from pixelgym.actions import ActionType
from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.calibration_v4c import validate_v4c_capture
from pixelgym.grounding.evaluation import ResponseCache
from pixelgym.grounding.providers import MockProvider
from pixelgym.grounding.v4c_backend import V4CReplayBackend
from pixelgym.grounding.v4c_evaluation import (
    _candidate_center,
    _image_for_condition,
    _load_inputs,
    _paid_call_ledger_path,
    _parse_action,
    _state_for_observation,
    _target_center,
    planned_v4c_calls,
    read_paid_call_ledger,
    record_v4c_evaluation,
    run_v4c_evaluation,
    summarize_v4c_evaluation,
)
from pixelgym.grounding.v4c_protocol import (
    V4C_CALL_CAP,
    V4C_EPISODES,
    V4C_SEEDS,
    V4C_SKIP_ID,
    _validate_deferred_dependency,
    episode_for_seed,
    episode_max_actions,
    validate_v4c_protocol,
)
from pixelgym.serialization import load_jsonl

REPOSITORY_ROOT = Path(__file__).parents[2]
PRIOR_GROUNDING_SEEDS = set(range(20)) | set(range(20, 24)) | {30, 31} | set(range(40, 50)) | {7}


def _click(x: int, y: int) -> dict[str, int]:
    return {"action_type": int(ActionType.CLICK), "x": x, "y": y, "key": 0}


def test_v4c_protocol_freezes_longer_horizon_episodes_and_cap() -> None:
    summary = validate_v4c_protocol()
    assert summary == {
        "episode_count": 10,
        "family_counts": {
            "pin_traverse_apply": 4,
            "reconcile_deferred_evidence": 3,
            "constraint_repair_chain": 3,
        },
        "call_cap": V4C_CALL_CAP,
    }
    decision_counts = sorted(len(episode["stages"]) for episode in V4C_EPISODES)
    assert decision_counts == [6, 6, 6, 6, 7, 7, 7, 8, 8, 8]
    assert sum(count + 2 for count in decision_counts) * 2 == 178
    assert not set(V4C_SEEDS) & PRIOR_GROUNDING_SEEDS


@pytest.mark.parametrize(
    "mutation,error",
    [
        (
            lambda e: e["stages"][e["consumer_stage"] - 1]["facts"].append(
                f"Reminder: the value is {e['carrier']['value']}."
            ),
            "leaked into a post-commit stage's facts",
        ),
        (
            lambda e: e["stages"][e["consumer_stage"] + 1]["options"].append(
                {"semantic_id": "late_leak", "label": f"Reuse {e['carrier']['value']}"}
            ),
            "leaked into a non-consumer stage's options",
        ),
        (
            lambda e: e["stages"][e["commit_stage"]].__setitem__("target", "dismiss_notice"),
            "commit stage target must be the pin control",
        ),
        (
            lambda e: e["stages"][e["commit_stage"]]["options"].pop(1),
            "must offer the progressing skip control",
        ),
        (
            lambda e: e.__setitem__("consumer_stage", e["commit_stage"] + 2),
            "at least three carrier-free decisions",
        ),
        (
            lambda e: e["stages"][e["consumer_stage"] + 1]["options"].append(
                {"semantic_id": V4C_SKIP_ID, "label": "Continue without pinning"}
            ),
            "skip control may exist only on the commit stage",
        ),
    ],
)
def test_v4c_dependency_validator_rejects_structural_leaks(mutation: Any, error: str) -> None:
    episode = copy.deepcopy(episode_for_seed(60))
    mutation(episode)
    with pytest.raises(ValueError, match=error):
        _validate_deferred_dependency(episode)


def test_v4c_capture_validator_rejects_duplicate_state() -> None:
    states = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4c-pilot-states.jsonl")
    candidates = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4c-pilot-candidates.jsonl")
    overlays = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4c-pilot-overlays.jsonl")
    states[-1] = states[0]
    with pytest.raises(ValueError, match="incomplete or contains duplicates"):
        validate_v4c_capture(states, candidates, overlays)


def test_v4c_evaluation_fails_closed_on_unknown_screenshot_pixels() -> None:
    with pytest.raises(ValueError, match="unknown v4c screenshot pixel hash"):
        _state_for_observation({"state_by_pixels": {}}, np.zeros((768, 1024, 3), dtype=np.uint8))


def test_v4c_candidate_and_overlay_artifacts_contain_no_target_identity() -> None:
    rows = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4c-pilot-candidates.jsonl")
    rows += load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4c-pilot-overlays.jsonl")

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value).union(*(keys(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(child) for child in value))
        return set()

    forbidden = {"target", "target_id", "skip", "commit_stage", "consumer_stage"}
    assert all(not (forbidden & keys(row)) for row in rows)


def test_v4c_capture_validator_rejects_nested_target_leak() -> None:
    states = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4c-pilot-states.jsonl")
    candidates = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4c-pilot-candidates.jsonl")
    overlays = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4c-pilot-overlays.jsonl")
    candidates[0]["candidates"][0]["target"] = "leaked"
    with pytest.raises(ValueError, match="must not leak targets"):
        validate_v4c_capture(states, candidates, overlays)


def test_v4c_evaluation_does_not_import_capture_instrumentation() -> None:
    source = REPOSITORY_ROOT / "pixelgym/grounding/v4c_evaluation.py"
    tree = ast.parse(source.read_text())
    from_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = "pixelgym.grounding.calibration_v4c"
    assert forbidden not in from_modules
    assert forbidden not in imported_modules


def test_v4c_capture_app_has_no_runtime_network_calls() -> None:
    app_source = (REPOSITORY_ROOT / "pixelgym/grounding/v4c_app/static/app.js").read_text()
    prohibited = (
        r"\bfetch\s*\(",
        r"\bXMLHttpRequest\b",
        r"\bnavigator\s*\.\s*sendBeacon\s*\(",
        r"\bWebSocket\s*\(",
        r"\bEventSource\s*\(",
    )
    assert all(re.search(pattern, app_source) is None for pattern in prohibited)


@pytest.mark.parametrize(
    "raw,error",
    [
        ('{"action_type":0,"x":1,"y":1,"key":0}', "action_type=CLICK"),
        ('{"action_type":1,"x":1024,"y":1,"key":0}', "outside"),
        ('{"action_type":1,"x":1,"y":1}', "exactly"),
        ("not json", "invalid JSON"),
    ],
)
def test_v4c_parser_rejects_invalid_actions(raw: str, error: str) -> None:
    action, message = _parse_action(raw)
    assert action is None
    assert message is not None and error in message


def test_v4c_environment_has_no_premature_reward_and_visible_recovery() -> None:
    inputs = _load_inputs(REPOSITORY_ROOT)
    seed = 60
    episode = episode_for_seed(seed)
    decisions = len(episode["stages"])
    commit = episode["commit_stage"]
    backend = V4CReplayBackend(REPOSITORY_ROOT)
    env = PixelGuiEnv(
        backend, instruction="Complete workflow", max_episode_steps=episode_max_actions(seed)
    )
    try:
        env.reset(seed=seed)
        observation, reward, terminated, truncated, _ = env.step(_click(5, 400))
        state = _state_for_observation(inputs, observation)
        assert (state["stage"], state["pinned"], state["recovery"]) == (0, False, True)
        assert (reward, terminated, truncated) == (0.0, False, False)
        for stage in range(decisions):
            pinned = stage > commit
            observation, reward, terminated, truncated, _ = env.step(
                _click(*_target_center(inputs, seed, stage, pinned, stage == 0))
            )
            if stage < decisions - 1:
                after = _state_for_observation(inputs, observation)
                assert after["pinned"] is (stage + 1 > commit)
                assert (reward, terminated, truncated) == (0.0, False, False)
        assert (reward, terminated, truncated) == (1.0, True, False)
        with pytest.raises(RuntimeError, match="after the episode already ended"):
            env.step(_click(0, 0))
    finally:
        env.close()


def test_v4c_skip_branch_progresses_unpinned_to_ambiguous_consumer() -> None:
    inputs = _load_inputs(REPOSITORY_ROOT)
    seed = 67
    episode = episode_for_seed(seed)
    decisions = len(episode["stages"])
    commit = episode["commit_stage"]
    consumer = episode["consumer_stage"]
    backend = V4CReplayBackend(REPOSITORY_ROOT)
    env = PixelGuiEnv(
        backend, instruction="Complete workflow", max_episode_steps=episode_max_actions(seed)
    )
    try:
        env.reset(seed=seed)
        for stage in range(commit):
            env.step(_click(*_target_center(inputs, seed, stage, False, False)))
        observation, reward, terminated, truncated, _ = env.step(
            _click(*_candidate_center(inputs, seed, commit, False, False, V4C_SKIP_ID))
        )
        state = _state_for_observation(inputs, observation)
        assert (state["stage"], state["pinned"], state["recovery"]) == (commit + 1, False, False)
        assert (reward, terminated, truncated) == (0.0, False, False)
        for stage in range(commit + 1, decisions):
            observation, reward, terminated, truncated, _ = env.step(
                _click(*_target_center(inputs, seed, stage, False, False))
            )
            if stage == consumer - 1:
                faced = _state_for_observation(inputs, observation)
                assert faced["stage"] == consumer and faced["pinned"] is False
        assert (reward, terminated, truncated) == (1.0, True, False)
        assert len(backend.read_submissions()) == 1
    finally:
        env.close()


def test_v4c_step_limit_truncates_without_reward() -> None:
    backend = V4CReplayBackend(REPOSITORY_ROOT)
    env = PixelGuiEnv(backend, instruction="Complete workflow", max_episode_steps=1)
    try:
        env.reset(seed=60)
        _, reward, terminated, truncated, _ = env.step(_click(5, 400))
        assert (reward, terminated, truncated) == (0.0, False, True)
        with pytest.raises(RuntimeError, match="after the episode already ended"):
            env.step(_click(5, 400))
    finally:
        env.close()


def test_v4c_free_plan_reports_exact_upper_bound_without_provider_calls(tmp_path: Path) -> None:
    provider = MockProvider()
    plan = planned_v4c_calls(
        repository_root=REPOSITORY_ROOT,
        provider=provider,
        cache=ResponseCache(tmp_path / "cache"),
    )
    assert plan["upper_bound_calls"] == 178
    assert plan["cached_reachable_state_requests"] == 0
    assert plan["proposal_covered_states"] == plan["proposal_total_states"] == 244
    assert provider.call_count == 0


def test_v4c_runner_enforces_cap_before_first_uncached_call(tmp_path: Path) -> None:
    provider = MockProvider()
    with pytest.raises(RuntimeError, match="approved cap of 0"):
        run_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            provider=provider,
            predictions_path=REPOSITORY_ROOT / ".cache" / f"{tmp_path.name}-predictions.jsonl",
            conditions_path=REPOSITORY_ROOT / ".cache" / f"{tmp_path.name}-conditions.jsonl",
            max_new_calls=0,
            cache_directory=tmp_path / "cache",
        )
    assert provider.call_count == 0


@pytest.mark.parametrize("case", ["same", "outside", "existing"])
def test_v4c_runner_preflights_immutable_output_paths_before_inputs(
    tmp_path: Path, case: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    first = root / "predictions.jsonl"
    second = root / "conditions.jsonl"
    expected = "fresh immutable"
    if case == "same":
        second = first
        expected = "must differ"
    elif case == "outside":
        second = tmp_path / "outside.jsonl"
        expected = "inside the repository"
    else:
        first.write_text("occupied")
    provider = MockProvider()
    with pytest.raises(ValueError, match=expected):
        run_v4c_evaluation(
            repository_root=root,
            provider=provider,
            predictions_path=first,
            conditions_path=second,
            max_new_calls=V4C_CALL_CAP,
            cache_directory=tmp_path / "cache",
        )
    assert provider.call_count == 0


def _fixture_condition(
    seed: int, condition: str, *, success: bool, parse_failure: bool, cached: bool
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    episode = episode_for_seed(seed)
    decisions = len(episode["stages"])
    commit = episode["commit_stage"]
    consumer = episode["consumer_stage"]
    base = {
        "seed": seed,
        "condition": condition,
        "cache_hit": cached,
        "parse_status": "parsed",
        "reward": 0.0,
        "terminated": False,
        "truncated": False,
        "model": "gpt-5.6-luna",
        "parameters": {"reasoning_effort": "low", "temperature": None},
    }
    if parse_failure:
        rows = [
            {
                **base,
                "action_index": 1,
                "parse_status": "invalid",
                "checkpoint_after": 0,
                "pinned_after": False,
            }
        ]
        derived: dict[str, Any] = {
            "success": False,
            "terminated": False,
            "truncated": False,
            "checkpoint_count": 0,
            "committed": False,
            "consumer_pinned": None,
            "consumer_correct": None,
            "failures": ["parse_failure"],
        }
    elif success:
        rows = [
            {
                **base,
                "action_index": index,
                "checkpoint_after": index,
                "pinned_after": index > commit,
                "reward": 1.0 if index == decisions else 0.0,
                "terminated": index == decisions,
            }
            for index in range(1, decisions + 1)
        ]
        derived = {
            "success": True,
            "terminated": True,
            "truncated": False,
            "checkpoint_count": decisions,
            "committed": True,
            "consumer_pinned": consumer > commit,
            "consumer_correct": True,
            "failures": [],
        }
    else:
        limit = episode_max_actions(seed)
        rows = [
            {
                **base,
                "action_index": index,
                "checkpoint_after": 0,
                "pinned_after": False,
                "truncated": index == limit,
            }
            for index in range(1, limit + 1)
        ]
        derived = {
            "success": False,
            "terminated": False,
            "truncated": True,
            "checkpoint_count": 0,
            "committed": False,
            "consumer_pinned": None,
            "consumer_correct": None,
            "failures": [],
        }
    summary = {
        "seed": seed,
        "condition": condition,
        "action_count": len(rows),
        "new_calls": 0 if cached else len(rows),
        "cache_hits": len(rows) if cached else 0,
        **derived,
    }
    return rows, summary


def _write_summary_fixture(
    root: Path,
    *,
    raw_success: int,
    marks_success: int,
    failure: str | None = None,
    prefix: str = "",
    cached: bool = False,
) -> tuple[Path, Path]:
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "grounding-v4c-pilot-capture.json").write_text(
        json.dumps({"proposal_covered_state_count": 244, "actionable_state_count": 244})
    )
    predictions: list[dict[str, Any]] = []
    conditions = []
    for seed in range(60, 70):
        for condition in ("raw", "marks"):
            success_count = raw_success if condition == "raw" else marks_success
            parse_failure = bool(failure) and seed == 60 and condition == "raw"
            rows, summary = _fixture_condition(
                seed,
                condition,
                success=seed - 60 < success_count and not parse_failure,
                parse_failure=parse_failure,
                cached=cached,
            )
            predictions.extend(rows)
            conditions.append(summary)
    predictions_path = artifacts / f"{prefix}predictions.jsonl"
    conditions_path = artifacts / f"{prefix}conditions.jsonl"
    predictions_path.write_text("".join(json.dumps(row) + "\n" for row in predictions))
    conditions_path.write_text("".join(json.dumps(row) + "\n" for row in conditions))
    return predictions_path, conditions_path


@pytest.mark.parametrize(
    "raw,marks,failure,route",
    [
        (5, 6, None, "freeze_v4c_request_ceiling_approval"),
        (4, 5, None, "freeze_v4c_request_ceiling_approval"),
        (9, 8, None, "report_saturation_stop"),
        (8, 9, None, "report_saturation_stop"),
        (3, 8, None, "floor_transport_audit"),
        (8, 8, "parse_failure", "floor_transport_audit"),
        (8, 4, None, "human_review"),
    ],
)
def test_v4c_offline_routing(
    tmp_path: Path, raw: int, marks: int, failure: str | None, route: str
) -> None:
    predictions, conditions = _write_summary_fixture(
        tmp_path, raw_success=raw, marks_success=marks, failure=failure
    )
    results = summarize_v4c_evaluation(
        repository_root=tmp_path,
        predictions_path=predictions,
        conditions_path=conditions,
    )
    assert results["routing"]["decision"] == route


def test_v4c_offline_summary_reports_commitment_and_carrier_utilization(tmp_path: Path) -> None:
    predictions, conditions = _write_summary_fixture(tmp_path, raw_success=5, marks_success=6)
    results = summarize_v4c_evaluation(
        repository_root=tmp_path,
        predictions_path=predictions,
        conditions_path=conditions,
    )
    diagnostics = results["diagnostics"]
    assert diagnostics["commit_counts"] == {"raw": 5, "marks": 6}
    assert diagnostics["carrier_utilization"] == {
        "pinned_consumer_faced": 11,
        "pinned_consumer_correct": 11,
        "unpinned_consumer_faced": 0,
        "unpinned_consumer_correct": 0,
    }


def test_v4c_offline_summary_tracks_and_enforces_cumulative_paid_calls(
    tmp_path: Path,
) -> None:
    prior_predictions, prior_conditions = _write_summary_fixture(
        tmp_path, raw_success=9, marks_success=10, prefix="prior-"
    )
    prior_new_calls = sum(1 for row in load_jsonl(prior_predictions) if row["cache_hit"] is False)
    predictions, conditions = _write_summary_fixture(
        tmp_path, raw_success=9, marks_success=10, prefix="current-", cached=True
    )
    results = summarize_v4c_evaluation(
        repository_root=tmp_path,
        predictions_path=predictions,
        conditions_path=conditions,
        prior_predictions_path=prior_predictions,
        prior_conditions_path=prior_conditions,
    )
    assert prior_new_calls == 140
    assert results["collection"]["new_call_count"] == 0
    assert results["collection"]["prior_paid_call_count"] == prior_new_calls
    assert results["collection"]["cumulative_paid_call_count"] == prior_new_calls
    assert results["prior_collection"]["new_call_count"] == prior_new_calls

    excess_predictions, excess_conditions = _write_summary_fixture(
        tmp_path, raw_success=9, marks_success=10, prefix="excess-"
    )
    with pytest.raises(ValueError, match="cumulative paid calls"):
        summarize_v4c_evaluation(
            repository_root=tmp_path,
            predictions_path=excess_predictions,
            conditions_path=excess_conditions,
            prior_predictions_path=prior_predictions,
            prior_conditions_path=prior_conditions,
        )


def test_v4c_offline_summary_rejects_condition_prediction_counter_mismatch(
    tmp_path: Path,
) -> None:
    predictions, conditions = _write_summary_fixture(tmp_path, raw_success=9, marks_success=10)
    rows = load_jsonl(conditions)
    rows[0]["new_calls"] -= 1
    conditions.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="counters do not match"):
        summarize_v4c_evaluation(
            repository_root=tmp_path,
            predictions_path=predictions,
            conditions_path=conditions,
        )


def test_v4c_offline_summary_rejects_aliased_evidence_paths(tmp_path: Path) -> None:
    predictions, _ = _write_summary_fixture(tmp_path, raw_success=9, marks_success=10)
    with pytest.raises(ValueError, match="summary evidence paths must be distinct"):
        summarize_v4c_evaluation(
            repository_root=tmp_path,
            predictions_path=predictions,
            conditions_path=predictions,
        )


def test_v4c_runner_enforces_cumulative_ledger_across_invocations(tmp_path: Path) -> None:
    provider = MockProvider()
    cache = ResponseCache(tmp_path / "cache")
    ledger = _paid_call_ledger_path(cache, provider)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"attempted_paid_calls": V4C_CALL_CAP}) + "\n")
    with pytest.raises(RuntimeError, match="cumulative attempted paid calls"):
        run_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            provider=provider,
            predictions_path=REPOSITORY_ROOT / ".cache" / f"{tmp_path.name}-l-predictions.jsonl",
            conditions_path=REPOSITORY_ROOT / ".cache" / f"{tmp_path.name}-l-conditions.jsonl",
            max_new_calls=V4C_CALL_CAP,
            cache_directory=cache.directory,
        )
    assert provider.call_count == 0
    assert read_paid_call_ledger(cache, provider) == V4C_CALL_CAP


def test_v4c_runner_ledger_counts_every_attempted_paid_call(tmp_path: Path) -> None:
    provider = MockProvider()
    cache = ResponseCache(tmp_path / "cache")
    # Evidence paths must live inside the repository, so use a unique throwaway
    # directory under the ignored .cache tree and remove it afterwards.
    output_dir = REPOSITORY_ROOT / ".cache" / f"test-run-{uuid4().hex}"
    try:
        result = run_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            provider=provider,
            predictions_path=output_dir / "predictions.jsonl",
            conditions_path=output_dir / "conditions.jsonl",
            max_new_calls=V4C_CALL_CAP,
            cache_directory=cache.directory,
        )
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
    assert result["new_calls"] == provider.call_count == 20
    assert result["prior_attempted_paid_calls"] == 0
    assert result["cumulative_attempted_paid_calls"] == 20
    assert read_paid_call_ledger(cache, provider) == 20


def test_v4c_image_for_condition_rejects_tampered_bytes(tmp_path: Path) -> None:
    (tmp_path / "marked.png").write_bytes(b"tampered marked bytes")
    (tmp_path / "raw.png").write_bytes(b"tampered raw bytes")
    inputs = {
        "overlay_by_raw": {
            "raw-digest": {
                "marked_image_path": "marked.png",
                "marked_image_sha256": "0" * 64,
            }
        }
    }
    with pytest.raises(ValueError, match="request image digest mismatch"):
        _image_for_condition(tmp_path, inputs, {"image_sha256": "raw-digest"}, "marks")
    with pytest.raises(ValueError, match="request image digest mismatch"):
        _image_for_condition(
            tmp_path,
            inputs,
            {"image_path": "raw.png", "image_sha256": "0" * 64},
            "raw",
        )


@pytest.mark.parametrize("field,value", [("success", False), ("checkpoint_count", 3)])
def test_v4c_offline_summary_rejects_edited_condition_outcomes(
    tmp_path: Path, field: str, value: Any
) -> None:
    predictions, conditions = _write_summary_fixture(tmp_path, raw_success=9, marks_success=10)
    rows = load_jsonl(conditions)
    rows[0][field] = value
    conditions.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="summaries do not match prediction records"):
        summarize_v4c_evaluation(
            repository_root=tmp_path,
            predictions_path=predictions,
            conditions_path=conditions,
        )


def test_v4c_recorder_rejects_aliased_output_before_overwrite(tmp_path: Path) -> None:
    predictions, conditions = _write_summary_fixture(tmp_path, raw_success=9, marks_success=10)
    original = predictions.read_bytes()
    with pytest.raises(ValueError, match="result, and manifest paths must be distinct"):
        record_v4c_evaluation(
            repository_root=tmp_path,
            predictions_path=predictions,
            conditions_path=conditions,
            results_path=predictions,
            manifest_path=tmp_path / "artifacts" / "manifest.json",
        )
    assert predictions.read_bytes() == original
