#!/usr/bin/env python3
"""Capture the complete deterministic state graph for the v4c pilot.

Run as ``python -m legacy.grounding.scripts.capture_grounding_v4c_pilot``.
"""

from pathlib import Path

from legacy.grounding.calibration_v4c import capture_v4c_pilot

if __name__ == "__main__":
    print(capture_v4c_pilot(Path(__file__).resolve().parents[3]))
