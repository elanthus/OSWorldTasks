"""Opt-in browser checks for the vendor-form submission boundary.

The ordinary fast suite must remain browser-free. Run this module explicitly with
``PIXELGYM_RUN_BROWSER_TESTS=1`` when Chromium is installed for Playwright.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.tasks.vendor_form.browser_contract import READY_SELECTOR, local_vendor_form_server
from pixelgym.tasks.vendor_form.ui import (
    DESIGN_HEIGHT,
    DESIGN_WIDTH,
    INCOMPLETE_SUBMISSION_MESSAGE,
    TEXT_WIDGETS,
    Rect,
    WidgetId,
    layout_for,
)
from pixelgym.validation.browser_boundary import (
    browser_boundary_evidence_passed,
    browser_boundary_source_hashes_match,
    validate_browser_boundary,
)

_INCOMPLETE_RECORDING_FAILED_MESSAGE = (
    f"{INCOMPLETE_SUBMISSION_MESSAGE} Submission attempt was not recorded."
)

pytestmark = [
    pytest.mark.browser_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_BROWSER_TESTS") != "1",
        reason="set PIXELGYM_RUN_BROWSER_TESTS=1 to run browser integration tests",
    ),
]


def _json_request(url: str, *, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=5.0) as response:
        return json.load(response)


_WIDGET_SELECTORS = {
    WidgetId.COMPANY_NAME: "#company_name",
    WidgetId.CONTACT_EMAIL: "#contact_email",
    WidgetId.CONTACT_PHONE: "#contact_phone",
    WidgetId.TAX_ID: "#tax_id",
    WidgetId.COUNTRY: "#country",
    WidgetId.PAYMENT_TERMS: "#payment_terms",
    WidgetId.EXPEDITED_ONBOARDING: ".checkbox-row label",
    WidgetId.SUBMIT: "#submit-button",
}


def _bounding_rect(locator: Any) -> dict[str, float]:
    rect = locator.evaluate(
        """element => {
          const box = element.getBoundingClientRect();
          return {x: box.x, y: box.y, width: box.width, height: box.height};
        }"""
    )
    assert isinstance(rect, dict)
    return rect


def _browser_rects(page: Any) -> tuple[dict[WidgetId, dict[str, float]], list[dict[str, float]]]:
    widgets = {
        widget: _bounding_rect(page.locator(selector))
        for widget, selector in _WIDGET_SELECTORS.items()
    }
    payment_options = [
        _bounding_rect(page.locator("#payment_terms label").nth(index))
        for index in range(page.locator("#payment_terms label").count())
    ]
    return widgets, payment_options


def _assert_rect_matches_browser(name: str, actual: dict[str, float], expected: Rect) -> None:
    for field in ("x", "y", "width", "height"):
        browser_value = actual[field]
        layout_value = getattr(expected, field)
        assert abs(browser_value - layout_value) <= 0.5, (
            f"{name}: browser {field}={browser_value} differs from "
            f"Layout {field}={layout_value} by more than 0.5 CSS px"
        )


def _semantic_control_at(page: Any, center: tuple[int, int]) -> str | None:
    return page.evaluate(
        """([x, y]) => {
          const element = document.elementFromPoint(x, y);
          if (!element) return null;
          if (element.id) return element.id;
          const label = element.closest("label");
          const input = label && label.querySelector("input");
          return input ? input.id : null;
        }""",
        list(center),
    )


def _load_form_page(base_url: str, playwright: Any) -> tuple[Any, Any, dict]:
    _json_request(f"{base_url}/api/reset", payload={"seed": 7})
    task = _json_request(f"{base_url}/api/task")
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": DESIGN_WIDTH, "height": DESIGN_HEIGHT})
    page.goto(base_url)
    page.locator(READY_SELECTOR).wait_for(state="attached")
    return browser, page, task


def test_fake_layout_matches_browser_control_hit_regions() -> None:
    """Pin the build-time layout contract to real Chromium geometry.

    Coordinates are browser CSS pixels at 1024x768 with a top-left origin;
    centers are integer `(x + width // 2, y + height // 2)` points. Layout
    values are integers, so the 0.5 CSS-pixel tolerance admits only the maximum
    quantization error for a fractional browser edge and rejects a one-pixel
    drift. Every assertion includes the widget or option name.
    """
    playwright_api = pytest.importorskip("playwright.sync_api")

    with local_vendor_form_server() as base_url, playwright_api.sync_playwright() as playwright:
        browser, page, task = _load_form_page(base_url, playwright)
        try:
            layout = layout_for(task, DESIGN_WIDTH, DESIGN_HEIGHT)
            browser_widgets, browser_options = _browser_rects(page)

            for widget in WidgetId:
                _assert_rect_matches_browser(
                    widget.value, browser_widgets[widget], layout.controls[widget]
                )

            payment_values = task["options"]["payment_terms"]
            assert len(browser_options) == len(payment_values) == len(layout.payment_options)
            for index, (value, browser_rect, layout_rect) in enumerate(
                zip(payment_values, browser_options, layout.payment_options, strict=True)
            ):
                _assert_rect_matches_browser(
                    f"payment_terms[{index}]={value}", browser_rect, layout_rect
                )

            expected_centers = {
                widget: widget.value for widget in (*TEXT_WIDGETS, WidgetId.COUNTRY)
            }
            expected_centers[WidgetId.EXPEDITED_ONBOARDING] = "expedited_onboarding"
            expected_centers[WidgetId.SUBMIT] = "submit-button"
            for widget, expected_id in expected_centers.items():
                assert _semantic_control_at(page, layout.controls[widget].center) == expected_id, (
                    f"{widget.value}: Layout center does not hit the browser control"
                )
            for index, option in enumerate(layout.payment_options):
                assert _semantic_control_at(page, option.center) == f"payment_terms_{index}", (
                    f"payment_terms[{index}]: Layout center does not hit the browser radio"
                )
        finally:
            browser.close()


def test_payment_option_geometry_is_stable_for_varying_label_lengths() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")

    with local_vendor_form_server() as base_url, playwright_api.sync_playwright() as playwright:
        browser, page, task = _load_form_page(base_url, playwright)
        try:
            layout = layout_for(task, DESIGN_WIDTH, DESIGN_HEIGHT)
            page.evaluate(
                """values => document.querySelectorAll("#payment_terms label").forEach(
                  (label, index) => label.lastChild.textContent = values[index]
                )""",
                ["N", "Due on receipt", "Net 123456789"],
            )
            _widgets, browser_options = _browser_rects(page)
            for index, (browser_rect, layout_rect) in enumerate(
                zip(browser_options, layout.payment_options, strict=True)
            ):
                _assert_rect_matches_browser(
                    f"payment_terms[{index}] varying-label", browser_rect, layout_rect
                )
        finally:
            browser.close()


def test_layout_drift_failure_names_the_mismatched_widget() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")

    with local_vendor_form_server() as base_url, playwright_api.sync_playwright() as playwright:
        browser, page, task = _load_form_page(base_url, playwright)
        try:
            layout = layout_for(task, DESIGN_WIDTH, DESIGN_HEIGHT)
            browser_widgets, _browser_options = _browser_rects(page)
            widget = WidgetId.CONTACT_EMAIL
            original = layout.controls[widget]
            layout.controls[widget] = replace(original, x=original.x + 1)

            with pytest.raises(AssertionError, match=widget.value):
                _assert_rect_matches_browser(
                    widget.value, browser_widgets[widget], layout.controls[widget]
                )
        finally:
            browser.close()


def test_country_and_keyboard_focus_contract_matches_browser() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")
    assert {"c", "Enter", "Tab"} <= set(KEY_ALLOWLIST)

    with local_vendor_form_server() as base_url, playwright_api.sync_playwright() as playwright:
        browser, page, task = _load_form_page(base_url, playwright)
        try:
            layout = layout_for(task, DESIGN_WIDTH, DESIGN_HEIGHT)
            page.evaluate(
                """() => {
                  window.pixelgymSubmitCount = 0;
                  document.getElementById("vendor-form").addEventListener(
                    "submit", () => window.pixelgymSubmitCount += 1
                  );
                }"""
            )

            page.mouse.click(*layout.controls[WidgetId.COUNTRY].center)
            page.keyboard.press("c")
            page.keyboard.press("Enter")
            assert page.locator("#country").input_value() == task["options"]["country"][2]
            assert page.evaluate("window.pixelgymSubmitCount") == 0

            cases = [
                *((widget.value, f"#{widget.value}", 1) for widget in TEXT_WIDGETS),
                ("country", "#country", 0),
                ("payment_terms", "#payment_terms_0", 0),
                ("expedited_onboarding", "#expedited_onboarding", 1),
                ("submit", "#submit-button", 1),
            ]
            for name, selector, expected_submits in cases:
                page.evaluate("window.pixelgymSubmitCount = 0")
                page.locator(selector).focus()
                page.keyboard.press("Enter")
                assert page.evaluate("window.pixelgymSubmitCount") == expected_submits, name

            page.evaluate("document.activeElement.blur()")
            page.evaluate("window.pixelgymSubmitCount = 0")
            page.keyboard.press("Enter")
            assert page.evaluate("window.pixelgymSubmitCount") == 0, "no focused control"

            page.locator("#submit-button").focus()
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement.tagName") == "BODY"
            assert page.evaluate("document.activeElement.id") == ""
        finally:
            browser.close()


def test_incomplete_browser_submit_is_recorded_and_rejected_by_evaluator() -> None:
    pytest.importorskip("playwright.sync_api")
    repository_root = Path(__file__).resolve().parents[2]

    evidence = validate_browser_boundary(repository_root)

    assert browser_boundary_evidence_passed(evidence) is True
    assert browser_boundary_source_hashes_match(evidence, repository_root) is True
    assert evidence["boundary"]["submission_count"] == 1
    assert evidence["boundary"]["evaluation"]["derived_environment_reward"] == 0.0


def test_incomplete_browser_submit_reports_when_attempt_was_not_recorded() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")

    with local_vendor_form_server() as base_url, playwright_api.sync_playwright() as playwright:
        _json_request(f"{base_url}/api/reset", payload={"seed": 7})
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.goto(base_url)
            page.locator(READY_SELECTOR).wait_for(state="attached")
            page.evaluate("() => { document.getElementById('task_id').value = 'vf-stale'; }")

            with page.expect_response(
                lambda response: response.url.endswith("/api/submit")
            ) as submission_response:
                page.locator("#submit-button").click()

            assert submission_response.value.status == 409
            playwright_api.expect(page.locator("#submit-status")).to_have_text(
                _INCOMPLETE_RECORDING_FAILED_MESSAGE
            )
        finally:
            browser.close()

        state = _json_request(f"{base_url}/api/state")

    assert state["submissions"] == []
