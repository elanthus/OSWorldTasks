#!/usr/bin/env python3
"""Plan or execute one GLM normalized-coordinate candidate trial."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from legacy.grounding.v5.d56_glm_normalized_trial import (
    build_plan,
    execute_trial,
    plan_digest,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--approved-plan-sha256")
    parser.add_argument("--frozen-llama-trial-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _under_root(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    output = _under_root(root, args.output)
    frozen_llama_output = _under_root(root, args.frozen_llama_trial_output)
    if output.exists():
        raise FileExistsError(f"refusing to replace existing output: {output}")
    if args.plan_only:
        if args.plan is not None or args.approved_plan_sha256 is not None:
            raise ValueError("plan mode does not accept execution approval arguments")
        plan = build_plan(
            root,
            frozen_llama_trial_output_directory=frozen_llama_output,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), "plan_sha256": plan_digest(plan)}, indent=2))
        return
    if args.plan is None or args.approved_plan_sha256 is None:
        raise ValueError("execution requires --plan and --approved-plan-sha256")
    plan_path = _under_root(root, args.plan)
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("GLM candidate trial plan must be a JSON object")
    result = execute_trial(
        root,
        plan=value,
        approved_plan_sha256=args.approved_plan_sha256,
        frozen_llama_trial_output_directory=frozen_llama_output,
        output_directory=output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
