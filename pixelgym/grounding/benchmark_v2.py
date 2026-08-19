"""Build and validate the balanced v2 grounding benchmark from frozen v1 captures.

V2 deliberately reuses the 100 deterministic seed-by-screen-state screenshots and
target-neutral candidate/mark proposals captured for v1.  It changes only the target
allocation: every target occurs in every screen state for two distinct seeds.  No model
output is read or produced here.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pixelgym.grounding.capture import build_contact_sheet
from pixelgym.grounding.overlays import build_overlay_contact_sheet, proposal_match, validate_marks
from pixelgym.grounding.schema import (
    SCREEN_STATES,
    TARGET_SPECS,
    TASK_SEEDS,
    TargetSpec,
    css_bbox_to_screenshot,
    validate_bbox,
    validate_candidate_set,
)
from pixelgym.serialization import canonical_json_text, load_jsonl

PROTOCOL_VERSION = "pixelgym-grounding-v2"
EXAMPLE_SCHEMA_VERSION = "pixelgym-grounding-example-v2"
CANDIDATE_SCHEMA_VERSION = "pixelgym-grounding-candidates-v2"
OVERLAY_SCHEMA_VERSION = "pixelgym-grounding-overlay-v2"
MANIFEST_SCHEMA_VERSION = "pixelgym-grounding-benchmark-manifest-v2"

_V1_PROTOCOL_VERSION = "pixelgym-grounding-v1"
_V1_EXAMPLE_SCHEMA_VERSION = "pixelgym-grounding-example-v1"
_V1_CANDIDATE_SCHEMA_VERSION = "pixelgym-grounding-candidates-v1"
_V1_OVERLAY_SCHEMA_VERSION = "pixelgym-grounding-overlay-v1"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _index_by_example_id(
    rows: list[dict[str, Any]], *, label: str
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = row.get("example_id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"{label} example IDs must be nonempty strings")
        if identifier in indexed:
            raise ValueError(f"{label} contains duplicate example IDs")
        indexed[identifier] = row
    return indexed


def _validate_source_grid(
    examples: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    overlays: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    expected_count = len(TASK_SEEDS) * len(SCREEN_STATES)
    if not len(examples) == len(candidates) == len(overlays) == expected_count:
        raise ValueError("v1 sources must contain the complete 100-row capture grid")
    cells = [(row.get("task_seed"), row.get("screen_state")) for row in examples]
    expected_cells = [(seed, state) for seed in TASK_SEEDS for state in SCREEN_STATES]
    if cells != expected_cells:
        raise ValueError("v1 examples are not in canonical seed-by-screen-state order")
    if any(
        row.get("protocol_version") != _V1_PROTOCOL_VERSION
        or row.get("schema_version") != _V1_EXAMPLE_SCHEMA_VERSION
        for row in examples
    ):
        raise ValueError("v1 example source has an unexpected version")

    candidate_by_id = _index_by_example_id(candidates, label="v1 candidates")
    overlay_by_id = _index_by_example_id(overlays, label="v1 overlays")
    if len(candidate_by_id) != expected_count or len(overlay_by_id) != expected_count:
        raise ValueError("v1 candidate or overlay source contains duplicate example IDs")
    expected_ids = {row["example_id"] for row in examples}
    if set(candidate_by_id) != expected_ids or set(overlay_by_id) != expected_ids:
        raise ValueError("v1 examples, candidates, and overlays do not join one-to-one")
    if any(
        row.get("protocol_version") != _V1_PROTOCOL_VERSION
        or row.get("schema_version") != _V1_CANDIDATE_SCHEMA_VERSION
        for row in candidates
    ):
        raise ValueError("v1 candidate source has an unexpected version")
    if any(
        row.get("protocol_version") != _V1_PROTOCOL_VERSION
        or row.get("schema_version") != _V1_OVERLAY_SCHEMA_VERSION
        for row in overlays
    ):
        raise ValueError("v1 overlay source has an unexpected version")
    return candidate_by_id, overlay_by_id


def derive_records(
    source_examples: list[dict[str, Any]],
    source_candidates: list[dict[str, Any]],
    source_overlays: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive v2 metadata without recapturing images or consulting model outputs."""
    candidate_by_id, overlay_by_id = _validate_source_grid(
        source_examples, source_candidates, source_overlays
    )
    examples: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    overlays: list[dict[str, Any]] = []
    for number, source in enumerate(source_examples, start=1):
        target = target_for_seed_state(source["task_seed"], source["screen_state"])
        source_candidate = candidate_by_id[source["example_id"]]
        candidate_set = source_candidate["candidates"]
        target_candidates = [
            item for item in candidate_set if item.get("semantic_id") == target.semantic_id
        ]
        if len(target_candidates) != 1:
            raise ValueError("balanced target does not match exactly one target-neutral candidate")
        target_candidate = target_candidates[0]
        example_id = (
            f"vendor-form-v2-{number:04d}-{target.semantic_id.replace('_', '-')}"
        )
        example = {
            **source,
            "schema_version": EXAMPLE_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "example_id": example_id,
            "target_id": target.semantic_id,
            "target": target.instruction,
            "bbox": list(target_candidate["bbox"]),
            "css_bbox": list(target_candidate["css_bbox"]),
            "element_type": target.element_type,
        }
        candidate = {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "example_id": example_id,
            "candidates": candidate_set,
        }
        source_overlay = overlay_by_id[source["example_id"]]
        target_proposed, target_mark_id = proposal_match(target.semantic_id, source_overlay["marks"])
        overlay = {
            **source_overlay,
            "schema_version": OVERLAY_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "example_id": example_id,
            "target_proposed": target_proposed,
            "target_mark_id": target_mark_id,
        }
        examples.append(example)
        candidates.append(candidate)
        overlays.append(overlay)
    validate_records(examples, candidates, overlays)
    return examples, candidates, overlays


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
    if len(set(ids)) != expected_count or set(candidate_by_id) != set(ids) or set(overlay_by_id) != set(ids):
        raise ValueError("v2 examples, candidates, and overlays must join one-to-one")

    cell_counts: Counter[tuple[str, str]] = Counter()
    target_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    seed_counts: Counter[int] = Counter()
    seed_state_counts: Counter[tuple[int, str]] = Counter()
    image_paths: set[str] = set()
    for example in examples:
        if example.get("schema_version") != EXAMPLE_SCHEMA_VERSION or example.get(
            "protocol_version"
        ) != PROTOCOL_VERSION:
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
        if candidate.get("schema_version") != CANDIDATE_SCHEMA_VERSION or candidate.get(
            "protocol_version"
        ) != PROTOCOL_VERSION:
            raise ValueError("v2 candidate version does not match")
        validate_candidate_set(candidate.get("candidates"), width=width, height=height)
        matches = [
            item
            for item in candidate["candidates"]
            if item["semantic_id"] == example["target_id"]
        ]
        if len(matches) != 1 or matches[0]["bbox"] != example["bbox"]:
            raise ValueError("v2 target box does not match its target-neutral candidate")

        overlay = overlay_by_id[example["example_id"]]
        if overlay.get("schema_version") != OVERLAY_SCHEMA_VERSION or overlay.get(
            "protocol_version"
        ) != PROTOCOL_VERSION:
            raise ValueError("v2 overlay version does not match")
        if (
            overlay.get("raw_image_path") != example.get("image_path")
            or overlay.get("raw_image_sha256") != example.get("image_sha256")
        ):
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


def validate_image_artifacts(
    examples: list[dict[str, Any]],
    overlays: list[dict[str, Any]],
    *,
    repository_root: Path,
) -> None:
    """Verify reused raw and marked bytes against every retained digest."""
    overlay_by_id = {row["example_id"]: row for row in overlays}
    for example in examples:
        raw_path = repository_root / example["image_path"]
        if _sha256(raw_path.read_bytes()) != example["image_sha256"]:
            raise ValueError("v2 raw image digest does not match the reused capture")
        overlay = overlay_by_id[example["example_id"]]
        marked_path = repository_root / overlay["marked_image_path"]
        if _sha256(marked_path.read_bytes()) != overlay["marked_image_sha256"]:
            raise ValueError("v2 marked image digest does not match the reused overlay")


def build_benchmark_v2(repository_root: Path) -> dict[str, Any]:
    """Write checked-in v2 metadata and audit sheets from immutable v1 inputs."""
    artifact_root = repository_root / "artifacts"
    source_paths = {
        "dataset": artifact_root / "grounding-dataset.jsonl",
        "candidates": artifact_root / "grounding-candidates.jsonl",
        "overlays": artifact_root / "grounding-overlays.jsonl",
    }
    examples, candidates, overlays = derive_records(
        load_jsonl(source_paths["dataset"]),
        load_jsonl(source_paths["candidates"]),
        load_jsonl(source_paths["overlays"]),
    )
    validate_image_artifacts(examples, overlays, repository_root=repository_root)
    output_paths = {
        "dataset": artifact_root / "grounding-v2-dataset.jsonl",
        "candidates": artifact_root / "grounding-v2-candidates.jsonl",
        "overlays": artifact_root / "grounding-v2-overlays.jsonl",
    }
    for name, rows in (("dataset", examples), ("candidates", candidates), ("overlays", overlays)):
        output_paths[name].write_text(
            "".join(canonical_json_text(row) + "\n" for row in rows), encoding="utf-8"
        )

    contact_sheet = artifact_root / "grounding-v2" / "contact-sheet.png"
    marks_contact_sheet = artifact_root / "grounding-v2" / "marks-contact-sheet.png"
    build_contact_sheet(examples, repository_root=repository_root, output_path=contact_sheet)
    build_overlay_contact_sheet(
        overlays, repository_root=repository_root, output_path=marks_contact_sheet
    )
    audit_paths = {
        "contact_sheet": contact_sheet,
        "marks_contact_sheet": marks_contact_sheet,
    }
    allocation = validate_records(examples, candidates, overlays)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "status": "dataset_ready_evaluation_not_run",
        "design": "20 seeds x 5 screen states; one target per screenshot; two seed replicates per target-by-state cell",
        **allocation,
        "source_v1": {
            name: {
                "path": path.relative_to(repository_root).as_posix(),
                "sha256": _sha256(path.read_bytes()),
            }
            for name, path in source_paths.items()
        },
        "outputs": {
            name: {
                "path": path.relative_to(repository_root).as_posix(),
                "sha256": _sha256(path.read_bytes()),
            }
            for name, path in {**output_paths, **audit_paths}.items()
        },
        "contact_sheet_path": contact_sheet.relative_to(repository_root).as_posix(),
        "marks_contact_sheet_path": marks_contact_sheet.relative_to(repository_root).as_posix(),
        "model_calls_performed": 0,
    }
    manifest_path = artifact_root / "grounding-v2-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
