"""Frozen pre-refactor parity projections for representative D5.6 plans."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pixelgym.serialization import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/unit/fixtures/grounding_v5_runner_plan_parity.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _assignment(plan: dict[str, Any]) -> list[list[Any]]:
    task = plan.get("task")
    records = [task] if isinstance(task, dict) else plan["task_order"]
    limit_key = "action_limit" if isinstance(task, dict) else "max_episode_steps"
    return [
        [record["seed"], record["task_id"], record[limit_key], record["family"]]
        for record in records
    ]


def _classifications(summary: dict[str, Any]) -> dict[str, int]:
    classifications = summary.get("classifications")
    if isinstance(classifications, dict):
        return {str(key): int(value) for key, value in classifications.items()}
    episode = summary["episode_result"]
    return {str(episode["classification"]): 1}


def test_representative_d56_plans_match_frozen_pre_refactor_projections() -> None:
    fixture = _load(FIXTURE)
    assert fixture["schema_version"] == "pixelgym-grounding-runner-parity-fixture-v1"

    for expected in fixture["plans"].values():
        plan = _load(ROOT / expected["source_plan"])
        summary = _load(ROOT / expected["source_summary"])
        digest = "sha256:" + hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
        stop_rules = plan.get("stop_conditions", plan.get("stop_rules"))

        assert digest == expected["canonical_plan_sha256"]
        assert _assignment(plan) == expected["task_assignment"]
        assert stop_rules == expected["stop_rules"]
        assert _classifications(summary) == expected["summary_classifications"]
