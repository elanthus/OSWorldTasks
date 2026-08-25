"""Opt-in bitwise browser replay for every v5 development task."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable

import pytest

from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import Partition
from pixelgym.grounding.v5.generator import generate_task, tasks_for_partition
from pixelgym.grounding.v5.seeds import SEED_RECORDS
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

    def sample() -> tuple[str, str]:
        state = json.dumps(evaluate("window.__pixelgymV5State()"), sort_keys=True)
        return _digest(screenshot()), _digest(state.encode())

    trace: list[tuple[str, str]] = [sample()]
    if recovery:
        first = task.stages[0]
        wrong = next(
            control for control in first.controls if control.control_id != first.target_control_id
        )
        locator(f'[data-control-id="{wrong.control_id}"]').click()
        trace.append(sample())
    for stage in task.stages:
        if stage.kind.value == "text":
            field = locator('[data-control-id="text_input"]')
            field.click()
            trace.append(sample())
            for character in stage.required_text:
                page.keyboard.type(character)
                trace.append(sample())
            locator('[data-control-id="continue"]').click()
        else:
            locator(f'[data-control-id="{stage.target_control_id}"]').click()
            if stage.recovery_stage:
                trace.append(sample())
                locator('[data-control-id="repair_implicated"]').click()
        trace.append(sample())
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


def test_v5_browser_and_fake_backend_control_geometry_and_recovery_are_equivalent() -> None:
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    twin_seed = next(record.seed for record in SEED_RECORDS if record.variant == "twin_b")
    recovery_task = next(
        task
        for task in (tasks_for_partition(Partition.CONFIRMATORY))
        if any(stage.recovery_stage for stage in task.stages)
    )
    seeds = (twin_seed, recovery_task.seed)
    with local_v5_server() as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1024, "height": 768})
        try:
            for seed in seeds:
                task = generate_task(seed)
                page.goto(f"{origin}/?seed={seed}", wait_until="networkidle")
                page.wait_for_selector(READY_SELECTOR, state="attached")
                backend = V5FakeBackend()
                backend.reset(seed)
                for stage in task.stages:
                    browser_controls = page.evaluate("window.__pixelgymV5Candidates()")
                    assert [row["control_id"] for row in browser_controls] == [
                        control.control_id for control in backend.visible_controls()
                    ]
                    assert [tuple(round(value) for value in row["bbox"]) for row in browser_controls] == [
                        control.bbox for control in backend.visible_controls()
                    ]
                    if stage.required_text:
                        page.locator('[data-control-id="text_input"]').click()
                        backend.click(*backend.control_center("text_input"))
                        page.keyboard.type(stage.required_text)
                        for character in stage.required_text:
                            backend.key(character)
                        page.locator('[data-control-id="continue"]').click()
                        backend.click(*backend.control_center("continue"))
                        continue
                    if stage.recovery_stage:
                        wrong = next(
                            control
                            for control in stage.controls
                            if control.control_id != stage.target_control_id
                        )
                        page.locator(f'[data-control-id="{wrong.control_id}"]').click()
                        backend.click(*backend.control_center(wrong.control_id))
                        assert "repair_implicated" not in {
                            row["control_id"]
                            for row in page.evaluate("window.__pixelgymV5Candidates()")
                        }
                    page.locator(f'[data-control-id="{stage.target_control_id}"]').click()
                    backend.click(*backend.control_center(stage.target_control_id))
                    if stage.recovery_stage:
                        assert [
                            row["control_id"]
                            for row in page.evaluate("window.__pixelgymV5Candidates()")
                        ] == ["repair_implicated"]
                        page.locator('[data-control-id="repair_implicated"]').click()
                        backend.click(*backend.control_center("repair_implicated"))
        finally:
            browser.close()
