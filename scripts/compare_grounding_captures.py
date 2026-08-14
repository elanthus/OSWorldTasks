#!/usr/bin/env python3
"""Compare two grounding PNG sets without tolerance or masking."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.determinism import compare_png_directories, write_comparison


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference_dir", type=Path)
    parser.add_argument("candidate_dir", type=Path)
    parser.add_argument("--reference-label", required=True)
    parser.add_argument("--candidate-label", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = compare_png_directories(
        args.reference_dir,
        args.candidate_dir,
        reference_label=args.reference_label,
        candidate_label=args.candidate_label,
    )
    write_comparison(result, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
