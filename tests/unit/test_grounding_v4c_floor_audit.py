from pathlib import Path

from scripts.audit_grounding_v4c_floor import audit

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_qwen_floor_audit_preserves_score_and_identifies_normalized_grid() -> None:
    report = audit(
        REPOSITORY_ROOT,
        REPOSITORY_ROOT / "artifacts" / "grounding-v4c-pilot-predictions-qwen3-8-27b.jsonl",
        REPOSITORY_ROOT / "artifacts" / "grounding-v4c-pilot-results-qwen3-8-27b.json",
    )

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
