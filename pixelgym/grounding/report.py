"""Deterministic, offline figures and report for stored grounding evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from pixelgym.grounding.analysis import analyze_predictions
from pixelgym.grounding.evaluation import PROMPT_VERSION
from pixelgym.grounding.schema import PROTOCOL_VERSION
from pixelgym.serialization import load_jsonl, resolve_repository_output
from pixelgym.tasks.vendor_form.render import BOLD_FONT, REGULAR_FONT

REPORT_VERSION = "pixelgym-grounding-report-v1"
FIGURE_VERSION = "pixelgym-grounding-figure-v1"

_BLUE = (35, 92, 190)
_RAW = (66, 105, 165)
_MARKS = (37, 151, 92)
_RED = (205, 51, 67)
_INK = (28, 31, 35)
_MUTED = (93, 99, 107)
_GRID = (220, 224, 230)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(BOLD_FONT if bold else REGULAR_FONT), size)


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percent(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{100 * value:.{digits}f}%"


def _decimal(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _annotate(
    image: Image.Image, bbox: list[int], point: list[float] | None, correct: bool
) -> Image.Image:
    result = image.convert("RGB").copy()
    draw = ImageDraw.Draw(result)
    x0, y0, x1, y1 = bbox
    draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=_BLUE, width=3)
    if point is not None:
        x, y = round(point[0]), round(point[1])
        color = _MARKS if correct else _RED
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=color, outline="white", width=2)
    return result


def render_pair_image(
    *,
    repository_root: Path,
    example: dict[str, Any],
    raw_record: dict[str, Any],
    marks_record: dict[str, Any],
    output_path: Path,
    subtitle: str,
) -> None:
    raw = _annotate(
        Image.open(repository_root / raw_record["image_path"]),
        example["bbox"],
        raw_record["point"],
        raw_record["correct"],
    )
    marks = _annotate(
        Image.open(repository_root / marks_record["image_path"]),
        example["bbox"],
        marks_record["point"],
        marks_record["correct"],
    )
    header_height = 108
    image = Image.new("RGB", (raw.width + marks.width, raw.height + header_height), "white")
    image.paste(raw, (0, header_height))
    image.paste(marks, (raw.width, header_height))
    draw = ImageDraw.Draw(image)
    draw.text((12, 9), example["target"], font=_font(17, bold=True), fill=_INK)
    draw.text((12, 37), subtitle, font=_font(13), fill=_MUTED)
    draw.text(
        (12, 64),
        f"RAW {raw_record['raw_response']!r} | correct={raw_record['correct']}",
        font=_font(12),
        fill=_INK,
    )
    draw.text(
        (raw.width + 12, 64),
        f"MARKS {marks_record['raw_response']!r} | correct={marks_record['correct']}",
        font=_font(12),
        fill=_INK,
    )
    draw.text(
        (12, 86),
        "Blue = frozen target; green = correct prediction; red = incorrect prediction",
        font=_font(11),
        fill=_MUTED,
    )
    draw.line((raw.width, 0, raw.width, image.height), fill=(160, 160, 160), width=1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=False, compress_level=9)


def render_error_review_images(
    *,
    repository_root: Path,
    examples: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    error_reviews: list[dict[str, Any]],
) -> None:
    example_by_id = {row["example_id"]: row for row in examples}
    records = {(row["example_id"], row["condition"]): row for row in predictions}
    for review in error_reviews:
        example_id = review["example_id"]
        output_path = resolve_repository_output(
            repository_root, review["review_image_path"]
        )
        render_pair_image(
            repository_root=repository_root,
            example=example_by_id[example_id],
            raw_record=records[(example_id, "raw")],
            marks_record=records[(example_id, "marks")],
            output_path=output_path,
            subtitle=(
                f"Review {review['condition']} error | suggested: "
                f"{', '.join(review['categories'])} | "
                f"status: {review['review_status']}"
            ),
        )


def write_accuracy_figure(results: dict[str, Any], output_path: Path) -> None:
    width, height = 1000, 620
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((54, 32), "Raw coordinates vs set-of-marks", font=_font(30, bold=True), fill=_INK)
    draw.text(
        (54, 76),
        f"{results['collection']['example_count']} paired examples | {results['model']}",
        font=_font(17),
        fill=_MUTED,
    )
    chart_left, chart_right, chart_top, chart_bottom = 120, 640, 130, 470
    for tick in range(0, 101, 20):
        y = chart_bottom - (chart_bottom - chart_top) * tick / 100
        draw.line((chart_left, y, chart_right, y), fill=_GRID, width=1)
        draw.text((66, y - 9), f"{tick}%", font=_font(13), fill=_MUTED)
    values = [results["conditions"][name]["accuracy"] for name in ("raw", "marks")]
    labels = ["Raw", "Marks"]
    colors = [_RAW, _MARKS]
    centers = [280, 500]
    for label, value, color, center in zip(labels, values, colors, centers, strict=True):
        top = chart_bottom - (chart_bottom - chart_top) * value
        draw.rounded_rectangle((center - 65, top, center + 65, chart_bottom), 8, fill=color)
        draw.text((center - 35, chart_bottom + 18), label, font=_font(18, bold=True), fill=_INK)
        draw.text((center - 34, top - 30), _percent(value), font=_font(17, bold=True), fill=color)

    delta = results["paired"]["delta_percentage_points"]
    low, high = results["paired"]["bootstrap_95_ci_percentage_points"]
    draw.text((700, 152), "Paired difference", font=_font(19, bold=True), fill=_INK)
    draw.text(
        (680, 190),
        f"{delta:+.1f} percentage points",
        font=_font(19, bold=True),
        fill=_MARKS,
    )
    draw.text((700, 229), f"95% bootstrap CI [{low:+.1f}, {high:+.1f}]", font=_font(15), fill=_INK)
    draw.text(
        (700, 263),
        f"Exact McNemar p = {results['paired']['mcnemar_exact_p_value']:.4g}",
        font=_font(15),
        fill=_INK,
    )
    axis_left, axis_right, axis_y = 710, 930, 350
    draw.line((axis_left, axis_y, axis_right, axis_y), fill=_INK, width=2)
    zero_x = (axis_left + axis_right) / 2
    draw.line((zero_x, axis_y - 30, zero_x, axis_y + 30), fill=_MUTED, width=1)
    for tick in (-100, -50, 0, 50, 100):
        x = axis_left + (tick + 100) / 200 * (axis_right - axis_left)
        draw.line((x, axis_y - 5, x, axis_y + 5), fill=_INK, width=1)
        draw.text((x - 15, axis_y + 13), str(tick), font=_font(11), fill=_MUTED)
    low_x = axis_left + (low + 100) / 200 * (axis_right - axis_left)
    high_x = axis_left + (high + 100) / 200 * (axis_right - axis_left)
    delta_x = axis_left + (delta + 100) / 200 * (axis_right - axis_left)
    draw.line((low_x, axis_y, high_x, axis_y), fill=_MARKS, width=7)
    draw.ellipse((delta_x - 7, axis_y - 7, delta_x + 7, axis_y + 7), fill=_INK)
    draw.text((700, 418), "CI is for the paired accuracy difference.", font=_font(13), fill=_MUTED)
    draw.text(
        (54, 552),
        f"Figure version: {FIGURE_VERSION} | bootstrap seed "
        f"{results['analysis_config']['paired_bootstrap_seed']}",
        font=_font(12),
        fill=_MUTED,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=False, compress_level=9)


def write_control_type_figure(results: dict[str, Any], output_path: Path) -> None:
    slices = results["slices"]["element_type"]
    labels = list(slices)
    width, row_height = 1100, 76
    height = 150 + row_height * len(labels)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((42, 24), "Descriptive accuracy by control type", font=_font(28, bold=True), fill=_INK)
    draw.text(
        (42, 67),
        "Target identity is aliased with screen state; these are not independent effects.",
        font=_font(15),
        fill=_MUTED,
    )
    x0, x1 = 320, 1020
    for index, label in enumerate(labels):
        row = slices[label]
        y = 116 + index * row_height
        draw.text((42, y + 17), f"{label} (n={row['example_count']})", font=_font(16), fill=_INK)
        draw.line((x0, y + 18, x1, y + 18), fill=_GRID, width=12)
        draw.line(
            (x0, y + 18, x0 + (x1 - x0) * row["raw_accuracy"], y + 18),
            fill=_RAW,
            width=12,
        )
        draw.line((x0, y + 45, x1, y + 45), fill=_GRID, width=12)
        draw.line(
            (x0, y + 45, x0 + (x1 - x0) * row["marks_accuracy"], y + 45),
            fill=_MARKS,
            width=12,
        )
        draw.text((1030, y + 8), _percent(row["raw_accuracy"], 0), font=_font(12), fill=_RAW)
        draw.text((1030, y + 35), _percent(row["marks_accuracy"], 0), font=_font(12), fill=_MARKS)
    draw.rectangle((42, height - 30, 58, height - 14), fill=_RAW)
    draw.text((65, height - 33), "Raw", font=_font(12), fill=_INK)
    draw.rectangle((120, height - 30, 136, height - 14), fill=_MARKS)
    draw.text((143, height - 33), "Marks", font=_font(12), fill=_INK)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=False, compress_level=9)


def write_gallery(
    *,
    repository_root: Path,
    examples: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    output_directory: Path,
) -> list[dict[str, Any]]:
    output_directory.mkdir(parents=True, exist_ok=True)
    example_by_id = {row["example_id"]: row for row in examples}
    records: dict[str, dict[str, dict[str, Any]]] = {}
    for record in predictions:
        records.setdefault(record["example_id"], {})[record["condition"]] = record
    buckets: dict[str, list[str]] = {
        "marks_win": [],
        "raw_win": [],
        "unchanged_correct": [],
        "unchanged_incorrect": [],
    }
    for example in examples:
        pair = records[example["example_id"]]
        raw, marks = pair["raw"]["correct"], pair["marks"]["correct"]
        if not raw and marks:
            bucket = "marks_win"
        elif raw and not marks:
            bucket = "raw_win"
        elif raw and marks:
            bucket = "unchanged_correct"
        else:
            bucket = "unchanged_incorrect"
        buckets[bucket].append(example["example_id"])
    manifest = []
    for bucket, ids in buckets.items():
        limit = 3 if bucket == "marks_win" else 2
        selected_ids = []
        selected_types: set[str] = set()
        for example_id in ids:
            element_type = example_by_id[example_id]["element_type"]
            if element_type in selected_types:
                continue
            selected_ids.append(example_id)
            selected_types.add(element_type)
            if len(selected_ids) == limit:
                break
        for example_id in selected_ids:
            pair = records[example_id]
            relative = Path("artifacts/grounding/gallery") / f"{bucket}-{example_id}.png"
            render_pair_image(
                repository_root=repository_root,
                example=example_by_id[example_id],
                raw_record=pair["raw"],
                marks_record=pair["marks"],
                output_path=repository_root / relative,
                subtitle=bucket.replace("_", " "),
            )
            manifest.append(
                {
                    "case": bucket,
                    "example_id": example_id,
                    "image_path": relative.as_posix(),
                    "raw_correct": pair["raw"]["correct"],
                    "marks_correct": pair["marks"]["correct"],
                }
            )
    expected_images = {repository_root / item["image_path"] for item in manifest}
    generated_prefixes = tuple(f"{bucket}-" for bucket in buckets)
    for existing in output_directory.glob("*.png"):
        if existing.name.startswith(generated_prefixes) and existing not in expected_images:
            existing.unlink()
    (output_directory / "manifest.json").write_text(_json_text(manifest))
    return manifest


def _report_markdown(results: dict[str, Any], gallery: list[dict[str, Any]]) -> str:
    raw = results["conditions"]["raw"]
    marks = results["conditions"]["marks"]
    paired = results["paired"]
    som = results["set_of_marks"]
    low, high = paired["bootstrap_95_ci_percentage_points"]
    review = results["error_taxonomy"]
    raw_distance = raw["normalized_center_distance"]
    marks_distance = marks["normalized_center_distance"]
    coordinate_scaling_count = review["category_counts"].get("coordinate scaling error", 0)
    coordinate_scaling_clause = (
        "label is a reviewer inference"
        if coordinate_scaling_count == 1
        else "labels are reviewer inferences"
    )
    review_warning = (
        "All error labels were manually inspected."
        if review["all_manually_reviewed"]
        else "Error labels remain pending visual review; do not treat the taxonomy as final."
    )
    lines = [
        "# PixelGym Grounding Experiment",
        "",
        f"- Protocol: `{PROTOCOL_VERSION}`",
        f"- Prompt: `{PROMPT_VERSION}`",
        f"- Provider/model: `{results['provider']}` / `{results['model']}`",
        (
            f"- Collection window: {results['collection']['first_timestamp_utc'] or 'missing'} "
            f"to {results['collection']['last_timestamp_utc'] or 'missing'}"
        ),
        (
            f"- Sample: {results['collection']['example_count']} examples, "
            f"{results['collection']['condition_record_count']} condition records, "
            f"{results['collection']['excluded_example_count']} exclusions"
        ),
        "",
        "## Main result",
        "",
        (
            f"Raw-coordinate accuracy was **{raw['correct_count']}/{raw['record_count']} "
            f"({_percent(raw['accuracy'])})**. Set-of-marks accuracy was "
            f"**{marks['correct_count']}/{marks['record_count']} ({_percent(marks['accuracy'])})**. "
            f"The paired difference was **{paired['delta_percentage_points']:+.1f} percentage "
            f"points** with a percentile-bootstrap 95% CI of **[{low:+.1f}, {high:+.1f}]** "
            f"and a two-sided exact McNemar p-value of "
            f"**{paired['mcnemar_exact_p_value']:.4g}**."
        ),
        "",
        "![Raw-versus-marks accuracy](grounding/figures/raw-vs-marks-accuracy.png)",
        "",
        (
            "This estimates the paired effect of adding the frozen marks overlay for this model, "
            "task family, prompt, and capture setup. It does not establish a mechanism or "
            "generalize to other GUI tasks or models."
        ),
        "",
        "## Proposal coverage and conditional selection",
        "",
        (
            f"Proposal coverage was **{som['proposal_covered_count']}/"
            f"{som['proposal_total_count']} ({_percent(som['proposal_coverage'])})**. Conditional "
            f"on the target being proposed, mark-selection accuracy was "
            f"**{som['conditional_selection_correct_count']}/"
            f"{som['conditional_selection_total_count']} "
            f"({_percent(som['conditional_selection_accuracy'])})**. These are reported "
            "separately; selection accuracy does not absorb proposal failures."
        ),
        "",
        "## Data integrity and output failures",
        "",
        (
            f"Invalid output rates were raw={_percent(raw['invalid_output_rate'])} and "
            f"marks={_percent(marks['invalid_output_rate'])}. Request-failure rates were "
            f"raw={_percent(raw['request_failure_rate'])} and "
            f"marks={_percent(marks['request_failure_rate'])}. Invalid outputs and request "
            "failures remain in the denominator and score as incorrect. No examples were excluded."
        ),
        "",
        "## Slices",
        "",
        "![Descriptive accuracy by control type](grounding/figures/control-type-accuracy.png)",
        "",
        (
            "**Design limitation:** target identity and screen state are perfectly aliased in the "
            "frozen capture grid: each target appears in exactly one of the five screen states. "
            "The control-type rows below are descriptive compositions only; differences cannot be "
            "attributed independently to control type rather than screen state."
        ),
        "",
        "| Element type | n | Raw | Marks | Delta (pp) |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, row in results["slices"]["element_type"].items():
        lines.append(
            f"| {label} | {row['example_count']} | {_percent(row['raw_accuracy'])} | "
            f"{_percent(row['marks_accuracy'])} | {row['paired_delta_percentage_points']:+.1f} |"
        )
    lines.extend(
        [
            "",
            "| Target size | n | Raw | Marks | Delta (pp) |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, row in results["slices"]["target_size"].items():
        lines.append(
            f"| {label} | {row['example_count']} | {_percent(row['raw_accuracy'])} | "
            f"{_percent(row['marks_accuracy'])} | {row['paired_delta_percentage_points']:+.1f} |"
        )
    lines.extend(
        [
            "",
            "## Normalized center distance",
            "",
            (
                f"For parsed raw points, screenshot-diagonal-normalized center distance had mean "
                f"{_decimal(raw_distance['mean'])}, median {_decimal(raw_distance['median'])}, "
                f"and p90 {_decimal(raw_distance['p90'])}. The marked condition had mean "
                f"{_decimal(marks_distance['mean'])}; valid selected marks are converted to their "
                "candidate centers by the frozen scoring rule, so a correct marked selection has "
                "distance zero by construction."
            ),
            "",
            "## Error review",
            "",
            review_warning,
            "",
            "| Category | Error records |",
            "|---|---:|",
        ]
    )
    for category, count in review["category_counts"].items():
        lines.append(f"| {category} | {count} |")
    lines.extend(
        [
            "",
            (
                f"Categories are non-exclusive, so their counts can sum above the "
                f"{review['error_record_count']} error records. The "
                f"{coordinate_scaling_count} coordinate-scaling {coordinate_scaling_clause} "
                "from horizontal "
                "alignment and displacement, not proof of the causal mechanism."
            ),
            "",
            "## Representative examples",
            "",
        ]
    )
    if gallery:
        for item in gallery:
            lines.append(
                f"- `{item['case']}` — `{item['example_id']}`: "
                f"[{item['image_path']}](./{Path(item['image_path']).relative_to('artifacts')})"
            )
    else:
        lines.append("No gallery examples were available.")
    if paired["raw_only_correct_count"] == 0:
        lines.append("- `raw_win` — none observed (raw correct, marks incorrect).")
    if paired["both_incorrect_count"] == 0:
        lines.append("- `unchanged_incorrect` — none observed (both conditions incorrect).")
    lines.extend(
        [
            "",
            "## Latency and usage",
            "",
            (
                f"The {results['latency']['all']['observed_count']} of "
                f"{results['collection']['condition_record_count']} stored calls with latency "
                f"evidence took {results['latency']['all']['total_ms'] / 1000:.1f} seconds in "
                f"aggregate provider latency (median "
                f"{_decimal(results['latency']['all']['median_ms'], 1)} ms). Usage fields are "
                "summed exactly as returned by the provider in `grounding-results.json`; they are "
                "not converted into a monetary estimate."
            ),
            "",
            "## Limitations",
            "",
            "- One synthetic vendor-onboarding task family, one resolution, and one model were used.",
            (
                "- Target identity is perfectly aliased with screen state in the frozen dataset, "
                "so control-type slices are descriptive and do not identify a control-type effect."
            ),
            "- The model name may be a moving provider alias rather than an immutable snapshot.",
            (
                "- Candidate generation is deterministic and target-agnostic, but the resulting "
                "overlays are specific to this fixed application layout."
            ),
            (
                "- A paired observational result supports the effect of the overlay intervention "
                "in this setup; it does not prove why an error changed."
            ),
            (
                "- Bootstrap intervals describe uncertainty over this frozen example set, not over "
                "all possible applications or model versions."
            ),
            "",
            "## Offline reproduction",
            "",
            "No model or network calls are made by this command:",
            "",
            "```bash",
            ".venv/bin/python scripts/generate_grounding_report.py",
            "```",
            "",
            (
                "Inputs are the frozen dataset, overlays, immutable paired predictions, and "
                "manually reviewed error taxonomy. Their SHA-256 digests are recorded in "
                "`grounding-results.json` together with every example-level outcome and the fixed "
                "analysis configuration."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def generate_results_package(
    *,
    repository_root: Path,
    predictions_path: Path,
    error_review_path: Path,
    results_path: Path,
    report_path: Path,
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 20_260_809,
) -> dict[str, Any]:
    protocol_path = repository_root / "artifacts" / "grounding-protocol.md"
    dataset_path = repository_root / "artifacts" / "grounding-dataset.jsonl"
    overlays_path = repository_root / "artifacts" / "grounding-overlays.jsonl"
    examples = load_jsonl(dataset_path)
    predictions = load_jsonl(predictions_path)
    error_reviews = load_jsonl(error_review_path)
    results = analyze_predictions(
        examples=examples,
        predictions=predictions,
        error_reviews=error_reviews,
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
    )
    results["report_version"] = REPORT_VERSION
    input_paths = [
        protocol_path,
        dataset_path,
        overlays_path,
        predictions_path,
        error_review_path,
    ]
    decisions_path = repository_root / "artifacts" / "grounding-error-review-decisions.json"
    if decisions_path.is_file():
        input_paths.append(decisions_path)
    results["inputs"] = {
        path.relative_to(repository_root).as_posix(): _sha256(path) for path in input_paths
    }
    figures = repository_root / "artifacts" / "grounding" / "figures"
    accuracy_path = figures / "raw-vs-marks-accuracy.png"
    control_path = figures / "control-type-accuracy.png"
    write_accuracy_figure(results, accuracy_path)
    write_control_type_figure(results, control_path)
    gallery = write_gallery(
        repository_root=repository_root,
        examples=examples,
        predictions=predictions,
        output_directory=repository_root / "artifacts" / "grounding" / "gallery",
    )
    results["outputs"] = {
        "accuracy_figure": {
            "path": accuracy_path.relative_to(repository_root).as_posix(),
            "sha256": _sha256(accuracy_path),
        },
        "control_type_figure": {
            "path": control_path.relative_to(repository_root).as_posix(),
            "sha256": _sha256(control_path),
        },
        "gallery_manifest": {
            "path": "artifacts/grounding/gallery/manifest.json",
            "sha256": _sha256(repository_root / "artifacts/grounding/gallery/manifest.json"),
        },
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(_json_text(results))
    report_path.write_text(_report_markdown(results, gallery))
    return results
