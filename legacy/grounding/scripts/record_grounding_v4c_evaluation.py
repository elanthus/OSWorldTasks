#!/usr/bin/env python3
"""Generate the v4c result summary from stored records without model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from legacy.grounding.v4c_evaluation import (
    initialize_v4c_model_manifest,
    record_v4c_evaluation,
    summarize_v4c_evaluation,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--conditions", type=Path, required=True)
    parser.add_argument("--attempts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("artifacts/grounding-v4c-pilot-manifest.json"),
    )
    parser.add_argument("--prior-predictions", type=Path)
    parser.add_argument("--prior-conditions", type=Path)
    parser.add_argument("--artifact-label", default="luna")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--manifest-template", type=Path)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    summary_args = {
        "repository_root": root,
        "predictions_path": args.predictions.resolve(),
        "conditions_path": args.conditions.resolve(),
        "attempts_path": args.attempts.resolve(),
        "prior_predictions_path": args.prior_predictions.resolve()
        if args.prior_predictions is not None
        else None,
        "prior_conditions_path": args.prior_conditions.resolve()
        if args.prior_conditions is not None
        else None,
    }
    if args.summary_only:
        results = summarize_v4c_evaluation(**summary_args)
        encoded = json.dumps(results, indent=2, sort_keys=True) + "\n"
        if args.output.is_file() and args.output.read_text(encoding="utf-8") != encoded:
            raise ValueError("refusing to overwrite different immutable v4c results")
        args.output.write_text(encoded, encoding="utf-8")
    else:
        manifest_path = (
            (root / args.manifest).resolve()
            if not args.manifest.is_absolute()
            else args.manifest.resolve()
        )
        if not manifest_path.is_file() and args.manifest_template is not None:
            summary = summarize_v4c_evaluation(**summary_args)
            initialize_v4c_model_manifest(
                template_path=args.manifest_template.resolve(),
                manifest_path=manifest_path,
                model=summary["model"],
                parameters=summary["parameters"],
            )
        results = record_v4c_evaluation(
            **summary_args,
            results_path=args.output.resolve(),
            manifest_path=manifest_path,
            artifact_label=args.artifact_label,
            plan_path=args.plan.resolve() if args.plan is not None else None,
            audit_path=args.audit.resolve() if args.audit is not None else None,
        )
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
