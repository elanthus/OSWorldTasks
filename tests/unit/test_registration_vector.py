"""Registered Gymnasium ID and vectorized smoke tests over several fake backends (Gym 0.28)."""

from __future__ import annotations

import json
from pathlib import Path

import gymnasium as gym
import numpy as np
import pytest
from gymnasium.utils.env_checker import check_env

from pixelgym.env import PixelGuiEnv
from pixelgym.registration import VENDOR_FORM_ID, register_envs

_GOLDEN = Path(__file__).parent / "fixtures/golden_trajectory_seed7.json"
_NOOP = {"action_type": 0, "x": 0, "y": 0, "key": 0}


def _batch(actions: list[dict[str, int]]) -> dict[str, np.ndarray]:
    return {name: np.array([a[name] for a in actions], dtype=np.int64) for name in _NOOP}


def test_id_is_registered_on_import_and_makes_the_fake_environment() -> None:
    register_envs()  # idempotent
    assert VENDOR_FORM_ID in gym.registry
    env = gym.make(VENDOR_FORM_ID, width=320, height=240)
    try:
        assert isinstance(env.unwrapped, PixelGuiEnv)
        assert env.spec is not None and env.spec.max_episode_steps is None
        observation, info = env.reset(seed=7)
        assert observation.shape == (240, 320, 3) and observation.dtype == np.uint8
        assert set(info) == {"episode_id"}
    finally:
        env.close()


def test_registered_environment_passes_the_gymnasium_checker() -> None:
    env = gym.make(VENDOR_FORM_ID, width=160, height=120, disable_env_checker=True)
    try:
        check_env(env.unwrapped, skip_render_check=True)
    finally:
        env.close()


def test_sync_vector_reset_matches_single_environment_resets() -> None:
    seeds = [7, 11, 23, 101]
    vector = gym.vector.SyncVectorEnv(
        [lambda: gym.make(VENDOR_FORM_ID, width=256, height=192) for _ in seeds]
    )
    try:
        observations, _info = vector.reset(seed=seeds)
        assert observations.shape == (len(seeds), 192, 256, 3)
        for index, seed in enumerate(seeds):
            single = gym.make(VENDOR_FORM_ID, width=256, height=192)
            expected, _ = single.reset(seed=seed)
            single.close()
            assert np.array_equal(observations[index], expected)
    finally:
        vector.close()


def test_sync_vector_random_steps_truncate_and_autoreset_each_environment() -> None:
    vector = gym.vector.SyncVectorEnv(
        [
            lambda: gym.make(VENDOR_FORM_ID, width=256, height=192, max_episode_steps=5)
            for _ in range(4)
        ]
    )
    try:
        vector.reset(seed=[1, 2, 3, 4])
        vector.action_space.seed(0)
        truncations = np.zeros(4, dtype=int)
        for _ in range(12):
            observations, rewards, _terminated, truncated, infos = vector.step(
                vector.action_space.sample()
            )
            assert observations.shape == (4, 192, 256, 3) and observations.dtype == np.uint8
            assert rewards.dtype == np.float64 and set(rewards.tolist()) <= {0.0, 1.0}
            truncations += truncated
            if truncated.any():
                assert "final_observation" in infos
        # A random policy never submits a correct form, so every episode ends by truncation.
        assert truncations.tolist() == [2, 2, 2, 2]
    finally:
        vector.close()


def test_sync_vector_pays_reward_only_to_the_environment_that_submits() -> None:
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    vector = gym.vector.SyncVectorEnv([lambda: gym.make(VENDOR_FORM_ID) for _ in range(3)])
    try:
        vector.reset(seed=[golden["seed"], 8, 9])
        rewards_seen = []
        for action in golden["actions"]:
            _obs, rewards, terminated, truncated, _infos = vector.step(
                _batch([action, _NOOP, _NOOP])
            )
            rewards_seen.append(rewards.tolist())
            assert not truncated.any()
        assert rewards_seen[-1] == [1.0, 0.0, 0.0]
        assert all(row == [0.0, 0.0, 0.0] for row in rewards_seen[:-1])
        assert terminated.tolist() == [True, False, False]
    finally:
        vector.close()


@pytest.mark.parametrize("context", ["spawn"])
def test_async_vector_steps_in_subprocesses(context: str) -> None:
    vector = gym.vector.AsyncVectorEnv(
        [lambda: gym.make(VENDOR_FORM_ID, width=256, height=192) for _ in range(2)],
        context=context,
    )
    try:
        observations, _info = vector.reset(seed=[7, 8])
        assert observations.shape == (2, 192, 256, 3)
        vector.action_space.seed(1)
        for _ in range(5):
            observations, rewards, _terminated, _truncated, _infos = vector.step(
                vector.action_space.sample()
            )
        assert observations.shape == (2, 192, 256, 3) and rewards.shape == (2,)
    finally:
        vector.close()
