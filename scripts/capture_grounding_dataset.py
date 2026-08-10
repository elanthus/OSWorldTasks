#!/usr/bin/env python3
"""Capture and validate the frozen Day 3 grounding benchmark."""

from __future__ import annotations

import json
from pathlib import Path

from pixelgym.grounding.capture import capture_dataset


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    print(json.dumps(capture_dataset(repository_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
