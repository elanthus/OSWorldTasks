"""Opt-in browser check for duplicate clicks on the control-plane submit form (#165).

Server-side resubmission idempotency is unit-tested in tests/unit/platform/test_service_web.py.
This module covers what only a real browser can: a second click while the first POST is still
in flight must not send another request. It serves the real control app on loopback from an
isolated control store and drives it with Playwright Chromium. No Docker, MLflow, Metaflow,
provider, or external network is used. Run explicitly with ``PIXELGYM_RUN_BROWSER_TESTS=1``
when Playwright Chromium is installed:

    PIXELGYM_RUN_BROWSER_TESTS=1 pytest tests/integration/platform/test_web_browser_submission.py
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.web import create_control_app

playwright_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

pytestmark = [
    pytest.mark.browser_integration,
    pytest.mark.local_http_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_BROWSER_TESTS") != "1",
        reason="set PIXELGYM_RUN_BROWSER_TESTS=1 to run browser integration tests",
    ),
]

CSRF_SECRET = "browser-test-csrf-secret-local-only"


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _serve(app: Any) -> Iterator[str]:
    """Serve the control app on loopback and always shut it down."""
    port = _free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, name="control-web-browser-server", daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{base_url}/static/platform.css", timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.02)
    else:
        server.should_exit = True
        thread.join(timeout=2.0)
        raise RuntimeError("control web browser server did not become ready")
    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("control web browser server did not shut down")


def _events(control: ControlStore, event_type: str) -> list[dict[str, Any]]:
    return [item for item in control.audit_events() if item["event_type"] == event_type]


def test_repeated_click_submission_is_sent_once_and_resubmission_is_idempotent(
    tmp_path: Path,
) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    app = create_control_app(
        control, csrf_secret=CSRF_SECRET, submit_callback=lambda submission_id, payload: None
    )
    with _serve(app) as url, playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except playwright_api.Error as default_error:
            # Fall back to the full Chromium build when only it (not the headless shell)
            # is installed for this Playwright release.
            try:
                browser = playwright.chromium.launch(headless=True, channel="chromium")
            except playwright_api.Error:
                pytest.skip(f"Playwright Chromium is not installed: {default_error}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(10_000)

            # Hold the first POST in flight so a second click lands while the page is still
            # open, as it does for an impatient reviewer on a slow evaluation start.
            held: list[Any] = []
            page.route(f"{url}/experiments", lambda route: held.append(route))
            page.goto(url)
            button = page.get_by_role("button", name="Submit fixed evaluation")
            box = button.bounding_box()
            assert box is not None
            centre = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.click(*centre)
            page.wait_for_timeout(300)
            page.mouse.click(*centre)
            page.wait_for_timeout(300)
            assert len(held) == 1
            held[0].continue_()
            page.wait_for_url(f"{url}/submissions/submission-*")
            page.unroute(f"{url}/experiments")
            first_url = page.url
            assert len(_events(control, "submission.created")) == 1
            assert _events(control, "submission.resubmitted") == []

            page.goto(url)
            page.get_by_role("button", name="Submit fixed evaluation").click()
            page.wait_for_url(f"{url}/submissions/submission-*")
            assert page.url == first_url
            assert len(_events(control, "submission.created")) == 1
            assert len(_events(control, "submission.resubmitted")) == 1

            submission_id = first_url.rsplit("/", 1)[1]
            page.goto(f"{url}/submissions")
            playwright_api.expect(
                page.locator(f'a[href="/submissions/{submission_id}"]')
            ).to_have_count(1)
        finally:
            browser.close()
