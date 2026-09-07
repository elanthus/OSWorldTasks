#!/usr/bin/env python3
"""Validate or execute one versioned manifest-driven grounding plan."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pixelgym.grounding.v5.calibration_runner import run_calibration_plan
from pixelgym.grounding.v5.plan import load_plan
from pixelgym.grounding.v5.provider_adapters import build_provider_adapter


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approved-plan-sha256")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    plan_path = args.plan if args.plan.is_absolute() else root / args.plan
    plan = load_plan(plan_path)
    if args.validate_only:
        if args.approved_plan_sha256 is not None:
            raise ValueError("validation does not accept an execution approval digest")
        print(
            json.dumps(
                {
                    "plan": str(args.plan),
                    "plan_sha256": plan.digest,
                    "policy_task_pairs": len(plan.assignments),
                    "provider_adapter": plan.provider.adapter,
                    "provider_transport": plan.provider.transport,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    if args.approved_plan_sha256 is None:
        raise ValueError("execution requires --approved-plan-sha256")
    adapter = build_provider_adapter(root, plan)
    result = run_calibration_plan(
        root,
        plan=plan,
        approved_plan_sha256=args.approved_plan_sha256,
        adapter=adapter,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
