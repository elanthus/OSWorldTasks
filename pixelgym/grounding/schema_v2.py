"""Capture-free schema and validation for the balanced v2 grounding benchmark.

These definitions are shared by the v2 builder and by the evaluation runner.  They live
apart from :mod:`pixelgym.grounding.benchmark_v2` so that evaluation code can validate
the crossed allocation without importing build-time capture instrumentation
(environment contract invariant 14).  Nothing here reads images or calls a model.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pixelgym.grounding.overlays import proposal_match, validate_marks
from pixelgym.grounding.schema import (
    SCREEN_STATES,
    TARGET_SPECS,
    TASK_SEEDS,
    TargetSpec,
    css_bbox_to_screenshot,
    validate_bbox,
    validate_candidate_set,
)

PROTOCOL_VERSION = "pixelgym-grounding-v2"
EXAMPLE_SCHEMA_VERSION = "pixelgym-grounding-example-v2"
CANDIDATE_SCHEMA_VERSION = "pixelgym-grounding-candidates-v2"
OVERLAY_SCHEMA_VERSION = "pixelgym-grounding-overlay-v2"
MANIFEST_SCHEMA_VERSION = "pixelgym-grounding-benchmark-manifest-v2"


def target_for_seed_state(seed: int, screen_state: str) -> TargetSpec:
    """Return the preregistered balanced target for one seed/state capture.

    The cyclic shift makes every target-by-state cell contain seeds ``r`` and
    ``r + 10`` while each seed receives five distinct targets.  Unlike v1, neither
    target identity nor control type determines screen state.
    """
    if seed not in TASK_SEEDS:
        raise ValueError("task seed is outside the v2 allocation")
    try:
        state_index = SCREEN_STATES.index(screen_state)
    except ValueError as exc:
        raise ValueError("screen state is outside the v2 allocation") from exc
    return TARGET_SPECS[(seed + state_index) % len(TARGET_SPECS)]


def _index_by_example_id(rows: list[dict[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = row.get("example_id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"{label} example IDs must be nonempty strings")
        if identifier in indexed:
            raise ValueError(f"{label} contains duplicate example IDs")
        indexed[identifier] = row
    return indexed


def validate_records(
    examples: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    overlays: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate exact v2 versions, joins, boxes, and crossed allocation."""
    expected_count = len(TASK_SEEDS) * len(SCREEN_STATES)
    if not len(examples) == len(candidates) == len(overlays) == expected_count:
        raise ValueError("v2 benchmark must contain exactly 100 joined examples")
    candidate_by_id = _index_by_example_id(candidates, label="v2 candidates")
    overlay_by_id = _index_by_example_id(overlays, label="v2 overlays")
    ids = [row.get("example_id") for row in examples]
    if not all(isinstance(identifier, str) and identifier for identifier in ids):
        raise ValueError("v2 example IDs must be nonempty strings")
    if (
        len(set(ids)) != expected_count
        or set(candidate_by_id) != set(ids)
        or set(overlay_by_id) != set(ids)
    ):
        raise ValueError("v2 examples, candidates, and overlays must join one-to-one")

    cell_counts: Counter[tuple[str, str]] = Counter()
    target_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    seed_counts: Counter[int] = Counter()
    seed_state_counts: Counter[tuple[int, str]] = Counter()
    image_paths: set[str] = set()
    for example in examples:
        if (
            example.get("schema_version") != EXAMPLE_SCHEMA_VERSION
            or example.get("protocol_version") != PROTOCOL_VERSION
        ):
            raise ValueError("v2 example version does not match")
        seed, state = example.get("task_seed"), example.get("screen_state")
        if type(seed) is not int or not isinstance(state, str):
            raise ValueError("v2 seed and screen state have invalid types")
        target = target_for_seed_state(seed, state)
        if (
            example.get("target_id") != target.semantic_id
            or example.get("target") != target.instruction
            or example.get("element_type") != target.element_type
        ):
            raise ValueError("v2 example target does not match the balanced allocation")
        width, height = example.get("screen_width"), example.get("screen_height")
        if type(width) is not int or type(height) is not int:
            raise ValueError("v2 screen dimensions must be integers")
        validate_bbox(example.get("bbox"), width=width, height=height)
        if example["bbox"] != css_bbox_to_screenshot(
            example["css_bbox"],
            css_width=example["css_width"],
            css_height=example["css_height"],
            screen_width=width,
            screen_height=height,
        ):
            raise ValueError("v2 CSS-to-screenshot box transformation does not match")

        candidate = candidate_by_id[example["example_id"]]
        if (
            candidate.get("schema_version") != CANDIDATE_SCHEMA_VERSION
            or candidate.get("protocol_version") != PROTOCOL_VERSION
        ):
            raise ValueError("v2 candidate version does not match")
        validate_candidate_set(candidate.get("candidates"), width=width, height=height)
        matches = [
            item for item in candidate["candidates"] if item["semantic_id"] == example["target_id"]
        ]
        if len(matches) != 1 or matches[0]["bbox"] != example["bbox"]:
            raise ValueError("v2 target box does not match its target-neutral candidate")

        overlay = overlay_by_id[example["example_id"]]
        if (
            overlay.get("schema_version") != OVERLAY_SCHEMA_VERSION
            or overlay.get("protocol_version") != PROTOCOL_VERSION
        ):
            raise ValueError("v2 overlay version does not match")
        if overlay.get("raw_image_path") != example.get("image_path") or overlay.get(
            "raw_image_sha256"
        ) != example.get("image_sha256"):
            raise ValueError("v2 overlay does not reference the example's raw image")
        validate_marks(overlay.get("marks"), width=width, height=height)
        proposed, mark_id = proposal_match(example["target_id"], overlay["marks"])
        if (overlay.get("target_proposed"), overlay.get("target_mark_id")) != (
            proposed,
            mark_id,
        ):
            raise ValueError("v2 proposal coverage fields do not match the balanced target")

        target_id = example.get("target_id")
        image_path = example.get("image_path")
        if not isinstance(target_id, str) or not isinstance(image_path, str):
            raise TypeError("v2 target ID and image path must be strings")
        cell_counts[(target_id, state)] += 1
        target_counts[target_id] += 1
        state_counts[state] += 1
        seed_counts[seed] += 1
        seed_state_counts[(seed, state)] += 1
        image_paths.add(image_path)

    expected_targets = {target.semantic_id for target in TARGET_SPECS}
    expected_cells = {(target, state) for target in expected_targets for state in SCREEN_STATES}
    if set(cell_counts) != expected_cells or set(cell_counts.values()) != {2}:
        raise ValueError("v2 target-by-screen-state allocation is not fully crossed and balanced")
    if set(target_counts) != expected_targets or set(target_counts.values()) != {10}:
        raise ValueError("v2 target allocation is not balanced")
    if set(state_counts) != set(SCREEN_STATES) or set(state_counts.values()) != {20}:
        raise ValueError("v2 screen-state allocation is not balanced")
    if set(seed_counts) != set(TASK_SEEDS) or set(seed_counts.values()) != {5}:
        raise ValueError("v2 seed allocation is not balanced")
    expected_seed_states = {(seed, state) for seed in TASK_SEEDS for state in SCREEN_STATES}
    if set(seed_state_counts) != expected_seed_states or set(seed_state_counts.values()) != {1}:
        raise ValueError("v2 must use every seed-by-screen-state screenshot exactly once")
    if len(image_paths) != expected_count:
        raise ValueError("v2 must reference 100 distinct screenshot paths")
    return {
        "example_count": expected_count,
        "unique_screenshot_count": len(image_paths),
        "paired_condition_call_count": expected_count * 2,
        "target_counts": dict(sorted(target_counts.items())),
        "screen_state_counts": dict(sorted(state_counts.items())),
        "seed_counts": {str(key): value for key, value in sorted(seed_counts.items())},
        "target_screen_state_replicates": 2,
        "target_screen_state_fully_crossed": True,
        "target_screen_state_perfect_aliasing": False,
        "proposal_coverage_count": sum(row["target_proposed"] is True for row in overlays),
    }
