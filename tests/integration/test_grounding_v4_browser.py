"""Opt-in deterministic browser capture for the v4 pilot."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from legacy.grounding.calibration_v4 import capture_v4_calibration_dataset
from legacy.grounding.v4_protocol import V4_CONDITION_CALL_CAP

pytestmark = [
    pytest.mark.browser_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_BROWSER_TESTS") != "1",
        reason="set PIXELGYM_RUN_BROWSER_TESTS=1 to run browser integration tests",
    ),
]


def test_v4_capture_is_bitwise_repeatable_and_target_complete() -> None:
    """Regenerate repository artifacts and verify the resulting capture evidence."""
    pytest.importorskip("playwright.sync_api")
    repository_root = Path(__file__).resolve().parents[2]
    manifest = capture_v4_calibration_dataset(repository_root)
    capture = manifest["outputs"]["capture_evidence"]
    assert manifest["calibration_example_count"] == 10
    assert manifest["condition_call_cap"] == V4_CONDITION_CALL_CAP
    assert capture["path"] == "artifacts/grounding-v4-pilot-capture.json"
    evidence = json.loads((repository_root / capture["path"]).read_text(encoding="utf-8"))
    repeatability = evidence["repeatability"]
    assert repeatability["differing_file_count"] == 0
    assert repeatability["differing_pixel_count"] == 0
    assert repeatability["byte_identical_file_count"] == repeatability["file_count"]
    assert (
        repeatability["reference_aggregate_sha256"]
        == repeatability["candidate_aggregate_sha256"]
    )
    assert evidence["family_counts"] == {
        "cross_panel_policy": 3,
        "recovery_state": 3,
        "relational_table": 4,
    }
