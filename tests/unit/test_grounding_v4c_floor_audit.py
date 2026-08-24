import hashlib
import json
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


def test_floor_audit_ignores_invalid_action_dict_and_rejects_unbound_results(
    tmp_path: Path,
) -> None:
    evidence = REPOSITORY_ROOT / ".cache" / tmp_path.name
    evidence.mkdir(parents=True, exist_ok=True)
    predictions = evidence / "predictions.jsonl"
    results = evidence / "results.json"
    predictions.write_text(
        json.dumps(
            {
                "parse_status": "invalid",
                "parsed_action": {"action_type": 1, "x": 10, "y": 20, "key": 0},
                "request_failure": None,
                "cache_hit": True,
            }
        )
        + "\n"
    )
    prediction_reference = {
        "path": predictions.relative_to(REPOSITORY_ROOT).as_posix(),
        "sha256": hashlib.sha256(predictions.read_bytes()).hexdigest(),
    }
    results.write_text(
        json.dumps(
            {
                "protocol_version": "pixelgym-grounding-v4c-pilot",
                "model": "test-model",
                "predictions": prediction_reference,
                "collection": {"action_record_count": 1, "new_call_count": 0},
            }
        )
    )
    try:
        report = audit(REPOSITORY_ROOT, predictions, results)
        assert report["stored_response_checks"]["parsed_action_count"] == 0
        assert report["stored_response_checks"]["parse_status_counts"] == {"invalid": 1}
        assert report["coordinate_frame"]["observed_x_range"] is None
        assert report["coordinate_frame"]["observed_y_range"] is None
        assert report["coordinate_frame"]["native_point_inside_correct_target_count"] == 0
        assert report["coordinate_frame"]["normalized_point_inside_correct_target_count"] == 0

        value = json.loads(results.read_text())
        value["predictions"]["sha256"] = "0" * 64
        results.write_text(json.dumps(value))
        try:
            audit(REPOSITORY_ROOT, predictions, results)
        except ValueError as error:
            assert "not bound" in str(error)
        else:
            raise AssertionError("unbound result was accepted")
    finally:
        predictions.unlink(missing_ok=True)
        results.unlink(missing_ok=True)
        evidence.rmdir()
