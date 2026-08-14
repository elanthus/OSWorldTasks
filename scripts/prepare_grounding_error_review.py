#!/usr/bin/env python3
"""Create visual-review records for every incorrect stored grounding prediction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.analysis import build_error_review_template
from pixelgym.grounding.report import render_error_review_images
from pixelgym.serialization import load_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--predictions",
        type=Path,
        default=Path("artifacts/grounding-predictions.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/grounding-error-review.jsonl"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    predictions_path = (repository_root / args.predictions).resolve()
    output_path = (repository_root / args.output).resolve()
    examples = load_jsonl(repository_root / "artifacts" / "grounding-dataset.jsonl")
    predictions = load_jsonl(predictions_path)
    reviews = build_error_review_template(examples, predictions)
    encoded = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in reviews
    )
    if output_path.exists():
        raise SystemExit(
            f"refusing to overwrite {output_path}; preserve completed manual classifications"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded)
    render_error_review_images(
        repository_root=repository_root,
        examples=examples,
        predictions=predictions,
        error_reviews=reviews,
    )
    print(
        json.dumps(
            {
                "error_record_count": len(reviews),
                "output_path": output_path.relative_to(repository_root).as_posix(),
                "review_status": "pending_visual_review",
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
