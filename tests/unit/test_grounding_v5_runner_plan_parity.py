"""Frozen pre-refactor parity projections for representative D5.6 plans."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.plan import legacy_plan_projection, legacy_summary_classifications

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/unit/fixtures/grounding_v5_runner_plan_parity.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_representative_d56_plans_match_frozen_pre_refactor_projections() -> None:
    fixture = _load(FIXTURE)
    assert fixture["schema_version"] == "pixelgym-grounding-runner-parity-fixture-v1"

    for expected in fixture["plans"].values():
        plan = _load(ROOT / expected["source_plan"])
        summary = _load(ROOT / expected["source_summary"])
        projection = legacy_plan_projection(plan)

        assert projection.canonical_plan_sha256 == expected["canonical_plan_sha256"]
        assert [list(item) for item in projection.task_assignment] == expected["task_assignment"]
        assert list(projection.stop_rules) == expected["stop_rules"]
        assert legacy_summary_classifications(summary) == expected["summary_classifications"]
