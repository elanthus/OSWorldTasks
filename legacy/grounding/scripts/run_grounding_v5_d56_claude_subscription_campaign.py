#!/usr/bin/env python3
"""Plan and execute the preauthorized Sonnet medium smoke and full run.

Run with ``python -m legacy.grounding.scripts.run_grounding_v5_d56_claude_subscription_campaign``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from legacy.grounding.v5.d56_claude_subscription_campaign import (
    build_full_plan,
    build_smoke_plan,
    execute_full,
    execute_smoke,
    plan_digest,
    plan_file_bytes,
    successful_smoke_evidence_from_files,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke-plan-only", action="store_true")
    mode.add_argument("--execute-smoke", action="store_true")
    mode.add_argument("--full-plan-only", action="store_true")
    mode.add_argument("--execute-full", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--approved-plan-sha256")
    parser.add_argument("--smoke-output", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _under_root(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _load_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("plan must be one JSON object")
    return value


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    output = _under_root(root, args.output)
    if output.exists():
        raise FileExistsError(f"refusing to replace existing output: {output}")
    if args.smoke_plan_only:
        if args.plan is not None or args.approved_plan_sha256 is not None:
            raise ValueError("smoke planning does not accept execution approval inputs")
        if args.smoke_output is not None:
            raise ValueError("smoke planning does not accept prior smoke output")
        plan = build_smoke_plan(root)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(plan_file_bytes(plan))
        print(json.dumps({"plan_sha256": plan_digest(plan)}, indent=2, sort_keys=True))
        return
    if args.full_plan_only:
        if args.plan is not None or args.approved_plan_sha256 is not None:
            raise ValueError("full planning does not accept execution approval inputs")
        if args.smoke_output is None:
            raise ValueError("full planning requires --smoke-output")
        smoke = successful_smoke_evidence_from_files(_under_root(root, args.smoke_output))
        plan = build_full_plan(root, successful_smoke_evidence=smoke)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(plan_file_bytes(plan))
        print(json.dumps({"plan_sha256": plan_digest(plan)}, indent=2, sort_keys=True))
        return
    if args.plan is None or args.approved_plan_sha256 is None:
        raise ValueError("execution requires --plan and --approved-plan-sha256")
    plan = _load_object(_under_root(root, args.plan))
    if args.execute_smoke:
        if args.smoke_output is not None:
            raise ValueError("smoke execution does not accept prior smoke output")
        summary = execute_smoke(
            root,
            plan=plan,
            approved_plan_sha256=args.approved_plan_sha256,
            output_directory=output,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return
    if args.smoke_output is None:
        raise ValueError("full execution requires --smoke-output")
    smoke = successful_smoke_evidence_from_files(_under_root(root, args.smoke_output))
    summary = execute_full(
        root,
        plan=plan,
        approved_plan_sha256=args.approved_plan_sha256,
        successful_smoke_evidence=smoke,
        output_directory=output,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
