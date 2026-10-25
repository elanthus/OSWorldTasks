#!/usr/bin/env python3
"""Run or plan the frozen grounding evaluation with an explicit call cap.

``--protocol v1`` (the default) reads the frozen, reported v1 benchmark and keeps every
v1 input, output, and cache path unchanged.  ``--protocol v2`` reads the crossed v2
dataset, stamps ``pixelgym-grounding-v2``, writes only under v2 paths, and with
``--plan-only`` also drafts ``artifacts/grounding-v2-freeze-draft.json`` for the owner.
V2 execution against any paid provider remains owner-gated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pixelgym.grounding.evaluation import ResponseCache, planned_new_calls, run_evaluation
from pixelgym.grounding.freeze_v2 import write_v2_freeze_draft
from pixelgym.grounding.protocols import PROTOCOL_NAMES, protocol_for
from pixelgym.grounding.providers import CodexCLIProvider, MockProvider, OpenRouterProvider


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("codex", "openrouter", "mock"), required=True)
    parser.add_argument("--protocol", choices=PROTOCOL_NAMES, default="v1")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--pilot", action="store_true")
    scope.add_argument("--full", action="store_true")
    parser.add_argument("--max-new-calls", type=int, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, repository_root: Path | None = None) -> None:
    args = parse_args(argv)
    if args.max_new_calls < 0:
        raise SystemExit("--max-new-calls must be nonnegative")
    repository_root = repository_root or Path(__file__).resolve().parents[1]
    protocol = protocol_for(args.protocol)
    if args.provider == "codex":
        provider = CodexCLIProvider()
    elif args.provider == "openrouter":
        provider = OpenRouterProvider()
    else:
        provider = MockProvider()
    pilot = args.pilot
    output = args.output or (
        repository_root / protocol.pilot_predictions_path
        if pilot
        else repository_root / protocol.full_predictions_path
    )
    result: dict[str, Any]
    if args.plan_only:
        result = dict(
            planned_new_calls(
                repository_root=repository_root,
                provider=provider,
                cache=ResponseCache(repository_root / protocol.cache_directory),
                pilot=pilot,
                protocol=protocol,
            )
        )
        if protocol.name == "v2":
            result["freeze_draft"] = write_v2_freeze_draft(repository_root)
    else:
        result = run_evaluation(
            repository_root=repository_root,
            provider=provider,
            output_path=output,
            pilot=pilot,
            max_new_calls=args.max_new_calls,
            protocol=protocol,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
