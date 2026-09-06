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

import pytest
from PIL import Image

from pixelgym.tasks.vendor_form.browser_contract import (
    BROWSER_ARGS,
    READY_SELECTOR,
    local_vendor_form_server,
)
from pixelgym.tasks.vendor_form.ui import INCOMPLETE_SUBMISSION_MESSAGE
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
