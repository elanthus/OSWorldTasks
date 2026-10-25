"""Build and validate the balanced v2 grounding benchmark from frozen v1 captures.

V2 deliberately reuses the 100 deterministic seed-by-screen-state screenshots and
target-neutral candidate/mark proposals captured for v1.  It changes only the target
allocation: every target occurs in every screen state for two distinct seeds.  No model
output is read or produced here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pixelgym.grounding.capture import build_contact_sheet
from pixelgym.grounding.overlays import build_overlay_contact_sheet, proposal_match
from pixelgym.grounding.schema import (
    SCREEN_STATES,
    TASK_SEEDS,
)
from pixelgym.grounding.schema_v2 import (
    CANDIDATE_SCHEMA_VERSION,
    EXAMPLE_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    OVERLAY_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    _index_by_example_id,
    target_for_seed_state,
    validate_records,
)
from pixelgym.serialization import canonical_json_text, load_jsonl

_V1_PROTOCOL_VERSION = "pixelgym-grounding-v1"
_V1_EXAMPLE_SCHEMA_VERSION = "pixelgym-grounding-example-v1"
_V1_CANDIDATE_SCHEMA_VERSION = "pixelgym-grounding-candidates-v1"
_V1_OVERLAY_SCHEMA_VERSION = "pixelgym-grounding-overlay-v1"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
        example_id = f"vendor-form-v2-{number:04d}-{target.semantic_id.replace('_', '-')}"
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
        target_proposed, target_mark_id = proposal_match(
            target.semantic_id, source_overlay["marks"]
        )
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
        "design": (
            "20 seeds x 5 screen states; one target per screenshot; two seed "
            "replicates per target-by-state cell"
        ),
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
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
