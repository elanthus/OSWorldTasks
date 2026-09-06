"""Opt-in browser checks for the vendor-form submission boundary.

The ordinary fast suite must remain browser-free. Run this module explicitly with
``PIXELGYM_RUN_BROWSER_TESTS=1`` when Chromium is installed for Playwright.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.tasks.vendor_form.browser_contract import (
    BROWSER_ARGS,
    READY_SELECTOR,
    local_vendor_form_server,
)
from pixelgym.tasks.vendor_form.ui import (
    DESIGN_HEIGHT,
    DESIGN_WIDTH,
    INCOMPLETE_SUBMISSION_MESSAGE,
    TEXT_WIDGETS,
    Layout,
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
_READY_ERROR_SELECTOR = "body[data-pixelgym-ready-error]"
_DETERMINISTIC_FONT = '"PixelGym Sans"'
_FONT_FILENAMES = {"DejaVuSans.ttf", "DejaVuSans-Bold.ttf"}
_FONT_FACE_RULE = re.compile(r"@font-face\s*\{[^}]*\}", re.DOTALL)

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


def _assert_fixed_width_controls_do_not_clip(page: Any, values: list[str]) -> None:
    widths = page.locator("#payment_terms label").evaluate_all(
        "elements => elements.map(element => ({scroll: element.scrollWidth, client: element.clientWidth}))"
    )
    assert len(widths) == len(values)
    for index, (value, width) in enumerate(zip(values, widths, strict=True)):
        assert width["scroll"] <= width["client"], (
            f"payment_terms[{index}]={value}: label content width {width['scroll']} "
            f"exceeds client width {width['client']}"
        )

    for name, selector in (
        ("expedited_onboarding", ".checkbox-row label"),
        ("submit", "#submit-button"),
    ):
        width = page.locator(selector).evaluate(
            "element => ({scroll: element.scrollWidth, client: element.clientWidth})"
        )
        assert width["scroll"] <= width["client"], (
            f"{name}: content width {width['scroll']} exceeds client width {width['client']}"
        )


def _assert_rect_matches_browser(name: str, actual: dict[str, float], expected: Rect) -> None:
    for field in ("x", "y", "width", "height"):
        browser_value = actual[field]
        layout_value = getattr(expected, field)
        assert abs(browser_value - layout_value) <= 0.5, (
            f"{name}: browser {field}={browser_value} differs from "
            f"Layout {field}={layout_value} by more than 0.5 CSS px"
        )


def _assert_layout_matches_browser(
    page: Any, task: dict
) -> tuple[Layout, list[dict[str, float]]]:
    layout = layout_for(task, DESIGN_WIDTH, DESIGN_HEIGHT)
    browser_widgets, browser_options = _browser_rects(page)

    for widget in WidgetId:
        _assert_rect_matches_browser(widget.value, browser_widgets[widget], layout.controls[widget])

    payment_values = task["options"]["payment_terms"]
    assert len(browser_options) == len(payment_values) == len(layout.payment_options)
    _assert_fixed_width_controls_do_not_clip(page, payment_values)
    for index, (value, browser_rect, layout_rect) in enumerate(
        zip(payment_values, browser_options, layout.payment_options, strict=True)
    ):
        _assert_rect_matches_browser(
            f"payment_terms[{index}]={value}", browser_rect, layout_rect
        )

    return layout, browser_options


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
            layout, _browser_options = _assert_layout_matches_browser(page, task)

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
            varied_values = ["N", "Due on receipt", "Net 123456789"]
            page.evaluate(
                """values => document.querySelectorAll("#payment_terms label").forEach(
                  (label, index) => label.lastChild.textContent = values[index]
                )""",
                varied_values,
            )
            _widgets, browser_options = _browser_rects(page)
            _assert_fixed_width_controls_do_not_clip(page, varied_values)
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
            widget = WidgetId.CONTACT_EMAIL
            page.add_style_tag(content="#contact_email { position: relative; left: 1px; }")

            with pytest.raises(AssertionError, match=widget.value):
                _assert_layout_matches_browser(page, task)
        finally:
            browser.close()


def test_country_and_keyboard_focus_contract_matches_browser() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")
    assert {"c", " ", "Enter", "Tab"} <= set(KEY_ALLOWLIST)

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
            country = page.locator("#country")
            value_before_space = country.input_value()
            page.keyboard.press("Space")
            assert page.evaluate("document.activeElement.id") == "country"
            assert country.input_value() == value_before_space

            page.keyboard.press("c")
            page.keyboard.press("Enter")
            assert country.input_value() == task["options"]["country"][2]
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


def _assert_label_text_pixels(
    image_bytes: bytes,
    bounding_box: dict[str, float],
    *,
    background: tuple[int, int, int],
) -> None:
    with Image.open(BytesIO(image_bytes)) as image:
        rgb = image.convert("RGB")
        left = int(bounding_box["x"])
        top = int(bounding_box["y"])
        right = int(bounding_box["x"] + bounding_box["width"])
        bottom = int(bounding_box["y"] + bounding_box["height"])
        crop_bytes = rgb.crop((left, top, right, bottom)).tobytes()
        background_bytes = bytes(background)
        assert (
            sum(
                crop_bytes[offset : offset + 3] != background_bytes
                for offset in range(0, len(crop_bytes), 3)
            )
            > 20
        )


def test_ready_waits_for_exact_deterministic_fonts_and_rendered_text() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")

    with local_vendor_form_server() as base_url, playwright_api.sync_playwright() as playwright:
        _json_request(f"{base_url}/api/reset", payload={"seed": 7})
        browser = playwright.chromium.launch(headless=True, args=list(BROWSER_ARGS))
        try:
            context = browser.new_context(viewport={"width": 1024, "height": 768})
            page = context.new_page()
            held_font_routes = {}
            page_ready_posts = []
            page.on(
                "request",
                lambda request: page_ready_posts.append(request)
                if request.method == "POST" and request.url.endswith("/api/page-ready")
                else None,
            )
            page.add_init_script(
                """
                window.__pixelgymReadySetCount = 0;
                document.addEventListener("DOMContentLoaded", () => {
                  window.__pixelgymReadyObserver = new MutationObserver((records) => {
                    for (const record of records) {
                      if (record.target.dataset.pixelgymReady === "true") {
                        window.__pixelgymReadySetCount += 1;
                      }
                    }
                  });
                  window.__pixelgymReadyObserver.observe(document.body, {
                    attributes: true,
                    attributeFilter: ["data-pixelgym-ready"],
                  });
                }, { once: true });
                """
            )
            def hold_font(route):
                held_font_routes[route.request.url.rsplit("/", 1)[-1]] = route

            page.route("**/*.ttf", hold_font)

            with (
                page.expect_request("**/DejaVuSans.ttf"),
                page.expect_request("**/DejaVuSans-Bold.ttf"),
            ):
                page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_function("() => document.fonts.status === 'loading'")
            page.wait_for_function(
                "() => document.getElementById('rc-company_name').textContent.length > 0"
            )

            assert set(held_font_routes) == _FONT_FILENAMES
            assert page.locator(READY_SELECTOR).count() == 0
            assert page.locator(_READY_ERROR_SELECTOR).count() == 0
            assert _json_request(f"{base_url}/api/page-ready") == {"ready": False}
            assert page_ready_posts == []

            for route in held_font_routes.values():
                route.continue_()
            page.locator(READY_SELECTOR).wait_for(state="attached")

            assert _json_request(f"{base_url}/api/page-ready") == {"ready": True}
            assert len(page_ready_posts) == 1
            assert page.evaluate("() => window.__pixelgymReadySetCount") == 1
            assert page.evaluate("() => getComputedStyle(document.body).fontFamily") == (
                _DETERMINISTIC_FONT
            )
            assert page.evaluate(
                "() => document.fonts.check('14px \\\"PixelGym Sans\\\"')"
            )
            assert page.evaluate(
                "() => document.fonts.check('bold 14px \\\"PixelGym Sans\\\"')"
            )

            screenshot = page.screenshot(type="png", animations="disabled")
            request_label_box = page.locator('label[for="rc-company_name"]').bounding_box()
            form_label_box = page.locator('label[for="company_name"]').bounding_box()
            assert request_label_box is not None
            assert form_label_box is not None
            _assert_label_text_pixels(screenshot, request_label_box, background=(238, 241, 246))
            _assert_label_text_pixels(screenshot, form_label_box, background=(255, 255, 255))
        finally:
            browser.close()


def test_missing_font_faces_end_in_font_load_error_never_ready() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")

    with local_vendor_form_server() as base_url, playwright_api.sync_playwright() as playwright:
        _json_request(f"{base_url}/api/reset", payload={"seed": 7})
        browser = playwright.chromium.launch(headless=True, args=list(BROWSER_ARGS))
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})

            def remove_font_faces(route):
                response = route.fetch()
                stylesheet, count = _FONT_FACE_RULE.subn("", response.text())
                assert count == 2
                route.fulfill(response=response, body=stylesheet)

            page.route("**/static/style.css", remove_font_faces)
            page.goto(base_url, wait_until="domcontentloaded")
            page.locator(
                'body[data-pixelgym-ready-error="font-load"]'
            ).wait_for(state="attached")

            assert page.locator(READY_SELECTOR).count() == 0
            assert _json_request(f"{base_url}/api/page-ready") == {"ready": False}
        finally:
            browser.close()


@pytest.mark.parametrize("failure_stage", ["task-fetch", "render", "font-load", "page-ready"])
def test_initialization_failure_never_sets_ready(failure_stage: str) -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")

    with local_vendor_form_server() as base_url, playwright_api.sync_playwright() as playwright:
        _json_request(f"{base_url}/api/reset", payload={"seed": 7})
        browser = playwright.chromium.launch(headless=True, args=list(BROWSER_ARGS))
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            if failure_stage == "task-fetch":
                page.route("**/api/task", lambda route: route.abort())
            elif failure_stage == "render":
                malformed_task = _json_request(f"{base_url}/api/task")
                malformed_task["options"]["country"] = None
                page.route(
                    "**/api/task",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(malformed_task),
                    ),
                )
            elif failure_stage == "font-load":
                page.route("**/*.ttf", lambda route: route.abort())
            else:
                page.route("**/api/page-ready", lambda route: route.abort())

            page.goto(base_url, wait_until="domcontentloaded")
            page.locator(
                f'body[data-pixelgym-ready-error="{failure_stage}"]'
            ).wait_for(state="attached")

            assert page.locator(READY_SELECTOR).count() == 0
            assert _json_request(f"{base_url}/api/page-ready") == {"ready": False}
        finally:
            browser.close()


def test_fresh_document_starts_without_stale_ready_marker_after_reset() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")

    with local_vendor_form_server() as base_url, playwright_api.sync_playwright() as playwright:
        _json_request(f"{base_url}/api/reset", payload={"seed": 7})
        browser = playwright.chromium.launch(headless=True, args=list(BROWSER_ARGS))
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.goto(base_url)
            page.locator(READY_SELECTOR).wait_for(state="attached")
            assert _json_request(f"{base_url}/api/page-ready") == {"ready": True}

            _json_request(f"{base_url}/api/reset", payload={"seed": 7})
            held_task_routes = []
            page.route("**/api/task", lambda route: held_task_routes.append(route))
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("() => document.readyState === 'complete'")

            assert len(held_task_routes) == 1
            assert page.locator(READY_SELECTOR).count() == 0
            assert page.locator(_READY_ERROR_SELECTOR).count() == 0
            assert _json_request(f"{base_url}/api/page-ready") == {"ready": False}

            held_task_routes[0].continue_()
            page.locator(READY_SELECTOR).wait_for(state="attached")
            assert _json_request(f"{base_url}/api/page-ready") == {"ready": True}
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


def test_reset_requires_reload_to_render_the_new_task() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")

    with local_vendor_form_server() as base_url, playwright_api.sync_playwright() as playwright:
        first_reset = _json_request(f"{base_url}/api/reset", payload={"seed": 7})
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.goto(base_url)
            page.locator(READY_SELECTOR).wait_for(state="attached")
            assert page.locator("#task_id").input_value() == first_reset["task_id"]

            second_reset = _json_request(f"{base_url}/api/reset", payload={"seed": 8})
            second_task = _json_request(f"{base_url}/api/task")
            assert second_reset == {
                "task_id": second_task["task_id"],
                "seed": 8,
                "requires_reload": True,
            }
            assert page.locator("#task_id").input_value() == first_reset["task_id"]
            assert _json_request(f"{base_url}/api/page-ready") == {"ready": False}

            page.reload()
            page.locator(READY_SELECTOR).wait_for(state="attached")

            assert page.locator("#task_id").input_value() == second_task["task_id"]
            for field_name, value in second_task["fields"].items():
                expected = "Yes" if value is True else "No" if value is False else value
                playwright_api.expect(page.locator(f"#rc-{field_name}")).to_have_text(expected)
        finally:
            browser.close()
