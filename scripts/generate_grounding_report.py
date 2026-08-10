#!/usr/bin/env python3
"""Recreate grounding statistics, figures, gallery, and report without model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.analysis import DEFAULT_BOOTSTRAP_SAMPLES, DEFAULT_BOOTSTRAP_SEED
from pixelgym.grounding.report import generate_results_package


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--predictions",
        type=Path,
        default=Path("artifacts/grounding-predictions.jsonl"),
    )
    parser.add_argument(
        "--error-review",
        type=Path,
        default=Path("artifacts/grounding-error-review.jsonl"),
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("artifacts/grounding-results.json"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("artifacts/grounding-report.md"),
    )
    parser.add_argument("--bootstrap-samples", type=int, default=DEFAULT_BOOTSTRAP_SAMPLES)
    parser.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    return parser.parse_args()


def _resolve(repository_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repository_root / path


def main() -> None:
    args = parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    results = generate_results_package(
        repository_root=repository_root,
        predictions_path=_resolve(repository_root, args.predictions),
        error_review_path=_resolve(repository_root, args.error_review),
        results_path=_resolve(repository_root, args.results),
        report_path=_resolve(repository_root, args.report),
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.bootstrap_seed,
    )
    print(
        json.dumps(
            {
                "example_count": results["collection"]["example_count"],
                "paired_delta_percentage_points": results["paired"]["delta_percentage_points"],
                "all_errors_manually_reviewed": results["error_taxonomy"]["all_manually_reviewed"],
                "results_path": args.results.as_posix(),
                "report_path": args.report.as_posix(),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
