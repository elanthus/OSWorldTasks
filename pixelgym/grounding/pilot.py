"""Generate the human-review package for the frozen ten-example pilot."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from pixelgym.grounding.evaluation import PROMPT_VERSION, prompt_for
from pixelgym.grounding.schema import PROTOCOL_VERSION
from pixelgym.serialization import load_jsonl
from pixelgym.tasks.vendor_form.render import BOLD_FONT, REGULAR_FONT

PILOT_AUDIT_VERSION = "pixelgym-grounding-pilot-audit-v1"


def _annotate_panel(
    image: Image.Image,
    *,
    bbox: list[int],
    point: list[float] | None,
    correct: bool,
) -> Image.Image:
    annotated = image.convert("RGB").copy()
    draw = ImageDraw.Draw(annotated)
    x0, y0, x1, y1 = bbox
    draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=(35, 92, 190), width=3)
    if point is not None:
        x, y = round(point[0]), round(point[1])
        color = (20, 145, 70) if correct else (220, 30, 45)
        radius = 8
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline="white", width=2)
    return annotated


def _pair_image(
    *,
    repository_root: Path,
    example: dict[str, Any],
    records: dict[str, dict[str, Any]],
    output_path: Path,
) -> None:
    raw_record = records["raw"]
    marks_record = records["marks"]
    raw = _annotate_panel(
        Image.open(repository_root / raw_record["image_path"]),
        bbox=example["bbox"],
        point=raw_record["point"],
        correct=raw_record["correct"],
    )
    marks = _annotate_panel(
        Image.open(repository_root / marks_record["image_path"]),
        bbox=example["bbox"],
        point=marks_record["point"],
        correct=marks_record["correct"],
    )
    header_height = 92
    combined = Image.new("RGB", (raw.width + marks.width, raw.height + header_height), "white")
    combined.paste(raw, (0, header_height))
    combined.paste(marks, (raw.width, header_height))
    draw = ImageDraw.Draw(combined)
    heading = ImageFont.truetype(str(BOLD_FONT), 16)
    body = ImageFont.truetype(str(REGULAR_FONT), 13)
    draw.text((12, 8), example["target"], font=heading, fill=(20, 20, 20))
    draw.text(
        (12, 34),
        f"RAW: {raw_record['raw_response'].strip()}  correct={raw_record['correct']}",
        font=body,
        fill=(20, 20, 20),
    )
    draw.text(
        (raw.width + 12, 34),
        f"MARKS: {marks_record['raw_response'].strip()}  correct={marks_record['correct']}",
        font=body,
        fill=(20, 20, 20),
    )
    draw.text(
        (12, 58),
        "Blue box = frozen target; green point = correct; red point = incorrect",
        font=body,
        fill=(45, 45, 45),
    )
    draw.line((raw.width, 0, raw.width, combined.height), fill=(160, 160, 160), width=1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.save(output_path, format="PNG", optimize=False, compress_level=9)


def _contact_sheet(pair_paths: list[Path], output_path: Path) -> None:
    columns = 2
    cell_width, cell_height = 1024, 430
    rows = (len(pair_paths) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
    for index, path in enumerate(pair_paths):
        pair = Image.open(path).convert("RGB")
        pair.thumbnail((cell_width, cell_height), Image.Resampling.LANCZOS)
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        sheet.paste(pair, (x, y))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="PNG", optimize=False, compress_level=9)


def generate_pilot_review(repository_root: Path, predictions_path: Path) -> dict[str, Any]:
    predictions = load_jsonl(predictions_path)
    examples = {
        row["example_id"]: row
        for row in load_jsonl(repository_root / "artifacts" / "grounding-dataset.jsonl")
    }
    overlays = {
        row["example_id"]: row
        for row in load_jsonl(repository_root / "artifacts" / "grounding-overlays.jsonl")
    }
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in predictions:
        if record["example_id"] not in examples:
            raise ValueError("pilot prediction references an unknown example")
        if record["condition"] in grouped[record["example_id"]]:
            raise ValueError("pilot has duplicate example-condition records")
        grouped[record["example_id"]][record["condition"]] = record
    if len(predictions) != 20 or len(grouped) != 10:
        raise ValueError("pilot must contain exactly ten paired examples")
    if any(set(pair) != {"raw", "marks"} for pair in grouped.values()):
        raise ValueError("every pilot example must have both conditions")

    parse_counts = Counter(record["parse_status"] for record in predictions)
    condition_correct = Counter(record["condition"] for record in predictions if record["correct"])
    condition_counts = Counter(record["condition"] for record in predictions)
    request_failure_count = sum(record["request_failure"] is not None for record in predictions)
    invalid_count = sum(record["parse_status"] != "parsed" for record in predictions)
    safe_metadata = all(
        set(record["provider_metadata"]) <= {"cli_version", "exit_code"} for record in predictions
    )
    raw_text = predictions_path.read_text()
    credential_scan_clean = not any(
        marker in raw_text for marker in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "Bearer ", "sk-")
    )
    per_example = []
    pair_paths = []
    annotated_dir = predictions_path.parent / "annotated"
    for example_id, pair in grouped.items():
        example = examples[example_id]
        overlay = overlays[example_id]
        raw_prompt = prompt_for(example, "raw")
        marks_prompt = prompt_for(example, "marks")
        target_equivalent = example["target"] in raw_prompt and example["target"] in marks_prompt
        raw_point_in_bounds = pair["raw"]["point"] is None or (
            0 <= pair["raw"]["point"][0] < example["screen_width"]
            and 0 <= pair["raw"]["point"][1] < example["screen_height"]
        )
        selected_mark_exists = pair["marks"]["mark_id"] is None or any(
            mark["mark_id"] == pair["marks"]["mark_id"] for mark in overlay["marks"]
        )
        per_example.append(
            {
                "example_id": example_id,
                "target": example["target"],
                "element_type": example["element_type"],
                "screen_state": example["screen_state"],
                "target_equivalent_between_conditions": target_equivalent,
                "raw_point_in_bounds": raw_point_in_bounds,
                "selected_mark_exists": selected_mark_exists,
                "target_proposed": overlay["target_proposed"],
                "raw_correct": pair["raw"]["correct"],
                "marks_correct": pair["marks"]["correct"],
                "raw_response": pair["raw"]["raw_response"],
                "marks_response": pair["marks"]["raw_response"],
            }
        )
        pair_path = annotated_dir / f"{example_id}.png"
        _pair_image(
            repository_root=repository_root,
            example=example,
            records=pair,
            output_path=pair_path,
        )
        pair_paths.append(pair_path)

    output_dir = predictions_path.parent
    contact_sheet_path = output_dir / "contact-sheet.png"
    _contact_sheet(pair_paths, contact_sheet_path)
    latencies = [float(record["latency_ms"]) for record in predictions]
    audit = {
        "schema_version": PILOT_AUDIT_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "provider": predictions[0]["provider"],
        "model": predictions[0]["model"],
        "example_count": len(grouped),
        "condition_call_count": len(predictions),
        "request_failure_count": request_failure_count,
        "invalid_or_unparseable_count": invalid_count,
        "parse_status_counts": dict(sorted(parse_counts.items())),
        "raw_correct_count": condition_correct["raw"],
        "raw_accuracy": condition_correct["raw"] / condition_counts["raw"],
        "marks_correct_count": condition_correct["marks"],
        "marks_accuracy": condition_correct["marks"] / condition_counts["marks"],
        "proposal_covered_count": sum(item["target_proposed"] for item in per_example),
        "proposal_coverage": sum(item["target_proposed"] for item in per_example)
        / len(per_example),
        "all_targets_equivalent_between_conditions": all(
            item["target_equivalent_between_conditions"] for item in per_example
        ),
        "all_raw_points_in_bounds": all(item["raw_point_in_bounds"] for item in per_example),
        "all_selected_marks_exist": all(item["selected_mark_exists"] for item in per_example),
        "credential_scan_clean": credential_scan_clean,
        "provider_metadata_allowlist_clean": safe_metadata,
        "latency_ms": {
            "mean": statistics.fmean(latencies),
            "median": statistics.median(latencies),
            "minimum": min(latencies),
            "maximum": max(latencies),
        },
        "predictions_path": predictions_path.relative_to(repository_root).as_posix(),
        "contact_sheet_path": contact_sheet_path.relative_to(repository_root).as_posix(),
        "per_example": per_example,
    }
    audit_path = output_dir / "audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    markdown = [
        "# Grounding Pilot Audit",
        "",
        f"- Protocol: `{PROTOCOL_VERSION}`",
        f"- Provider/model: `{audit['provider']}` / `{audit['model']}`",
        f"- Calls: {audit['condition_call_count']} ({audit['example_count']} paired examples)",
        f"- Parse failures: {audit['invalid_or_unparseable_count']}",
        f"- Request failures: {audit['request_failure_count']}",
        f"- Raw accuracy: {audit['raw_correct_count']}/10 ({audit['raw_accuracy']:.0%})",
        f"- Marks accuracy: {audit['marks_correct_count']}/10 ({audit['marks_accuracy']:.0%})",
        f"- Proposal coverage: {audit['proposal_covered_count']}/10 ({audit['proposal_coverage']:.0%})",
        f"- Same target text in both prompts: {audit['all_targets_equivalent_between_conditions']}",
        f"- Raw coordinate bounds valid: {audit['all_raw_points_in_bounds']}",
        f"- Selected mark IDs exist: {audit['all_selected_marks_exist']}",
        f"- Credential marker scan clean: {audit['credential_scan_clean']}",
        "",
        "Blue boxes in the review images are frozen ground truth and were added only after",
        "model collection. Green points are correct; red points are incorrect. The first",
        "three raw misses land on the visually similar read-only request card, which must be",
        "considered during the human ambiguity review rather than silently excluded.",
        "",
    ]
    (output_dir / "audit.md").write_text("\n".join(markdown))
    return audit
