#!/usr/bin/env python3
"""Capture the complete deterministic state graph for the v4b pilot.

Run as ``python -m legacy.grounding.scripts.capture_grounding_v4b_pilot``.
"""

from pathlib import Path

from legacy.grounding.calibration_v4b import capture_v4b_pilot

if __name__ == "__main__":
    print(capture_v4b_pilot(Path(__file__).resolve().parents[3]))
