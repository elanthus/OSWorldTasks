"""Reward-timing regression for the OSWorld backend's input acknowledgement barrier.

Scenario from the 2026-11 review: the task page's submit handler POSTs asynchronously, so with
server latency the frame settles (two bitwise-equal screenshots) *before* the submission reaches
the guest server. A backend that dispatches, waits for stable frames, and reads submissions once
then pays reward one step late, or never if that step was the last before truncation.

The fakes here inject that latency in *reads*, not seconds: the guest records the submission
only after a configurable number of acknowledgement polls, and the page acknowledges the input
only once its submission has landed -- exactly the ordering `app.js` enforces. No Docker, browser,
network, or real sleep is involved; the backend's clock is replaced with a deterministic one.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from pixelgym.actions import ActionType
from pixelgym.backends.osworld import OSWorldBackend, OSWorldBackendConfig, OSWorldBackendError
from pixelgym.env import PixelGuiEnv
from pixelgym.task_spec import Submission
from pixelgym.tasks.vendor_form import generator

WIDTH, HEIGHT = 4, 3
PRE_SUBMIT, POST_SUBMIT = 17, 200


def _png(value: int) -> bytes:
    output = io.BytesIO()
    Image.fromarray(np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8)).save(output, format="PNG")
    return output.getvalue()


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        self.now += 0.01
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class _SlowGuest:
    """Guest whose submit POST lands `latency_reads` acknowledgement polls after the click.

    The page shows the pre-submit frame until the submission is recorded and the post-submit
    frame afterwards, and acknowledges an input only once any submission it caused has landed.
    The provider's own post-action observation is always the stale pre-submit frame, which is
    what a too-fast screenshot after the click captures.
    """

    def __init__(self, record: dict[str, Any], *, latency_reads: int) -> None:
        self.record = record
        self.latency_reads = latency_reads
        self.inputs = 0
        self.acknowledged = 0
        self.submissions: list[Submission] = []
        self._pending_since_read: int | None = None
        self.ack_reads = 0
        self.overshoot = 0

    # -- task surface used by OSWorldBackend --------------------------------
    bundle_sha256 = "bundle-sha"

    def read_privileged_state(self, _env: Any) -> dict[str, Any]:
        return {"task": self.record, "submissions": []}

    def read_submissions(self, _env: Any) -> list[Submission]:
        return list(self.submissions)

    def read_input_acknowledgements(self, _env: Any) -> int:
        self.ack_reads += 1
        if self._pending_since_read is not None:
            if self.ack_reads - self._pending_since_read >= self.latency_reads:
                self.submissions.append(
                    Submission(
                        task_id=self.record["task_id"],
                        seed=self.record["seed"],
                        values=dict(self.record["fields"]),
                        submitted_at_step=len(self.submissions) + 1,
                    )
                )
                self._pending_since_read = None
                self.acknowledged = self.inputs
        else:
            self.acknowledged = self.inputs
        return self.acknowledged + self.overshoot

    # -- provider-side hooks -------------------------------------------------
    def on_input(self, submits: bool) -> None:
        self.inputs += 1
        if submits:
            self._pending_since_read = self.ack_reads

    @property
    def frame(self) -> bytes:
        return _png(POST_SUBMIT if self.submissions else PRE_SUBMIT)


class _Controller:
    def __init__(self, guest: _SlowGuest) -> None:
        self.guest = guest

    def get_screenshot(self) -> bytes:
        return self.guest.frame


class _Provider:
    def __init__(self, guest: _SlowGuest) -> None:
        self.guest = guest
        self.controller = _Controller(guest)
        self.closed = False

    def reset(self, task_config: Any, seed: int) -> dict[str, bytes]:
        return {"screenshot": _png(PRE_SUBMIT)}

    def step(self, action: dict[str, Any], pause: float) -> tuple[Any, ...]:
        # Every click in these tests targets Submit; keys never submit.
        self.guest.on_input(submits=action["action_type"] == "CLICK")
        return ({"screenshot": _png(PRE_SUBMIT)}, 0, False, {})

    def close(self) -> None:
        self.closed = True


def _env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    latency_reads: int,
    max_episode_steps: int = 10,
    ack_timeout: float = 5.0,
) -> tuple[PixelGuiEnv, _SlowGuest, _Clock]:
    record = generator.generate_task(7)
    guest = _SlowGuest(record, latency_reads=latency_reads)
    monkeypatch.setattr(
        "pixelgym.backends.osworld.create_osworld_task",
        lambda seed, cache_dir: (guest, generator.generate_task(seed)),
    )
    clock = _Clock()
    monkeypatch.setattr("pixelgym.backends.osworld.time", clock)
    image = tmp_path / "guest.qcow2"
    image.write_bytes(b"placeholder")
    backend = OSWorldBackend(
        OSWorldBackendConfig(
            guest_image_path=image,
            cache_dir=tmp_path / "cache",
            width=WIDTH,
            height=HEIGHT,
            stabilization_poll_interval=0,
            input_ack_timeout=ack_timeout,
            input_ack_poll_interval=0.05,
            noop_seconds=0,
        ),
        desktop_env_factory=lambda **_kwargs: _Provider(guest),
    )
    return PixelGuiEnv(backend, max_episode_steps=max_episode_steps), guest, clock


def _click() -> dict[str, int]:
    return {"action_type": int(ActionType.CLICK), "x": 1, "y": 1, "key": 0}


def _key() -> dict[str, int]:
    return {"action_type": int(ActionType.KEY), "x": 0, "y": 0, "key": 0}


@pytest.mark.parametrize("latency_reads", [1, 3, 25])
def test_submission_from_this_step_is_rewarded_in_this_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, latency_reads: int
) -> None:
    env, guest, _clock = _env(tmp_path, monkeypatch, latency_reads=latency_reads)
    env.reset(seed=7)

    observation, reward, terminated, truncated, _info = env.step(_click())

    assert (reward, terminated, truncated) == (1.0, True, False)
    assert len(guest.submissions) == 1
    # The provider's stale post-action frame is discarded: the observation is the settled
    # post-submission frame, not the pre-submit frame captured right after the click.
    assert observation.tolist() == np.full((HEIGHT, WIDTH, 3), POST_SUBMIT, np.uint8).tolist()


def test_submission_on_the_last_step_is_not_lost_to_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, _guest, _clock = _env(tmp_path, monkeypatch, latency_reads=5, max_episode_steps=3)
    env.reset(seed=7)
    assert env.step(_key())[1:4] == (0.0, False, False)
    assert env.step(_key())[1:4] == (0.0, False, False)

    _observation, reward, terminated, truncated, _info = env.step(_click())

    assert (reward, terminated, truncated) == (1.0, True, False)


def test_non_submitting_inputs_still_wait_for_their_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, guest, _clock = _env(tmp_path, monkeypatch, latency_reads=1)
    env.reset(seed=7)
    reads_after_reset = guest.ack_reads

    env.step(_key())

    assert guest.ack_reads == reads_after_reset + 1
    assert env.backend.last_input_ack_reads == 1  # type: ignore[attr-defined]


def test_missing_acknowledgement_fails_closed_without_reward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, _guest, clock = _env(tmp_path, monkeypatch, latency_reads=10_000, ack_timeout=0.5)
    env.reset(seed=7)

    with pytest.raises(OSWorldBackendError, match="was not acknowledged"):
        env.step(_click())
    assert clock.sleeps and set(clock.sleeps) == {0.05}


def test_acknowledgement_overshoot_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, guest, _clock = _env(tmp_path, monkeypatch, latency_reads=1)
    env.reset(seed=7)
    guest.overshoot = 1  # the page saw an input this backend never sent

    with pytest.raises(
        OSWorldBackendError, match="acknowledged 2 inputs but the host dispatched 1"
    ):
        env.step(_key())


def test_reset_rejects_a_page_that_already_acknowledged_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, guest, _clock = _env(tmp_path, monkeypatch, latency_reads=1)
    guest.overshoot = 1

    with pytest.raises(OSWorldBackendError, match="was not acknowledged"):
        env.reset(seed=7)


def test_without_the_barrier_the_same_guest_pays_reward_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: the pre-fix order (dispatch, stable frames, one read) misses the
    submission on the step that caused it, so the tests above can fail."""

    env, guest, _clock = _env(tmp_path, monkeypatch, latency_reads=1)
    env.reset(seed=7)
    monkeypatch.setattr(OSWorldBackend, "_await_input_acknowledgements", lambda self: None)

    observation, reward, terminated, _truncated, _info = env.step(_click())

    assert (reward, terminated) == (0.0, False)
    assert guest.submissions == []
    assert observation.tolist() == np.full((HEIGHT, WIDTH, 3), PRE_SUBMIT, np.uint8).tolist()
