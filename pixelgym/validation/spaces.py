"""Action/observation space integrity validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from gymnasium.utils.env_checker import check_env

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.env import PixelGuiEnv


def _valid(
    action_type: Any = ActionType.NOOP, *, x: Any = 0, y: Any = 0, key: Any = 0
) -> dict[str, Any]:
    return {"action_type": action_type, "x": x, "y": y, "key": key}


def _calls(backend: FakeBackend) -> tuple[int, int, int]:
    return backend.noop_calls, len(backend.click_calls), len(backend.key_calls)


def validate_space_integrity(
    golden_fixture: Path, *, sampled_action_count: int = 500
) -> dict[str, Any]:
    checker_env = PixelGuiEnv(FakeBackend(width=64, height=48), max_episode_steps=200)
    check_env(checker_env, skip_render_check=True)
    checker_env.close()

    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend, max_episode_steps=200)
    env.action_space.seed(20260624)
    observation, _info = env.reset(seed=7)
    observations_contained = env.observation_space.contains(observation)
    sampled_records = []
    for index in range(sampled_action_count):
        action = env.action_space.sample()
        observation, _reward, terminated, truncated, _info = env.step(action)
        contained = env.observation_space.contains(observation)
        observations_contained = observations_contained and contained
        sampled_records.append({"sample_index": index, "observation_contained": contained})
        if terminated or truncated:
            observation, _info = env.reset(seed=7)
            observations_contained = observations_contained and env.observation_space.contains(
                observation
            )

    boundary_records = []
    for name, action in (
        ("top-left", _valid(ActionType.CLICK, x=0, y=0)),
        ("bottom-right", _valid(ActionType.CLICK, x=63, y=47)),
        ("first-key", _valid(ActionType.KEY, key=0)),
        ("last-key", _valid(ActionType.KEY, key=len(KEY_ALLOWLIST) - 1)),
    ):
        observation, _reward, terminated, truncated, _info = env.step(action)
        contained = env.observation_space.contains(observation)
        boundary_records.append(
            {"name": name, "accepted": True, "observation_contained": contained}
        )
        observations_contained = observations_contained and contained
        if terminated or truncated:
            env.reset(seed=7)

    invalid_actions: list[tuple[str, Any]] = [
        ("negative-x", _valid(ActionType.CLICK, x=-1, y=0)),
        ("x-equals-width", _valid(ActionType.CLICK, x=64, y=0)),
        ("negative-y", _valid(ActionType.CLICK, x=0, y=-1)),
        ("y-equals-height", _valid(ActionType.CLICK, x=0, y=48)),
        ("negative-key", _valid(ActionType.KEY, key=-1)),
        ("key-equals-count", _valid(ActionType.KEY, key=len(KEY_ALLOWLIST))),
        ("unknown-action-type", _valid(3)),
        ("boolean-action-type", _valid(True)),
        ("float-x", _valid(ActionType.CLICK, x=1.0, y=0)),
        ("string-key", _valid(ActionType.KEY, key="0")),
        ("numpy-array-x", _valid(ActionType.CLICK, x=np.array(1), y=0)),
        ("missing-key", {"action_type": 0, "x": 0, "y": 0}),
        ("extra-key", {**_valid(), "extra": 0}),
        ("not-a-mapping", [0, 0, 0, 0]),
    ]
    invalid_records = []
    for name, action in invalid_actions:
        before = _calls(backend)
        try:
            env.step(action)
        except (TypeError, ValueError):
            rejected = True
        else:
            rejected = False
        after = _calls(backend)
        invalid_records.append(
            {
                "name": name,
                "rejected": rejected,
                "rejected_before_backend_execution": before == after,
            }
        )
    env.close()

    fixture = json.loads(golden_fixture.read_text(encoding="utf-8"))
    terminal_env = PixelGuiEnv(FakeBackend())
    terminal_env.reset(seed=fixture["seed"])
    for action in fixture["actions"]:
        _observation, _reward, terminated, _truncated, _info = terminal_env.step(action)
    assert terminated
    try:
        terminal_env.step(_valid())
    except RuntimeError:
        post_terminal_rejected = True
    else:
        post_terminal_rejected = False
    terminal_env.close()

    truncated_env = PixelGuiEnv(FakeBackend(), max_episode_steps=1)
    truncated_env.reset(seed=7)
    _observation, _reward, _terminated, truncated, _info = truncated_env.step(_valid())
    assert truncated
    try:
        truncated_env.step(_valid())
    except RuntimeError:
        post_truncation_rejected = True
    else:
        post_truncation_rejected = False
    truncated_env.close()

    invalid_passed = all(
        record["rejected"] and record["rejected_before_backend_execution"]
        for record in invalid_records
    )
    boundaries_passed = all(
        record["accepted"] and record["observation_contained"] for record in boundary_records
    )
    passed = (
        observations_contained
        and invalid_passed
        and boundaries_passed
        and post_terminal_rejected
        and post_truncation_rejected
    )
    return {
        "schema_version": 1,
        "validator": "space-integrity",
        "backend": "fake",
        "gymnasium_checker_passed": True,
        "sampled_action_count": sampled_action_count,
        "sampled_actions": sampled_records,
        "boundary_cases": boundary_records,
        "invalid_actions": invalid_records,
        "post_terminal_step_rejected": post_terminal_rejected,
        "post_truncation_step_rejected": post_truncation_rejected,
        "summary": {
            "observations_contained": observations_contained,
            "valid_boundaries_accepted": boundaries_passed,
            "invalid_inputs_rejected_before_backend": invalid_passed,
            "post_episode_calls_rejected": (post_terminal_rejected and post_truncation_rejected),
            "passed": passed,
        },
    }
