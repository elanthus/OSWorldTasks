"""Integrity checks for the checked-in manifest-runner maintenance measurement."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
ARTIFACT = ROOT / "artifacts/grounding-manifest-runner-maintenance-measurement.json"
ARTIFACT_SHA256 = "60ebd8becf068fac62b953f2118a9502432a097f78ffc717fbf43efbe2caf62f"


def test_grounding_maintenance_measurement_is_pinned_and_self_describing() -> None:
    raw = ARTIFACT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ARTIFACT_SHA256
    value = json.loads(raw)
    assert value["schema_version"] == "pixelgym-grounding-maintenance-measurement-v1"
    assert value["before"]["grounding_production_physical_loc"] == 32623
    assert value["after"]["grounding_production_physical_loc"] == 18204
    assert value["before"]["top_level_python_script_count"] == 67
    assert value["after"]["top_level_python_script_count"] == 43
    assert value["after"]["experiment_specific_maintenance_surface_count"] == 0
    assert not any(name.startswith("legacy/") for name in value["after"]["grounding_wheel_files"])
    strings = _string_values(value)
    assert not [
        item
        for item in strings
        if item.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", item)
    ]


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for member in value for item in _string_values(member)]
    if isinstance(value, dict):
        return [item for member in value.values() for item in _string_values(member)]
    return []
