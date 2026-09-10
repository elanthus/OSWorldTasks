#!/usr/bin/env python3
"""Prepare an exact no-call comparison plan, or derive diagnostics from stored outcomes."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from pixelgym.grounding.v5.controlled_comparison import (
    build_comparison_plan,
    summarize_comparison,
)
from pixelgym.grounding.v5.plan import load_plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("plan")
    prepare.add_argument("--phase", choices=("smoke", "calibration"), required=True)
    prepare.add_argument("--maximum-spend-usd", required=True)
    prepare.add_argument("--run-output", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    report = commands.add_parser("report")
    report.add_argument("--plan", type=Path, required=True)
    report.add_argument("--summary", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.command == "plan":
        dirty = subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True
        )
        if dirty:
            raise ValueError("commit implementation before freezing a comparison plan")
        revision = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        plan = build_comparison_plan(
            root,
            code_revision=revision,
            phase=args.phase,
            maximum_spend_usd=args.maximum_spend_usd,
            output_directory=args.run_output,
        )
        value = plan.raw
        result = {"plan_sha256": plan.digest, "budgets": value["budgets"], "provider_calls_made": 0}
    else:
        value = summarize_comparison(
            load_plan(args.plan), json.loads(args.summary.read_text(encoding="utf-8"))
        )
        result = {"output": str(args.output), "provider_calls_made": 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
