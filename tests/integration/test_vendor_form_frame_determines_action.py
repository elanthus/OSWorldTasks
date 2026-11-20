"""Opt-in: in Chromium the frame determines the oracle's next action, and the caret is static.

The browser counterpart of `tests/unit/test_frame_determines_oracle_action.py`. The oracle's
actions are derived from a `FakeBackend` at the 1024x768 design size (the oracle reads privileged
field values, which the page does not expose) and replayed on the real task app in headless
Chromium through the same acknowledgement barrier the OSWorld backend uses. For every oracle step
the settled Chromium frame digest is paired with the action; no digest may map to two actions.

It also checks the caret against invariant 10: after typing, twenty raw screenshots taken over
about two seconds -- several native caret blink periods -- must be bitwise identical, and the page
must have no running animations.

Run with ``PIXELGYM_RUN_BROWSER_TESTS=1``. ``PIXELGYM_BROWSER_ORACLE_SEEDS`` (default 10) sets how
many seeds the frame/action check covers.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections import defaultdict
from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.tasks.vendor_form.ui import DESIGN_HEIGHT, DESIGN_WIDTH, WidgetId
from tests.support.golden_solver import build_dynamic_solve_actions

pytestmark = [
    pytest.mark.browser_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_BROWSER_TESTS") != "1",
        reason="set PIXELGYM_RUN_BROWSER_TESTS=1 to run browser integration tests",
    ),
]

ORACLE_SEEDS = range(int(os.environ.get("PIXELGYM_BROWSER_ORACLE_SEEDS", "10")))


@pytest.fixture(scope="module")
def browser_page() -> Iterator[tuple[Any, Any]]:
    playwright_api = pytest.importorskip("playwright.sync_api")
    from tests.support.browser_backend import (
        PlaywrightVendorFormBackend,
        launch_chromium,
        vendor_form_server,
    )

    with vendor_form_server() as base_url, playwright_api.sync_playwright() as playwright:
        browser = launch_chromium(playwright)
        try:
            page = browser.new_page(viewport={"width": DESIGN_WIDTH, "height": DESIGN_HEIGHT})
            yield page, PlaywrightVendorFormBackend(page, base_url)
        finally:
            browser.close()


def _digest(frame: np.ndarray) -> str:
    return hashlib.sha256(frame.tobytes()).hexdigest()


def test_no_chromium_frame_precedes_two_different_oracle_actions(
    browser_page: tuple[Any, Any],
) -> None:
    _page, real = browser_page
    fake = FakeBackend()
    seen: dict[str, set[tuple[int, ...]]] = defaultdict(set)
    for seed in ORACLE_SEEDS:
        fake.reset(seed)
        real.reset(seed)
        for action in build_dynamic_solve_actions(fake):
            seen[_digest(real.screenshot())].add(
                (int(action["action_type"]), action["x"], action["y"], action["key"])
            )
            if action["action_type"] == ActionType.CLICK:
                fake.click(action["x"], action["y"])
                real.click(action["x"], action["y"])
            else:
                key = KEY_ALLOWLIST[action["key"]]
                fake.key(key)
                real.key(key)
        assert len(real.read_submissions()) == 1
    conflicts = {digest: actions for digest, actions in seen.items() if len(actions) > 1}
    assert conflicts == {}, f"{len(conflicts)} Chromium frames precede more than one oracle action"


def test_a_trailing_space_moves_the_caret_in_chromium(browser_page: tuple[Any, Any]) -> None:
    _page, real = browser_page
    fake = FakeBackend()
    fake.reset(0)
    real.reset(0)
    real.click(*fake.layout.controls[WidgetId.COMPANY_NAME].center)
    for character in "Acme":
        real.key(character)
    before = real.screenshot()

    real.key(" ")

    assert real.read_form_state()["values"]["company_name"] == "Acme "
    assert not np.array_equal(real.screenshot(), before)


def test_the_caret_is_static_across_blink_periods(browser_page: tuple[Any, Any]) -> None:
    page, real = browser_page
    fake = FakeBackend()
    fake.reset(3)
    real.reset(3)
    real.click(*fake.layout.controls[WidgetId.CONTACT_EMAIL].center)
    for character in "ab ":
        real.key(character)

    digests = set()
    for _ in range(20):
        digests.add(hashlib.sha256(page.screenshot(type="png")).hexdigest())
        time.sleep(0.1)

    assert len(digests) == 1
    assert page.evaluate("document.getAnimations().length") == 0
    caret = page.evaluate(
        "(() => { const s = getComputedStyle(document.getElementById('pixelgym-caret'));"
        " return [s.display, s.animationName, s.transitionDuration]; })()"
    )
    assert caret == ["block", "none", "0s"]


def test_enter_and_space_on_the_country_select_change_nothing(
    browser_page: tuple[Any, Any],
) -> None:
    _page, real = browser_page
    fake = FakeBackend()
    task = fake.reset(5)
    real.reset(5)
    real.click(*fake.layout.controls[WidgetId.COUNTRY].center)
    real.key(task["fields"]["country"][0].lower())
    state, frame = real.read_form_state(), real.screenshot()

    real.key("Enter")
    real.key(" ")

    assert real.read_form_state() == state
    assert np.array_equal(real.screenshot(), frame)
    assert real.read_submissions() == []
