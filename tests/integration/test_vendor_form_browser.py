"""Opt-in browser checks for the vendor-form submission boundary.

The ordinary fast suite must remain browser-free. Run this module explicitly with
``PIXELGYM_RUN_BROWSER_TESTS=1`` when Chromium is installed for Playwright.
"""

from __future__ import annotations

import json
import os
import urllib.request

import pytest

from pixelgym.evaluator import evaluate
from pixelgym.grounding.capture import local_capture_server
from pixelgym.task_spec import Submission, TaskSpec

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
    playwright_api = pytest.importorskip("playwright.sync_api")

    with local_capture_server() as base_url, playwright_api.sync_playwright() as playwright:
        _json_request(f"{base_url}/api/reset", payload={"seed": 7})
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.goto(base_url, wait_until="networkidle")

            with page.expect_response(
                lambda response: response.url.endswith("/api/submit")
            ) as submission_response:
                page.locator("#submit-button").click()

            assert submission_response.value.ok
            assert (
                page.locator("#submit-status").inner_text()
                == "Complete all required fields before submitting."
            )
        finally:
            browser.close()

        state = _json_request(f"{base_url}/api/state")

    assert len(state["submissions"]) == 1
    assert state["submissions"][0]["values"] == {
        "company_name": "",
        "contact_email": "",
        "contact_phone": "",
        "tax_id": "",
        "country": "",
        "payment_terms": "",
        "expedited_onboarding": False,
    }

    task = TaskSpec.from_generated(
        state["task"],
        instruction="Fill out the form exactly as shown on the request card, then submit.",
        app_url="browser-integration://vendor-form",
        max_episode_steps=200,
    )
    result = evaluate(task, [Submission.from_record(record) for record in state["submissions"]])
    assert result.submitted is True
    assert result.success is False
    assert 0.0 <= result.score < 1.0
    assert set(result.mismatched_fields) == {
        "company_name",
        "contact_email",
        "contact_phone",
        "country",
        "payment_terms",
        "tax_id",
    }
