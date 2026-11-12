#!/usr/bin/env python3
"""Split the frozen v1 raw-coordinate misses by whether they land on the read-only request card.

Reads ``artifacts/grounding-results.json`` and ``artifacts/grounding-predictions.jsonl``
(cross-checked by the clustered supplement's loader) and
``artifacts/grounding-error-review-decisions.json`` and writes
``artifacts/grounding-ambiguity-split-v1.json`` plus a Markdown rendering of it. The frozen
report and its inputs are not modified. Standard library only; intervals reuse the
clustered supplement's bootstrap functions, generator, and seed so the numbers are comparable.

The vendor form shows a read-only request card in the left half of the screen whose rows sit at
the same heights as the editable form's rows in the right half. A raw miss is classified as
"request card, same row" when its parsed point lies in the left panel (``x < 512``) and its ``y``
lies inside the vertical extent of the target's own frozen box. This rule uses only frozen data:
the point, the target box, and the fixed 1024-pixel screen split. It is a sensitivity analysis,
not a re-scoring: the frozen v1 headline stays 56/100 raw.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    # Run as a file, sys.path[0] is `scripts/`; the package import below needs the repo root.
    sys.path.insert(0, str(REPO_ROOT))

from scripts.generate_grounding_clustered_analysis import (
    PREDICTIONS_PATH,
    RESULTS_PATH,
    SAMPLES,
    SEED,
    cluster_bootstrap,
    example_bootstrap,
    load_rows,
)

SCHEMA_VERSION = "pixelgym-grounding-ambiguity-split-v1"
REVIEW_PATH = "artifacts/grounding-error-review-decisions.json"
JSON_OUTPUT = "artifacts/grounding-ambiguity-split-v1.json"
MD_OUTPUT = "artifacts/grounding-ambiguity-split-v1.md"
SCREEN_WIDTH = 1024
REQUEST_PANEL_RIGHT = SCREEN_WIDTH // 2  # left half: read-only request card; right half: form
REQUEST_CARD_CLASS = "request_card_same_row"
OTHER_CLASS = "other_miss"


class AmbiguitySplitError(ValueError):
    """Raised when the frozen inputs do not have the expected structure."""


def classify(point: list[float] | None, bbox: list[int]) -> str:
    """Return the miss class for one raw point against its target's frozen ``[x0, y0, x1, y1]``."""

    if point is None:
        return OTHER_CLASS
    x, y = point
    _x0, y0, _x1, y1 = bbox
    if x < REQUEST_PANEL_RIGHT and y0 <= y < y1:
        return REQUEST_CARD_CLASS
    return OTHER_CLASS


def load_examples(root: Path) -> list[dict[str, Any]]:
    """Per-example rows with raw point, target box, and screen state, cross-checked."""

    checked = {row["example_id"]: row for row in load_rows(root)}
    results = json.loads((root / RESULTS_PATH).read_text(encoding="utf-8"))
    examples = []
    for row in results["per_example"]:
        example_id = row["example_id"]
        if example_id not in checked:
            raise AmbiguitySplitError(f"{example_id} missing from the cross-checked rows")
        bbox, point = row.get("bbox"), row["raw"].get("point")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            raise AmbiguitySplitError(f"{example_id} lacks a four-value bbox")
        examples.append(
            {
                "example_id": example_id,
                "target_id": row["target_id"],
                "screen_state": row["screen_state"],
                "bbox": bbox,
                "raw_point": point,
                "raw_parse_status": row["raw"]["parse_status"],
                "raw": checked[example_id]["raw"],
                "marks": checked[example_id]["marks"],
            }
        )
    return examples


def load_review(root: Path) -> dict[str, list[str]]:
    """Manual-review categories per ``<example_id>/<condition>`` key."""

    decisions = json.loads((root / REVIEW_PATH).read_text(encoding="utf-8"))
    categories: dict[str, list[str]] = {}
    for category, keys in decisions["category_assignments"].items():
        for key in keys:
            categories.setdefault(key, []).append(category)
    return categories


def _clusters(examples: list[dict[str, Any]], raw_key: str) -> list[list[int]]:
    order: list[str] = []
    for example in examples:
        if example["target_id"] not in order:
            order.append(example["target_id"])
    return [
        [int(e["marks"]) - int(e[raw_key]) for e in examples if e["target_id"] == target]
        for target in order
    ]


def _interval(low: float, high: float) -> list[float]:
    return [round(100 * low, 9), round(100 * high, 9)]


def analyze(
    examples: list[dict[str, Any]],
    review: dict[str, list[str]],
    samples: int = SAMPLES,
    seed: int = SEED,
) -> dict[str, Any]:
    misses = []
    for example in examples:
        if example["raw"]:
            example["miss_class"] = None
            example["raw_forgiving"] = True
            continue
        miss_class = classify(example["raw_point"], example["bbox"])
        example["miss_class"] = miss_class
        example["raw_forgiving"] = miss_class == REQUEST_CARD_CLASS
        misses.append(example)
    card = [e for e in misses if e["miss_class"] == REQUEST_CARD_CLASS]
    other = [e for e in misses if e["miss_class"] == OTHER_CLASS]
    left_panel_other_row = [
        e for e in other if e["raw_point"] is not None and e["raw_point"][0] < REQUEST_PANEL_RIGHT
    ]
    n = len(examples)
    raw_correct = sum(e["raw"] for e in examples)
    forgiving_correct = sum(e["raw_forgiving"] for e in examples)
    marks_correct = sum(e["marks"] for e in examples)
    strict_diffs = [int(e["marks"]) - int(e["raw"]) for e in examples]
    forgiving_diffs = [int(e["marks"]) - int(e["raw_forgiving"]) for e in examples]

    def review_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for row in rows:
            for category in review.get(f"{row['example_id']}/raw", ["(not reviewed)"]):
                counter[category] += 1
        return dict(sorted(counter.items()))

    card_x = [e["raw_point"][0] for e in card]
    return {
        "schema_version": SCHEMA_VERSION,
        "inputs": [RESULTS_PATH, PREDICTIONS_PATH, REVIEW_PATH],
        "rule": {
            "request_card_same_row": (
                f"raw miss whose parsed point has x < {REQUEST_PANEL_RIGHT} (left half of the "
                f"{SCREEN_WIDTH}-pixel screen, the read-only request card) and y0 <= y < y1 of "
                "the target's frozen box"
            ),
            "other_miss": "every other raw miss, including unparsed responses",
            "forgiving_raw": "raw correct, or a request_card_same_row miss",
        },
        "counts": {
            "example_count": n,
            "raw_correct": raw_correct,
            "raw_misses": len(misses),
            "request_card_same_row": len(card),
            "other_misses": len(other),
            "left_panel_other_row": len(left_panel_other_row),
            "forgiving_raw_correct": forgiving_correct,
            "marks_correct": marks_correct,
        },
        "request_card_same_row_by_target": dict(
            sorted(Counter(e["target_id"] for e in card).items())
        ),
        "request_card_same_row_by_screen_state": dict(
            sorted(Counter(e["screen_state"] for e in card).items())
        ),
        "request_card_same_row_x_range": [min(card_x), max(card_x)] if card_x else None,
        "other_misses_by_target": dict(sorted(Counter(e["target_id"] for e in other).items())),
        "manual_review_categories": {
            "request_card_same_row": review_counts(card),
            "other_misses": review_counts(other),
        },
        "strict": {
            "delta_percentage_points": 100 * sum(strict_diffs) / n,
            "example_level_bootstrap_95_ci_percentage_points": _interval(
                *example_bootstrap(strict_diffs, samples, seed)
            ),
            "target_clustered_bootstrap_95_ci_percentage_points": _interval(
                *cluster_bootstrap(_clusters(examples, "raw"), samples, seed)
            ),
        },
        "forgiving": {
            "delta_percentage_points": 100 * sum(forgiving_diffs) / n,
            "example_level_bootstrap_95_ci_percentage_points": _interval(
                *example_bootstrap(forgiving_diffs, samples, seed)
            ),
            "target_clustered_bootstrap_95_ci_percentage_points": _interval(
                *cluster_bootstrap(_clusters(examples, "raw_forgiving"), samples, seed)
            ),
        },
        "request_card_same_row_examples": [
            {
                "example_id": e["example_id"],
                "target_id": e["target_id"],
                "screen_state": e["screen_state"],
                "raw_point": e["raw_point"],
                "target_bbox": e["bbox"],
            }
            for e in card
        ],
        "interpretation_note": (
            "The instruction names a field ('Click the Company name field'); the request card "
            "shows a read-only box with the same label in the same row. A point on that box is a "
            "wrong click under the frozen protocol, and it stays one here. The forgiving column "
            "only shows how much of the raw-versus-marks gap comes from that one confusable "
            "region. It is a post-hoc sensitivity analysis on the same 100 examples, not a "
            "pre-registered re-scoring, and the frozen v1 headline is unchanged."
        ),
    }


def _pp(value: float) -> str:
    return f"{value:+.1f}"


def render_markdown(result: dict[str, Any]) -> str:
    """Render the Markdown supplement using only values present in ``result``."""

    c = result["counts"]
    lines = [
        "# Grounding v1 request-card ambiguity split",
        "",
        (
            "Generated by `scripts/generate_grounding_ambiguity_split.py` from "
            + ", ".join(f"`{path}`" for path in result["inputs"])
            + ". Structured source: `artifacts/grounding-ambiguity-split-v1.json`. This "
            "supplements, and does not replace, the frozen `artifacts/grounding-report.md`."
        ),
        "",
        "## Rule",
        "",
    ]
    lines += [f"- `{name}`: {text}." for name, text in result["rule"].items()]
    lines += [
        "",
        "## Counts",
        "",
        (
            f"Of {c['raw_misses']} raw-coordinate misses on {c['example_count']} examples, "
            f"{c['request_card_same_row']} land on the read-only request card in the target's "
            f"own row and {c['other_misses']} do not. {c['left_panel_other_row']} misses land in "
            "the request panel in a different row."
        ),
        "",
        "| Target | Request card, same row | Other raw misses |",
        "|---|---:|---:|",
    ]
    targets = sorted(
        set(result["request_card_same_row_by_target"]) | set(result["other_misses_by_target"])
    )
    for target in targets:
        lines.append(
            f"| `{target}` | {result['request_card_same_row_by_target'].get(target, 0)} | "
            f"{result['other_misses_by_target'].get(target, 0)} |"
        )
    x_range = result["request_card_same_row_x_range"]
    lines += [
        "",
        (
            "Request-card misses by screen state: "
            + ", ".join(
                f"{state} {count}"
                for state, count in result["request_card_same_row_by_screen_state"].items()
            )
            + "."
        ),
    ]
    if x_range is not None:
        lines.append(f"Their x coordinates span {x_range[0]:.0f}–{x_range[1]:.0f}.")
    lines += [
        "",
        (
            "Manual-review categories (frozen `grounding-error-review-decisions.json`; an example "
            "can carry more than one):"
        ),
        "",
    ]
    for group, counts in result["manual_review_categories"].items():
        rendered = ", ".join(f"{name} {count}" for name, count in counts.items())
        lines.append(f"- `{group}`: {rendered}.")
    strict, forgiving = result["strict"], result["forgiving"]
    lines += [
        "",
        "## Strict versus forgiving",
        "",
        (
            "| Scoring | Raw correct | Marks correct | Marks minus raw | Example-level 95% CI | "
            "Target-clustered 95% CI |"
        ),
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, block, raw in (
        ("Strict (frozen protocol)", strict, c["raw_correct"]),
        ("Forgiving request-card same-row", forgiving, c["forgiving_raw_correct"]),
    ):
        ex = block["example_level_bootstrap_95_ci_percentage_points"]
        cl = block["target_clustered_bootstrap_95_ci_percentage_points"]
        lines.append(
            f"| {name} | {raw}/{c['example_count']} | {c['marks_correct']}/{c['example_count']} "
            f"| {_pp(block['delta_percentage_points'])} pp | [{_pp(ex[0])}, {_pp(ex[1])}] | "
            f"[{_pp(cl[0])}, {_pp(cl[1])}] |"
        )
    lines += ["", "## Interpretation", "", result["interpretation_note"], ""]
    return "\n".join(lines)


def serialize(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-dir", type=Path, default=None, help="default: <root>/artifacts")
    args = parser.parse_args(argv)
    root = args.repository_root
    result = analyze(load_examples(root), load_review(root))
    out = args.output_dir or root / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    (out / Path(JSON_OUTPUT).name).write_text(serialize(result), encoding="utf-8")
    (out / Path(MD_OUTPUT).name).write_text(render_markdown(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
