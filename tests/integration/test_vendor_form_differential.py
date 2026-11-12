"""Opt-in randomized differential test: fake backend vs. the real form in Chromium.

Hypothesis generates action sequences from the agent's action space (CLICK and KEY), runs each
on a fresh `FakeBackend` and on the real task app in headless Chromium through the same host
contract the OSWorld backend uses, and after *every* action compares form values, keyboard focus
(including which radio holds it), the status line, and the privileged submission history. A
mismatch fails with the shrunk action sequence, so a counterexample is short enough to read.

Clicks are drawn mostly from points derived from the widget geometry -- centres, edges, corners,
one pixel outside each edge, labels, the request card, empty space -- plus uniform points over
the whole screen. Keys are drawn with extra weight on the named keys and a few letters, but every
allowlisted key can appear.

Run with ``PIXELGYM_RUN_BROWSER_TESTS=1``. ``PIXELGYM_DIFFERENTIAL_EXAMPLES`` (default 120) and
``PIXELGYM_DIFFERENTIAL_MAX_ACTIONS`` (default 30) scale the search. The suite's Hypothesis
profile is derandomized, so a given setting replays the same sequences.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.backends.fake import FakeBackend
from pixelgym.tasks.vendor_form import generator
from pixelgym.tasks.vendor_form.ui import DESIGN_HEIGHT, DESIGN_WIDTH, Layout, Rect, layout_for

pytestmark = [
    pytest.mark.browser_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_BROWSER_TESTS") != "1",
        reason="set PIXELGYM_RUN_BROWSER_TESTS=1 to run browser integration tests",
    ),
]

EXAMPLES = int(os.environ.get("PIXELGYM_DIFFERENTIAL_EXAMPLES", "120"))
MAX_ACTIONS = int(os.environ.get("PIXELGYM_DIFFERENTIAL_MAX_ACTIONS", "30"))
SEEDS = (7, 11, 23, 101)

Action = tuple[str, Any]


def _rect_points(rect: Rect) -> list[tuple[int, int]]:
    cx, cy = rect.center
    inside = [
        (cx, cy),
        (rect.x + 4, cy),
        (rect.right - 5, cy),
        (rect.x, rect.y),
        (rect.right - 1, rect.bottom - 1),
    ]
    outside = [(rect.x - 1, cy), (rect.right, cy), (cx, rect.y - 1), (cx, rect.bottom)]
    return inside + outside


def _click_points(layout: Layout) -> list[tuple[int, int]]:
    rects = [
        *layout.controls.values(),
        *layout.labels.values(),
        *layout.payment_options,
        *layout.request_values.values(),
        *layout.request_labels.values(),
        layout.status,
        layout.form_heading,
        layout.request_heading,
    ]
    points = {point for rect in rects for point in _rect_points(rect)}
    return sorted((x, y) for x, y in points if 0 <= x < DESIGN_WIDTH and 0 <= y < DESIGN_HEIGHT)


# Every seed shares one layout (the payment-option count is fixed by the generator).
_LAYOUT = layout_for(generator.generate_task(SEEDS[0]), DESIGN_WIDTH, DESIGN_HEIGHT)
_NAMED_KEYS = ("Tab", "Enter", "Backspace", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", " ")

_clicks = st.one_of(
    st.sampled_from(_click_points(_LAYOUT)),
    st.sampled_from(_click_points(_LAYOUT)),
    st.tuples(st.integers(0, DESIGN_WIDTH - 1), st.integers(0, DESIGN_HEIGHT - 1)),
).map(lambda point: ("click", point))
_keys = st.one_of(
    st.sampled_from(_NAMED_KEYS),
    st.sampled_from(_NAMED_KEYS),
    st.sampled_from(("a", "b", "c", "B", "N", "X", "1", "@")),
    st.sampled_from(KEY_ALLOWLIST),
).map(lambda key: ("key", key))
_actions = st.lists(st.one_of(_clicks, _keys), min_size=1, max_size=MAX_ACTIONS)


def _fake_state(fake: FakeBackend) -> dict[str, Any]:
    form = fake.form
    return {
        "values": form.values(),
        "focus": None if form.focus is None else form.focus.value,
        "payment_focus_index": form.payment_focus_index,
        "status": form.status,
    }


def _apply(backend: Any, action: Action) -> None:
    kind, argument = action
    if kind == "click":
        backend.click(*argument)
    else:
        backend.key(argument)


@pytest.fixture(scope="module")
def real_backend() -> Iterator[Any]:
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
            yield PlaywrightVendorFormBackend(page, base_url)
        finally:
            browser.close()


def _run_and_compare(real: Any, seed: int, actions: list[Action]) -> None:
    fake = FakeBackend()
    assert dict(fake.reset(seed)) == dict(real.reset(seed))
    for index, action in enumerate(actions):
        _apply(fake, action)
        _apply(real, action)
        expected = _fake_state(fake)
        actual = real.read_form_state()
        assert actual == expected, (
            f"seed {seed}: form state diverged after action {index} {action!r}\n"
            f"  fake:    {expected}\n  browser: {actual}\n  sequence: {actions[: index + 1]!r}"
        )
        fake_submissions = list(fake.read_submissions())
        real_submissions = list(real.read_submissions())
        assert real_submissions == fake_submissions, (
            f"seed {seed}: submissions diverged after action {index} {action!r}\n"
            f"  fake:    {fake_submissions}\n  browser: {real_submissions}\n"
            f"  sequence: {actions[: index + 1]!r}"
        )


@settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(seed=st.sampled_from(SEEDS), actions=_actions)
def test_fake_and_browser_agree_on_random_action_sequences(
    real_backend: Any, seed: int, actions: list[Action]
) -> None:
    _run_and_compare(real_backend, seed, actions)


@pytest.mark.parametrize(
    ("name", "actions"),
    [
        # The divergences found by the 2026-11 review, kept as named regressions.
        (
            "caret ArrowLeft",
            [("click", (768, 94)), ("key", "a"), ("key", "b"), ("key", "ArrowLeft"), ("key", "c")],
        ),
        (
            "caret ArrowUp",
            [("click", (768, 94)), ("key", "a"), ("key", "b"), ("key", "ArrowUp"), ("key", "c")],
        ),
        (
            "caret click near left edge",
            [
                ("click", (768, 94)),
                ("key", "a"),
                ("key", "b"),
                ("key", "c"),
                ("click", (768, 280)),
                ("click", (540, 94)),
                ("key", "X"),
            ],
        ),
        ("label click focuses", [("click", (768, 280)), ("click", (768, 67))]),
        ("Tab into radios then ArrowDown", [("key", "Tab")] * 6 + [("key", "ArrowDown")]),
        ("Tab into radios then Space", [("key", "Tab")] * 6 + [("key", " ")]),
        (
            "select ArrowDown twice",
            [("click", (768, 343)), ("key", "ArrowDown"), ("key", "ArrowDown")],
        ),
    ],
)
def test_review_divergences_stay_fixed(real_backend: Any, name: str, actions: list[Action]) -> None:
    del name
    _run_and_compare(real_backend, 7, actions)
