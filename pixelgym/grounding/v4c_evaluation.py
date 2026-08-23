"""Plan, execute, and summarize the capped v4c longer-horizon Luna pilot."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal, cast

from pixelgym.actions import ActionType, InvalidActionError
from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.evaluation import ResponseCache, cache_key
from pixelgym.grounding.providers import GroundingProvider
from pixelgym.grounding.v4c_backend import V4CReplayBackend
from pixelgym.grounding.v4c_protocol import (
    V4C_CALL_CAP,
    V4C_CONDITIONS,
    V4C_EPISODES,
    V4C_HEIGHT,
    V4C_PROTOCOL_VERSION,
    V4C_SEEDS,
    V4C_SKIP_ID,
    V4C_WIDTH,
    episode_decisions,
    episode_for_seed,
    episode_max_actions,
    validate_v4c_protocol,
)
from pixelgym.serialization import canonical_json_text, load_jsonl

V4C_PROMPT_VERSION = "pixelgym-grounding-v4c-prompt-v1"
V4C_PARSER_VERSION = "pixelgym-grounding-v4c-parser-v1"
V4C_ACTION_SCHEMA_VERSION = "pixelgym-grounding-v4c-action-v1"
V4C_PREDICTION_SCHEMA_VERSION = "pixelgym-grounding-v4c-prediction-v1"
V4C_CONDITION_SCHEMA_VERSION = "pixelgym-grounding-v4c-condition-v1"
V4C_RESULTS_SCHEMA_VERSION = "pixelgym-grounding-v4c-results-v1"
V4C_REACHABLE_STATE_COUNT = 264
V4C_ACTIONABLE_STATE_COUNT = 244
Condition = Literal["raw", "marks"]

ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action_type": {"type": "integer", "const": int(ActionType.CLICK)},
        "x": {"type": "integer", "minimum": 0, "maximum": V4C_WIDTH - 1},
        "y": {"type": "integer", "minimum": 0, "maximum": V4C_HEIGHT - 1},
        "key": {"type": "integer", "const": 0},
    },
    "required": ["action_type", "x", "y", "key"],
    "additionalProperties": False,
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prompt_for_v4c(condition: Condition) -> str:
    prefix = (
        "Complete the visible multi-step vendor workflow. Read only the attached "
        "1024 by 768 screenshot and choose the single on-screen action that follows the "
        "visible instruction and facts. "
    )
    if condition == "marks":
        prefix += "Every actionable control is outlined and numbered as a location aid. "
    return prefix + (
        "Return only the exact PixelGym click action JSON mapping: action_type must be 1, "
        "x and y must be integer screenshot-pixel coordinates, and the inert key must be 0. "
        "The origin is the upper-left. Do not explain and do not use tools."
    )


def _load_inputs(repository_root: Path) -> dict[str, Any]:
    states = load_jsonl(repository_root / "artifacts" / "grounding-v4c-pilot-states.jsonl")
    candidates = load_jsonl(repository_root / "artifacts" / "grounding-v4c-pilot-candidates.jsonl")
    overlays = load_jsonl(repository_root / "artifacts" / "grounding-v4c-pilot-overlays.jsonl")
    if (
        len(states) != V4C_REACHABLE_STATE_COUNT
        or len(candidates) != V4C_REACHABLE_STATE_COUNT
        or len(overlays) != V4C_ACTIONABLE_STATE_COUNT
    ):
        raise ValueError("v4c inputs require 264 states/candidate records and 244 overlays")
    for rows in (states, candidates, overlays):
        if any(row.get("protocol_version") != V4C_PROTOCOL_VERSION for row in rows):
            raise ValueError("v4c input protocol version mismatch")
    state_by_pixels = {row["pixel_sha256"]: row for row in states}
    if len(state_by_pixels) != len(states):
        raise ValueError("v4c raw states must have unique pixel hashes")
    overlay_by_raw = {row["raw_image_sha256"]: row for row in overlays}
    if len(overlay_by_raw) != len(overlays):
        raise ValueError("v4c overlays must have unique raw image hashes")
    state_by_id = {row["state_id"]: row for row in states}
    candidate_by_id = {row["state_id"]: row["candidates"] for row in candidates}
    if len(state_by_id) != len(states) or len(candidate_by_id) != len(candidates):
        raise ValueError("duplicate v4c state identity")
    return {
        "states": states,
        "state_by_pixels": state_by_pixels,
        "state_by_id": state_by_id,
        "candidate_by_id": candidate_by_id,
        "overlay_by_raw": overlay_by_raw,
    }


def _state_for_observation(inputs: dict[str, Any], observation: Any) -> dict[str, Any]:
    digest = _sha256(observation.tobytes())
    try:
        return cast(dict[str, Any], inputs["state_by_pixels"][digest])
    except KeyError as exc:
        raise ValueError(f"unknown v4c screenshot pixel hash {digest}") from exc


def _image_for_condition(
    repository_root: Path,
    inputs: dict[str, Any],
    state: dict[str, Any],
    condition: Condition,
) -> tuple[Path, str]:
    if condition == "raw":
        return repository_root / state["image_path"], state["image_sha256"]
    overlay = inputs["overlay_by_raw"].get(state["image_sha256"])
    if overlay is None:
        raise ValueError(f"unknown v4c raw screenshot hash {state['image_sha256']}")
    return repository_root / overlay["marked_image_path"], overlay["marked_image_sha256"]


def _parse_action(raw_response: str | None) -> tuple[dict[str, int] | None, str | None]:
    if raw_response is None:
        return None, "response text is missing"
    try:
        value = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    if not isinstance(value, dict) or set(value) != {"action_type", "x", "y", "key"}:
        return None, "response must contain exactly action_type, x, y, and key"
    if any(type(value[name]) is not int for name in ("action_type", "x", "y", "key")):
        return None, "all action fields must be integers"
    if value["action_type"] != int(ActionType.CLICK) or value["key"] != 0:
        return None, "v4c requires action_type=CLICK and inert key=0"
    if not (0 <= value["x"] < V4C_WIDTH and 0 <= value["y"] < V4C_HEIGHT):
        return None, "click coordinates lie outside the screenshot"
    return value, None


def _cache_identity(
    provider: GroundingProvider, condition: Condition, prompt: str, image_sha256: str
) -> str:
    return cache_key(
        provider=provider,
        condition=condition,
        prompt=prompt,
        image_sha256=image_sha256,
        schema=ACTION_SCHEMA,
        prompt_version=V4C_PROMPT_VERSION,
        protocol_version=V4C_PROTOCOL_VERSION,
    )


def _state_identity(seed: int, stage: int, pinned: bool, recovery: bool) -> str:
    suffix = "recovery" if recovery else "main"
    return f"v4c-{seed}-s{stage}-p{int(pinned)}-{suffix}"


def _candidate_center(
    inputs: dict[str, Any], seed: int, stage: int, pinned: bool, recovery: bool, semantic_id: str
) -> tuple[int, int]:
    state_id = _state_identity(seed, stage, pinned, recovery)
    candidate = next(
        row for row in inputs["candidate_by_id"][state_id] if row["semantic_id"] == semantic_id
    )
    x0, y0, x1, y1 = candidate["bbox"]
    return (x0 + x1) // 2, (y0 + y1) // 2


def _target_center(
    inputs: dict[str, Any], seed: int, stage: int, pinned: bool, recovery: bool
) -> tuple[int, int]:
    target = episode_for_seed(seed)["stages"][stage]["target"]
    return _candidate_center(inputs, seed, stage, pinned, recovery, target)


def _click_action(x: int, y: int) -> dict[str, int]:
    return {"action_type": int(ActionType.CLICK), "x": x, "y": y, "key": 0}


def _pinned_after(stage: int, commit_stage: int) -> bool:
    return stage > commit_stage


def _validate_transitions(repository_root: Path, inputs: dict[str, Any]) -> None:
    for seed in V4C_SEEDS:
        episode = episode_for_seed(seed)
        decisions = len(episode["stages"])
        commit = episode["commit_stage"]
        consumer = episode["consumer_stage"]
        max_steps = episode_max_actions(seed)

        # Golden trajectory: every decision is decidable from the current screenshot,
        # the pin lands on the chip, and reward 1.0 arrives exactly once at the end.
        backend = V4CReplayBackend(repository_root)
        env = PixelGuiEnv(
            backend, instruction="Complete the visible workflow.", max_episode_steps=max_steps
        )
        try:
            observation, _ = env.reset(seed=seed)
            state = _state_for_observation(inputs, observation)
            if state["stage"] != 0 or state["pinned"] or state["recovery"]:
                raise ValueError("v4c reset did not return the initial unpinned state")
            for stage in range(decisions):
                pinned = _pinned_after(stage, commit)
                x, y = _target_center(inputs, seed, stage, pinned, False)
                observation, reward, terminated, truncated, _ = env.step(_click_action(x, y))
                if stage < decisions - 1:
                    after = _state_for_observation(inputs, observation)
                    if after["stage"] != stage + 1 or after["pinned"] is not _pinned_after(
                        stage + 1, commit
                    ):
                        raise ValueError("v4c golden trajectory left the pinned main path")
                    if reward != 0.0 or terminated or truncated:
                        raise ValueError("v4c awarded premature reward")
            if reward != 1.0 or not terminated or truncated:
                raise ValueError("v4c correct trajectory did not terminate with one reward")
        finally:
            env.close()

        # Recovery entry and completion at the preregistered probe stages.
        for wrong_stage in sorted({0, commit, consumer, decisions - 1}):
            backend = V4CReplayBackend(repository_root)
            env = PixelGuiEnv(
                backend, instruction="Complete the visible workflow.", max_episode_steps=max_steps
            )
            try:
                env.reset(seed=seed)
                for stage in range(wrong_stage):
                    pinned = _pinned_after(stage, commit)
                    env.step(_click_action(*_target_center(inputs, seed, stage, pinned, False)))
                observation, reward, terminated, truncated, _ = env.step(_click_action(5, 740))
                recovery_state = _state_for_observation(inputs, observation)
                if not recovery_state["recovery"] or recovery_state["stage"] != wrong_stage:
                    raise ValueError("v4c wrong action did not enter its recovery state")
                if reward != 0.0 or terminated or truncated:
                    raise ValueError("v4c recovery action awarded reward or ended early")
                for stage in range(wrong_stage, decisions):
                    pinned = _pinned_after(stage, commit)
                    observation, reward, terminated, truncated, _ = env.step(
                        _click_action(
                            *_target_center(inputs, seed, stage, pinned, stage == wrong_stage)
                        )
                    )
                if reward != 1.0 or not terminated or truncated:
                    raise ValueError("v4c recovery trajectory did not complete")
            finally:
                env.close()

        # Skip branch: the progressing non-target leaves the chip empty for the
        # remainder of the episode, including the ambiguous consumer stage.
        backend = V4CReplayBackend(repository_root)
        env = PixelGuiEnv(
            backend, instruction="Complete the visible workflow.", max_episode_steps=max_steps
        )
        try:
            env.reset(seed=seed)
            for stage in range(commit):
                env.step(_click_action(*_target_center(inputs, seed, stage, False, False)))
            observation, reward, terminated, truncated, _ = env.step(
                _click_action(*_candidate_center(inputs, seed, commit, False, False, V4C_SKIP_ID))
            )
            skipped = _state_for_observation(inputs, observation)
            if skipped["stage"] != commit + 1 or skipped["pinned"] or skipped["recovery"]:
                raise ValueError("v4c skip control did not progress on the unpinned branch")
            if reward != 0.0 or terminated or truncated:
                raise ValueError("v4c skip control awarded reward or ended the episode")
            for stage in range(commit + 1, decisions):
                observation, reward, terminated, truncated, _ = env.step(
                    _click_action(*_target_center(inputs, seed, stage, False, False))
                )
                if stage < decisions - 1:
                    after = _state_for_observation(inputs, observation)
                    if after["pinned"]:
                        raise ValueError("v4c unpinned branch regained the chip")
            if reward != 1.0 or not terminated or truncated:
                raise ValueError("v4c unpinned branch could not reach the terminal submission")
        finally:
            env.close()


def planned_v4c_calls(
    *, repository_root: Path, provider: GroundingProvider, cache: ResponseCache
) -> dict[str, Any]:
    protocol = validate_v4c_protocol()
    inputs = _load_inputs(repository_root)
    _validate_transitions(repository_root, inputs)
    reachable_requests = 0
    cached_requests = 0
    for state in inputs["states"]:
        if state["stage"] >= episode_decisions(state["seed"]):
            continue
        for raw_condition in V4C_CONDITIONS:
            condition: Condition = raw_condition  # type: ignore[assignment]
            image_path, image_sha = _image_for_condition(repository_root, inputs, state, condition)
            if _sha256(image_path.read_bytes()) != image_sha:
                raise ValueError("v4c request image digest mismatch")
            prompt = prompt_for_v4c(condition)
            key = _cache_identity(provider, condition, prompt, image_sha)
            reachable_requests += 1
            cached_requests += cache.get(key) is not None
    proposal_covered = sum(
        row.get("proposal_coverage") is True for row in inputs["overlay_by_raw"].values()
    )
    if proposal_covered != V4C_ACTIONABLE_STATE_COUNT:
        raise ValueError("v4c marks proposal coverage is below 100%")
    return {
        "protocol_version": V4C_PROTOCOL_VERSION,
        "episode_count": protocol["episode_count"],
        "condition_count": len(V4C_CONDITIONS),
        "max_actions_per_condition": {str(seed): episode_max_actions(seed) for seed in V4C_SEEDS},
        "upper_bound_calls": V4C_CALL_CAP,
        "reachable_actionable_state_requests": reachable_requests,
        "cached_reachable_state_requests": cached_requests,
        "proposal_covered_states": proposal_covered,
        "proposal_total_states": V4C_ACTIONABLE_STATE_COUNT,
    }


def run_v4c_evaluation(
    *,
    repository_root: Path,
    provider: GroundingProvider,
    predictions_path: Path,
    conditions_path: Path,
    max_new_calls: int,
    cache_directory: Path | None = None,
) -> dict[str, Any]:
    if not 0 <= max_new_calls <= V4C_CALL_CAP:
        raise ValueError("v4c max_new_calls must be between 0 and 178")
    root = repository_root.resolve()
    predictions_path = predictions_path.resolve()
    conditions_path = conditions_path.resolve()
    if predictions_path == conditions_path:
        raise ValueError("v4c prediction and condition paths must differ")
    if any(not path.is_relative_to(root) for path in (predictions_path, conditions_path)):
        raise ValueError("v4c evidence paths must be inside the repository")
    if predictions_path.exists() or conditions_path.exists():
        raise ValueError("v4c evaluation requires fresh immutable output paths")
    cache = ResponseCache(
        cache_directory or repository_root / ".cache" / "grounding-v4c" / "responses"
    )
    inputs = _load_inputs(repository_root)
    new_calls = 0
    cache_hits = 0
    predictions: list[dict[str, Any]] = []
    condition_rows: list[dict[str, Any]] = []
    for seed in V4C_SEEDS:
        episode = episode_for_seed(seed)
        decisions = len(episode["stages"])
        consumer_stage = episode["consumer_stage"]
        max_actions = episode_max_actions(seed)
        for raw_condition in V4C_CONDITIONS:
            condition: Condition = raw_condition  # type: ignore[assignment]
            backend = V4CReplayBackend(repository_root)
            env = PixelGuiEnv(
                backend,
                instruction="Complete the visible multi-step vendor workflow.",
                max_episode_steps=max_actions,
            )
            observation, _ = env.reset(seed=seed)
            failures: list[str] = []
            success = False
            terminated = False
            truncated = False
            checkpoints = 0
            committed = False
            consumer_pinned: bool | None = None
            consumer_correct: bool | None = None
            condition_cache_hits = 0
            condition_new_calls = 0
            try:
                for action_index in range(1, max_actions + 1):
                    before = _state_for_observation(inputs, observation)
                    image_path, image_sha = _image_for_condition(
                        repository_root, inputs, before, condition
                    )
                    prompt = prompt_for_v4c(condition)
                    key = _cache_identity(provider, condition, prompt, image_sha)
                    response = cache.get(key)
                    cache_hit = response is not None
                    if response is None:
                        if new_calls >= max_new_calls:
                            raise RuntimeError(
                                f"v4c evaluation reached the approved cap of {max_new_calls} new calls"
                            )
                        response = provider.invoke(
                            image_path=image_path,
                            prompt=prompt,
                            schema=ACTION_SCHEMA,
                        )
                        cache.put(key, response)
                        new_calls += 1
                        condition_new_calls += 1
                    else:
                        cache_hits += 1
                        condition_cache_hits += 1
                    parse_status = "parsed"
                    parse_error = None
                    parsed_action = None
                    reward = 0.0
                    after = before
                    if response.request_failure is not None:
                        parse_status = "request_failure"
                        failures.append("request_failure")
                    else:
                        parsed_action, parse_error = _parse_action(response.raw_response)
                        if parsed_action is None:
                            parse_status = "invalid"
                            failures.append("parse_failure")
                        else:
                            try:
                                observation, reward, terminated, truncated, _ = env.step(
                                    parsed_action
                                )
                            except InvalidActionError as exc:
                                parse_status = "invalid_action"
                                parse_error = str(exc)
                                failures.append("invalid_action_failure")
                            else:
                                after = _state_for_observation(inputs, observation)
                                checkpoints = max(checkpoints, int(after["stage"]))
                                committed = committed or bool(after["pinned"])
                                if before["stage"] == consumer_stage and consumer_pinned is None:
                                    consumer_pinned = bool(before["pinned"])
                                    consumer_correct = after["stage"] == before["stage"] + 1
                                success = reward == 1.0 and terminated
                    predictions.append(
                        {
                            "schema_version": V4C_PREDICTION_SCHEMA_VERSION,
                            "protocol_version": V4C_PROTOCOL_VERSION,
                            "prompt_version": V4C_PROMPT_VERSION,
                            "parser_version": V4C_PARSER_VERSION,
                            "action_schema_version": V4C_ACTION_SCHEMA_VERSION,
                            "seed": seed,
                            "family": episode["family"],
                            "condition": condition,
                            "action_index": action_index,
                            "observation_sha256": before["image_sha256"],
                            "condition_image_sha256": image_sha,
                            "provider": provider.name,
                            "model": provider.model,
                            "parameters": provider.parameters,
                            "timestamp_utc": response.timestamp_utc,
                            "latency_ms": response.latency_ms,
                            "usage": response.usage,
                            "provider_metadata": response.provider_metadata,
                            "raw_response": response.raw_response,
                            "request_failure": response.request_failure,
                            "parse_status": parse_status,
                            "parse_error": parse_error,
                            "parsed_action": parsed_action,
                            "cache_hit": cache_hit,
                            "post_observation_sha256": after["image_sha256"],
                            "checkpoint_after": after["stage"],
                            "pinned_after": after["pinned"],
                            "recovery_after": after["recovery"],
                            "reward": reward,
                            "terminated": terminated,
                            "truncated": truncated,
                        }
                    )
                    if failures or terminated or truncated:
                        break
            finally:
                env.close()
            condition_rows.append(
                {
                    "schema_version": V4C_CONDITION_SCHEMA_VERSION,
                    "protocol_version": V4C_PROTOCOL_VERSION,
                    "seed": seed,
                    "family": episode["family"],
                    "condition": condition,
                    "decisions": decisions,
                    "success": success,
                    "terminated": terminated,
                    "truncated": truncated,
                    "action_count": sum(
                        row["seed"] == seed and row["condition"] == condition for row in predictions
                    ),
                    "checkpoint_count": checkpoints,
                    "committed": committed,
                    "consumer_pinned": consumer_pinned,
                    "consumer_correct": consumer_correct,
                    "new_calls": condition_new_calls,
                    "cache_hits": condition_cache_hits,
                    "failures": failures,
                }
            )
    if len(condition_rows) != 20:
        raise ValueError("v4c run did not produce exactly twenty condition summaries")
    for path, rows in ((predictions_path, predictions), (conditions_path, condition_rows)):
        encoded = "".join(canonical_json_text(row) + "\n" for row in rows)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.read_text() != encoded:
            raise ValueError(f"refusing to overwrite different immutable v4c {path.name}")
        path.write_text(encoded)
    return {
        "protocol_version": V4C_PROTOCOL_VERSION,
        "provider": provider.name,
        "model": provider.model,
        "approved_upper_bound_calls": V4C_CALL_CAP,
        "new_calls": new_calls,
        "cache_hits": cache_hits,
        "action_record_count": len(predictions),
        "condition_record_count": len(condition_rows),
        "predictions_path": predictions_path.relative_to(repository_root).as_posix(),
        "conditions_path": conditions_path.relative_to(repository_root).as_posix(),
    }


def _validate_v4c_collection(
    predictions: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    expected: set[tuple[int, str]],
) -> dict[str, int]:
    actual = [(row.get("seed"), row.get("condition")) for row in conditions]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("v4c condition summaries do not match the frozen 10x2 grid")
    if len(predictions) > V4C_CALL_CAP:
        raise ValueError("v4c predictions exceed the 178-call cap")
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for prediction in predictions:
        key = (prediction.get("seed"), prediction.get("condition"))
        if key not in expected:
            raise ValueError("v4c prediction does not match the frozen 10x2 grid")
        if not isinstance(prediction.get("cache_hit"), bool):
            raise TypeError("v4c prediction cache_hit must be boolean")
        grouped[key].append(prediction)
    if set(grouped) != expected:
        raise ValueError("v4c predictions do not cover the frozen 10x2 grid")
    for condition in conditions:
        key = (condition["seed"], condition["condition"])
        rows = grouped[key]
        cache_hits = sum(row["cache_hit"] is True for row in rows)
        new_calls = len(rows) - cache_hits
        if (
            condition.get("action_count") != len(rows)
            or condition.get("new_calls") != new_calls
            or condition.get("cache_hits") != cache_hits
        ):
            raise ValueError("v4c condition counters do not match prediction records")
    return {
        "action_records": len(predictions),
        "new_calls": sum(row["cache_hit"] is False for row in predictions),
        "cache_hits": sum(row["cache_hit"] is True for row in predictions),
    }


def _carrier_utilization(conditions: list[dict[str, Any]]) -> dict[str, int]:
    utilization = {
        "pinned_consumer_faced": 0,
        "pinned_consumer_correct": 0,
        "unpinned_consumer_faced": 0,
        "unpinned_consumer_correct": 0,
    }
    for row in conditions:
        pinned = row.get("consumer_pinned")
        if pinned is None:
            continue
        prefix = "pinned" if pinned else "unpinned"
        utilization[f"{prefix}_consumer_faced"] += 1
        utilization[f"{prefix}_consumer_correct"] += row.get("consumer_correct") is True
    return utilization


def summarize_v4c_evaluation(
    *,
    repository_root: Path,
    predictions_path: Path,
    conditions_path: Path,
    prior_predictions_path: Path | None = None,
    prior_conditions_path: Path | None = None,
) -> dict[str, Any]:
    root = repository_root.resolve()
    predictions_path = predictions_path.resolve()
    conditions_path = conditions_path.resolve()
    if (prior_predictions_path is None) != (prior_conditions_path is None):
        raise ValueError("v4c prior prediction and condition evidence must be supplied together")
    evidence_paths = [predictions_path, conditions_path]
    if prior_predictions_path is not None and prior_conditions_path is not None:
        prior_predictions_path = prior_predictions_path.resolve()
        prior_conditions_path = prior_conditions_path.resolve()
        evidence_paths.extend((prior_predictions_path, prior_conditions_path))
    if any(not path.is_relative_to(root) for path in evidence_paths):
        raise ValueError("all v4c summary evidence paths must be inside the repository")
    if len(set(evidence_paths)) != len(evidence_paths):
        raise ValueError("v4c summary evidence paths must be distinct")
    predictions = load_jsonl(predictions_path)
    conditions = load_jsonl(conditions_path)
    expected = {(seed, condition) for seed in V4C_SEEDS for condition in V4C_CONDITIONS}
    collection = _validate_v4c_collection(predictions, conditions, expected)
    prior_paid_calls = 0
    prior_collection = None
    if prior_predictions_path is not None and prior_conditions_path is not None:
        prior_predictions = load_jsonl(prior_predictions_path)
        prior_conditions = load_jsonl(prior_conditions_path)
        prior_counts = _validate_v4c_collection(prior_predictions, prior_conditions, expected)
        prior_paid_calls = prior_counts["new_calls"]
        prior_collection = {
            "new_call_count": prior_paid_calls,
            "predictions": {
                "path": prior_predictions_path.relative_to(root).as_posix(),
                "sha256": _sha256(prior_predictions_path.read_bytes()),
            },
            "condition_summaries": {
                "path": prior_conditions_path.relative_to(root).as_posix(),
                "sha256": _sha256(prior_conditions_path.read_bytes()),
            },
        }
    raw_success = sum(row["success"] is True for row in conditions if row["condition"] == "raw")
    marks_success = sum(row["success"] is True for row in conditions if row["condition"] == "marks")
    failures = Counter(failure for row in conditions for failure in row.get("failures", []))
    capture = json.loads(
        (repository_root / "artifacts" / "grounding-v4c-pilot-capture.json").read_text()
    )
    proposal_covered = capture["proposal_covered_state_count"]
    proposal_total = capture["actionable_state_count"]
    if proposal_covered != proposal_total:
        route = "floor_transport_audit"
        rationale = "marks proposal coverage was below 100%"
    elif failures or raw_success < 4 or marks_success < 4:
        route = "floor_transport_audit"
        rationale = "failure observed or at least one condition scored below 4/10"
    elif raw_success >= 9 or marks_success >= 9:
        route = "report_saturation_stop"
        rationale = (
            "at least one condition scored 9-10/10; any successor design requires "
            "a new explicit human scope decision"
        )
    elif 4 <= raw_success <= 7 and 5 <= marks_success <= 8:
        route = "freeze_v4c_request_ceiling_approval"
        rationale = "both conditions landed in the preregistered v4c bands"
    else:
        route = "human_review"
        rationale = "result does not match a preregistered automatic routing cell"
    incremental_new_calls = collection["new_calls"]
    cumulative_paid_calls = prior_paid_calls + incremental_new_calls
    if cumulative_paid_calls > V4C_CALL_CAP:
        raise ValueError("v4c cumulative paid calls exceed the approved 178-call cap")
    return {
        "schema_version": V4C_RESULTS_SCHEMA_VERSION,
        "protocol_version": V4C_PROTOCOL_VERSION,
        "model": predictions[0]["model"] if predictions else None,
        "parameters": predictions[0]["parameters"] if predictions else None,
        "collection": {
            "episode_count": len(V4C_EPISODES),
            "condition_record_count": len(conditions),
            "action_record_count": len(predictions),
            "new_call_count": incremental_new_calls,
            "prior_paid_call_count": prior_paid_calls,
            "cumulative_paid_call_count": cumulative_paid_calls,
            "cache_hit_count": collection["cache_hits"],
        },
        "prior_collection": prior_collection,
        "conditions": {
            "raw": {"success_count": raw_success, "episode_count": 10},
            "marks": {"success_count": marks_success, "episode_count": 10},
        },
        "failures": dict(sorted(failures.items())),
        "diagnostics": {
            "checkpoint_histogram": dict(
                sorted(Counter(str(row["checkpoint_count"]) for row in conditions).items())
            ),
            "action_count_histogram": dict(
                sorted(Counter(str(row["action_count"]) for row in conditions).items())
            ),
            "commit_counts": {
                condition: sum(
                    row.get("committed") is True
                    for row in conditions
                    if row["condition"] == condition
                )
                for condition in V4C_CONDITIONS
            },
            "carrier_utilization": _carrier_utilization(conditions),
        },
        "set_of_marks": {
            "proposal_covered_state_count": proposal_covered,
            "proposal_total_state_count": proposal_total,
        },
        "routing": {"decision": route, "rationale": rationale},
        "predictions": {
            "path": predictions_path.relative_to(root).as_posix(),
            "sha256": _sha256(predictions_path.read_bytes()),
        },
        "condition_summaries": {
            "path": conditions_path.relative_to(root).as_posix(),
            "sha256": _sha256(conditions_path.read_bytes()),
        },
    }


def record_v4c_evaluation(
    *,
    repository_root: Path,
    predictions_path: Path,
    conditions_path: Path,
    results_path: Path,
    manifest_path: Path,
    prior_predictions_path: Path | None = None,
    prior_conditions_path: Path | None = None,
) -> dict[str, Any]:
    """Write an offline result and update the v4c manifest without provider calls."""
    root = repository_root.resolve()
    paths = [predictions_path, conditions_path, results_path, manifest_path]
    if prior_predictions_path is not None:
        paths.append(prior_predictions_path)
    if prior_conditions_path is not None:
        paths.append(prior_conditions_path)
    resolved = [path.resolve() for path in paths]
    if any(not path.is_relative_to(root) for path in resolved):
        raise ValueError("all v4c evidence paths must be inside the repository")
    if len(set(resolved)) != len(resolved):
        raise ValueError("v4c evidence, result, and manifest paths must be distinct")
    predictions_path, conditions_path, results_path, manifest_path = resolved[:4]
    resolved_prior_predictions = resolved[4] if prior_predictions_path is not None else None
    resolved_prior_conditions = resolved[-1] if prior_conditions_path is not None else None
    results = summarize_v4c_evaluation(
        repository_root=root,
        predictions_path=predictions_path,
        conditions_path=conditions_path,
        prior_predictions_path=resolved_prior_predictions,
        prior_conditions_path=resolved_prior_conditions,
    )
    encoded_results = json.dumps(results, indent=2, sort_keys=True) + "\n"
    if results_path.is_file() and results_path.read_text() != encoded_results:
        raise ValueError("refusing to overwrite different immutable v4c results")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("protocol_version") != V4C_PROTOCOL_VERSION:
        raise ValueError("v4c manifest protocol version mismatch")
    if manifest.get("model") != results["model"]:
        raise ValueError("v4c manifest model does not match predictions")
    history = manifest.get("decision_history")
    outputs = manifest.get("outputs")
    if not isinstance(history, list) or not isinstance(outputs, dict):
        raise TypeError("v4c manifest history and outputs must be structured")
    route = results["routing"]["decision"]
    status_by_route = {
        "floor_transport_audit": "evaluated_floor_transport_audit_required",
        "report_saturation_stop": "evaluated_saturation_reported_human_scope_decision_required",
        "freeze_v4c_request_ceiling_approval": "evaluated_ceiling_approval_required",
        "human_review": "evaluated_human_review_required",
    }
    decision = {
        "route": route,
        "rationale": results["routing"]["rationale"],
        "raw_success": results["conditions"]["raw"]["success_count"],
        "marks_success": results["conditions"]["marks"]["success_count"],
        "incremental_new_calls": results["collection"]["new_call_count"],
        "cumulative_paid_calls": results["collection"]["cumulative_paid_call_count"],
        "cache_hits": results["collection"]["cache_hit_count"],
        "failures": results["failures"],
    }
    if history and history[-1] != decision:
        raise ValueError("refusing to replace different v4c decision history")
    results_path.write_text(encoded_results, encoding="utf-8")
    if not history:
        history.append(decision)
    manifest["status"] = status_by_route[route]
    manifest["model_calls_performed"] = results["collection"]["cumulative_paid_call_count"]
    for name, path in (
        ("predictions_luna", predictions_path),
        ("conditions_luna", conditions_path),
        ("results_luna", results_path),
    ):
        outputs[name] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path.read_bytes()),
        }
    plan_path = root / "artifacts" / "grounding-v4c-pilot-plan-luna.json"
    if plan_path.is_file():
        outputs["plan_luna"] = {
            "path": plan_path.relative_to(root).as_posix(),
            "sha256": _sha256(plan_path.read_bytes()),
        }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return results
