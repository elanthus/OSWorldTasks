"""Deterministic set-of-marks rendering with target-independent proposals."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from pixelgym.grounding.schema import PROTOCOL_VERSION, validate_bbox, validate_candidate_set
from pixelgym.serialization import canonical_json_text, load_jsonl
from pixelgym.tasks.vendor_form.render import BOLD_FONT

OVERLAY_VERSION = "pixelgym-set-of-marks-v1"
OVERLAY_SCHEMA_VERSION = "pixelgym-grounding-overlay-v1"

_BOX_COLOR = (215, 38, 61)
_BADGE_COLOR = (145, 18, 38)
_BADGE_TEXT = (255, 255, 255)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ordered_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a new stable spatial ordering without reading any target data."""
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate["bbox"][1],
            candidate["bbox"][0],
            candidate["semantic_id"],
        ),
    )


def proposal_match(target_id: str, marks: list[dict[str, Any]]) -> tuple[bool, int | None]:
    matches = [mark for mark in marks if mark["semantic_id"] == target_id]
    if len(matches) > 1:
        raise ValueError(f"target {target_id!r} has multiple proposal matches")
    if not matches:
        return False, None
    return True, int(matches[0]["mark_id"])


def _boxes_overlap(first: list[int], second: list[int]) -> bool:
    return (
        max(first[0], second[0]) < min(first[2], second[2])
        and max(first[1], second[1]) < min(first[3], second[3])
    )


def _badge_position(
    draw: ImageDraw.ImageDraw,
    *,
    mark_id: int,
    bbox: list[int],
    element_type: str,
    font: ImageFont.FreeTypeFont,
    width: int,
    height: int,
    forbidden_bboxes: list[list[int]],
) -> list[int]:
    text = str(mark_id)
    text_box = draw.textbbox((0, 0), text, font=font)
    badge_width = math.ceil(text_box[2] - text_box[0]) + 6
    badge_height = math.ceil(text_box[3] - text_box[1]) + 4
    if badge_width > width or badge_height > height:
        raise ValueError("mark badge cannot fit inside the image")
    x0, y0, x1, y1 = bbox
    positions: list[tuple[int, int]] = []
    vertical_anchors = (y0, y1 - badge_height, (y0 + y1 - badge_height) // 2)
    horizontal_anchors = (x0, x1 - badge_width, (x0 + x1 - badge_width) // 2)
    for gap in range(2, 66, 2):
        if element_type == "radio":
            positions.extend((badge_x, y1 + gap) for badge_x in horizontal_anchors)
        positions.extend((x1 + gap, badge_y) for badge_y in vertical_anchors)
        positions.extend(
            (x0 - gap - badge_width, badge_y) for badge_y in vertical_anchors
        )
        positions.extend((badge_x, y0 - gap - badge_height) for badge_x in horizontal_anchors)
        positions.extend((badge_x, y1 + gap) for badge_x in horizontal_anchors)
    for badge_x, badge_y in positions:
        badge_bbox = [
            badge_x,
            badge_y,
            badge_x + badge_width,
            badge_y + badge_height,
        ]
        if (
            0 <= badge_x
            and 0 <= badge_y
            and badge_x + badge_width <= width
            and badge_y + badge_height <= height
            and not any(_boxes_overlap(badge_bbox, other) for other in forbidden_bboxes)
        ):
            return badge_bbox
    raise ValueError("mark badge cannot be placed without overlapping another box")


def render_overlay(
    raw_image: Image.Image, candidates: list[dict[str, Any]]
) -> tuple[Image.Image, list[dict[str, Any]]]:
    """Render all candidates.

    Deliberately no target argument exists: callers cannot target-condition proposal
    selection, mark order, styling, or placement.
    """
    image = raw_image.convert("RGB").copy()
    width, height = image.size
    validate_candidate_set(candidates, width=width, height=height)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(BOLD_FONT), 11)
    marks: list[dict[str, Any]] = []
    ordered = ordered_candidates(candidates)
    element_bboxes = [list(candidate["bbox"]) for candidate in ordered]
    for mark_id, candidate in enumerate(ordered, start=1):
        bbox = list(candidate["bbox"])
        x0, y0, x1, y1 = bbox
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=_BOX_COLOR, width=2)
        badge_bbox = _badge_position(
            draw,
            mark_id=mark_id,
            bbox=bbox,
            element_type=candidate["element_type"],
            font=font,
            width=width,
            height=height,
            forbidden_bboxes=element_bboxes
            + [list(mark["badge_bbox"]) for mark in marks],
        )
        bx0, by0, bx1, by1 = badge_bbox
        draw.rectangle((bx0, by0, bx1 - 1, by1 - 1), fill=_BADGE_COLOR)
        draw.text(
            ((bx0 + bx1) / 2, (by0 + by1) / 2),
            str(mark_id),
            font=font,
            fill=_BADGE_TEXT,
            anchor="mm",
        )
        marks.append(
            {
                "mark_id": mark_id,
                "semantic_id": candidate["semantic_id"],
                "element_type": candidate["element_type"],
                "visible_label": candidate["visible_label"],
                "bbox": bbox,
                "badge_bbox": badge_bbox,
            }
        )
    validate_marks(marks, width=width, height=height)
    return image, marks


def validate_marks(marks: Any, *, width: int, height: int) -> None:
    if not isinstance(marks, list) or not marks:
        raise ValueError("marks must be a nonempty list")
    expected_ids = list(range(1, len(marks) + 1))
    actual_ids = [mark.get("mark_id") for mark in marks if isinstance(mark, dict)]
    if actual_ids != expected_ids:
        raise ValueError("mark IDs must be unique sequential integers starting at one")
    semantic_ids: set[str] = set()
    for mark in marks:
        if set(mark) != {
            "mark_id",
            "semantic_id",
            "element_type",
            "visible_label",
            "bbox",
            "badge_bbox",
        }:
            raise ValueError("mark fields do not match the frozen schema")
        if mark["semantic_id"] in semantic_ids:
            raise ValueError("one candidate maps to multiple marks")
        semantic_ids.add(mark["semantic_id"])
        validate_bbox(mark["bbox"], width=width, height=height, name="mark bbox")
        validate_bbox(mark["badge_bbox"], width=width, height=height, name="badge bbox")


def validate_overlay_record(record: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "protocol_version",
        "overlay_version",
        "example_id",
        "raw_image_path",
        "raw_image_sha256",
        "marked_image_path",
        "marked_image_sha256",
        "screen_width",
        "screen_height",
        "marks",
        "target_proposed",
        "target_mark_id",
    }
    if set(record) != required:
        raise ValueError("overlay record fields do not match the frozen schema")
    if record["schema_version"] != OVERLAY_SCHEMA_VERSION:
        raise ValueError("overlay schema version does not match")
    if record["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError("overlay protocol version does not match")
    if record["overlay_version"] != OVERLAY_VERSION:
        raise ValueError("overlay version does not match")
    validate_marks(record["marks"], width=record["screen_width"], height=record["screen_height"])
    mark_ids = {mark["mark_id"] for mark in record["marks"]}
    if record["target_proposed"] != (record["target_mark_id"] in mark_ids):
        raise ValueError("proposal coverage fields are inconsistent")


def build_overlay_contact_sheet(
    records: list[dict[str, Any]], *, repository_root: Path, output_path: Path
) -> None:
    columns = 5
    thumb_width, thumb_height, caption_height = 256, 192, 22
    rows = (len(records) + columns - 1) // columns
    sheet = Image.new(
        "RGB", (columns * thumb_width, rows * (thumb_height + caption_height)), "white"
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, record in enumerate(records):
        marked = Image.open(repository_root / record["marked_image_path"]).convert("RGB")
        marked.thumbnail((thumb_width, thumb_height), Image.Resampling.LANCZOS)
        column, row = index % columns, index // columns
        origin_x = column * thumb_width
        origin_y = row * (thumb_height + caption_height)
        sheet.paste(marked, (origin_x, origin_y))
        draw.text(
            (origin_x + 4, origin_y + thumb_height + 3),
            record["example_id"],
            font=font,
            fill=(20, 20, 20),
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="PNG", optimize=False, compress_level=9)


def generate_overlays(repository_root: Path) -> dict[str, Any]:
    artifact_root = repository_root / "artifacts"
    examples = load_jsonl(artifact_root / "grounding-dataset.jsonl")
    candidate_rows = load_jsonl(artifact_root / "grounding-candidates.jsonl")
    candidates_by_example = {row["example_id"]: row["candidates"] for row in candidate_rows}
    if len(candidates_by_example) != len(candidate_rows):
        raise ValueError("candidate metadata contains duplicate example IDs")
    marked_dir = artifact_root / "grounding" / "images" / "marks"
    marked_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for number, example in enumerate(examples, start=1):
        candidates = candidates_by_example[example["example_id"]]
        raw_path = repository_root / example["image_path"]
        raw_bytes = raw_path.read_bytes()
        if _sha256(raw_bytes) != example["image_sha256"]:
            raise ValueError("raw image digest changed after dataset capture")
        with Image.open(raw_path) as raw_image:
            marked_image, marks = render_overlay(raw_image, candidates)
        marked_path = marked_dir / f"vendor-form-{number:04d}.png"
        marked_image.save(marked_path, format="PNG", optimize=False, compress_level=9)
        marked_bytes = marked_path.read_bytes()

        # Coverage is joined only after target-independent rendering and mapping.
        target_proposed, target_mark_id = proposal_match(example["target_id"], marks)
        record = {
            "schema_version": OVERLAY_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "overlay_version": OVERLAY_VERSION,
            "example_id": example["example_id"],
            "raw_image_path": example["image_path"],
            "raw_image_sha256": example["image_sha256"],
            "marked_image_path": marked_path.relative_to(repository_root).as_posix(),
            "marked_image_sha256": _sha256(marked_bytes),
            "screen_width": example["screen_width"],
            "screen_height": example["screen_height"],
            "marks": marks,
            "target_proposed": target_proposed,
            "target_mark_id": target_mark_id,
        }
        validate_overlay_record(record)
        records.append(record)
    mapping_path = artifact_root / "grounding-overlays.jsonl"
    mapping_path.write_text(
        "".join(canonical_json_text(row) + "\n" for row in records), encoding="utf-8"
    )
    contact_sheet_path = artifact_root / "grounding" / "marks-contact-sheet.png"
    build_overlay_contact_sheet(
        records, repository_root=repository_root, output_path=contact_sheet_path
    )
    summary = {
        "protocol_version": PROTOCOL_VERSION,
        "overlay_version": OVERLAY_VERSION,
        "example_count": len(records),
        "marked_image_count": len(records),
        "proposal_covered_count": sum(row["target_proposed"] for row in records),
        "proposal_coverage": (
            sum(row["target_proposed"] for row in records) / len(records) if records else None
        ),
        "mapping_path": mapping_path.relative_to(repository_root).as_posix(),
        "marked_image_directory": marked_dir.relative_to(repository_root).as_posix(),
        "contact_sheet_path": contact_sheet_path.relative_to(repository_root).as_posix(),
        "automatic_checks_passed": len(records) == 100,
    }
    if not summary["automatic_checks_passed"]:
        raise ValueError("overlay output does not contain all 100 examples")
    (artifact_root / "grounding-overlay-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    return summary
