#!/usr/bin/env python3
"""Run or plan the frozen grounding evaluation with an explicit call cap."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.evaluation import ResponseCache, planned_new_calls, run_evaluation
from pixelgym.grounding.providers import CodexCLIProvider, MockProvider, OpenRouterProvider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("codex", "openrouter", "mock"), required=True)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--pilot", action="store_true")
    scope.add_argument("--full", action="store_true")
    parser.add_argument("--max-new-calls", type=int, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.max_new_calls < 0:
        raise SystemExit("--max-new-calls must be nonnegative")
    repository_root = Path(__file__).resolve().parents[1]
    if args.provider == "codex":
        provider = CodexCLIProvider()
    elif args.provider == "openrouter":
        provider = OpenRouterProvider()
    else:
        provider = MockProvider()
    pilot = args.pilot
    default_name = "pilot-predictions.jsonl" if pilot else "grounding-predictions.jsonl"
    output = args.output or (
        repository_root / "artifacts" / "day-3" / "pilot" / default_name
        if pilot
        else repository_root / "artifacts" / default_name
    )
    if args.plan_only:
        result = planned_new_calls(
            repository_root=repository_root,
            provider=provider,
            cache=ResponseCache(repository_root / ".cache" / "grounding" / "responses"),
            pilot=pilot,
        )
    else:
        result = run_evaluation(
            repository_root=repository_root,
            provider=provider,
            output_path=output,
            pilot=pilot,
            max_new_calls=args.max_new_calls,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
