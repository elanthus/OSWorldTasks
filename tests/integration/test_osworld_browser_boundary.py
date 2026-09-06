"""Opt-in real-guest evidence for Chromium's navigation-surface boundary."""

from __future__ import annotations

import math
import os
import signal
import time
from pathlib import Path

import pytest

from pixelgym.validation.browser_boundary import (
    guest_browser_boundary_evidence_passed,
    guest_browser_boundary_source_hashes_match,
    validate_guest_browser_boundary,
)

GUEST_IMAGE = Path(".cache/osworld/osworld-v2-ubuntu-x86.qcow2")

pytestmark = [
    pytest.mark.osworld_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_OSWORLD_TESTS") != "1" or not GUEST_IMAGE.is_file(),
        reason="set PIXELGYM_RUN_OSWORLD_TESTS=1 after preparing the pinned OSWorld image",
    ),
]


def test_guest_app_mode_hides_navigation_surface_and_fills_1024x768(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]

    def stop_loss(_signum: int, _frame: object) -> None:
        raise TimeoutError("guest browser-boundary test exceeded the 90-minute stop-loss")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_alarm_seconds = signal.alarm(0)
    alarm_started_at = time.monotonic()
    try:
        signal.signal(signal.SIGALRM, stop_loss)
        signal.alarm(90 * 60)
        evidence = validate_guest_browser_boundary(
            repository_root,
            guest_image_path=GUEST_IMAGE,
            screenshot_path=tmp_path / "guest-app.png",
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_alarm_seconds > 0:
            elapsed_seconds = math.ceil(time.monotonic() - alarm_started_at)
            signal.alarm(max(1, previous_alarm_seconds - elapsed_seconds))

    assert guest_browser_boundary_evidence_passed(evidence) is True
    assert guest_browser_boundary_source_hashes_match(evidence, repository_root) is True
    assert evidence["navigation_surface"]["observation_shape"] == [768, 1024, 3]
    assert evidence["task_app_page_ready"] == {"ready": True}
    assert evidence["browser_launch"]["presentation_mode"] == "app"
