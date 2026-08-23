#!/usr/bin/env python3
"""Capture the complete deterministic state graph for the v4c pilot."""

from pathlib import Path

from pixelgym.grounding.calibration_v4c import capture_v4c_pilot

if __name__ == "__main__":
    print(capture_v4c_pilot(Path(__file__).resolve().parents[1]))
