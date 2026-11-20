"""Opt-in randomized differential test: fake backend vs. the real form in Chromium.

Hypothesis generates action sequences from the agent's action space (CLICK and KEY), runs each
on a fresh `FakeBackend` and on the real task app in headless Chromium through the same host
contract the OSWorld backend uses, and after *every* action compares form values, keyboard focus
(including which radio holds it), the status line, and the privileged submission history. A
mismatch fails with the shrunk action sequence, so a counterexample is short enough to read.

Clicks are drawn mostly from points derived from the widget geometry -- centres, edges, corners,
one pixel outside each edge, labels, the request card, empty space -- plus uniform points over
the whole screen. Keys are drawn with extra weight on the named keys and a few letters, but every
allowlisted key can appear. The strategies and the comparison live in
`tests/support/vendor_form_differential.py`, shared with
`scripts/run_browser_differential_evidence.py`, which records runs under explicit Hypothesis seeds.

Run with ``PIXELGYM_RUN_BROWSER_TESTS=1``. ``PIXELGYM_DIFFERENTIAL_EXAMPLES`` (default 120) and
``PIXELGYM_DIFFERENTIAL_MAX_ACTIONS`` (default 30) scale the search. The suite's Hypothesis
profile is derandomized, so a given setting replays the same sequences; the evidence script is
what exercises other seeds.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pixelgym.tasks.vendor_form.ui import DESIGN_HEIGHT, DESIGN_WIDTH
from tests.support.vendor_form_differential import SEEDS, Action, actions_strategy, compare

pytestmark = [
    pytest.mark.browser_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_BROWSER_TESTS") != "1",
        reason="set PIXELGYM_RUN_BROWSER_TESTS=1 to run browser integration tests",
    ),
]

EXAMPLES = int(os.environ.get("PIXELGYM_DIFFERENTIAL_EXAMPLES", "120"))
MAX_ACTIONS = int(os.environ.get("PIXELGYM_DIFFERENTIAL_MAX_ACTIONS", "30"))


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
    divergence = compare(real, seed, actions)
    assert divergence is None, (
        f"seed {seed}: {divergence['kind']} diverged after action {divergence['index']}\n"
        f"  fake:    {divergence['fake']}\n  browser: {divergence['browser']}\n"
        f"  sequence: {actions!r}"
    )


@settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(seed=st.sampled_from(SEEDS), actions=actions_strategy(MAX_ACTIONS))
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
        # Input contract v3.
        ("trailing space", [("click", (768, 94)), ("key", "A"), ("key", " ")]),
        (
            "select Enter and Space inert",
            [("click", (768, 343)), ("key", "a"), ("key", "Enter"), ("key", " ")],
        ),
    ],
)
def test_review_divergences_stay_fixed(real_backend: Any, name: str, actions: list[Action]) -> None:
    del name
    _run_and_compare(real_backend, 7, actions)
