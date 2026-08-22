"""Unit tests for v3b calibration capture module — no browser, no network."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from pixelgym.grounding.calibration_v3b import (
    V3B_CALIBRATION_SEEDS,
    V3B_CANDIDATE_SCHEMA_VERSION,
    V3B_EXAMPLE_SCHEMA_VERSION,
    V3B_EXPECTED_CANDIDATE_COUNT,
    V3B_PROTOCOL_VERSION,
    V3B_TARGET_SPECS,
    v3b_calibration_target,
    validate_v3b_calibration_dataset,
)
from pixelgym.grounding.schema import SCREEN_STATES, TARGET_SPECS, TASK_SEEDS


def test_v3b_calibration_seeds_are_disjoint_from_frozen_task_seeds() -> None:
    assert set(V3B_CALIBRATION_SEEDS).isdisjoint(set(TASK_SEEDS))
    assert V3B_CALIBRATION_SEEDS == (20, 21, 22, 23)


def test_v3b_target_specs_are_distinct_from_v3a() -> None:
    v3a_ids = {spec.semantic_id for spec in TARGET_SPECS}
    v3b_ids = {spec.semantic_id for spec in V3B_TARGET_SPECS}
    assert v3b_ids.isdisjoint(v3a_ids), "v3b targets must not overlap with v3a"


def test_v3b_target_specs_have_10_unique_ids() -> None:
    ids = [spec.semantic_id for spec in V3B_TARGET_SPECS]
    assert len(ids) == 10
    assert len(set(ids)) == 10


def test_v3b_target_specs_cover_difficulty_levers() -> None:
    types = Counter(spec.element_type for spec in V3B_TARGET_SPECS)
    assert "text_input" in types, "need near-duplicate text inputs"
    assert "link" in types, "need occluder-region links"
    assert types["link"] >= 2, "need header and sidebar links"
    assert "select" in types
    assert "checkbox" in types
    assert "button" in types


def test_v3b_calibration_target_uses_crossed_allocation() -> None:
    cells: Counter[tuple[str, str]] = Counter()
    target_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()

    for seed in V3B_CALIBRATION_SEEDS:
        for state in SCREEN_STATES:
            target = v3b_calibration_target(seed, state)
            cells[(target.semantic_id, state)] += 1
            target_counts[target.semantic_id] += 1
            state_counts[state] += 1

    assert len(cells) == 20
    assert all(count == 1 for count in cells.values()), "no duplicate (target, state)"
    assert set(state_counts.values()) == {4}
    for seed in V3B_CALIBRATION_SEEDS:
        seed_targets = [
            v3b_calibration_target(seed, s).semantic_id for s in SCREEN_STATES
        ]
        assert len(set(seed_targets)) == 5, "each seed gets 5 distinct targets"


def test_v3b_calibration_target_rejects_scored_seeds() -> None:
    with pytest.raises(ValueError, match="calibration"):
        v3b_calibration_target(0, "initial")
    with pytest.raises(ValueError, match="calibration"):
        v3b_calibration_target(19, "initial")


def test_v3b_calibration_target_rejects_out_of_range_seeds() -> None:
    with pytest.raises(ValueError, match="v3b calibration"):
        v3b_calibration_target(99, "initial")


def test_v3b_calibration_target_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="state"):
        v3b_calibration_target(20, "nonexistent_state")


def test_v3b_expected_candidate_count() -> None:
    assert V3B_EXPECTED_CANDIDATE_COUNT == 38


def _v3b_candidate_list() -> list[dict[str, Any]]:
    """Build a synthetic candidate list matching the v3b page's 38 elements."""
    all_candidates = [
        ("nav_vendors", "link", "Vendors"),
        ("nav_contracts", "link", "Contracts"),
        ("nav_reports", "link", "Reports"),
        ("nav_settings", "link", "Settings"),
        ("nav_help", "link", "Help"),
        ("sb_dashboard", "link", "Dashboard"),
        ("sb_new_vendor", "link", "New Vendor"),
        ("sb_vendor_list", "link", "Vendor List"),
        ("sb_pending", "link", "Pending Review"),
        ("sb_import", "link", "Import Data"),
        ("sb_export", "link", "Export Data"),
        ("sb_audit", "link", "Audit Log"),
        ("sb_support", "link", "Support"),
        ("company_name", "text_input", "Company name"),
        ("company_legal_name", "text_input", "Company legal name"),
        ("company_trading_name", "text_input", "Company trading name"),
        ("registration_id", "text_input", "Registration ID"),
        ("tax_id", "text_input", "Tax ID"),
        ("vat_number", "text_input", "VAT number"),
        ("contact_email", "text_input", "Contact email"),
        ("billing_email", "text_input", "Billing email"),
        ("support_email", "text_input", "Support email"),
        ("contact_phone", "text_input", "Contact phone"),
        ("office_phone", "text_input", "Office phone"),
        ("mobile_phone", "text_input", "Mobile phone"),
        ("country", "select", "Country"),
        ("region", "select", "Region"),
        ("city", "text_input", "City"),
        ("postal_code", "text_input", "Postal code"),
        ("payment_terms_net_15", "radio", "Net 15"),
        ("payment_terms_net_30", "radio", "Net 30"),
        ("payment_terms_net_45", "radio", "Net 45"),
        ("currency", "select", "Currency"),
        ("expedited_onboarding", "checkbox", "Expedited onboarding"),
        ("preferred_vendor", "checkbox", "Preferred vendor"),
        ("save_draft", "button", "Save Draft"),
        ("submit", "button", "Submit"),
        ("cancel", "button", "Cancel"),
    ]
    assert len(all_candidates) == V3B_EXPECTED_CANDIDATE_COUNT
    return [
        {
            "semantic_id": sem_id,
            "element_type": elem_type,
            "visible_label": label,
            "css_bbox": [10.0, 10.0 + i * 18, 200.0, 28.0 + i * 18],
            "bbox": [10, 10 + i * 18, 200, 28 + i * 18],
        }
        for i, (sem_id, elem_type, label) in enumerate(all_candidates)
    ]


def _make_v3b_example(
    seed: int,
    state: str,
    number: int,
    *,
    screen_width: int = 1024,
    screen_height: int = 768,
) -> dict[str, Any]:
    target = v3b_calibration_target(seed, state)
    slug = target.semantic_id.replace("_", "-")
    candidates = _v3b_candidate_list()
    target_candidate = next(
        c for c in candidates if c["semantic_id"] == target.semantic_id
    )
    return {
        "schema_version": V3B_EXAMPLE_SCHEMA_VERSION,
        "protocol_version": V3B_PROTOCOL_VERSION,
        "example_id": f"vendor-form-v3b-cal-{number:04d}-{slug}",
        "image_path": (
            f"artifacts/grounding-v3b/images/raw/vendor-form-v3b-cal-{number:04d}.png"
        ),
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


def _make_v3b_candidate_record(example: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": V3B_CANDIDATE_SCHEMA_VERSION,
        "protocol_version": V3B_PROTOCOL_VERSION,
        "example_id": example["example_id"],
        "candidates": _v3b_candidate_list(),
    }


def _build_full_v3b_grid() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    examples: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    number = 0
    for seed in V3B_CALIBRATION_SEEDS:
        for state in SCREEN_STATES:
            number += 1
            ex = _make_v3b_example(seed, state, number)
            examples.append(ex)
            candidates.append(_make_v3b_candidate_record(ex))
    return examples, candidates


def test_validate_v3b_calibration_dataset_accepts_complete_grid() -> None:
    examples, candidates = _build_full_v3b_grid()
    summary = validate_v3b_calibration_dataset(examples, candidates)
    assert summary["example_count"] == 20
    assert summary["candidate_record_count"] == 20
    assert set(summary["screen_state_counts"].values()) == {4}
    assert summary["calibration_label"] == "CALIBRATION"
    assert summary["variant"] == "v3b"
    assert summary["expected_candidate_count"] == V3B_EXPECTED_CANDIDATE_COUNT


def test_validate_v3b_dataset_rejects_extra_candidate_record_fields() -> None:
    examples, candidates = _build_full_v3b_grid()
    candidates[0]["target_id"] = examples[0]["target_id"]
    with pytest.raises(ValueError, match="candidate record fields"):
        validate_v3b_calibration_dataset(examples, candidates)


def test_validate_v3b_dataset_rejects_wrong_count() -> None:
    examples, candidates = _build_full_v3b_grid()
    with pytest.raises(ValueError, match="20"):
        validate_v3b_calibration_dataset(examples[:-1], candidates[:-1])


def test_validate_v3b_dataset_rejects_scored_seeds() -> None:
    examples, candidates = _build_full_v3b_grid()
    examples[0]["task_seed"] = 0
    with pytest.raises(ValueError, match="v3b calibration"):
        validate_v3b_calibration_dataset(examples, candidates)


def test_validate_v3b_dataset_rejects_wrong_target_allocation() -> None:
    examples, candidates = _build_full_v3b_grid()
    examples[0]["target_id"] = "submit"
    examples[0]["target"] = "Click the Submit button"
    examples[0]["element_type"] = "button"
    with pytest.raises(ValueError, match="allocation"):
        validate_v3b_calibration_dataset(examples, candidates)


def test_validate_v3b_dataset_rejects_wrong_schema_version() -> None:
    examples, candidates = _build_full_v3b_grid()
    examples[0]["schema_version"] = "wrong-version"
    with pytest.raises(ValueError, match="schema version"):
        validate_v3b_calibration_dataset(examples, candidates)


def test_validate_v3b_dataset_rejects_wrong_protocol_version() -> None:
    examples, candidates = _build_full_v3b_grid()
    examples[0]["protocol_version"] = "wrong-protocol"
    with pytest.raises(ValueError, match="protocol version"):
        validate_v3b_calibration_dataset(examples, candidates)


def test_v3b_example_id_format() -> None:
    target = v3b_calibration_target(20, "initial")
    slug = target.semantic_id.replace("_", "-")
    example = _make_v3b_example(20, "initial", 1)
    assert example["example_id"] == f"vendor-form-v3b-cal-0001-{slug}"
    assert example["protocol_version"] == V3B_PROTOCOL_VERSION
    assert example["schema_version"] == V3B_EXAMPLE_SCHEMA_VERSION


def test_v3b_candidate_list_count_matches_expected() -> None:
    candidates = _v3b_candidate_list()
    assert len(candidates) == V3B_EXPECTED_CANDIDATE_COUNT
    ids = {c["semantic_id"] for c in candidates}
    assert len(ids) == V3B_EXPECTED_CANDIDATE_COUNT


def test_v3b_all_targets_present_in_candidate_list() -> None:
    candidate_ids = {c["semantic_id"] for c in _v3b_candidate_list()}
    for spec in V3B_TARGET_SPECS:
        assert spec.semantic_id in candidate_ids, (
            f"v3b target {spec.semantic_id!r} not in candidate list"
        )
