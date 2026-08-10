"""Recompute and print aggregate metrics from stored real-reset evidence."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

DEFAULT_EVIDENCE = Path("artifacts/day-2/raw/real-reset.json")
GUEST_CLOCK_REGION_XYXY = (1000, 0, 1080, 32)


def _all_equal(values: list[Any]) -> bool:
    return len(set(values)) == 1


def _inside(inner: list[int], outer: tuple[int, int, int, int]) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def aggregate(evidence: dict[str, Any]) -> dict[str, Any]:
    records = evidence.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("evidence must contain at least one reset record")

    changed = [record for record in records if record["differing_pixel_count"] > 0]
    boxes = [record["differing_pixel_bbox_xyxy"] for record in changed]
    if any(box is None for box in boxes):
        raise ValueError("every changed reset must include a differing-pixel bounding box")

    union_box = None
    if boxes:
        union_box = [
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        ]

    task_hashes = [record["task_spec_sha256"] for record in records]
    app_hashes = [record["application_state_sha256"] for record in records]
    shapes = [tuple(record["screenshot_shape"]) for record in records]
    dtypes = [record["screenshot_dtype"] for record in records]
    pixel_counts = [record["differing_pixel_count"] for record in records]
    channel_deltas = [record["max_per_channel_delta"] for record in records]
    ssims = [record["ssim"] for record in records]
    reset_seconds = [record["reset_to_stable_frame_seconds"] for record in records]

    result = {
        "source": evidence.get("backend", "unknown"),
        "seed": evidence.get("seed"),
        "reset_count": len(records),
        "comparisons_to_reset_0": len(records) - 1,
        "semantic_state": {
            "task_spec_hash_exact": _all_equal(task_hashes),
            "unique_task_spec_hashes": len(set(task_hashes)),
            "application_state_hash_exact": _all_equal(app_hashes),
            "unique_application_state_hashes": len(set(app_hashes)),
        },
        "screenshot_contract": {
            "shape_exact": _all_equal(shapes),
            "shape": list(shapes[0]),
            "dtype_exact": _all_equal(dtypes),
            "dtype": dtypes[0],
        },
        "raw_visual_difference": {
            "bitwise_identical_across_resets": all(count == 0 for count in pixel_counts),
            "changed_comparison_count": len(changed),
            "maximum_differing_pixel_count": max(pixel_counts),
            "sum_of_per_comparison_differing_pixel_counts": sum(pixel_counts),
            "maximum_per_channel_delta": max(channel_deltas),
            "union_bounding_box_xyxy": union_box,
            "all_differences_inside_guest_clock_region": all(
                _inside(box, GUEST_CLOCK_REGION_XYXY) for box in boxes
            ),
            "guest_clock_region_xyxy": list(GUEST_CLOCK_REGION_XYXY),
            "mask_or_tolerance_applied": False,
        },
        "perceptual_similarity": {
            "minimum_ssim": min(ssims),
            "mean_ssim": statistics.fmean(ssims),
            "maximum_ssim": max(ssims),
        },
        "reset_to_stable_frame_seconds": {
            "minimum": min(reset_seconds),
            "mean": statistics.fmean(reset_seconds),
            "maximum": max(reset_seconds),
        },
    }

    stored = evidence.get("summary", {})
    checks = {
        "bitwise_visual_determinism": result["raw_visual_difference"][
            "bitwise_identical_across_resets"
        ],
        "maximum_differing_pixel_count": result["raw_visual_difference"][
            "maximum_differing_pixel_count"
        ],
        "maximum_per_channel_delta": result["raw_visual_difference"]["maximum_per_channel_delta"],
        "minimum_ssim": result["perceptual_similarity"]["minimum_ssim"],
        "maximum_reset_to_stable_frame_seconds": result["reset_to_stable_frame_seconds"]["maximum"],
        "semantic_task_state_exact": result["semantic_state"]["task_spec_hash_exact"],
        "privileged_application_state_exact": result["semantic_state"][
            "application_state_hash_exact"
        ],
        "screenshot_shape_exact": result["screenshot_contract"]["shape_exact"],
        "screenshot_dtype_exact": result["screenshot_contract"]["dtype_exact"],
    }
    result["stored_summary_cross_check"] = {
        "all_recomputed_values_match": all(
            stored.get(key) == value for key, value in checks.items()
        ),
        "checked_field_count": len(checks),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", nargs="?", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    print(json.dumps(aggregate(evidence), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
