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
import time
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

_GOLDEN = Path(__file__).resolve().parents[1] / "unit/fixtures/golden_trajectory_seed7.json"


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


def _run(delay: float, actions: list[dict[str, int]], **backend_kwargs: Any) -> dict[str, Any]:
    playwright_api = pytest.importorskip("playwright.sync_api")
    from tests.support.browser_backend import launch_chromium, vendor_form_server

    with vendor_form_server(submit_delay=delay) as base_url, playwright_api.sync_playwright() as pw:
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
            # Give any straggling request ample time, then capture what the page settles to.
            time.sleep(max(1.0, 4 * delay))
            return {
                "timeline": timeline,
                "in_step_reads": in_step_reads,
                "final_observation": observation,
                "later_frame": backend.screenshot(),
                "later_submissions": len(backend.read_submissions()),
                "status": backend.read_form_state()["status"],
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

    result = _run(delay, actions)

    expected = [(0.0, False, False)] * (len(actions) - 1) + [(1.0, True, False)]
    assert result["timeline"] == expected
    assert result["in_step_reads"][-1] == 1
    assert result["status"] == "Submitted."
    assert np.array_equal(result["final_observation"], result["later_frame"]), (
        "the observation returned with the reward was not the settled post-submission frame"
    )


@pytest.mark.parametrize("delay", [0.05, 0.3])
def test_incomplete_submit_is_visible_to_the_same_steps_evaluator_read(delay: float) -> None:
    result = _run(delay, [_submit_click()])

    assert result["timeline"] == [(0.0, False, True)]
    assert result["in_step_reads"] == [1]
    assert result["status"] == INCOMPLETE_SUBMISSION_MESSAGE
    assert np.array_equal(result["final_observation"], result["later_frame"])


def test_negative_control_without_the_barrier_misses_the_submission() -> None:
    """Proves the tests above can fail: the pre-fix host order with a 1 s submit delay reads
    submissions before the POST lands."""

    result = _run(1.0, [_submit_click()], acknowledge_inputs=False)

    assert result["in_step_reads"] == [0]
    assert result["later_submissions"] == 1
