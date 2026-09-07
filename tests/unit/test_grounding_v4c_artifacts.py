"""Integrity checks for the checked-in v4c pilot evidence.

Ported from the now-deleted `legacy.grounding`-backed
`test_grounding_v4c.py::test_v4c_candidate_and_overlay_artifacts_contain_no_target_identity`
(issue #170; the body already imported no `legacy` symbols).

The floor-audit check below replaces
`test_grounding_v4c_floor_audit.py::test_qwen_floor_audit_preserves_score_and_identifies_normalized_grid`,
which called `legacy.grounding.scripts.audit_grounding_v4c_floor.audit()` to re-derive the
report from the raw predictions/results plus the frozen v4c protocol and captured candidate
geometry. That re-derivation is reproducible at git tag `legacy-grounding-final`
(`python -m legacy.grounding.scripts.audit_grounding_v4c_floor`). Rather than porting the
whole derivation pipeline, this test treats the already-committed
`artifacts/grounding-v4c-pilot-floor-audit-qwen3-8-27b.json` report as the frozen fixture and
re-verifies it is still hash-bound to, and consistent with, the raw predictions/results files.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pixelgym.serialization import load_jsonl

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_v4c_candidate_and_overlay_artifacts_contain_no_target_identity() -> None:
    rows = load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4c-pilot-candidates.jsonl")
    rows += load_jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4c-pilot-overlays.jsonl")

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value).union(*(keys(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(child) for child in value))
        return set()

    forbidden = {"target", "target_id", "skip", "commit_stage", "consumer_stage"}
    assert all(not (forbidden & keys(row)) for row in rows)


def test_v4c_qwen_floor_audit_report_is_hash_bound_to_raw_evidence() -> None:
    artifacts = REPOSITORY_ROOT / "artifacts"
    report = json.loads(
        (artifacts / "grounding-v4c-pilot-floor-audit-qwen3-8-27b.json").read_text(
            encoding="utf-8"
        )
    )
    predictions_path = artifacts / "grounding-v4c-pilot-predictions-qwen3-8-27b.jsonl"
    results_path = artifacts / "grounding-v4c-pilot-results-qwen3-8-27b.json"

    assert report["sources"]["predictions"]["path"] == (
        "artifacts/grounding-v4c-pilot-predictions-qwen3-8-27b.jsonl"
    )
    assert (
        hashlib.sha256(predictions_path.read_bytes()).hexdigest()
        == report["sources"]["predictions"]["sha256"]
    )
    assert report["sources"]["results"]["path"] == (
        "artifacts/grounding-v4c-pilot-results-qwen3-8-27b.json"
    )
    assert (
        hashlib.sha256(results_path.read_bytes()).hexdigest()
        == report["sources"]["results"]["sha256"]
    )

    predictions = load_jsonl(predictions_path)
    assert report["stored_response_checks"]["record_count"] == len(predictions)
    parsed = [
        row
        for row in predictions
        if row.get("parse_status") == "parsed" and isinstance(row.get("parsed_action"), dict)
    ]
    assert report["stored_response_checks"]["parsed_action_count"] == len(parsed)
    assert report["stored_response_checks"] == {
        "record_count": 178,
        "parsed_action_count": 178,
        "parse_status_counts": {"parsed": 178},
        "request_failure_count": 0,
        "unknown_observation_hash_count": 0,
    }

    coordinate = report["coordinate_frame"]
    assert coordinate["native_point_inside_correct_target_count"] == 1
    assert coordinate["normalized_point_inside_correct_target_count"] == 177
    assert coordinate["normalized_semantic_nearest_target_count"] == 178
