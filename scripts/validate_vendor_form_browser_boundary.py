#!/usr/bin/env python3
"""Run and store vendor-form browser-boundary evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.validation.browser_boundary import validate_browser_boundary

DEFAULT_OUTPUT = Path("artifacts/day-2/raw/browser-boundary.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    evidence = validate_browser_boundary(repository_root, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence["summary"], indent=2, sort_keys=True))
    return 0 if evidence["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
