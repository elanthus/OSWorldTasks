"""In-process fake backend (no VM, no network, no wall-clock).

Implements the backend contract D1.6 asks for
(`plans/day-1-environment-core.md`): `reset` installs a freshly seeded task
(via the real generator, `pixelgym.tasks.vendor_form.generator`, so
determinism is genuine), `screenshot` returns a deterministic RGB frame,
`click`/`key` accept an already-validated action without raising,
`read_submissions` exposes privileged submission state, and
`install_submission` is the "allow tests to install precise state
transitions" hook the plan itself calls for -- producing a submission is
normally the job of the real vendor-form app
(`pixelgym.tasks.vendor_form.app.server`), which this backend does not run,
so tests need a direct way to install one. The plan does **not** ask this
backend to simulate focusing a field, typing into it, or clicking an
on-screen Submit button -- only to accept the click/key event and expose
submission state, which it does.

D1.6's required unit-test matrix is mostly covered by
`tests/unit/test_env.py` against this backend: same/different-seed task and
initial observation, observation-space containment, accepted action-space
samples, boundary clicks, out-of-range/malformed-action rejection, zero
reward before a valid submission, reward-of-one and termination on a valid
submission, step-limit truncation, and stepping after episode end raising.
Two items from that matrix are not yet covered, and neither is fixable by
changing this backend:

- "Every golden-trajectory prefix receives zero" -- blocked on D1.7, which
  has not produced a golden trajectory yet to replay prefixes of.
- "Reward cannot fire a second time" as its own named test -- structurally
  guaranteed already (episode termination plus the post-terminal `step()`
  guard, both tested), but not asserted under that exact label.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from pixelgym.task_spec import Submission
from pixelgym.tasks.vendor_form import generator

DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 768


class FakeBackend:
    """Minimal in-process implementation of `pixelgym.backends.base.Backend`."""

    def __init__(self, *, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> None:
        self._width = width
        self._height = height
        self._task_record: Mapping[str, Any] | None = None
        self._submissions: list[Submission] = []
        self.click_calls: list[tuple[int, int]] = []
        self.key_calls: list[str] = []
        self.closed = False

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def app_url(self) -> str:
        return "fake://vendor-form"

    def reset(self, seed: int) -> Mapping[str, Any]:
        self._task_record = generator.generate_task(seed)
        self._submissions = []
        self.click_calls = []
        self.key_calls = []
        return self._task_record

    def screenshot(self) -> np.ndarray:
        # Content-independent: this double does not model rendered form
        # state (D1.6 scope). Shape/dtype match the declared observation
        # space, which is what D1.5's own contract requires.
        return np.zeros((self._height, self._width, 3), dtype=np.uint8)

    def click(self, x: int, y: int) -> None:
        self.click_calls.append((x, y))

    def key(self, key: str) -> None:
        self.key_calls.append(key)

    def read_submissions(self) -> Sequence[Submission]:
        return list(self._submissions)

    def close(self) -> None:
        self.closed = True

    def current_fields(self) -> dict[str, Any]:
        """Test-only accessor for the active task's expected field values,
        for building a correct/incorrect `Submission` in tests. Not part of
        the `Backend` protocol."""
        if self._task_record is None:
            raise RuntimeError("current_fields called before reset")
        return dict(self._task_record["fields"])

    def install_submission(self, values: Mapping[str, Any], *, final: bool = True) -> None:
        """Test-only hook standing in for "the agent clicked through the
        form and pressed Submit", until D1.6 wires up real field/Submit
        interaction. Not part of the `Backend` protocol."""
        if self._task_record is None:
            raise RuntimeError("install_submission called before reset")
        self._submissions.append(
            Submission(
                task_id=self._task_record["task_id"],
                seed=self._task_record["seed"],
                values=dict(values),
                submitted_at_step=len(self._submissions) + 1,
                final=final,
            )
        )
