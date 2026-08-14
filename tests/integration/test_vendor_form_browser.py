"""Opt-in browser checks for the vendor-form submission boundary.

The ordinary fast suite must remain browser-free. Run this module explicitly with
``PIXELGYM_RUN_BROWSER_TESTS=1`` when Chromium is installed for Playwright.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pytest

from pixelgym.grounding.capture import local_capture_server
from pixelgym.tasks.vendor_form.ui import INCOMPLETE_SUBMISSION_MESSAGE
from pixelgym.validation.browser_boundary import (
    browser_boundary_evidence_passed,
    browser_boundary_source_hashes_match,
    validate_browser_boundary,
)

_READY_SELECTOR = 'body[data-pixelgym-ready="true"]'
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

    with local_capture_server() as base_url, playwright_api.sync_playwright() as playwright:
        _json_request(f"{base_url}/api/reset", payload={"seed": 7})
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.goto(base_url)
            page.locator(_READY_SELECTOR).wait_for(state="attached")
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
