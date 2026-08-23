"""Fast structural tests for the v4 pilot; no browser or network."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest

from pixelgym.grounding.calibration_v4 import (
    V4_CALIBRATION_SEEDS,
    V4_CANDIDATE_IDS,
    V4_CANDIDATE_SCHEMA_VERSION,
    V4_EXAMPLE_SCHEMA_VERSION,
    V4_EXPECTED_CANDIDATE_COUNT,
    V4_TARGET_FAMILIES,
    V4_TARGET_SPECS,
    require_v4_bitwise_repeatability,
    v4_calibration_target,
    v4_example_id,
    validate_v4_calibration_dataset,
)
from pixelgym.grounding.schema import SCREEN_STATES, TASK_SEEDS
from pixelgym.grounding.v4_protocol import V4_PROTOCOL_VERSION


def test_v4_has_ten_unique_targets_and_disjoint_calibration_seeds() -> None:
    assert V4_CALIBRATION_SEEDS == (30, 31)
    assert set(V4_CALIBRATION_SEEDS).isdisjoint(TASK_SEEDS)
    assert len(V4_TARGET_SPECS) == 10
    assert len({target.semantic_id for target in V4_TARGET_SPECS}) == 10


def test_v4_frozen_family_mix_is_four_three_three() -> None:
    counts = Counter(V4_TARGET_FAMILIES.values())
    assert counts == {
        "relational_table": 4,
        "cross_panel_policy": 3,
        "recovery_state": 3,
    }


def test_v4_allocation_uses_every_target_once() -> None:
    allocated = [
        v4_calibration_target(seed, state).semantic_id
        for seed in V4_CALIBRATION_SEEDS
        for state in SCREEN_STATES
    ]
    assert allocated == [target.semantic_id for target in V4_TARGET_SPECS]
    assert len(set(allocated)) == 10


def test_v4_target_rejects_scored_unknown_seed_and_state() -> None:
    with pytest.raises(ValueError, match="overlaps"):
        v4_calibration_target(0, "initial")
    with pytest.raises(ValueError, match="v4 calibration"):
        v4_calibration_target(99, "initial")
    with pytest.raises(ValueError, match="state"):
        v4_calibration_target(30, "unknown")


def _candidate_type(semantic_id: str) -> str:
    if semantic_id.startswith(("nav_", "sb_", "name_")):
        return "link"
    if semantic_id in {"billing_email", "vat_number", "search_input"}:
        return "text_input"
    return "button"


def _candidate_list() -> list[dict[str, Any]]:
    assert len(V4_CANDIDATE_IDS) == V4_EXPECTED_CANDIDATE_COUNT
    return [
        {
            "semantic_id": semantic_id,
            "element_type": _candidate_type(semantic_id),
            "visible_label": semantic_id.replace("_", " "),
            "css_bbox": [10.0, 10.0 + index * 12, 180.0, 20.0 + index * 12],
            "bbox": [10, 10 + index * 12, 180, 20 + index * 12],
        }
        for index, semantic_id in enumerate(V4_CANDIDATE_IDS)
    ]


def _example(seed: int, state: str, number: int) -> dict[str, Any]:
    target = v4_calibration_target(seed, state)
    target_candidate = next(
        candidate
        for candidate in _candidate_list()
        if candidate["semantic_id"] == target.semantic_id
    )
    return {
        "schema_version": V4_EXAMPLE_SCHEMA_VERSION,
        "protocol_version": V4_PROTOCOL_VERSION,
        "example_id": v4_example_id(seed, state),
        "image_path": f"artifacts/grounding-v4-pilot/images/raw/v4-{number:04d}.png",
        "image_sha256": "a" * 64,
        "target_id": target.semantic_id,
        "target": target.instruction,
        "target_family": V4_TARGET_FAMILIES[target.semantic_id],
        "bbox": list(target_candidate["bbox"]),
        "css_bbox": list(target_candidate["css_bbox"]),
        "element_type": target.element_type,
        "task_seed": seed,
        "task_id": f"v4-{seed}",
        "screen_state": state,
        "css_width": 1024,
        "css_height": 768,
        "screen_width": 1024,
        "screen_height": 768,
        "device_scale_factor": 1.0,
        "capture_version": "pixelgym-browser-capture-v1",
    }


def _grid() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    examples = []
    records = []
    number = 0
    for seed in V4_CALIBRATION_SEEDS:
        for state in SCREEN_STATES:
            number += 1
            example = _example(seed, state, number)
            examples.append(example)
            records.append(
                {
                    "schema_version": V4_CANDIDATE_SCHEMA_VERSION,
                    "protocol_version": V4_PROTOCOL_VERSION,
                    "example_id": example["example_id"],
                    "candidates": _candidate_list(),
                }
            )
    return examples, records


def test_validate_v4_accepts_exact_grid_and_reports_family_mix() -> None:
    examples, records = _grid()
    summary = validate_v4_calibration_dataset(examples, records)
    assert summary["example_count"] == 10
    assert summary["candidate_record_count"] == 10
    assert summary["family_counts"] == {
        "cross_panel_policy": 3,
        "recovery_state": 3,
        "relational_table": 4,
    }
    assert summary["expected_candidate_count"] == 39


def test_validate_v4_rejects_wrong_count_and_duplicate_example_id() -> None:
    examples, records = _grid()
    with pytest.raises(ValueError, match="exactly 10"):
        validate_v4_calibration_dataset(examples[:-1], records[:-1])
    examples[1]["example_id"] = examples[0]["example_id"]
    with pytest.raises(ValueError, match="duplicate v4 example_id"):
        validate_v4_calibration_dataset(examples, records)


def test_validate_v4_rejects_wrong_allocation() -> None:
    examples, records = _grid()
    examples[0]["target_id"] = "edit_pacific"
    with pytest.raises(ValueError, match="frozen allocation"):
        validate_v4_calibration_dataset(examples, records)


def test_validate_v4_rejects_extra_candidate_record_fields() -> None:
    examples, records = _grid()
    records[0]["target_id"] = examples[0]["target_id"]
    with pytest.raises(ValueError, match="fields do not match"):
        validate_v4_calibration_dataset(examples, records)


def test_candidate_record_join_ids_are_opaque_and_target_free() -> None:
    examples, records = _grid()
    validate_v4_calibration_dataset(examples, records)

    for example, record in zip(examples, records, strict=True):
        assert record["example_id"] == example["example_id"]
        assert record["example_id"] == v4_example_id(
            example["task_seed"], example["screen_state"]
        )
        top_level = {key: value for key, value in record.items() if key != "candidates"}
        serialized_top_level = json.dumps(top_level, sort_keys=True)
        assert example["target_id"] not in serialized_top_level
        assert set(record) == {
            "schema_version",
            "protocol_version",
            "example_id",
            "candidates",
        }


def test_validate_v4_rejects_missing_or_extra_candidate() -> None:
    examples, records = _grid()
    records[0]["candidates"] = records[0]["candidates"][:-1]
    with pytest.raises(ValueError, match="candidate count"):
        validate_v4_calibration_dataset(examples, records)

    examples, records = _grid()
    records[0]["candidates"][-1]["semantic_id"] = "unexpected_control"
    with pytest.raises(ValueError, match="frozen set"):
        validate_v4_calibration_dataset(examples, records)


def test_v4_repeatability_guard_fails_closed() -> None:
    repeatability = {
        "file_count": 10,
        "byte_identical_file_count": 10,
        "differing_file_count": 0,
        "differing_pixel_count": 0,
    }
    require_v4_bitwise_repeatability(repeatability)

    for field, value in (
        ("file_count", 0),
        ("file_count", 9),
        ("byte_identical_file_count", 9),
        ("differing_file_count", 1),
        ("differing_pixel_count", 1),
    ):
        changed = {**repeatability, field: value}
        with pytest.raises(RuntimeError, match="not bitwise repeatable"):
            require_v4_bitwise_repeatability(changed)


def test_every_v4_target_is_in_target_independent_candidate_set() -> None:
    candidate_ids = set(V4_CANDIDATE_IDS)
    assert set(V4_TARGET_FAMILIES) <= candidate_ids
    assert len(candidate_ids) == V4_EXPECTED_CANDIDATE_COUNT
