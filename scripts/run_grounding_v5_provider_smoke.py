#!/usr/bin/env python3
"""Plan or execute one approved development-only v5 OpenRouter smoke request."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pixelgym.grounding.v5.provider_smoke import (
    build_plan,
    execute_smoke,
    plan_digest,
    publishable_result,
    write_fresh_json,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--approved-plan-sha256")
    parser.add_argument("--maximum-spend-usd")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = args.output if args.output.is_absolute() else root / args.output
    if output.exists():
        raise FileExistsError(f"refusing provider work because output already exists: {output}")
    if args.plan_only:
        if args.plan is not None or args.approved_plan_sha256 is not None:
            raise ValueError("plan mode does not accept execution approval arguments")
        if args.maximum_spend_usd is None:
            raise ValueError("plan mode requires --maximum-spend-usd")
        try:
            maximum_spend = Decimal(args.maximum_spend_usd)
        except InvalidOperation as exc:
            raise ValueError("maximum spend must be a decimal number") from exc
        value = build_plan(root, maximum_spend_usd=maximum_spend)
        write_fresh_json(output, value)
        print(json.dumps({"output": str(output), "plan_sha256": plan_digest(value)}, indent=2))
        return

    if args.maximum_spend_usd is not None:
        raise ValueError("execution reads the maximum spend from the approved plan")
    if args.plan is None or args.approved_plan_sha256 is None:
        raise ValueError("execution requires --plan and --approved-plan-sha256")
    plan_path = args.plan if args.plan.is_absolute() else root / args.plan
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("smoke plan must be a JSON object")
    result = execute_smoke(
        root,
        plan=value,
        approved_plan_sha256=args.approved_plan_sha256,
    )
    write_fresh_json(output, result)
    print(json.dumps(publishable_result(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
