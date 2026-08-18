"""Backend protocol that `PixelGuiEnv` talks to.

`PixelGuiEnv` is written only against this `Protocol` -- it never imports a
concrete backend, OSWorld included (see AGENTS.md invariant 12: no OSWorld
import in the core module). `pixelgym.backends.fake.FakeBackend` and
`pixelgym.backends.osworld.OSWorldBackend` both satisfy this protocol without
the environment knowing which one it is holding.

A backend owns exactly the mechanics of *one running task application*:
installing a task for a seed, driving click/key input into it, capturing its
current screen, and exposing the privileged submission history the evaluator
reads. It does not know what a `TaskSpec` is for, does not evaluate success,
and does not decide reward -- those stay in `PixelGuiEnv` and
`pixelgym.evaluator`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

import numpy as np

from pixelgym.task_spec import Submission


@runtime_checkable
class Backend(Protocol):
    """Everything `PixelGuiEnv` needs from a running task application.

    `width` and `height` fix the environment's observation and action
    spaces for the lifetime of the backend instance -- they must be stable
    across resets. `app_url` is the launch descriptor recorded in
    `TaskSpec.app_url`; it identifies where the task application lives
    (e.g. an HTTP origin), not a per-task value.
    """

    @property
    def width(self) -> int:
        """Screen width in pixels. Stable across resets."""
        ...

    @property
    def height(self) -> int:
        """Screen height in pixels. Stable across resets."""
        ...

    @property
    def app_url(self) -> str:
        """The task application's launch descriptor (e.g. its HTTP origin)."""
        ...

    def reset(self, seed: int) -> Mapping[str, Any]:
        """Install a fresh task for `seed`, discarding any prior task and
        submission history, and return the generated task record.

        The record must be shaped like
        `pixelgym.tasks.vendor_form.generator.generate_task`'s output -- at
        minimum `task_id`, `seed`, and `fields` -- so the caller can build a
        `TaskSpec` via `TaskSpec.from_generated`. Calling `reset` with the same seed twice
        must produce byte-identical records (AGENTS.md invariant 9) and must
        leave no submission from the prior task reachable via
        `read_submissions` (invariant 11).
        """
        ...

    def screenshot(self) -> np.ndarray:
        """Return the current on-screen frame as an RGB array with shape
        `(height, width, 3)` and dtype `uint8`.

        Must return only once the frame is stable -- no mid-transition or
        mid-animation frame (AGENTS.md invariant 10). The environment does
        not poll or retry; a backend that needs to wait for rendering does
        so internally before returning.
        """
        ...

    def noop(self) -> None:
        """Advance by one bounded no-op/wait action.

        A fake backend performs no work. A real desktop backend uses its
        provider's structured wait path, which cannot declare completion.
        """
        ...

    def click(self, x: int, y: int) -> None:
        """Click at pixel `(x, y)`. `x` is in `[0, width)`, `y` in
        `[0, height)` -- already validated by `PixelGuiEnv` against the
        action space before this is called."""
        ...

    def key(self, key: str) -> None:
        """Send one keystroke. `key` is a literal member of
        `pixelgym.actions.KEY_ALLOWLIST` (a printable character, or one of
        the named keys such as ``"Tab"`` or ``"Enter"``), already resolved
        by `PixelGuiEnv` from the action's allowlist index."""
        ...

    def read_submissions(self) -> Sequence[Submission]:
        """Return every submission recorded for the current task, in the
        privileged shape the evaluator reads. This is the *only* channel
        through which `PixelGuiEnv` learns about submissions -- there is no
        path from screenshot pixels, UI state, or an agent-declared "done"
        into evaluation (AGENTS.md invariant 6)."""
        ...

    def close(self) -> None:
        """Release any resources the backend holds (processes, sockets,
        temp state). Safe to call multiple times."""
        ...
