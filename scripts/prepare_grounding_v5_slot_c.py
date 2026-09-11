#!/usr/bin/env python3
"""Prepare a fresh Slot C plan without credentials or provider calls."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from pixelgym.grounding.v5.provider_adapters import OpenRouterHttpAdapter
from pixelgym.grounding.v5.slot_c import build_slot_c_plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("smoke", "diagnostic", "calibration"), required=True)
    parser.add_argument("--candidate", choices=("vertex", "mistral"), default="vertex")
    parser.add_argument("--generation", choices=("v1", "v2"), default="v1")
    parser.add_argument("--maximum-spend-usd", required=True)
    parser.add_argument("--run-output", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    dirty = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True
    )
    if dirty:
        raise ValueError("commit implementation before freezing a Slot C plan")
    revision = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    plan = build_slot_c_plan(
        root,
        code_revision=revision,
        phase=args.phase,
        maximum_spend_usd=args.maximum_spend_usd,
        output_directory=args.run_output,
        candidate=args.candidate,
        generation=args.generation,
    )
    if (root / plan.outputs.directory).exists():
        raise FileExistsError("Slot C plans require a fresh run output directory")
    adapter = OpenRouterHttpAdapter(root, plan)
    adapter.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(plan.raw, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "plan_sha256": plan.digest,
                "code_revision": revision,
                "assigned_episodes": len(plan.assignments),
                "budgets": plan.raw["budgets"],
                "provider_calls_made": 0,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
