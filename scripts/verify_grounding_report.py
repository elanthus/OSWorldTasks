#!/usr/bin/env python3
"""Verify the canonical grounding report from frozen evidence without writing files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pixelgym.grounding.verification import (
    DEFAULT_PROVENANCE_PATH,
    GroundingVerificationError,
    verify_grounding_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only verification of canonical grounding identity, sample size, headline "
            "statistics, evidence digests, and stored rendered-image bytes."
        )
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root containing the frozen artifacts (default: script parent)",
    )
    parser.add_argument(
        "--provenance",
        type=Path,
        default=DEFAULT_PROVENANCE_PATH,
        help="absolute path or path relative to the repository root",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = verify_grounding_report(
            args.repository_root,
            provenance_path=args.provenance,
        )
    except GroundingVerificationError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "pixelgym-grounding-report-verification-v1",
                    "status": "failed",
                    "error": str(exc),
                    "wrote_files": False,
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
