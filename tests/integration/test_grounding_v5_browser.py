"""Opt-in bitwise browser replay for every v5 development task."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable

import pytest

from pixelgym.grounding.v5.contracts import Partition
from pixelgym.grounding.v5.generator import tasks_for_partition
from pixelgym.grounding.v5.server import READY_SELECTOR, local_v5_server

pytestmark = [
    pytest.mark.browser_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_BROWSER_TESTS") != "1",
        reason="set PIXELGYM_RUN_BROWSER_TESTS=1 to run browser integration tests",
    ),
]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _browser_trace(page: object, task: object, *, recovery: bool) -> tuple[tuple[str, str], ...]:
    screenshot: Callable[..., bytes] = page.screenshot
    evaluate: Callable[..., object] = page.evaluate
    locator: Callable[..., object] = page.locator
    trace: list[tuple[str, str]] = [
        (_digest(screenshot()), _digest(json.dumps(evaluate("window.__pixelgymV5State()"), sort_keys=True).encode()))
    ]
    if recovery:
        first = task.stages[0]
        wrong = next(control for control in first.controls if control.control_id != first.target_control_id)
        locator(f'[data-control-id="{wrong.control_id}"]').click()
        trace.append((_digest(screenshot()), _digest(json.dumps(evaluate("window.__pixelgymV5State()"), sort_keys=True).encode())))
    for stage in task.stages:
        if stage.kind.value == "text":
            field = locator('[data-control-id="text_input"]')
            field.click()
            trace.append((_digest(screenshot()), _digest(json.dumps(evaluate("window.__pixelgymV5State()"), sort_keys=True).encode())))
            for character in stage.required_text:
                page.keyboard.type(character)
                trace.append((_digest(screenshot()), _digest(json.dumps(evaluate("window.__pixelgymV5State()"), sort_keys=True).encode())))
            locator('[data-control-id="continue"]').click()
        else:
            locator(f'[data-control-id="{stage.target_control_id}"]').click()
            if stage.recovery_stage:
                trace.append((_digest(screenshot()), _digest(json.dumps(evaluate("window.__pixelgymV5State()"), sort_keys=True).encode())))
                locator('[data-control-id="repair_implicated"]').click()
        trace.append((_digest(screenshot()), _digest(json.dumps(evaluate("window.__pixelgymV5State()"), sort_keys=True).encode())))
    return tuple(trace)


def test_v5_every_development_golden_and_recovery_trace_is_bitwise_repeatable() -> None:
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with local_v5_server() as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1024, "height": 768})
        try:
            for task in tasks_for_partition(Partition.DEVELOPMENT):
                for recovery in (False, True):
                    traces = []
                    for _ in range(2):
                        page.goto(f"{origin}/?seed={task.seed}", wait_until="networkidle")
                        page.wait_for_selector(READY_SELECTOR, state="attached")
                        traces.append(_browser_trace(page, task, recovery=recovery))
                    assert traces[0] == traces[1]
        finally:
            browser.close()
