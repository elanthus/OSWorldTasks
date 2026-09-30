#!/usr/bin/env python3
"""Write the grounding report v2 and its provenance from frozen evidence (no model calls)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pixelgym.grounding.report_v2 import PROVENANCE_PATH, REPORT_PATH, render_outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="write both files here instead of their tracked paths",
    )
    args = parser.parse_args(argv)
    report, provenance = render_outputs(args.repository_root)
    for relative, text in ((REPORT_PATH, report), (PROVENANCE_PATH, provenance)):
        target = (
            args.output_dir / Path(relative).name
            if args.output_dir
            else args.repository_root / relative
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
