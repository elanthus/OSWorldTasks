"""Integrity checks for the checked-in v4 pilot evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _overlaps(first: list[int], second: list[int]) -> bool:
    return (
        max(first[0], second[0]) < min(first[2], second[2])
        and max(first[1], second[1]) < min(first[3], second[3])
    )


def test_v4_capture_source_hashes_match_checked_in_sources() -> None:
    evidence = json.loads(
        (REPOSITORY_ROOT / "artifacts/grounding-v4-pilot-capture.json").read_text(
            encoding="utf-8"
        )
    )

    assert evidence["source_sha256"]
    for relative, recorded in evidence["source_sha256"].items():
        actual = hashlib.sha256((REPOSITORY_ROOT / relative).read_bytes()).hexdigest()
        assert actual == recorded, relative


def test_v4_capture_is_bitwise_repeatable_and_manifest_hashes_match() -> None:
    evidence = json.loads(
        (REPOSITORY_ROOT / "artifacts/grounding-v4-pilot-capture.json").read_text(
            encoding="utf-8"
        )
    )
    repeatability = evidence["repeatability"]
    assert repeatability["byte_identical_file_count"] == repeatability["file_count"] == 10
    assert repeatability["differing_file_count"] == 0
    assert repeatability["differing_pixel_count"] == 0
    assert (
        repeatability["reference_aggregate_sha256"]
        == repeatability["candidate_aggregate_sha256"]
    )

    manifest = json.loads(
        (REPOSITORY_ROOT / "artifacts/grounding-v4-pilot-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for output in manifest["outputs"].values():
        actual = hashlib.sha256((REPOSITORY_ROOT / output["path"]).read_bytes()).hexdigest()
        assert actual == output["sha256"], output["path"]


def test_v4_candidate_join_ids_are_opaque_in_checked_in_evidence() -> None:
    examples = _jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4-pilot-dataset.jsonl")
    candidates = _jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4-pilot-candidates.jsonl")

    assert [row["example_id"] for row in candidates] == [
        f"vendor-workbench-v4-{number:04d}" for number in range(1, 11)
    ]
    for example, candidate_record in zip(examples, candidates, strict=True):
        assert candidate_record["example_id"] == example["example_id"]
        assert example["target_id"] not in candidate_record["example_id"]
        assert set(candidate_record) == {
            "schema_version",
            "protocol_version",
            "example_id",
            "candidates",
        }


def test_v4_overlay_badges_do_not_overlap_candidates_or_each_other() -> None:
    records = _jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4-pilot-overlays.jsonl")

    for record in records:
        element_boxes = [mark["bbox"] for mark in record["marks"]]
        badge_boxes = [mark["badge_bbox"] for mark in record["marks"]]
        assert all(
            not _overlaps(badge, element)
            for badge in badge_boxes
            for element in element_boxes
        ), record["example_id"]
        assert all(
            not _overlaps(first, second)
            for index, first in enumerate(badge_boxes)
            for second in badge_boxes[index + 1 :]
        ), record["example_id"]
