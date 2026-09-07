#!/usr/bin/env python3
"""Capture and validate the deterministic v4 compositional pilot."""

from __future__ import annotations

import json
from pathlib import Path

from legacy.grounding.calibration_v4 import capture_v4_calibration_dataset


def main() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    manifest = capture_v4_calibration_dataset(repository_root)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
