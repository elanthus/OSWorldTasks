#!/usr/bin/env python3
"""Plan or run the capped Luna evaluation for the frozen v4 pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from legacy.grounding.v4_evaluation import planned_v4_calls, run_v4_evaluation
from pixelgym.grounding.evaluation import ResponseCache
from pixelgym.grounding.providers import CodexCLIProvider, MockProvider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("luna", "mock"), required=True)
    parser.add_argument("--max-new-calls", type=int, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args()


def default_output_path(repository_root: Path, provider_name: str) -> Path:
    """Keep immutable prediction outputs separate for each provider."""
    return (
        repository_root
        / "artifacts"
        / f"grounding-v4-pilot-predictions-{provider_name}.jsonl"
    )


def main() -> None:
    args = parse_args()
    repository_root = Path(__file__).resolve().parents[3]
    provider = CodexCLIProvider(model="gpt-5.6-luna") if args.provider == "luna" else MockProvider()
    cache = ResponseCache(repository_root / ".cache" / "grounding-v4" / "responses")
    if args.plan_only:
        result = planned_v4_calls(
            repository_root=repository_root,
            provider=provider,
            cache=cache,
        )
    else:
        output = args.output or default_output_path(repository_root, args.provider)
        result = run_v4_evaluation(
            repository_root=repository_root,
            provider=provider,
            output_path=output,
            max_new_calls=args.max_new_calls,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
