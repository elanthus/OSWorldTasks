"""Integrity checks for the checked-in manifest-runner maintenance measurement."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
ARTIFACT = ROOT / "artifacts/grounding-manifest-runner-maintenance-measurement.json"
ARTIFACT_SHA256 = "7ae0c6411a79e6baf20fc2623e3ca8dcfac725a980559f4eb0794e5975389a74"


def test_grounding_maintenance_measurement_is_pinned_and_self_describing() -> None:
    raw = ARTIFACT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ARTIFACT_SHA256
    value = json.loads(raw)
    assert value["schema_version"] == "pixelgym-grounding-maintenance-measurement-v1"
    assert value["before"]["grounding_production_physical_loc"] == 32623
    assert value["after"]["grounding_production_physical_loc"] == 17970
    assert value["before"]["top_level_python_script_count"] == 67
    assert value["after"]["top_level_python_script_count"] == 43
    assert value["after"]["experiment_specific_maintenance_surface_count"] == 0
    assert not any(name.startswith("legacy/") for name in value["after"]["grounding_wheel_files"])
    assert str(ROOT) not in value["reproduction_command"]
