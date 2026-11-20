"""Opt-in browser regression for reward timing under submit latency.

`PixelGuiEnv` dispatches an action, captures the observation, and reads submissions once. With an
asynchronous submit POST and a slow task server, the frame used to settle before the submission
landed, so reward arrived a step late, or was lost on the last step before truncation. These tests
inject real server latency on ``POST /api/submit`` and drive the real page in Chromium through
`PlaywrightVendorFormBackend`, which implements the same acknowledgement barrier as the OSWorld
backend. The requirement checked: when ``step()`` returns, a submission caused by that step's
action was visible to the evaluator read *inside* that step, and the observation is the settled
post-submission frame.

Run with ``PIXELGYM_RUN_BROWSER_TESTS=1``.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pixelgym.actions import ActionType
from pixelgym.env import PixelGuiEnv
from pixelgym.task_spec import Submission
from pixelgym.tasks.vendor_form.ui import (
    DESIGN_HEIGHT,
    DESIGN_WIDTH,
    INCOMPLETE_SUBMISSION_MESSAGE,
    WidgetId,
    layout_for,
)

pytestmark = [
    pytest.mark.browser_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_BROWSER_TESTS") != "1",
        reason="set PIXELGYM_RUN_BROWSER_TESTS=1 to run browser integration tests",
    ),
]

_GOLDEN = Path(__file__).resolve().parents[1] / "unit/fixtures/golden_trajectory_seed7_v2.json"


def _recording_backend(page: Any, base_url: str, **kwargs: Any) -> Any:
    from tests.support.browser_backend import PlaywrightVendorFormBackend

    class Recording(PlaywrightVendorFormBackend):
        """Records what each evaluator read returned, i.e. what the step's reward saw."""

        def __init__(self, *args: Any, **inner: Any) -> None:
            super().__init__(*args, **inner)
            self.reads: list[int] = []

        def read_submissions(self) -> Sequence[Submission]:
            submissions = super().read_submissions()
            self.reads.append(len(submissions))
            return submissions

    return Recording(page, base_url, **kwargs)


def _run(
    delay: float,
    actions: list[dict[str, int]],
    *,
    expected_submissions: int,
    expected_status: str,
    submit_gate: threading.Event | None = None,
    submit_arrived: threading.Event | None = None,
    **backend_kwargs: Any,
) -> dict[str, Any]:
    playwright_api = pytest.importorskip("playwright.sync_api")
    from tests.support.browser_backend import launch_chromium, vendor_form_server, wait_until

    with (
        vendor_form_server(
            submit_delay=delay, submit_gate=submit_gate, submit_arrived=submit_arrived
        ) as base_url,
        playwright_api.sync_playwright() as pw,
    ):
        browser = launch_chromium(pw)
        try:
            page = browser.new_page(viewport={"width": DESIGN_WIDTH, "height": DESIGN_HEIGHT})
            backend = _recording_backend(page, base_url, **backend_kwargs)
            env = PixelGuiEnv(backend, max_episode_steps=len(actions))
            env.reset(seed=7)
            timeline = []
            observation = None
            for action in actions:
                observation, reward, terminated, truncated, _info = env.step(action)
                timeline.append((reward, terminated, truncated))
            assert observation is not None
            in_step_reads = list(backend.reads)
            if submit_gate is not None:
                submit_gate.set()  # only now may the held submission reach the server
            # Wait for the work to finish by condition, not elapsed time: the submission is
            # recorded and the page shows its final status. Then capture the settled frame.
            wait_until(
                lambda: (
                    len(backend.read_submissions()) == expected_submissions
                    and backend.read_form_state()["status"] == expected_status
                ),
                what=f"{expected_submissions} submission(s) and status {expected_status!r}",
            )
            return {
                "timeline": timeline,
                "in_step_reads": in_step_reads,
                "final_observation": observation,
                "later_frame": backend.screenshot(),
            }
        finally:
            browser.close()


def _submit_click() -> dict[str, int]:
    layout = layout_for({"options": {"payment_terms": ["", "", ""]}}, DESIGN_WIDTH, DESIGN_HEIGHT)
    x, y = layout.controls[WidgetId.SUBMIT].center
    return {"action_type": int(ActionType.CLICK), "x": x, "y": y, "key": 0}


@pytest.mark.parametrize("delay", [0.05, 0.3])
def test_golden_trajectory_rewards_on_its_final_step_under_submit_latency(delay: float) -> None:
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    actions = golden["actions"]

    result = _run(delay, actions, expected_submissions=1, expected_status="Submitted.")

    expected = [(0.0, False, False)] * (len(actions) - 1) + [(1.0, True, False)]
    assert result["timeline"] == expected
    assert result["in_step_reads"][-1] == 1
    assert np.array_equal(result["final_observation"], result["later_frame"]), (
        "the observation returned with the reward was not the settled post-submission frame"
    )


@pytest.mark.parametrize("delay", [0.05, 0.3])
def test_incomplete_submit_is_visible_to_the_same_steps_evaluator_read(delay: float) -> None:
    result = _run(
        delay,
        [_submit_click()],
        expected_submissions=1,
        expected_status=INCOMPLETE_SUBMISSION_MESSAGE,
    )

    assert result["timeline"] == [(0.0, False, True)]
    assert result["in_step_reads"] == [1]
    assert np.array_equal(result["final_observation"], result["later_frame"])


def test_negative_control_without_the_barrier_misses_the_submission() -> None:
    """Proves the tests above can fail: with the barrier off, the pre-fix host order (dispatch,
    stable frames, one read) reads submissions while the submit POST is still held at the server.
    The gate is released only after that read, so the order does not depend on timing."""

    result = _run(
        0.0,
        [_submit_click()],
        expected_submissions=1,
        expected_status=INCOMPLETE_SUBMISSION_MESSAGE,
        submit_gate=threading.Event(),
        acknowledge_inputs=False,
    )

    assert result["in_step_reads"] == [0]


def test_barrier_holds_the_step_until_a_gated_submission_is_released() -> None:
    """With the barrier on, the step cannot return while the submission is held at the server.
    A helper thread releases the gate only after the submit request has arrived -- i.e. while the
    step is already blocked -- and the in-step evaluator read must then see the submission."""

    gate, arrived = threading.Event(), threading.Event()
    releases: list[bool] = []

    def release_after_arrival() -> None:
        releases.append(arrived.wait(30.0))
        gate.set()

    releaser = threading.Thread(target=release_after_arrival, daemon=True)
    releaser.start()
    try:
        result = _run(
            0.0,
            [_submit_click()],
            expected_submissions=1,
            expected_status=INCOMPLETE_SUBMISSION_MESSAGE,
            submit_gate=gate,
            submit_arrived=arrived,
        )
    finally:
        gate.set()
        releaser.join(timeout=5.0)

    assert releases == [True]
    assert result["in_step_reads"] == [1]
