"""Fast contract tests for the frozen v4c longer-horizon pilot."""

from __future__ import annotations

import ast
import copy
import json
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pytest

from legacy.grounding.calibration_v4c import validate_v4c_capture
from legacy.grounding.v4c_backend import V4CReplayBackend
from legacy.grounding.v4c_evaluation import (
    V4C_ACTION_SCHEMA_VERSION,
    V4C_PARSER_VERSION,
    V4C_PREDICTION_SCHEMA_VERSION,
    V4C_PROMPT_VERSION,
    _candidate_center,
    _image_for_condition,
    _load_inputs,
    _paid_call_ledger_path,
    _parse_action,
    _replayed_condition_outcome,
    _state_for_observation,
    _state_identity,
    _target_center,
    initialize_v4c_model_manifest,
    planned_v4c_calls,
    read_paid_call_ledger,
    record_v4c_evaluation,
    run_v4c_evaluation,
    summarize_v4c_evaluation,
    write_v4c_attempts_snapshot,
)
from legacy.grounding.v4c_protocol import (
    V4C_CALL_CAP,
    V4C_EPISODES,
    V4C_PROTOCOL_VERSION,
    V4C_SEEDS,
    V4C_SKIP_ID,
    _validate_deferred_dependency,
    apply_v4c_click,
    episode_for_seed,
    episode_max_actions,
    validate_v4c_protocol,
)
from pixelgym.actions import ActionType
from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.evaluation import ResponseCache
from pixelgym.grounding.providers import MockProvider
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
    source = REPOSITORY_ROOT / "legacy/grounding/v4c_evaluation.py"
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
    forbidden = "legacy.grounding.calibration_v4c"
    assert forbidden not in from_modules
    assert forbidden not in imported_modules


def test_v4c_capture_app_has_no_runtime_network_calls() -> None:
    app_source = (REPOSITORY_ROOT / "legacy/grounding/v4c_app/static/app.js").read_text()
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
    output_dir = REPOSITORY_ROOT / ".cache" / f"test-run-{uuid4().hex}"
    try:
        with pytest.raises(RuntimeError, match="approved cap of 0"):
            run_v4c_evaluation(
                repository_root=REPOSITORY_ROOT,
                provider=provider,
                predictions_path=output_dir / "predictions.jsonl",
                conditions_path=output_dir / "conditions.jsonl",
                attempts_path=output_dir / "attempts.json",
                max_new_calls=0,
                cache_directory=tmp_path / "cache",
                ledger_directory=tmp_path / "ledgers",
            )
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
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
            attempts_path=root / "attempts.json",
            max_new_calls=V4C_CALL_CAP,
            cache_directory=tmp_path / "cache",
            ledger_directory=tmp_path / "ledgers",
        )
    assert provider.call_count == 0


_FIXTURE_INPUTS: dict[str, Any] | None = None


def _real_inputs() -> dict[str, Any]:
    global _FIXTURE_INPUTS
    if _FIXTURE_INPUTS is None:
        _FIXTURE_INPUTS = _load_inputs(REPOSITORY_ROOT)
    return _FIXTURE_INPUTS


def _fixture_condition(
    seed: int, condition: str, *, success: bool, parse_failure: bool, cached: bool
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build a prediction sequence consistent with the real captured state graph."""
    inputs = _real_inputs()
    episode = episode_for_seed(seed)
    decisions = len(episode["stages"])
    max_actions = episode_max_actions(seed)
    base = {
        "schema_version": V4C_PREDICTION_SCHEMA_VERSION,
        "protocol_version": V4C_PROTOCOL_VERSION,
        "prompt_version": V4C_PROMPT_VERSION,
        "parser_version": V4C_PARSER_VERSION,
        "action_schema_version": V4C_ACTION_SCHEMA_VERSION,
        "seed": seed,
        "family": episode["family"],
        "condition": condition,
        "provider": "mock-fixture",
        "model": "gpt-5.6-luna",
        "parameters": {"reasoning_effort": "low", "temperature": None},
        "cache_hit": cached,
        "request_failure": None,
    }
    rows: list[dict[str, Any]] = []
    stage, pinned, recovery = 0, False, False
    parsed_steps = 0
    while True:
        state = inputs["state_by_id"][_state_identity(seed, stage, pinned, recovery)]
        if condition == "raw":
            condition_image = state["image_sha256"]
        else:
            condition_image = inputs["overlay_by_raw"][state["image_sha256"]]["marked_image_sha256"]
        step = {
            **base,
            "action_index": len(rows) + 1,
            "observation_sha256": state["image_sha256"],
            "condition_image_sha256": condition_image,
        }
        if parse_failure:
            rows.append(
                {
                    **step,
                    "parse_status": "invalid",
                    "raw_response": "not json",
                    "parsed_action": None,
                    "reward": 0.0,
                    "terminated": False,
                    "truncated": False,
                    "post_observation_sha256": state["image_sha256"],
                    "checkpoint_after": stage,
                    "pinned_after": pinned,
                    "recovery_after": recovery,
                }
            )
            break
        if success:
            x, y = _target_center(inputs, seed, stage, pinned, recovery)
            clicked: str | None = episode["stages"][stage]["target"]
        else:
            x, y = 5, 400
            clicked = None
        after_stage, after_pinned, after_recovery = apply_v4c_click(episode, stage, pinned, clicked)
        parsed_steps += 1
        reward = 1.0 if after_stage == decisions else 0.0
        terminated = reward == 1.0
        truncated = not terminated and parsed_steps >= max_actions
        after = inputs["state_by_id"][
            _state_identity(seed, after_stage, after_pinned, after_recovery)
        ]
        action = {"action_type": 1, "x": x, "y": y, "key": 0}
        rows.append(
            {
                **step,
                "parse_status": "parsed",
                "raw_response": json.dumps(action),
                "parsed_action": action,
                "reward": reward,
                "terminated": terminated,
                "truncated": truncated,
                "post_observation_sha256": after["image_sha256"],
                "checkpoint_after": after_stage,
                "pinned_after": after_pinned,
                "recovery_after": after_recovery,
            }
        )
        stage, pinned, recovery = after_stage, after_pinned, after_recovery
        if terminated or truncated:
            break
    summary = {
        "seed": seed,
        "condition": condition,
        "action_count": len(rows),
        "new_calls": 0 if cached else len(rows),
        "cache_hits": len(rows) if cached else 0,
        **_replayed_condition_outcome(inputs, rows, seed, condition),
    }
    return rows, summary


def _rebind_attempts(
    attempts_path: Path,
    predictions_path: Path,
    conditions_path: Path,
    attempted: int | None = None,
) -> None:
    snapshot = json.loads(attempts_path.read_text())
    attempts_path.unlink()
    write_v4c_attempts_snapshot(
        provider_name=snapshot["provider"],
        model=snapshot["model"],
        attempted_paid_calls=snapshot["attempted_paid_calls"] if attempted is None else attempted,
        provenance=snapshot["attempt_ledger_provenance"],
        predictions_path=predictions_path,
        conditions_path=conditions_path,
        attempts_path=attempts_path,
    )


def _write_summary_fixture(
    evidence_dir: Path,
    *,
    raw_success: int,
    marks_success: int,
    failure: str | None = None,
    prefix: str = "",
    cached: bool = False,
    attempted: int | None = None,
) -> tuple[Path, Path, Path]:
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
    predictions_path = evidence_dir / f"{prefix}predictions.jsonl"
    conditions_path = evidence_dir / f"{prefix}conditions.jsonl"
    attempts_path = evidence_dir / f"{prefix}attempts.json"
    predictions_path.write_text("".join(json.dumps(row) + "\n" for row in predictions))
    conditions_path.write_text("".join(json.dumps(row) + "\n" for row in conditions))
    new_calls = sum(row["cache_hit"] is False for row in predictions)
    write_v4c_attempts_snapshot(
        provider_name="mock-fixture",
        model="gpt-5.6-luna",
        attempted_paid_calls=new_calls if attempted is None else attempted,
        provenance="test-fixture",
        predictions_path=predictions_path,
        conditions_path=conditions_path,
        attempts_path=attempts_path,
    )
    return predictions_path, conditions_path, attempts_path


@pytest.fixture
def evidence_dir() -> Iterator[Path]:
    path = REPOSITORY_ROOT / ".cache" / f"test-evidence-{uuid4().hex}"
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


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
    evidence_dir: Path, raw: int, marks: int, failure: str | None, route: str
) -> None:
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir, raw_success=raw, marks_success=marks, failure=failure
    )
    results = summarize_v4c_evaluation(
        repository_root=REPOSITORY_ROOT,
        predictions_path=predictions,
        conditions_path=conditions,
        attempts_path=attempts,
    )
    assert results["routing"]["decision"] == route


def test_v4c_offline_summary_reports_commitment_and_carrier_utilization(
    evidence_dir: Path,
) -> None:
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir, raw_success=5, marks_success=6
    )
    results = summarize_v4c_evaluation(
        repository_root=REPOSITORY_ROOT,
        predictions_path=predictions,
        conditions_path=conditions,
        attempts_path=attempts,
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
    evidence_dir: Path,
) -> None:
    prior_predictions, prior_conditions, _ = _write_summary_fixture(
        evidence_dir, raw_success=9, marks_success=10, prefix="prior-"
    )
    prior_new_calls = sum(1 for row in load_jsonl(prior_predictions) if row["cache_hit"] is False)
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir,
        raw_success=9,
        marks_success=10,
        prefix="current-",
        cached=True,
        attempted=prior_new_calls,
    )
    results = summarize_v4c_evaluation(
        repository_root=REPOSITORY_ROOT,
        predictions_path=predictions,
        conditions_path=conditions,
        attempts_path=attempts,
        prior_predictions_path=prior_predictions,
        prior_conditions_path=prior_conditions,
    )
    assert prior_new_calls == 140
    assert results["collection"]["new_call_count"] == 0
    assert results["collection"]["prior_paid_call_count"] == prior_new_calls
    assert results["collection"]["cumulative_paid_call_count"] == prior_new_calls
    assert results["collection"]["attempted_paid_call_count"] == prior_new_calls

    excess_predictions, excess_conditions, excess_attempts = _write_summary_fixture(
        evidence_dir, raw_success=9, marks_success=10, prefix="excess-"
    )
    with pytest.raises(ValueError, match="cumulative paid calls"):
        summarize_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            predictions_path=excess_predictions,
            conditions_path=excess_conditions,
            attempts_path=excess_attempts,
            prior_predictions_path=prior_predictions,
            prior_conditions_path=prior_conditions,
        )


def test_v4c_offline_summary_rejects_condition_prediction_counter_mismatch(
    evidence_dir: Path,
) -> None:
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir, raw_success=9, marks_success=10
    )
    rows = load_jsonl(conditions)
    rows[0]["new_calls"] -= 1
    conditions.write_text("".join(json.dumps(row) + "\n" for row in rows))
    _rebind_attempts(attempts, predictions, conditions)
    with pytest.raises(ValueError, match="counters do not match"):
        summarize_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            predictions_path=predictions,
            conditions_path=conditions,
            attempts_path=attempts,
        )


def test_v4c_offline_summary_rejects_aliased_evidence_paths(evidence_dir: Path) -> None:
    predictions, _, attempts = _write_summary_fixture(evidence_dir, raw_success=9, marks_success=10)
    with pytest.raises(ValueError, match="summary evidence paths must be distinct"):
        summarize_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            predictions_path=predictions,
            conditions_path=predictions,
            attempts_path=attempts,
        )


def test_v4c_runner_enforces_cumulative_ledger_across_invocations(tmp_path: Path) -> None:
    provider = MockProvider()
    ledger_dir = tmp_path / "ledgers"
    ledger = _paid_call_ledger_path(ledger_dir, provider)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text('{"provenance": "test"}\n' * V4C_CALL_CAP)
    output_dir = REPOSITORY_ROOT / ".cache" / f"test-run-{uuid4().hex}"
    try:
        with pytest.raises(RuntimeError, match="cumulative attempted paid calls"):
            run_v4c_evaluation(
                repository_root=REPOSITORY_ROOT,
                provider=provider,
                predictions_path=output_dir / "predictions.jsonl",
                conditions_path=output_dir / "conditions.jsonl",
                attempts_path=output_dir / "attempts.json",
                max_new_calls=V4C_CALL_CAP,
                cache_directory=tmp_path / "cache",
                ledger_directory=ledger_dir,
            )
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
    assert provider.call_count == 0
    assert read_paid_call_ledger(ledger_dir, provider) == V4C_CALL_CAP


def test_v4c_runner_ledger_is_not_reset_by_a_fresh_cache_directory(tmp_path: Path) -> None:
    provider = MockProvider()
    ledger_dir = tmp_path / "ledgers"
    ledger = _paid_call_ledger_path(ledger_dir, provider)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text('{"provenance": "test"}\n' * V4C_CALL_CAP)
    output_dir = REPOSITORY_ROOT / ".cache" / f"test-run-{uuid4().hex}"
    try:
        with pytest.raises(RuntimeError, match="cumulative attempted paid calls"):
            run_v4c_evaluation(
                repository_root=REPOSITORY_ROOT,
                provider=provider,
                predictions_path=output_dir / "predictions.jsonl",
                conditions_path=output_dir / "conditions.jsonl",
                attempts_path=output_dir / "attempts.json",
                max_new_calls=V4C_CALL_CAP,
                cache_directory=tmp_path / "second-fresh-cache",
                ledger_directory=ledger_dir,
            )
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
    assert provider.call_count == 0


def test_v4c_runner_ledger_counts_every_attempted_paid_call(tmp_path: Path) -> None:
    provider = MockProvider()
    ledger_dir = tmp_path / "ledgers"
    # Evidence paths must live inside the repository, so use a unique throwaway
    # directory under the ignored .cache tree and remove it afterwards.
    output_dir = REPOSITORY_ROOT / ".cache" / f"test-run-{uuid4().hex}"
    try:
        result = run_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            provider=provider,
            predictions_path=output_dir / "predictions.jsonl",
            conditions_path=output_dir / "conditions.jsonl",
            attempts_path=output_dir / "attempts.json",
            max_new_calls=V4C_CALL_CAP,
            cache_directory=tmp_path / "cache",
            ledger_directory=ledger_dir,
        )
        snapshot = json.loads((output_dir / "attempts.json").read_text())
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
    assert result["new_calls"] == provider.call_count == 20
    assert result["prior_attempted_paid_calls"] == 0
    assert result["cumulative_attempted_paid_calls"] == 20
    assert read_paid_call_ledger(ledger_dir, provider) == 20
    assert snapshot["attempted_paid_calls"] == 20
    assert snapshot["attempt_ledger_provenance"] == "runtime-ledger"


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
    evidence_dir: Path, field: str, value: Any
) -> None:
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir, raw_success=9, marks_success=10
    )
    rows = load_jsonl(conditions)
    rows[0][field] = value
    conditions.write_text("".join(json.dumps(row) + "\n" for row in rows))
    _rebind_attempts(attempts, predictions, conditions)
    with pytest.raises(ValueError, match="summaries do not match prediction records"):
        summarize_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            predictions_path=predictions,
            conditions_path=conditions,
            attempts_path=attempts,
        )


def test_v4c_offline_summary_replays_and_rejects_tampered_predictions(
    evidence_dir: Path,
) -> None:
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir, raw_success=9, marks_success=10
    )
    rows = load_jsonl(predictions)
    action = json.dumps({"action_type": 1, "x": 0, "y": 0, "key": 0})
    rows[0]["raw_response"] = action
    rows[0]["parsed_action"] = json.loads(action)
    rows[0]["observation_sha256"] = "0" * 64
    rows[0]["post_observation_sha256"] = "1" * 64
    predictions.write_text("".join(json.dumps(row) + "\n" for row in rows))
    _rebind_attempts(attempts, predictions, conditions)
    with pytest.raises(ValueError, match="does not match the replayed state"):
        summarize_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            predictions_path=predictions,
            conditions_path=conditions,
            attempts_path=attempts,
        )


def test_v4c_offline_summary_rejects_fabricated_terminal_reward(evidence_dir: Path) -> None:
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir, raw_success=9, marks_success=10
    )
    rows = load_jsonl(predictions)
    failing = [row for row in rows if row["seed"] == 69 and row["condition"] == "raw"]
    final = failing[-1]
    final["reward"] = 1.0
    final["terminated"] = True
    final["truncated"] = False
    predictions.write_text("".join(json.dumps(row) + "\n" for row in rows))
    _rebind_attempts(attempts, predictions, conditions)
    with pytest.raises(ValueError, match="does not match the replayed transition"):
        summarize_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            predictions_path=predictions,
            conditions_path=conditions,
            attempts_path=attempts,
        )


def test_v4c_offline_summary_rejects_unbound_or_deflated_attempts(evidence_dir: Path) -> None:
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir, raw_success=9, marks_success=10
    )
    _rebind_attempts(attempts, predictions, conditions, attempted=1)
    with pytest.raises(ValueError, match="below the verified stored responses"):
        summarize_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            predictions_path=predictions,
            conditions_path=conditions,
            attempts_path=attempts,
        )
    _rebind_attempts(attempts, predictions, conditions)
    rows = load_jsonl(predictions)
    predictions.write_text("".join(json.dumps(row) + "\n" for row in rows) + "\n")
    with pytest.raises(ValueError, match="not bound to this evidence"):
        summarize_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            predictions_path=predictions,
            conditions_path=conditions,
            attempts_path=attempts,
        )


def test_v4c_recorder_rejects_aliased_output_before_overwrite(evidence_dir: Path) -> None:
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir, raw_success=9, marks_success=10
    )
    original = predictions.read_bytes()
    with pytest.raises(ValueError, match="result, and manifest paths must be distinct"):
        record_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            predictions_path=predictions,
            conditions_path=conditions,
            attempts_path=attempts,
            results_path=predictions,
            manifest_path=evidence_dir / "manifest.json",
        )
    assert predictions.read_bytes() == original


def test_v4c_recorder_initializes_model_specific_manifest_and_output_keys(
    evidence_dir: Path,
) -> None:
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir, raw_success=9, marks_success=10
    )
    template = evidence_dir / "template.json"
    manifest = evidence_dir / "manifest.json"
    results = evidence_dir / "results.json"
    plan = evidence_dir / "plan.json"
    template.write_text(
        json.dumps(
            {
                "protocol_version": V4C_PROTOCOL_VERSION,
                "model": "old-model",
                "parameters": {},
                "status": "evaluated",
                "model_calls_performed": 99,
                "decision_history": [{"route": "old"}],
                "outputs": {
                    "capture": {"path": "capture.json", "sha256": "0" * 64},
                    "predictions_luna": {"path": "old.jsonl", "sha256": "1" * 64},
                    "floor_audit_luna": {"path": "old-audit.json", "sha256": "2" * 64},
                },
            }
        )
    )
    plan.write_text("{}\n")
    initialize_v4c_model_manifest(
        template_path=template,
        manifest_path=manifest,
        model="gpt-5.6-luna",
        parameters={"reasoning_effort": "low", "temperature": None},
    )

    record_v4c_evaluation(
        repository_root=REPOSITORY_ROOT,
        predictions_path=predictions,
        conditions_path=conditions,
        attempts_path=attempts,
        results_path=results,
        manifest_path=manifest,
        artifact_label="alternate-model",
        plan_path=plan,
    )

    recorded = json.loads(manifest.read_text())
    assert recorded["model_calls_performed"] > 0
    assert recorded["decision_history"][0]["route"] == "report_saturation_stop"
    assert set(recorded["outputs"]) == {
        "capture",
        "predictions_alternate-model",
        "conditions_alternate-model",
        "attempts_alternate-model",
        "results_alternate-model",
        "plan_alternate-model",
    }


def test_v4c_recorder_validates_optional_evidence_before_writing_result(
    evidence_dir: Path,
) -> None:
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir, raw_success=9, marks_success=10
    )
    manifest = evidence_dir / "manifest.json"
    results = evidence_dir / "results.json"
    manifest.write_text(
        json.dumps(
            {
                "protocol_version": V4C_PROTOCOL_VERSION,
                "model": "gpt-5.6-luna",
                "parameters": {"reasoning_effort": "low", "temperature": None},
                "decision_history": [],
                "outputs": {},
            }
        )
    )

    with pytest.raises(FileNotFoundError):
        record_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            predictions_path=predictions,
            conditions_path=conditions,
            attempts_path=attempts,
            results_path=results,
            manifest_path=manifest,
            audit_path=evidence_dir / "missing-audit.json",
        )

    assert not results.exists()


def test_v4c_recorder_requires_explicit_plan_for_non_luna_label(
    evidence_dir: Path,
) -> None:
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir, raw_success=9, marks_success=10
    )
    results = evidence_dir / "results.json"

    with pytest.raises(ValueError, match="require an explicit plan path"):
        record_v4c_evaluation(
            repository_root=REPOSITORY_ROOT,
            predictions_path=predictions,
            conditions_path=conditions,
            attempts_path=attempts,
            results_path=results,
            manifest_path=evidence_dir / "manifest.json",
            artifact_label="alternate-model",
        )

    assert not results.exists()


@pytest.mark.parametrize("changed_binding", ("plan", "audit"))
def test_v4c_recorder_preserves_existing_optional_bindings(
    evidence_dir: Path, changed_binding: str
) -> None:
    predictions, conditions, attempts = _write_summary_fixture(
        evidence_dir, raw_success=9, marks_success=10
    )
    template = evidence_dir / "template.json"
    manifest = evidence_dir / "manifest.json"
    results = evidence_dir / "results.json"
    plan = evidence_dir / "plan.json"
    audit = evidence_dir / "audit.json"
    replacement_plan = evidence_dir / "replacement-plan.json"
    replacement_audit = evidence_dir / "replacement-audit.json"
    template.write_text(json.dumps({"protocol_version": V4C_PROTOCOL_VERSION, "outputs": {}}))
    for path, content in (
        (plan, "plan\n"),
        (audit, "audit\n"),
        (replacement_plan, "replacement plan\n"),
        (replacement_audit, "replacement audit\n"),
    ):
        path.write_text(content)
    initialize_v4c_model_manifest(
        template_path=template,
        manifest_path=manifest,
        model="gpt-5.6-luna",
        parameters={"reasoning_effort": "low", "temperature": None},
    )
    common = {
        "repository_root": REPOSITORY_ROOT,
        "predictions_path": predictions,
        "conditions_path": conditions,
        "attempts_path": attempts,
        "results_path": results,
        "manifest_path": manifest,
        "artifact_label": "alternate-model",
    }
    record_v4c_evaluation(**common, plan_path=plan, audit_path=audit)
    original_results = results.read_bytes()
    original_manifest = manifest.read_bytes()

    with pytest.raises(ValueError, match="refusing to replace different v4c output binding"):
        record_v4c_evaluation(
            **common,
            plan_path=replacement_plan if changed_binding == "plan" else plan,
            audit_path=replacement_audit if changed_binding == "audit" else audit,
        )

    assert results.read_bytes() == original_results
    assert manifest.read_bytes() == original_manifest
