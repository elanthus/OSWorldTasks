#!/usr/bin/env python3
"""Generate deterministic target-independent set-of-marks images."""

from __future__ import annotations

import json
from pathlib import Path

from pixelgym.grounding.overlays import generate_overlays


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    print(json.dumps(generate_overlays(repository_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
