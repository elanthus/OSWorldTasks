"""Unit tests for v3a calibration capture module — no browser, no network."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.calibration_v3a import (
    CALIBRATION_CANDIDATE_SCHEMA_VERSION,
    CALIBRATION_EXAMPLE_SCHEMA_VERSION,
    CALIBRATION_SEEDS,
    V3A_PROTOCOL_VERSION,
    calibration_target,
    compare_calibration_non_image_evidence,
    validate_calibration_dataset,
)
from pixelgym.grounding.schema import SCREEN_STATES, TARGET_SPECS, TASK_SEEDS

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_calibration_seeds_are_disjoint_from_frozen_task_seeds() -> None:
    assert set(CALIBRATION_SEEDS).isdisjoint(set(TASK_SEEDS))
    assert CALIBRATION_SEEDS == (20, 21, 22, 23)


def test_calibration_target_uses_crossed_allocation_over_all_seeds() -> None:
    cells: Counter[tuple[str, str]] = Counter()
    target_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()

    for seed in CALIBRATION_SEEDS:
        for state in SCREEN_STATES:
            target = calibration_target(seed, state)
            cells[(target.semantic_id, state)] += 1
            target_counts[target.semantic_id] += 1
            state_counts[state] += 1

    assert len(cells) == 20
    assert all(count == 1 for count in cells.values()), "no duplicate (target, state) cells"
    assert set(state_counts.values()) == {4}
    for seed in CALIBRATION_SEEDS:
        seed_targets = [calibration_target(seed, s).semantic_id for s in SCREEN_STATES]
        assert len(set(seed_targets)) == 5, "each seed gets 5 distinct targets"


def test_calibration_target_rejects_scored_seeds() -> None:
    with pytest.raises(ValueError, match="calibration"):
        calibration_target(0, "initial")
    with pytest.raises(ValueError, match="calibration"):
        calibration_target(19, "initial")


def test_calibration_target_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="state"):
        calibration_target(20, "nonexistent_state")


def _candidate_list() -> list[dict[str, Any]]:
    return [
        {
            "semantic_id": spec.semantic_id,
            "element_type": spec.element_type,
            "visible_label": spec.semantic_id.replace("_", " ").title(),
            "css_bbox": [10.0, 10.0 + i * 40, 474.0, 40.0 + i * 40],
            "bbox": [10, 10 + i * 40, 474, 40 + i * 40],
        }
        for i, spec in enumerate(TARGET_SPECS)
    ]


def _make_calibration_example(
    seed: int, state: str, number: int, *, screen_width: int = 1024, screen_height: int = 768
) -> dict[str, Any]:
    target = calibration_target(seed, state)
    slug = target.semantic_id.replace("_", "-")
    candidates = _candidate_list()
    target_candidate = next(c for c in candidates if c["semantic_id"] == target.semantic_id)
    return {
        "schema_version": CALIBRATION_EXAMPLE_SCHEMA_VERSION,
        "protocol_version": V3A_PROTOCOL_VERSION,
        "example_id": f"vendor-form-v3a-cal-{number:04d}-{slug}",
        "image_path": f"artifacts/grounding-v3a/images/raw/vendor-form-v3a-cal-{number:04d}.png",
        "image_sha256": "a" * 64,
        "target_id": target.semantic_id,
        "target": target.instruction,
        "bbox": list(target_candidate["bbox"]),
        "css_bbox": list(target_candidate["css_bbox"]),
        "element_type": target.element_type,
        "task_seed": seed,
        "task_id": f"task-{seed}",
        "screen_state": state,
        "css_width": 1024,
        "css_height": 768,
        "screen_width": screen_width,
        "screen_height": screen_height,
        "device_scale_factor": 1.0,
        "capture_version": "pixelgym-browser-capture-v1",
    }


def _make_calibration_candidate(example: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": CALIBRATION_CANDIDATE_SCHEMA_VERSION,
        "protocol_version": V3A_PROTOCOL_VERSION,
        "example_id": example["example_id"],
        "candidates": _candidate_list(),
    }


def _build_full_calibration_grid() -> tuple[list[dict], list[dict]]:
    examples = []
    candidates = []
    number = 0
    for seed in CALIBRATION_SEEDS:
        for state in SCREEN_STATES:
            number += 1
            ex = _make_calibration_example(seed, state, number)
            examples.append(ex)
            candidates.append(_make_calibration_candidate(ex))
    return examples, candidates


def test_validate_calibration_dataset_accepts_complete_grid() -> None:
    examples, candidates = _build_full_calibration_grid()
    summary = validate_calibration_dataset(examples, candidates)
    assert summary["example_count"] == 20
    assert summary["candidate_record_count"] == 20
    assert set(summary["screen_state_counts"].values()) == {4}
    assert summary["calibration_label"] == "CALIBRATION"


def test_validate_calibration_dataset_rejects_extra_candidate_record_fields() -> None:
    examples, candidates = _build_full_calibration_grid()
    candidates[0]["target_id"] = examples[0]["target_id"]
    with pytest.raises(ValueError, match="candidate record fields"):
        validate_calibration_dataset(examples, candidates)


def test_validate_calibration_dataset_rejects_wrong_count() -> None:
    examples, candidates = _build_full_calibration_grid()
    with pytest.raises(ValueError, match="20"):
        validate_calibration_dataset(examples[:-1], candidates[:-1])


def test_validate_calibration_dataset_rejects_scored_seeds() -> None:
    examples, candidates = _build_full_calibration_grid()
    examples[0]["task_seed"] = 0
    with pytest.raises(ValueError, match="calibration"):
        validate_calibration_dataset(examples, candidates)


def test_non_image_repeatability_compares_tasks_and_candidates() -> None:
    record = {
        "example_id": "cell-1",
        "task_seed": 20,
        "task_id": "vf-test",
        "canonical_task_json": '{"seed":20}',
        "canonical_task_sha256": "a" * 64,
        "candidate_record": {
            "schema_version": CALIBRATION_CANDIDATE_SCHEMA_VERSION,
            "protocol_version": V3A_PROTOCOL_VERSION,
            "example_id": "cell-1",
            "candidates": _candidate_list(),
        },
    }
    summary = compare_calibration_non_image_evidence([record], [record])
    assert summary["matched"] is True
    assert summary["record_count"] == 1
    assert summary["reference_aggregate_sha256"] == summary["candidate_aggregate_sha256"]

    changed_task = [{**record, "task_id": "vf-other"}]
    with pytest.raises(RuntimeError, match="non-image evidence"):
        compare_calibration_non_image_evidence([record], changed_task)

    changed_candidates = [
        {
            **record,
            "candidate_record": {
                **record["candidate_record"],
                "candidates": record["candidate_record"]["candidates"][:-1],
            },
        }
    ]
    with pytest.raises(RuntimeError, match="non-image evidence"):
        compare_calibration_non_image_evidence([record], changed_candidates)


def test_checked_capture_artifact_attests_images_tasks_candidates_and_sources() -> None:
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

    for relative_path, expected_sha256 in capture["source_sha256"].items():
        actual_sha256 = hashlib.sha256((REPOSITORY_ROOT / relative_path).read_bytes()).hexdigest()
        assert actual_sha256 == expected_sha256, relative_path

    manifest = json.loads(
        (REPOSITORY_ROOT / "artifacts/grounding-v3a-manifest.json").read_text(encoding="utf-8")
    )
    expected_capture_sha256 = manifest["outputs"]["capture_evidence"]["sha256"]
    assert hashlib.sha256(capture_path.read_bytes()).hexdigest() == expected_capture_sha256


def test_validate_calibration_dataset_rejects_wrong_target_allocation() -> None:
    examples, candidates = _build_full_calibration_grid()
    examples[0]["target_id"] = "submit"
    examples[0]["target"] = "Click the Submit button"
    examples[0]["element_type"] = "button"
    with pytest.raises(ValueError, match="allocation"):
        validate_calibration_dataset(examples, candidates)


def test_calibration_example_id_format() -> None:
    target = calibration_target(20, "initial")
    slug = target.semantic_id.replace("_", "-")
    example = _make_calibration_example(20, "initial", 1)
    assert example["example_id"] == f"vendor-form-v3a-cal-0001-{slug}"
    assert example["protocol_version"] == V3A_PROTOCOL_VERSION
    assert example["schema_version"] == CALIBRATION_EXAMPLE_SCHEMA_VERSION
