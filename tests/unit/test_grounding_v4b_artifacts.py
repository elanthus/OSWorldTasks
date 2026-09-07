"""Integrity checks for the checked-in v4b pilot evidence.

Ported from the now-deleted `legacy.grounding`-backed
`test_grounding_v4b.py::test_v4b_candidate_and_overlay_artifacts_contain_no_target_identity`
and `test_grounding_v4b_runner.py::test_committed_haiku_result_matches_immutable_evidence`
(issue #170). Both bodies already imported no `legacy` symbols.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pixelgym.serialization import load_jsonl

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_v4b_candidate_and_overlay_artifacts_contain_no_target_identity() -> None:
    rows = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4b-pilot-candidates.jsonl")
    rows += load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4b-pilot-overlays.jsonl")

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value).union(*(keys(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(child) for child in value))
        return set()

    assert all(not ({"target", "target_id"} & keys(row)) for row in rows)


def test_v4b_committed_haiku_result_matches_immutable_evidence() -> None:
    artifacts = REPOSITORY_ROOT / "artifacts"
    result = json.loads(
        (artifacts / "grounding-v4b-pilot-results-haiku.json").read_text(encoding="utf-8")
    )
    plan = json.loads(
        (artifacts / "grounding-v4b-pilot-plan-haiku.json").read_text(encoding="utf-8")
    )

    assert plan["upper_bound_calls"] == 80
    assert plan["cached_reachable_state_requests"] == 0
    assert result["model"] == "claude-haiku-4-5-20251001"
    assert result["collection"] == {
        "action_record_count": 60,
        "cache_hit_count": 0,
        "condition_record_count": 20,
        "cumulative_paid_call_count": 60,
        "episode_count": 10,
        "new_call_count": 60,
        "prior_paid_call_count": 0,
    }
    assert result["conditions"] == {
        "marks": {"episode_count": 10, "success_count": 10},
        "raw": {"episode_count": 10, "success_count": 10},
    }
    assert result["failures"] == {}
    assert result["routing"]["decision"] == "design_longer_horizon_successor"
    expected_evidence = {
        "predictions": {
            "path": "artifacts/grounding-v4b-pilot-predictions-haiku.jsonl",
            "sha256": "1fa6822d8595c730c2e5e81b3fced548547aa3686d62e754902ed63bdb8d1e80",
        },
        "condition_summaries": {
            "path": "artifacts/grounding-v4b-pilot-conditions-haiku.jsonl",
            "sha256": "4d936bbcb00f58b770cecea7b8d57dc74c8c7af71af27308dd893861ec131d99",
        },
    }
    for key, expected in expected_evidence.items():
        assert result[key] == expected
        evidence_path = REPOSITORY_ROOT / result[key]["path"]
        assert hashlib.sha256(evidence_path.read_bytes()).hexdigest() == result[key]["sha256"]
