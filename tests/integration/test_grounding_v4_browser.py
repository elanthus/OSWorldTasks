"""Opt-in deterministic browser capture for the v4 pilot."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pixelgym.grounding.calibration_v4 import capture_v4_calibration_dataset

pytestmark = [
    pytest.mark.browser_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_BROWSER_TESTS") != "1",
        reason="set PIXELGYM_RUN_BROWSER_TESTS=1 to run browser integration tests",
    ),
]


def test_v4_capture_is_bitwise_repeatable_and_target_complete() -> None:
    pytest.importorskip("playwright.sync_api")
    repository_root = Path(__file__).resolve().parents[2]
    manifest = capture_v4_calibration_dataset(repository_root)
    capture = manifest["outputs"]["capture_evidence"]
    assert manifest["calibration_example_count"] == 10
    assert manifest["condition_call_cap"] == 20
    assert capture["path"] == "artifacts/grounding-v4-pilot-capture.json"
