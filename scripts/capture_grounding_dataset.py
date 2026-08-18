#!/usr/bin/env python3
"""Capture and validate the frozen grounding benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.capture import capture_dataset, refresh_capture_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="revalidate and refresh capture metadata without launching Playwright",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    summary = (
        refresh_capture_summary(repository_root)
        if args.summary_only
        else capture_dataset(repository_root)
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
