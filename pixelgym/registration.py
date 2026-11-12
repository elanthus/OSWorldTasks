"""Gymnasium registration for the fake-backend vendor-form environment.

``gym.make("PixelGym/VendorForm-v0")`` builds a `PixelGuiEnv` over an in-process `FakeBackend`.
The ID is registered two ways:

- through the ``gymnasium.envs`` entry point declared in ``pyproject.toml``, which Gymnasium 0.28
  loads on ``import gymnasium`` once the package is installed, and
- by importing this module (``import pixelgym.registration``), for checkouts whose installed
  metadata predates the entry point.

Both call `register_envs`, which is idempotent. Gymnasium imports this module during its own
import, so it imports only the registration API at module level; the environment and backend
are imported when an environment is made.

The episode step limit is the environment's own (`PixelGuiEnv` sets ``truncated``), so the ID
registers no extra `TimeLimit` wrapper: a second limit could truncate an episode the environment
had not truncated.
"""

from __future__ import annotations

from typing import Any

from gymnasium.envs.registration import register, registry

VENDOR_FORM_ID = "PixelGym/VendorForm-v0"


def make_vendor_form_env(
    *,
    width: int = 1024,
    height: int = 768,
    max_episode_steps: int = 200,
    **env_kwargs: Any,
) -> Any:
    """Entry point for `VENDOR_FORM_ID`: a `PixelGuiEnv` over a fresh `FakeBackend`."""

    from pixelgym.backends.fake import FakeBackend
    from pixelgym.env import PixelGuiEnv

    return PixelGuiEnv(
        FakeBackend(width=width, height=height),
        max_episode_steps=max_episode_steps,
        **env_kwargs,
    )


def register_envs() -> None:
    """Register `VENDOR_FORM_ID` unless it is already registered."""

    if VENDOR_FORM_ID in registry:
        return
    register(
        id=VENDOR_FORM_ID,
        entry_point="pixelgym.registration:make_vendor_form_env",
        max_episode_steps=None,
        disable_env_checker=False,
    )


register_envs()
