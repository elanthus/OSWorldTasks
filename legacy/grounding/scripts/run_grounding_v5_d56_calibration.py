#!/usr/bin/env python3
"""Plan or execute the exact four-policy, fifty-task D5.6 calibration.

Run as ``python -m legacy.grounding.scripts.run_grounding_v5_d56_calibration``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from legacy.grounding.v5.d56_calibration import (
    build_plan,
    execute_calibration,
    plan_digest,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--approved-plan-sha256")
    parser.add_argument("--smoke-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    output = args.output if args.output.is_absolute() else root / args.output
    smoke_output = (
        args.smoke_output if args.smoke_output.is_absolute() else root / args.smoke_output
    )
    if output.exists():
        raise FileExistsError(f"refusing to replace existing output: {output}")
    if args.plan_only:
        if args.plan is not None or args.approved_plan_sha256 is not None:
            raise ValueError("plan mode does not accept execution approval arguments")
        plan = build_plan(root, smoke_output_directory=smoke_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), "plan_sha256": plan_digest(plan)}, indent=2))
        return
    if args.plan is None or args.approved_plan_sha256 is None:
        raise ValueError("execution requires --plan and --approved-plan-sha256")
    plan_path = args.plan if args.plan.is_absolute() else root / args.plan
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("D5.6 calibration plan must be a JSON object")
    result = execute_calibration(
        root,
        plan=value,
        approved_plan_sha256=args.approved_plan_sha256,
        smoke_output_directory=smoke_output,
        output_directory=output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
