"""Unit tests for v3c calibration capture module — no browser, no network."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from pixelgym.grounding.calibration_v3b import V3B_TARGET_SPECS
from pixelgym.grounding.calibration_v3c import (
    V3C_CALIBRATION_SEEDS,
    V3C_CANDIDATE_SCHEMA_VERSION,
    V3C_EXAMPLE_SCHEMA_VERSION,
    V3C_EXPECTED_CANDIDATE_COUNT,
    V3C_MANIFEST_SCHEMA_VERSION,
    V3C_PROTOCOL_VERSION,
    V3C_TARGET_SPECS,
    v3c_calibration_target,
    validate_v3c_calibration_dataset,
)
from pixelgym.grounding.schema import SCREEN_STATES, TARGET_SPECS, TASK_SEEDS


def test_v3c_calibration_seeds_are_disjoint_from_frozen_task_seeds() -> None:
    assert set(V3C_CALIBRATION_SEEDS).isdisjoint(set(TASK_SEEDS))
    assert V3C_CALIBRATION_SEEDS == (20, 21, 22, 23)


def test_v3c_target_specs_are_distinct_from_v3a_and_v3b() -> None:
    v3a_ids = {spec.semantic_id for spec in TARGET_SPECS}
    v3b_ids = {spec.semantic_id for spec in V3B_TARGET_SPECS}
    v3c_ids = {spec.semantic_id for spec in V3C_TARGET_SPECS}
    assert v3c_ids.isdisjoint(v3a_ids), "v3c targets must not overlap with v3a"
    assert v3c_ids.isdisjoint(v3b_ids), "v3c targets must not overlap with v3b"


def test_v3c_target_specs_have_10_unique_ids() -> None:
    ids = [spec.semantic_id for spec in V3C_TARGET_SPECS]
    assert len(ids) == 10
    assert len(set(ids)) == 10


def test_v3c_target_specs_cover_difficulty_levers() -> None:
    types = Counter(spec.element_type for spec in V3C_TARGET_SPECS)
    assert "button" in types, "need repeated edit/delete buttons"
    assert types["button"] >= 4, "need multiple row-context disambiguation buttons"
    assert "link" in types, "need vendor name links and sort headers"
    assert "text_input" in types, "need search input"


def test_v3c_target_specs_include_repeated_controls() -> None:
    """Key v3c difficulty: targets include edit/delete from different rows."""
    edit_targets = [s for s in V3C_TARGET_SPECS if s.semantic_id.startswith("edit_")]
    delete_targets = [s for s in V3C_TARGET_SPECS if s.semantic_id.startswith("delete_")]
    assert len(edit_targets) >= 2, "need edit buttons from different rows"
    assert len(delete_targets) >= 2, "need delete buttons from different rows"


def test_v3c_calibration_target_uses_crossed_allocation() -> None:
    cells: Counter[tuple[str, str]] = Counter()
    target_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()

    for seed in V3C_CALIBRATION_SEEDS:
        for state in SCREEN_STATES:
            target = v3c_calibration_target(seed, state)
            cells[(target.semantic_id, state)] += 1
            target_counts[target.semantic_id] += 1
            state_counts[state] += 1

    assert len(cells) == 20
    assert all(count == 1 for count in cells.values()), "no duplicate (target, state)"
    assert set(state_counts.values()) == {4}
    for seed in V3C_CALIBRATION_SEEDS:
        seed_targets = [
            v3c_calibration_target(seed, s).semantic_id for s in SCREEN_STATES
        ]
        assert len(set(seed_targets)) == 5, "each seed gets 5 distinct targets"


def test_v3c_calibration_target_rejects_scored_seeds() -> None:
    with pytest.raises(ValueError, match="calibration"):
        v3c_calibration_target(0, "initial")
    with pytest.raises(ValueError, match="calibration"):
        v3c_calibration_target(19, "initial")


def test_v3c_calibration_target_rejects_out_of_range_seeds() -> None:
    with pytest.raises(ValueError, match="v3c calibration"):
        v3c_calibration_target(99, "initial")


def test_v3c_calibration_target_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="state"):
        v3c_calibration_target(20, "nonexistent_state")


def test_v3c_expected_candidate_count() -> None:
    assert V3C_EXPECTED_CANDIDATE_COUNT == 48


def _v3c_candidate_list() -> list[dict[str, Any]]:
    """Build a synthetic candidate list matching the v3c page's 48 elements."""
    all_candidates = [
        # Header nav (5)
        ("nav_vendors", "link", "Vendors"),
        ("nav_contracts", "link", "Contracts"),
        ("nav_reports", "link", "Reports"),
        ("nav_settings", "link", "Settings"),
        ("nav_help", "link", "Help"),
        # Sidebar (8)
        ("sb_dashboard", "link", "Dashboard"),
        ("sb_new_vendor", "link", "New Vendor"),
        ("sb_vendor_list", "link", "Vendor List"),
        ("sb_pending", "link", "Pending Review"),
        ("sb_import", "link", "Import Data"),
        ("sb_export", "link", "Export Data"),
        ("sb_audit", "link", "Audit Log"),
        ("sb_support", "link", "Support"),
        # Search + Add button (2)
        ("search_input", "text_input", "Search vendors..."),
        ("add_vendor", "button", "Add New Vendor"),
        # Column sort headers (4)
        ("sort_name", "link", "Name"),
        ("sort_status", "link", "Status"),
        ("sort_email", "link", "Email"),
        ("sort_country", "link", "Country"),
        # Per-row controls: name + Edit + Delete (3 x 8 = 24)
        ("name_1", "link", "Acme Industries"),
        ("edit_1", "button", "Edit"),
        ("delete_1", "button", "Delete"),
        ("name_2", "link", "GlobalTech Solutions"),
        ("edit_2", "button", "Edit"),
        ("delete_2", "button", "Delete"),
        ("name_3", "link", "Pacific Trading Co"),
        ("edit_3", "button", "Edit"),
        ("delete_3", "button", "Delete"),
        ("name_4", "link", "Nordic Supplies AB"),
        ("edit_4", "button", "Edit"),
        ("delete_4", "button", "Delete"),
        ("name_5", "link", "Meridian Partners"),
        ("edit_5", "button", "Edit"),
        ("delete_5", "button", "Delete"),
        ("name_6", "link", "Atlas Logistics"),
        ("edit_6", "button", "Edit"),
        ("delete_6", "button", "Delete"),
        ("name_7", "link", "Pinnacle Systems"),
        ("edit_7", "button", "Edit"),
        ("delete_7", "button", "Delete"),
        ("name_8", "link", "Coastal Ventures"),
        ("edit_8", "button", "Edit"),
        ("delete_8", "button", "Delete"),
        # Pagination (5)
        ("page_prev", "link", "Previous"),
        ("page_1", "link", "1"),
        ("page_2", "link", "2"),
        ("page_3", "link", "3"),
        ("page_next", "link", "Next"),
    ]
    assert len(all_candidates) == V3C_EXPECTED_CANDIDATE_COUNT
    return [
        {
            "semantic_id": sem_id,
            "element_type": elem_type,
            "visible_label": label,
            "css_bbox": [10.0, 10.0 + i * 14, 200.0, 24.0 + i * 14],
            "bbox": [10, 10 + i * 14, 200, 24 + i * 14],
        }
        for i, (sem_id, elem_type, label) in enumerate(all_candidates)
    ]


def _make_v3c_example(
    seed: int,
    state: str,
    number: int,
    *,
    screen_width: int = 1024,
    screen_height: int = 768,
) -> dict[str, Any]:
    target = v3c_calibration_target(seed, state)
    slug = target.semantic_id.replace("_", "-")
    candidates = _v3c_candidate_list()
    target_candidate = next(
        c for c in candidates if c["semantic_id"] == target.semantic_id
    )
    return {
        "schema_version": V3C_EXAMPLE_SCHEMA_VERSION,
        "protocol_version": V3C_PROTOCOL_VERSION,
        "example_id": f"vendor-list-v3c-cal-{number:04d}-{slug}",
        "image_path": (
            f"artifacts/grounding-v3c/images/raw/vendor-list-v3c-cal-{number:04d}.png"
        ),
        "image_sha256": "a" * 64,
        "target_id": target.semantic_id,
        "target": target.instruction,
        "bbox": list(target_candidate["bbox"]),
        "css_bbox": list(target_candidate["css_bbox"]),
        "element_type": target.element_type,
        "task_seed": seed,
        "task_id": f"v3c-{seed}",
        "screen_state": state,
        "css_width": 1024,
        "css_height": 768,
        "screen_width": screen_width,
        "screen_height": screen_height,
        "device_scale_factor": 1.0,
        "capture_version": "pixelgym-browser-capture-v1",
    }


def _make_v3c_candidate_record(example: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": V3C_CANDIDATE_SCHEMA_VERSION,
        "protocol_version": V3C_PROTOCOL_VERSION,
        "example_id": example["example_id"],
        "candidates": _v3c_candidate_list(),
    }


def _build_full_v3c_grid() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    examples: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    number = 0
    for seed in V3C_CALIBRATION_SEEDS:
        for state in SCREEN_STATES:
            number += 1
            ex = _make_v3c_example(seed, state, number)
            examples.append(ex)
            candidates.append(_make_v3c_candidate_record(ex))
    return examples, candidates


def test_validate_v3c_calibration_dataset_accepts_complete_grid() -> None:
    examples, candidates = _build_full_v3c_grid()
    summary = validate_v3c_calibration_dataset(examples, candidates)
    assert summary["example_count"] == 20
    assert summary["candidate_record_count"] == 20
    assert set(summary["screen_state_counts"].values()) == {4}
    assert summary["calibration_label"] == "CALIBRATION"
    assert summary["variant"] == "v3c"
    assert summary["expected_candidate_count"] == V3C_EXPECTED_CANDIDATE_COUNT


def test_validate_v3c_dataset_rejects_wrong_count() -> None:
    examples, candidates = _build_full_v3c_grid()
    with pytest.raises(ValueError, match="20"):
        validate_v3c_calibration_dataset(examples[:-1], candidates[:-1])


def test_validate_v3c_dataset_rejects_scored_seeds() -> None:
    examples, candidates = _build_full_v3c_grid()
    examples[0]["task_seed"] = 0
    with pytest.raises(ValueError, match="v3c calibration"):
        validate_v3c_calibration_dataset(examples, candidates)


def test_validate_v3c_dataset_rejects_wrong_target_allocation() -> None:
    examples, candidates = _build_full_v3c_grid()
    examples[0]["target_id"] = "add_vendor"
    examples[0]["target"] = "Click the Add New Vendor button"
    examples[0]["element_type"] = "button"
    with pytest.raises(ValueError, match="allocation"):
        validate_v3c_calibration_dataset(examples, candidates)


def test_validate_v3c_dataset_rejects_wrong_schema_version() -> None:
    examples, candidates = _build_full_v3c_grid()
    examples[0]["schema_version"] = "wrong-version"
    with pytest.raises(ValueError, match="schema version"):
        validate_v3c_calibration_dataset(examples, candidates)


def test_validate_v3c_dataset_rejects_wrong_protocol_version() -> None:
    examples, candidates = _build_full_v3c_grid()
    examples[0]["protocol_version"] = "wrong-protocol"
    with pytest.raises(ValueError, match="protocol version"):
        validate_v3c_calibration_dataset(examples, candidates)


def test_v3c_example_id_format() -> None:
    target = v3c_calibration_target(20, "initial")
    slug = target.semantic_id.replace("_", "-")
    example = _make_v3c_example(20, "initial", 1)
    assert example["example_id"] == f"vendor-list-v3c-cal-0001-{slug}"
    assert example["protocol_version"] == V3C_PROTOCOL_VERSION
    assert example["schema_version"] == V3C_EXAMPLE_SCHEMA_VERSION


def test_v3c_candidate_list_count_matches_expected() -> None:
    candidates = _v3c_candidate_list()
    assert len(candidates) == V3C_EXPECTED_CANDIDATE_COUNT
    ids = {c["semantic_id"] for c in candidates}
    assert len(ids) == V3C_EXPECTED_CANDIDATE_COUNT


def test_v3c_all_targets_present_in_candidate_list() -> None:
    candidate_ids = {c["semantic_id"] for c in _v3c_candidate_list()}
    for spec in V3C_TARGET_SPECS:
        assert spec.semantic_id in candidate_ids, (
            f"v3c target {spec.semantic_id!r} not in candidate list"
        )


def test_v3c_repeated_edit_delete_buttons_have_identical_labels() -> None:
    """The difficulty lever: all Edit buttons share the same visible_label."""
    candidates = _v3c_candidate_list()
    edit_labels = {
        c["visible_label"]
        for c in candidates
        if c["semantic_id"].startswith("edit_")
    }
    delete_labels = {
        c["visible_label"]
        for c in candidates
        if c["semantic_id"].startswith("delete_")
    }
    assert edit_labels == {"Edit"}, "all Edit buttons must share one label"
    assert delete_labels == {"Delete"}, "all Delete buttons must share one label"
