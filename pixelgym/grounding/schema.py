"""Frozen grounding-dataset schema and validation helpers.

This module is intentionally independent of both environment backends. Browser
instrumentation imports it while constructing the dataset; the evaluation adapter
consumes only the resulting JSONL and images.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = "pixelgym-grounding-v1"
EXAMPLE_SCHEMA_VERSION = "pixelgym-grounding-example-v1"
CANDIDATE_SCHEMA_VERSION = "pixelgym-grounding-candidates-v1"
CAPTURE_VERSION = "pixelgym-browser-capture-v1"

CSS_WIDTH = 1024
CSS_HEIGHT = 768
DEVICE_SCALE_FACTOR = 1.0
TARGET_AREA_SMALL_BELOW = 0.005
TARGET_AREA_MEDIUM_BELOW = 0.02
TASK_SEEDS = tuple(range(20))
SCREEN_STATES = (
    "initial",
    "text_field_focused",
    "validation_error",
    "partially_completed",
    "completed_review",
)


@dataclass(frozen=True)
class TargetSpec:
    semantic_id: str
    instruction: str
    element_type: str


TARGET_SPECS = (
    TargetSpec("company_name", "Click the Company name field", "text_input"),
    TargetSpec("contact_email", "Click the Contact email field", "text_input"),
    TargetSpec("contact_phone", "Click the Contact phone field", "text_input"),
    TargetSpec("tax_id", "Click the Tax ID field", "text_input"),
    TargetSpec("country", "Click the Country dropdown", "select"),
    TargetSpec("payment_terms_net_15", "Click the Net 15 payment terms option", "radio"),
    TargetSpec("payment_terms_net_30", "Click the Net 30 payment terms option", "radio"),
    TargetSpec("payment_terms_net_45", "Click the Net 45 payment terms option", "radio"),
    TargetSpec(
        "expedited_onboarding",
        "Click the Expedited onboarding checkbox",
        "checkbox",
    ),
    TargetSpec("submit", "Click the Submit button", "button"),
)

_EXAMPLE_KEYS = {
    "schema_version",
    "protocol_version",
    "example_id",
    "image_path",
    "image_sha256",
    "target_id",
    "target",
    "bbox",
    "css_bbox",
    "element_type",
    "task_seed",
    "task_id",
    "screen_state",
    "css_width",
    "css_height",
    "screen_width",
    "screen_height",
    "device_scale_factor",
    "capture_version",
}


def target_for_example_index(zero_based_index: int) -> TargetSpec:
    if zero_based_index < 0:
        raise ValueError("example index must be nonnegative")
    return TARGET_SPECS[zero_based_index % len(TARGET_SPECS)]


def css_bbox_to_screenshot(
    css_bbox: list[float] | tuple[float, float, float, float],
    *,
    css_width: int,
    css_height: int,
    screen_width: int,
    screen_height: int,
) -> list[int]:
    """Transform half-open CSS bounds into containing screenshot-pixel bounds."""
    if css_width <= 0 or css_height <= 0 or screen_width <= 0 or screen_height <= 0:
        raise ValueError("CSS and screenshot dimensions must be positive")
    x0, y0, x1, y1 = (float(value) for value in css_bbox)
    if not (math.isfinite(x0) and math.isfinite(y0) and math.isfinite(x1) and math.isfinite(y1)):
        raise ValueError("CSS bounding box coordinates must be finite")
    if x1 <= x0 or y1 <= y0:
        raise ValueError("CSS bounding box must have nonzero area")
    scale_x = screen_width / css_width
    scale_y = screen_height / css_height
    return [
        math.floor(x0 * scale_x),
        math.floor(y0 * scale_y),
        math.ceil(x1 * scale_x),
        math.ceil(y1 * scale_y),
    ]


def validate_bbox(bbox: Any, *, width: int, height: int, name: str = "bbox") -> None:
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(type(value) is not int for value in bbox)
    ):
        raise ValueError(f"{name} must be a four-integer list")
    x0, y0, x1, y1 = bbox
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError(f"{name} {bbox!r} lies outside {width}x{height}")


def validate_candidate_set(candidates: Any, *, width: int, height: int) -> None:
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a nonempty list")
    semantic_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise TypeError("each candidate must be an object")
        required = {"semantic_id", "element_type", "visible_label", "css_bbox", "bbox"}
        if set(candidate) != required:
            raise ValueError("candidate fields do not match the frozen schema")
        semantic_id = candidate["semantic_id"]
        if not isinstance(semantic_id, str) or not semantic_id:
            raise ValueError("candidate semantic_id must be a nonempty string")
        if semantic_id in semantic_ids:
            raise ValueError(f"duplicate candidate semantic_id {semantic_id!r}")
        semantic_ids.add(semantic_id)
        if not isinstance(candidate["element_type"], str) or not candidate["element_type"]:
            raise ValueError("candidate element_type must be a nonempty string")
        if not isinstance(candidate["visible_label"], str) or not candidate["visible_label"]:
            raise ValueError("candidate visible_label must be a nonempty string")
        css_bbox = candidate["css_bbox"]
        if not isinstance(css_bbox, list) or len(css_bbox) != 4:
            raise ValueError("candidate css_bbox must contain four numbers")
        validate_bbox(candidate["bbox"], width=width, height=height, name="candidate bbox")


def validate_example(example: dict[str, Any]) -> None:
    if set(example) != _EXAMPLE_KEYS:
        missing = sorted(_EXAMPLE_KEYS - set(example))
        extra = sorted(set(example) - _EXAMPLE_KEYS)
        raise ValueError(f"example fields do not match schema; missing={missing}, extra={extra}")
    if example["schema_version"] != EXAMPLE_SCHEMA_VERSION:
        raise ValueError("unknown example schema version")
    if example["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError("example protocol version does not match")
    if example["capture_version"] != CAPTURE_VERSION:
        raise ValueError("example capture version does not match")
    if type(example["task_seed"]) is not int or example["task_seed"] not in TASK_SEEDS:
        raise ValueError("task_seed is outside the frozen seed set")
    if example["screen_state"] not in SCREEN_STATES:
        raise ValueError("screen_state is outside the frozen state set")
    if example["css_width"] != CSS_WIDTH or example["css_height"] != CSS_HEIGHT:
        raise ValueError("CSS dimensions do not match the protocol")
    if example["screen_width"] <= 0 or example["screen_height"] <= 0:
        raise ValueError("screenshot dimensions must be positive")
    if example["device_scale_factor"] != DEVICE_SCALE_FACTOR:
        raise ValueError("device scale factor does not match the protocol")
    if not isinstance(example["image_sha256"], str) or len(example["image_sha256"]) != 64:
        raise ValueError("image_sha256 must be a 64-character digest")
    if not isinstance(example["example_id"], str) or not example["example_id"]:
        raise ValueError("example_id must be a nonempty string")
    if not isinstance(example["image_path"], str) or not example["image_path"].endswith(".png"):
        raise ValueError("image_path must name a PNG")
    target = next((spec for spec in TARGET_SPECS if spec.semantic_id == example["target_id"]), None)
    if target is None:
        raise ValueError("target_id is outside the frozen target set")
    if example["target"] != target.instruction or example["element_type"] != target.element_type:
        raise ValueError("target instruction or type does not match target_id")
    validate_bbox(
        example["bbox"],
        width=example["screen_width"],
        height=example["screen_height"],
    )
    expected_bbox = css_bbox_to_screenshot(
        example["css_bbox"],
        css_width=example["css_width"],
        css_height=example["css_height"],
        screen_width=example["screen_width"],
        screen_height=example["screen_height"],
    )
    if example["bbox"] != expected_bbox:
        raise ValueError("CSS-to-screenshot coordinate transformation does not match")


def target_area_ratio(example: dict[str, Any]) -> float:
    """Return the target box area as a fraction of the full screenshot area."""
    x0, y0, x1, y1 = example["bbox"]
    return float((x1 - x0) * (y1 - y0)) / float(
        example["screen_width"] * example["screen_height"]
    )


def target_area_slice(example: dict[str, Any]) -> str:
    ratio = target_area_ratio(example)
    if ratio < TARGET_AREA_SMALL_BELOW:
        return "small"
    if ratio < TARGET_AREA_MEDIUM_BELOW:
        return "medium"
    return "large"
