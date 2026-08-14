#!/usr/bin/env python3
"""Print raw D3.11 evidence without declaring a release verdict."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.release import collect_release_observations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    observations = collect_release_observations(repository_root)
    encoded = json.dumps(observations, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = args.output if args.output.is_absolute() else repository_root / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
