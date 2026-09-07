#!/usr/bin/env python3
"""Summarize stored v4 predictions and update the evidence manifest offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from legacy.grounding.v4_evaluation import record_v4_evaluation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--predictions",
        type=Path,
        default=Path("artifacts/grounding-v4-pilot-predictions-luna.jsonl"),
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("artifacts/grounding-v4-pilot-results-luna.json"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("artifacts/grounding-v4-pilot-manifest.json"),
    )
    return parser.parse_args()


def _resolve(repository_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repository_root / path


def main() -> None:
    args = parse_args()
    repository_root = Path(__file__).resolve().parents[3]
    results = record_v4_evaluation(
        repository_root=repository_root,
        predictions_path=_resolve(repository_root, args.predictions),
        results_path=_resolve(repository_root, args.results),
        manifest_path=_resolve(repository_root, args.manifest),
    )
    print(
        json.dumps(
            {
                "marks_correct": results["conditions"]["marks"]["correct_count"],
                "parse_failures": results["failures"]["parse_failure_count"],
                "raw_correct": results["conditions"]["raw"]["correct_count"],
                "request_failures": results["failures"]["request_failure_count"],
                "route": results["routing"]["decision"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
