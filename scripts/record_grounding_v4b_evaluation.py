#!/usr/bin/env python3
"""Generate the v4b result summary from stored records without model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.v4b_evaluation import record_v4b_evaluation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--conditions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("artifacts/grounding-v4b-pilot-manifest.json"),
    )
    parser.add_argument("--prior-paid-calls", type=int, default=0)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    results = record_v4b_evaluation(
        repository_root=root,
        predictions_path=args.predictions.resolve(),
        conditions_path=args.conditions.resolve(),
        results_path=args.output.resolve(),
        manifest_path=(root / args.manifest).resolve()
        if not args.manifest.is_absolute()
        else args.manifest.resolve(),
        prior_paid_calls=args.prior_paid_calls,
    )
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
