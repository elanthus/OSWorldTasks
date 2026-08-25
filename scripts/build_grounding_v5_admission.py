"""Generate v5 no-cost admission evidence without provider access."""

from __future__ import annotations

import argparse
from pathlib import Path

from pixelgym.grounding.v5.admission import build_admission_evidence
from pixelgym.serialization import canonical_json_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("admission output must be a fresh immutable path")
    evidence = build_admission_evidence()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(evidence) + b"\n")


if __name__ == "__main__":
    main()
