"""Integrity checks for the checked-in v3a calibration capture evidence.

Ported from the now-deleted `legacy.grounding.calibration_v3a`-backed
`test_checked_capture_artifact_attests_images_tasks_candidates_and_sources`
(issue #170). `CALIBRATION_SEEDS` is inlined below because it was frozen and
verified at git tag `legacy-grounding-final`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CALIBRATION_SEEDS = (20, 21, 22, 23)


def test_v3a_checked_capture_artifact_attests_images_tasks_candidates_and_sources() -> None:
    capture_path = REPOSITORY_ROOT / "artifacts/grounding-v3a-capture.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))

    assert capture["repeatability"]["file_count"] == 20
    assert capture["repeatability"]["byte_identical_file_count"] == 20
    assert capture["repeatability"]["differing_file_count"] == 0

    non_image = capture["non_image_repeatability"]
    assert non_image["matched"] is True
    assert non_image["record_count"] == 20
    assert non_image["reference_aggregate_sha256"] == non_image["candidate_aggregate_sha256"]
    assert len(non_image["tasks"]) == len(CALIBRATION_SEEDS)
    assert {task["task_seed"] for task in non_image["tasks"]} == set(CALIBRATION_SEEDS)

    source_sha256 = capture["source_sha256"]
    assert "pixelgym/grounding/calibration_v3a.py" in source_sha256
    assert "pixelgym/grounding/overlays.py" in source_sha256
    assert all(
        len(digest) == 64 and set(digest) <= set("0123456789abcdef")
        for digest in source_sha256.values()
    )

    manifest = json.loads(
        (REPOSITORY_ROOT / "artifacts/grounding-v3a-manifest.json").read_text(encoding="utf-8")
    )
    expected_capture_sha256 = manifest["outputs"]["capture_evidence"]["sha256"]
    assert hashlib.sha256(capture_path.read_bytes()).hexdigest() == expected_capture_sha256
