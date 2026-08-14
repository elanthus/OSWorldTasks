from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from pixelgym.grounding.capture import (
    CAPTURE_SUMMARY_SCHEMA_VERSION,
    build_contact_sheet,
    refresh_capture_summary,
    validate_dataset,
)
from pixelgym.grounding.schema import (
    CANDIDATE_SCHEMA_VERSION,
    CAPTURE_VERSION,
    EXAMPLE_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    SCREEN_STATES,
    TASK_SEEDS,
    target_for_example_index,
)


def _synthetic_dataset(root: Path) -> tuple[list[dict], list[dict]]:
    image_dir = root / "artifacts" / "grounding" / "images" / "raw"
    image_dir.mkdir(parents=True)
    examples = []
    candidate_records = []
    index = 0
    for seed in TASK_SEEDS:
        for state in SCREEN_STATES:
            target = target_for_example_index(index)
            image_path = image_dir / f"vendor-form-{index + 1:04d}.png"
            Image.new("RGB", (1024, 768), (index % 255, 20, 30)).save(image_path)
            image_bytes = image_path.read_bytes()
            example_id = f"vendor-form-{index + 1:04d}-{target.semantic_id}"
            bbox = [10, 10, 20, 20]
            examples.append(
                {
                    "schema_version": EXAMPLE_SCHEMA_VERSION,
                    "protocol_version": PROTOCOL_VERSION,
                    "example_id": example_id,
                    "image_path": image_path.relative_to(root).as_posix(),
                    "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
                    "target_id": target.semantic_id,
                    "target": target.instruction,
                    "bbox": bbox,
                    "css_bbox": [10.0, 10.0, 20.0, 20.0],
                    "element_type": target.element_type,
                    "task_seed": seed,
                    "task_id": f"vf-{seed:016x}",
                    "screen_state": state,
                    "css_width": 1024,
                    "css_height": 768,
                    "screen_width": 1024,
                    "screen_height": 768,
                    "device_scale_factor": 1.0,
                    "capture_version": CAPTURE_VERSION,
                }
            )
            candidate_records.append(
                {
                    "schema_version": CANDIDATE_SCHEMA_VERSION,
                    "protocol_version": PROTOCOL_VERSION,
                    "example_id": example_id,
                    "candidates": [
                        {
                            "semantic_id": target.semantic_id,
                            "element_type": target.element_type,
                            "visible_label": target.instruction.removeprefix("Click the "),
                            "css_bbox": [10.0, 10.0, 20.0, 20.0],
                            "bbox": bbox,
                        }
                    ],
                }
            )
            index += 1
    return examples, candidate_records


def test_dataset_validation_checks_balancing_hashes_and_target_join(tmp_path: Path) -> None:
    examples, candidates = _synthetic_dataset(tmp_path)

    summary = validate_dataset(examples, candidates, repository_root=tmp_path)

    assert summary["example_count"] == 100
    assert set(summary["target_counts"].values()) == {10}
    assert set(summary["screen_state_counts"].values()) == {20}
    assert summary["target_screen_state_perfect_aliasing"] is True
    assert all(len(states) == 1 for states in summary["target_screen_states"].values())
    assert summary["summary_schema_version"] == CAPTURE_SUMMARY_SCHEMA_VERSION
    assert summary["automatic_integrity_checks_passed"] is True
    assert summary["known_design_limitations"] == [
        {
            "code": "target_screen_state_perfect_aliasing",
            "effect": (
                "target identity and screen state effects are not independently identifiable"
            ),
        }
    ]
    assert "automatic_checks_passed" not in summary

    candidates[0]["candidates"][0]["semantic_id"] = "wrong"
    with pytest.raises(ValueError, match="target"):
        validate_dataset(examples, candidates, repository_root=tmp_path)


def test_capture_summary_can_be_refreshed_without_recapturing_images(tmp_path: Path) -> None:
    examples, candidates = _synthetic_dataset(tmp_path)
    artifact_root = tmp_path / "artifacts"
    (artifact_root / "grounding-dataset.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in examples), encoding="utf-8"
    )
    (artifact_root / "grounding-candidates.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in candidates), encoding="utf-8"
    )
    capture_path = artifact_root / "grounding-capture.json"
    capture_path.write_text(
        json.dumps(
            {
                "browser_engine": "chromium",
                "browser_version": "test-browser",
                "browser_args": ["--test-flag"],
            }
        ),
        encoding="utf-8",
    )
    image_hashes_before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (artifact_root / "grounding/images/raw").glob("*.png")
    }

    summary = refresh_capture_summary(tmp_path)

    image_hashes_after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (artifact_root / "grounding/images/raw").glob("*.png")
    }
    assert image_hashes_after == image_hashes_before
    assert summary["target_screen_state_perfect_aliasing"] is True
    assert summary["summary_provenance"] == {
        "mode": "offline_revalidation_of_frozen_capture",
        "browser_recapture_performed": False,
    }
    assert json.loads(capture_path.read_text()) == summary


def test_contact_sheet_is_deterministic(tmp_path: Path) -> None:
    examples, _ = _synthetic_dataset(tmp_path)
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"

    build_contact_sheet(examples, repository_root=tmp_path, output_path=first)
    build_contact_sheet(examples, repository_root=tmp_path, output_path=second)

    assert first.read_bytes() == second.read_bytes()
    with Image.open(first) as image:
        assert image.size == (1280, 4800)
