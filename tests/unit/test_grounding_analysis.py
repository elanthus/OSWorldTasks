from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from pixelgym.grounding.analysis import (
    ANALYSIS_SCHEMA_VERSION,
    ERROR_REVIEW_SCHEMA_VERSION,
    analyze_predictions,
    apply_manual_error_review_decisions,
    build_error_review_template,
    mcnemar_exact,
    paired_bootstrap_interval,
)
from pixelgym.grounding.evaluation import PREDICTION_SCHEMA_VERSION, PROMPT_VERSION
from pixelgym.grounding.report import generate_results_package, render_error_review_images
from pixelgym.grounding.schema import PROTOCOL_VERSION


def _example(index: int) -> dict:
    kinds = ("text_input", "radio", "button", "checkbox")
    return {
        "example_id": f"example-{index}",
        "target_id": f"target-{index}",
        "target": f"Click target {index}",
        "element_type": kinds[index],
        "screen_state": "initial",
        "task_seed": index,
        "bbox": [10, 10, 30, 30],
        "screen_width": 100,
        "screen_height": 80,
    }


def _prediction(
    index: int,
    condition: str,
    *,
    correct: bool,
    target_proposed: bool | None,
    parse_status: str = "parsed",
) -> dict:
    point = [20.0, 20.0] if correct else ([70.0, 60.0] if parse_status == "parsed" else None)
    parsed = {"x": int(point[0]), "y": int(point[1])} if condition == "raw" and point else None
    if condition == "marks" and point:
        parsed = {"mark_id": 1 if correct else 2}
    return {
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "example_id": f"example-{index}",
        "condition": condition,
        "provider": "fixture-provider",
        "model": "fixture-model",
        "parameters": {"temperature": 0},
        "timestamp_utc": f"2026-08-10T00:00:{index * 2 + (condition == 'marks'):02d}+00:00",
        "latency_ms": 100.0 + index,
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "provider_metadata": {},
        "image_path": f"artifacts/images/{condition}-{index}.png",
        "image_sha256": "a" * 64,
        "prompt_sha256": "b" * 64,
        "cache_key": f"cache-{index}-{condition}",
        "raw_response": json.dumps(parsed) if parsed is not None else "not json",
        "parse_status": parse_status,
        "parse_error": None if parse_status == "parsed" else "invalid JSON",
        "parsed_prediction": parsed,
        "point": point,
        "mark_id": parsed.get("mark_id") if condition == "marks" and parsed else None,
        "correct": correct,
        "normalized_center_distance": 0.0 if correct else (0.5 if point else None),
        "target_proposed": target_proposed,
        "request_failure": None,
    }


def _fixture() -> tuple[list[dict], list[dict]]:
    examples = [_example(index) for index in range(4)]
    outcomes = ((False, True), (True, False), (True, True), (False, False))
    predictions = []
    for index, (raw_correct, marks_correct) in enumerate(outcomes):
        predictions.append(
            _prediction(
                index,
                "raw",
                correct=raw_correct,
                target_proposed=None,
                parse_status="invalid" if index == 3 else "parsed",
            )
        )
        predictions.append(
            _prediction(
                index,
                "marks",
                correct=marks_correct,
                target_proposed=index != 3,
            )
        )
    return examples, predictions


def _manual_reviews(examples: list[dict], predictions: list[dict]) -> list[dict]:
    reviews = build_error_review_template(examples, predictions)
    for review in reviews:
        review["review_status"] = "manual_visual_review"
        review["inference"] = "Manually classified from the annotated stored pair."
    return reviews


def test_exact_mcnemar_and_paired_bootstrap_are_deterministic() -> None:
    assert mcnemar_exact(0, 5) == pytest.approx(0.0625)
    assert mcnemar_exact(3, 3) == 1.0
    assert mcnemar_exact(0, 0) == 1.0
    first = paired_bootstrap_interval([-1, 0, 1, 1], samples=500, seed=42)
    second = paired_bootstrap_interval([-1, 0, 1, 1], samples=500, seed=42)
    assert first == second
    assert first[0] <= 0.25 <= first[1]


def test_analysis_keeps_paired_outcomes_and_separates_proposal_coverage() -> None:
    examples, predictions = _fixture()
    reviews = _manual_reviews(examples, predictions)
    result = analyze_predictions(
        examples=examples,
        predictions=predictions,
        error_reviews=reviews,
        bootstrap_samples=500,
        bootstrap_seed=7,
    )

    assert result["conditions"]["raw"]["accuracy"] == 0.5
    assert result["schema_version"] == "pixelgym-grounding-results-v2"
    assert result["schema_version"] == ANALYSIS_SCHEMA_VERSION
    assert result["conditions"]["marks"]["accuracy"] == 0.5
    assert result["conditions"]["raw"]["invalid_output_count"] == 1
    assert result["paired"]["raw_only_correct_count"] == 1
    assert result["paired"]["marks_only_correct_count"] == 1
    assert result["paired"]["mcnemar_exact_p_value"] == 1.0
    assert result["set_of_marks"]["proposal_coverage"] == 0.75
    assert result["set_of_marks"]["conditional_selection_accuracy"] == pytest.approx(2 / 3)
    assert result["error_taxonomy"]["error_record_count"] == 4
    assert result["error_taxonomy"]["all_manually_reviewed"] is True
    assert len(result["per_example"]) == 4
    assert result["usage_totals"] == {"input_tokens": 80, "output_tokens": 16}


def test_error_review_allows_multiple_nonexclusive_categories() -> None:
    examples, predictions = _fixture()
    reviews = _manual_reviews(examples, predictions)
    reviews[0]["categories"] = ["small target", "crowded or overlapping controls"]

    result = analyze_predictions(
        examples=examples,
        predictions=predictions,
        error_reviews=reviews,
        bootstrap_samples=10,
    )

    assert result["error_taxonomy"]["error_record_count"] == 4
    assert result["error_taxonomy"]["category_counts"]["small target"] == 1
    assert result["error_taxonomy"]["category_counts"]["crowded or overlapping controls"] == 1


def test_manual_error_decisions_must_cover_every_error() -> None:
    examples, predictions = _fixture()
    reviews = build_error_review_template(examples, predictions)
    keys = [f"{row['example_id']}/{row['condition']}" for row in reviews]
    decisions = {
        "schema_version": "pixelgym-grounding-error-review-decisions-v1",
        "protocol_version": PROTOCOL_VERSION,
        "category_assignments": {"wrong semantic element": keys},
        "category_interpretation": {"wrong semantic element": "Reviewed observation."},
    }

    finalized = apply_manual_error_review_decisions(reviews, decisions)
    assert all(row["review_status"] == "manual_visual_review" for row in finalized)
    assert all(row["categories"] == ["wrong semantic element"] for row in finalized)

    decisions["category_assignments"]["wrong semantic element"] = keys[:-1]
    with pytest.raises(ValueError, match="do not cover every error"):
        apply_manual_error_review_decisions(reviews, decisions)


def test_analysis_rejects_missing_pair_and_taxonomy_record() -> None:
    examples, predictions = _fixture()
    with pytest.raises(ValueError, match="raw and one marks"):
        analyze_predictions(
            examples=examples,
            predictions=predictions[:-1],
            error_reviews=[],
            bootstrap_samples=10,
        )
    reviews = _manual_reviews(examples, predictions)
    with pytest.raises(ValueError, match="error reviews do not match"):
        analyze_predictions(
            examples=examples,
            predictions=predictions,
            error_reviews=reviews[:-1],
            bootstrap_samples=10,
        )


@pytest.mark.parametrize("path", ["../outside.png", "/tmp/outside.png"])
def test_error_review_image_path_cannot_escape_repository(
    tmp_path: Path, path: str
) -> None:
    review = {
        "example_id": "example-0",
        "condition": "raw",
        "categories": ["wrong semantic element"],
        "review_status": "manual_visual_review",
        "review_image_path": path,
    }

    with pytest.raises(ValueError, match="repository-relative"):
        render_error_review_images(
            repository_root=tmp_path,
            examples=[],
            predictions=[],
            error_reviews=[review],
        )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def test_offline_results_package_is_reproducible_and_traceable(tmp_path: Path) -> None:
    examples, predictions = _fixture()
    reviews = _manual_reviews(examples, predictions)
    reviews[0]["categories"] = ["coordinate scaling error"]
    reviews[1]["categories"] = ["coordinate scaling error"]
    predictions[0]["latency_ms"] = None
    predictions[0]["timestamp_utc"] = None
    for record in predictions:
        if record["condition"] == "raw":
            record["normalized_center_distance"] = None
    artifact_dir = tmp_path / "artifacts"
    for record in predictions:
        path = tmp_path / record["image_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 80), "white").save(path)
        record["image_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    protocol_path = artifact_dir / "grounding-protocol.md"
    dataset_path = artifact_dir / "grounding-dataset.jsonl"
    overlay_path = artifact_dir / "grounding-overlays.jsonl"
    predictions_path = artifact_dir / "grounding-predictions.jsonl"
    reviews_path = artifact_dir / "grounding-error-review.jsonl"
    protocol_path.write_text("# Frozen protocol\n")
    _write_jsonl(dataset_path, examples)
    _write_jsonl(overlay_path, [{"example_id": row["example_id"]} for row in examples])
    _write_jsonl(predictions_path, predictions)
    _write_jsonl(reviews_path, reviews)
    results_path = artifact_dir / "grounding-results.json"
    report_path = artifact_dir / "grounding-report.md"
    stale_gallery = artifact_dir / "grounding/gallery/raw_win-stale.png"
    stale_gallery.parent.mkdir(parents=True)
    stale_gallery.write_bytes(b"stale")

    first = generate_results_package(
        repository_root=tmp_path,
        predictions_path=predictions_path,
        error_review_path=reviews_path,
        results_path=results_path,
        report_path=report_path,
        bootstrap_samples=200,
        bootstrap_seed=11,
    )
    first_results = results_path.read_bytes()
    first_report = report_path.read_bytes()
    second = generate_results_package(
        repository_root=tmp_path,
        predictions_path=predictions_path,
        error_review_path=reviews_path,
        results_path=results_path,
        report_path=report_path,
        bootstrap_samples=200,
        bootstrap_seed=11,
    )

    assert first == second
    assert results_path.read_bytes() == first_results
    assert report_path.read_bytes() == first_report
    assert "No model or network calls" in report_path.read_text()
    assert "4 error records" in report_path.read_text()
    assert (
        "2 coordinate-scaling labels are reviewer inferences"
        in report_path.read_text()
    )
    assert "perfectly aliased" in report_path.read_text()
    assert "n/a" in report_path.read_text()
    assert first["latency"]["all"]["missing_count"] == 1
    assert first["collection"]["timestamp_missing_count"] == 1
    assert (artifact_dir / "grounding/figures/raw-vs-marks-accuracy.png").is_file()
    assert (artifact_dir / "grounding/figures/control-type-accuracy.png").is_file()
    assert not stale_gallery.exists()
    stored = json.loads(results_path.read_text())
    assert (
        stored["inputs"]["artifacts/grounding-predictions.jsonl"]
        == hashlib.sha256(predictions_path.read_bytes()).hexdigest()
    )
    assert (
        stored["inputs"]["artifacts/grounding-protocol.md"]
        == hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    )
    assert all(row["schema_version"] == ERROR_REVIEW_SCHEMA_VERSION for row in reviews)
