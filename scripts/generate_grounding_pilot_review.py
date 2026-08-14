#!/usr/bin/env python3
"""Create annotated images and a parser audit from stored pilot responses."""

from __future__ import annotations

import json
from pathlib import Path

from pixelgym.grounding.pilot import generate_pilot_review


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    predictions = repository_root / "artifacts" / "day-3" / "pilot" / "pilot-predictions.jsonl"
    print(json.dumps(generate_pilot_review(repository_root, predictions), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
