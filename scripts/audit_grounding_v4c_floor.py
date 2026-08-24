#!/usr/bin/env python3
"""Generate a no-call coordinate/transport audit from stored v4c predictions."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pixelgym.grounding.v4c_evaluation import _load_inputs
from pixelgym.grounding.v4c_protocol import V4C_HEIGHT, V4C_WIDTH, episode_for_seed
from pixelgym.serialization import load_jsonl


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contains(bbox: list[int], x: int, y: int) -> bool:
    left, top, right, bottom = bbox
    return left <= x < right and top <= y < bottom


def _observed_range(values: list[int]) -> list[int] | None:
    return [min(values), max(values)] if values else None


def _nearest(candidates: list[dict[str, Any]], x: int, y: int) -> str:
    def distance(candidate: dict[str, Any]) -> float:
        left, top, right, bottom = candidate["bbox"]
        return (x - (left + right) / 2) ** 2 + (y - (top + bottom) / 2) ** 2

    return min(candidates, key=distance)["semantic_id"]


def audit(repository_root: Path, predictions_path: Path, results_path: Path) -> dict[str, Any]:
    predictions = load_jsonl(predictions_path)
    results = json.loads(results_path.read_text(encoding="utf-8"))
    prediction_sha256 = _sha256(predictions_path)
    prediction_reference = results.get("predictions")
    expected_path = predictions_path.relative_to(repository_root).as_posix()
    if not isinstance(prediction_reference, dict) or prediction_reference != {
        "path": expected_path,
        "sha256": prediction_sha256,
    }:
        raise ValueError("v4c floor audit results are not bound to the supplied predictions")
    if results.get("collection", {}).get("action_record_count") != len(predictions):
        raise ValueError("v4c floor audit result count does not match supplied predictions")
    inputs = _load_inputs(repository_root)
    states_by_image = {row["image_sha256"]: row for row in inputs["states"]}
    parse_counts: Counter[str] = Counter()
    unknown_observations = 0
    native_inside_target = 0
    normalized_inside_target = 0
    native_nearest_target = 0
    normalized_nearest_target = 0
    parsed_rows = 0
    xs: list[int] = []
    ys: list[int] = []
    request_failures = 0
    paid_cost_usd = 0.0
    paid_prompt_tokens = 0
    paid_completion_tokens = 0

    for row in predictions:
        parse_counts[str(row.get("parse_status"))] += 1
        request_failures += row.get("request_failure") is not None
        if row.get("cache_hit") is False and isinstance(row.get("usage"), dict):
            usage = row["usage"]
            paid_cost_usd += float(usage.get("cost", 0.0))
            paid_prompt_tokens += int(usage.get("prompt_tokens", 0))
            paid_completion_tokens += int(usage.get("completion_tokens", 0))
        action = row.get("parsed_action")
        if row.get("parse_status") != "parsed" or not isinstance(action, dict):
            continue
        parsed_rows += 1
        x, y = int(action["x"]), int(action["y"])
        xs.append(x)
        ys.append(y)
        state = states_by_image.get(row.get("observation_sha256"))
        if state is None:
            unknown_observations += 1
            continue
        candidates = inputs["candidate_by_id"][state["state_id"]]
        target = episode_for_seed(int(row["seed"]))["stages"][state["stage"]]["target"]
        target_candidate = next(
            candidate for candidate in candidates if candidate["semantic_id"] == target
        )
        scaled_x = min(max(round(x * V4C_WIDTH / 1000), 0), V4C_WIDTH - 1)
        scaled_y = min(max(round(y * V4C_HEIGHT / 1000), 0), V4C_HEIGHT - 1)
        native_inside_target += _contains(target_candidate["bbox"], x, y)
        normalized_inside_target += _contains(target_candidate["bbox"], scaled_x, scaled_y)
        native_nearest_target += _nearest(candidates, x, y) == target
        normalized_nearest_target += _nearest(candidates, scaled_x, scaled_y) == target

    return {
        "schema_version": "pixelgym-grounding-v4c-floor-audit-v1",
        "protocol_version": results["protocol_version"],
        "model": results["model"],
        "sources": {
            "predictions": {
                "path": expected_path,
                "sha256": prediction_sha256,
            },
            "results": {
                "path": results_path.relative_to(repository_root).as_posix(),
                "sha256": _sha256(results_path),
            },
        },
        "stored_response_checks": {
            "record_count": len(predictions),
            "parsed_action_count": parsed_rows,
            "parse_status_counts": dict(sorted(parse_counts.items())),
            "request_failure_count": request_failures,
            "unknown_observation_hash_count": unknown_observations,
        },
        "coordinate_frame": {
            "native_screen": {"width": V4C_WIDTH, "height": V4C_HEIGHT},
            "observed_x_range": _observed_range(xs),
            "observed_y_range": _observed_range(ys),
            "tested_normalized_grid": {"width": 1000, "height": 1000},
            "native_point_inside_correct_target_count": native_inside_target,
            "normalized_point_inside_correct_target_count": normalized_inside_target,
            "native_semantic_nearest_target_count": native_nearest_target,
            "normalized_semantic_nearest_target_count": normalized_nearest_target,
        },
        "provider_usage": {
            "paid_request_count": results["collection"]["new_call_count"],
            "prompt_tokens": paid_prompt_tokens,
            "completion_tokens": paid_completion_tokens,
            "reported_cost_usd": round(paid_cost_usd, 8),
        },
        "interpretation": (
            "The frozen observed score remains unchanged. The 1000x1000 transform is an offline "
            "diagnostic indicating a coordinate-frame mismatch; applying it in a future scored run "
            "would require a new frozen adapter protocol and explicit paid-call approval."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = audit(root, args.predictions.resolve(), args.results.resolve())
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output.is_file() and args.output.read_text(encoding="utf-8") != encoded:
        raise ValueError("refusing to overwrite different immutable v4c floor audit")
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
