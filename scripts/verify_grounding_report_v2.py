#!/usr/bin/env python3
"""Verify the grounding report v2 and its provenance regenerate byte-identically; never writes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pixelgym.grounding.report_v2 import GroundingReportV2Error, verify_outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        result = verify_outputs(args.repository_root)
    except (GroundingReportV2Error, OSError, KeyError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc), "wrote_files": False},
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
