#!/usr/bin/env python3
"""Apply stored manual decisions to the generated grounding error-review template."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from pixelgym.grounding.analysis import apply_manual_error_review_decisions
from pixelgym.serialization import load_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--review",
        type=Path,
        default=Path("artifacts/grounding-error-review.jsonl"),
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        default=Path("artifacts/grounding-error-review-decisions.json"),
    )
    return parser.parse_args()


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main() -> None:
    args = parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    review_path = _resolve(repository_root, args.review)
    decisions_path = _resolve(repository_root, args.decisions)
    reviews = load_jsonl(review_path)
    decisions = json.loads(decisions_path.read_text())
    finalized = apply_manual_error_review_decisions(reviews, decisions)
    encoded = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in finalized
    )
    if all(row["review_status"] == "manual_visual_review" for row in reviews):
        if review_path.read_text() != encoded:
            raise SystemExit("refusing to replace a different finalized error review")
    else:
        review_path.write_text(encoded)
    print(
        json.dumps(
            {
                "error_record_count": len(finalized),
                "manual_visual_review_count": sum(
                    row["review_status"] == "manual_visual_review" for row in finalized
                ),
                "category_counts": dict(
                    sorted(
                        Counter(
                            category for row in finalized for category in row["categories"]
                        ).items()
                    )
                ),
                "review_path": review_path.relative_to(repository_root).as_posix(),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
