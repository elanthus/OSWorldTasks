#!/usr/bin/env python3
"""Plan or run a preregistered capped v4c provider evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.evaluation import ResponseCache
from pixelgym.grounding.providers import (
    CLAUDE_HAIKU_MODEL,
    ClaudeCodeCLIProvider,
    CodexCLIProvider,
    GroundingProvider,
    MockProvider,
    OpenRouterProvider,
)
from pixelgym.grounding.v4c_evaluation import planned_v4c_calls, run_v4c_evaluation


def provider_for_name(name: str) -> GroundingProvider:
    if name == "luna":
        return CodexCLIProvider(model="gpt-5.6-luna")
    if name == "haiku":
        return ClaudeCodeCLIProvider(model=CLAUDE_HAIKU_MODEL)
    if name == "openrouter":
        return OpenRouterProvider()
    if name == "mock":
        return MockProvider()
    raise ValueError(f"unsupported provider: {name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provider", choices=("luna", "haiku", "openrouter", "mock"), required=True
    )
    parser.add_argument("--max-new-calls", type=int, required=True)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--conditions", type=Path)
    parser.add_argument("--attempts", type=Path)
    parser.add_argument("--cache-directory", type=Path)
    parser.add_argument("--plan-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    provider = provider_for_name(args.provider)
    cache_dir = args.cache_directory or root / ".cache" / "grounding-v4c" / "responses"
    if args.plan_only:
        result = planned_v4c_calls(
            repository_root=root,
            provider=provider,
            cache=ResponseCache(cache_dir),
        )
        if args.plan_output is not None:
            encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
            if (
                args.plan_output.is_file()
                and args.plan_output.read_text(encoding="utf-8") != encoded
            ):
                raise ValueError("refusing to overwrite different immutable v4c plan")
            args.plan_output.parent.mkdir(parents=True, exist_ok=True)
            args.plan_output.write_text(encoded, encoding="utf-8")
    else:
        result = run_v4c_evaluation(
            repository_root=root,
            provider=provider,
            predictions_path=args.predictions
            or root / "artifacts" / f"grounding-v4c-pilot-predictions-{args.provider}.jsonl",
            conditions_path=args.conditions
            or root / "artifacts" / f"grounding-v4c-pilot-conditions-{args.provider}.jsonl",
            attempts_path=args.attempts
            or root / "artifacts" / f"grounding-v4c-pilot-attempts-{args.provider}.json",
            max_new_calls=args.max_new_calls,
            cache_directory=cache_dir,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
