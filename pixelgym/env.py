"""Gymnasium environment contract: `PixelGuiEnv`.

`PixelGuiEnv` implements the project's final behavioral contract (AGENTS.md
section 3) against any backend satisfying `pixelgym.backends.base.Backend`:

- The screenshot is the only observation.
- Actions are `NOOP`, bounded `CLICK`, or allowlisted `KEY`, validated and
  normalized to a `pixelgym.actions.ValidatedAction` snapshot before they
  ever reach the backend; dispatch uses only that snapshot; it never
  re-reads the caller's original action mapping.
- Reward is `0.0` until an exact valid submission, then `1.0` exactly once.
- Success terminates the episode; reaching the step limit without success
  truncates it -- the two are never conflated, and stepping after either
  raises.
- The same seed produces the same task and the same initial observation.

`info` never carries anything beyond `task_id` (AGENTS.md invariant 13:
"must never carry expected answers or bounding boxes"). In particular it
never carries the *seed* passed to `reset` -- the vendor-form task app is a
public deterministic generator (`pixelgym.tasks.vendor_form.generator`), so
handing back the seed would let an agent reconstruct every expected field
value in O(1) instead of having to read them off the rendered request card.
It also never carries evaluator diagnostics such as a partial `score` or
`mismatched_fields`; those stay host-side and are only ever converted into
the sparse `terminated`/`reward` signal (AGENTS.md invariant 6).

This module imports only `gymnasium`, `numpy`, and the rest of `pixelgym`
(AGENTS.md invariant 12: no OSWorld import in the core module).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from pixelgym.actions import (
    KEY_ALLOWLIST,
    ActionType,
    ValidatedAction,
    build_action_space,
    validate_action,
)
from pixelgym.backends.base import Backend, Frame
from pixelgym.evaluator import evaluate
from pixelgym.task_spec import TaskSpec

DEFAULT_INSTRUCTION = "Fill out the form exactly as shown on the request card, then submit."
DEFAULT_MAX_EPISODE_STEPS = 200

# TaskSpec.seed rejects bool but accepts any int; reset() without an explicit
# seed draws a task seed from the post-super().reset() np_random stream, so
# it stays deterministic once *that* stream has been seeded.
_TASK_SEED_UPPER_BOUND = 2**31 - 1


class BackendContractError(RuntimeError):
    """A backend returned something that violates the `Backend` protocol
    contract (e.g. `screenshot()` returning the wrong type, dtype, shape, or
    out-of-bounds values). Never silently cast, reshaped, or clipped."""


class PixelGuiEnv(gym.Env[Frame, Mapping[str, Any]]):
    """A pixel-only GUI environment over one `Backend`-driven task application."""

    metadata: dict[str, Any] = {"render_modes": []}  # noqa: RUF012

    def __init__(
        self,
        backend: Backend,
        *,
        instruction: str = DEFAULT_INSTRUCTION,
        max_episode_steps: int = DEFAULT_MAX_EPISODE_STEPS,
    ) -> None:
        super().__init__()
        if max_episode_steps <= 0:
            raise ValueError(f"max_episode_steps must be positive, got {max_episode_steps}")

        self.backend = backend
        self._instruction = instruction
        self._max_episode_steps = max_episode_steps

        self.observation_space: spaces.Box = spaces.Box(
            low=0, high=255, shape=(backend.height, backend.width, 3), dtype=np.uint8
        )
        self.action_space: spaces.Dict = build_action_space(backend.width, backend.height)

        self._task: TaskSpec | None = None
        self._step_count = 0
        self._episode_ended = False

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[Frame, dict[str, Any]]:
        super().reset(seed=seed, options=options)
        task_seed = (
            seed if seed is not None else int(self.np_random.integers(_TASK_SEED_UPPER_BOUND))
        )

        record = self.backend.reset(task_seed)
        self._task = TaskSpec.from_generated(
            record,
            instruction=self._instruction,
            app_url=self.backend.app_url,
            max_episode_steps=self._max_episode_steps,
        )
        self._step_count = 0
        self._episode_ended = False

        observation = self._capture_observation()
        info = {"task_id": self._task.task_id}
        return observation, info

    def step(
        self, action: Mapping[str, Any]
    ) -> tuple[Frame, float, bool, bool, dict[str, Any]]:
        if self._task is None or self._episode_ended:
            raise RuntimeError(
                "step() called before reset() or after the episode already ended "
                "(terminated or truncated); call reset() before stepping again."
            )

        validated = validate_action(self.action_space, action)
        self._dispatch(validated)

        observation = self._capture_observation()
        result = evaluate(self._task, self.backend.read_submissions())

        terminated = bool(result.success)
        reward = 1.0 if terminated else 0.0

        self._step_count += 1
        truncated = not terminated and self._step_count >= self._task.max_episode_steps
        self._episode_ended = terminated or truncated

        # `result` (EvaluationResult) carries privileged diagnostics -- score,
        # mismatched_fields, task_id_matches -- that must stay host-side; only
        # `success` may ever cross into the agent-facing reward/terminated
        # signal (AGENTS.md invariant 6). `info` here is deliberately just
        # `task_id`, not a copy of `result`.
        info = {"task_id": self._task.task_id}
        return observation, reward, terminated, truncated, info

    def close(self) -> None:
        self.backend.close()

    def _dispatch(self, action: ValidatedAction) -> None:
        # `action` is the ValidatedAction snapshot `validate_action` already
        # produced -- plain Python ints, each read from the caller's action
        # and converted exactly once. Dispatch uses only these fields; it
        # never re-reads the original caller-supplied mapping or calls
        # int(...) again, which is what defeats a stateful
        # `Mapping.__getitem__` or a stateful `int.__int__` that would
        # otherwise be able to return a different, unvalidated value on a
        # second read/conversion after validation already passed.
        action_type = ActionType(action.action_type)
        if action_type is ActionType.NOOP:
            self.backend.noop()
            return
        if action_type is ActionType.CLICK:
            self.backend.click(action.x, action.y)
            return
        if action_type is ActionType.KEY:
            self.backend.key(KEY_ALLOWLIST[action.key])
            return
        raise AssertionError(f"unhandled action type {action_type!r}")  # pragma: no cover

    def _capture_observation(self) -> Frame:
        frame = self.backend.screenshot()
        space = self.observation_space

        # Every check below rejects outright; none casts, reshapes, or clips
        # a nonconforming frame into shape.
        if not isinstance(frame, np.ndarray):
            raise BackendContractError(
                f"backend.screenshot() must return a numpy.ndarray, got {type(frame).__name__}"
            )
        if frame.dtype != space.dtype:
            raise BackendContractError(
                f"backend.screenshot() dtype {frame.dtype} does not match "
                f"observation_space dtype {space.dtype}"
            )
        if frame.shape != space.shape:
            raise BackendContractError(
                f"backend.screenshot() shape {frame.shape} does not match "
                f"observation_space shape {space.shape}"
            )
        if not space.contains(frame):
            raise BackendContractError(
                f"backend.screenshot() values are outside observation_space bounds "
                f"[{space.low.min()}, {space.high.max()}]"
            )
        return frame
