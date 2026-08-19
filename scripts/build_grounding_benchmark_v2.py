#!/usr/bin/env python3
"""Build the balanced v2 benchmark from the checked-in v1 capture assets."""

from __future__ import annotations

import json
from pathlib import Path

from pixelgym.grounding.benchmark_v2 import build_benchmark_v2


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    print(json.dumps(build_benchmark_v2(repository_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
