#!/usr/bin/env python3
"""Generate Codex CLI plans or execute one exactly approved smoke task.

Run as ``python -m legacy.grounding.scripts.run_grounding_v5_d56_codex_cli_calibration``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from legacy.grounding.v5.d56_codex_cli_calibration import (
    build_smoke_plan,
    build_successor_calibration_plan,
    execute_smoke,
    plan_digest,
    plan_file_bytes,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke-plan-only", action="store_true")
    mode.add_argument("--calibration-plan-only", action="store_true")
    mode.add_argument("--execute-smoke", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--approved-plan-sha256")
    parser.add_argument("--successful-smoke-evidence", type=Path)
    return parser.parse_args(argv)


def _under_root(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _load_object(path: Path, *, label: str) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    output = _under_root(root, args.output)
    if output.exists():
        raise FileExistsError(f"refusing to replace existing output: {output}")
    if args.smoke_plan_only:
        if any(
            value is not None
            for value in (
                args.plan,
                args.approved_plan_sha256,
                args.successful_smoke_evidence,
            )
        ):
            raise ValueError("smoke plan mode does not accept execution or smoke-evidence inputs")
        plan = build_smoke_plan(root)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(plan_file_bytes(plan))
        print(json.dumps({"plan_sha256": plan_digest(plan)}, indent=2, sort_keys=True))
        return
    if args.calibration_plan_only:
        if args.successful_smoke_evidence is None:
            raise ValueError("calibration planning requires successful smoke evidence")
        if args.plan is not None or args.approved_plan_sha256 is not None:
            raise ValueError("calibration plan mode does not accept execution approval inputs")
        evidence_path = _under_root(root, args.successful_smoke_evidence)
        evidence = _load_object(evidence_path, label="successful smoke evidence")
        plan = build_successor_calibration_plan(
            root,
            successful_smoke_evidence=evidence,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(plan_file_bytes(plan))
        print(json.dumps({"plan_sha256": plan_digest(plan)}, indent=2, sort_keys=True))
        return
    if args.plan is None or args.approved_plan_sha256 is None:
        raise ValueError("smoke execution requires --plan and --approved-plan-sha256")
    if args.successful_smoke_evidence is not None:
        raise ValueError("smoke execution does not accept successor smoke evidence")
    plan_path = _under_root(root, args.plan)
    plan = _load_object(plan_path, label="Codex CLI smoke plan")
    result = execute_smoke(
        root,
        plan=plan,
        approved_plan_sha256=args.approved_plan_sha256,
        output_directory=output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
