from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from pixelgym.grounding.analysis import (
    ANALYSIS_SCHEMA_VERSION,
    DISTANCE_THRESHOLD_METHOD,
    ERROR_REVIEW_SCHEMA_VERSION,
    analyze_predictions,
    apply_manual_error_review_decisions,
    build_error_review_template,
    distance_threshold_curve,
    mcnemar_exact,
    paired_bootstrap_interval,
)
from pixelgym.grounding.evaluation import (
    PREDICTION_SCHEMA_VERSION,
    PREDICTION_SCHEMA_VERSION_V2,
    PROMPT_VERSION,
    PROMPT_VERSION_V2,
)
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
    assert result["schema_version"] == "pixelgym-grounding-results-v3"
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


def _sized_example(index: int, bbox: list[int]) -> dict:
    example = _example(index % 4)
    example.update(
        {
            "example_id": f"example-{index}",
            "target_id": f"target-{index}",
            "target": f"Click target {index}",
            "task_seed": index,
            "bbox": bbox,
        }
    )
    return example


def _threshold_fixture() -> tuple[list[dict], list[dict]]:
    """Two examples per frozen target-size bucket, with hand-set stored distances."""
    # 100x80 screenshot => 8000 px area; small below 40 px, medium below 160 px.
    boxes = {
        "small": [10, 10, 15, 16],
        "medium": [10, 10, 20, 20],
        "large": [10, 10, 30, 30],
    }
    raw_distances = [0.30, 0.05, 0.20, None, 0.01, 0.0]
    examples: list[dict] = []
    predictions: list[dict] = []
    for index, (size, offset) in enumerate(
        [(size, offset) for size in ("small", "medium", "large") for offset in (0, 1)]
    ):
        del offset
        examples.append(_sized_example(index, list(boxes[size])))
        raw_distance = raw_distances[index]
        raw = _prediction(
            index,
            "raw",
            correct=raw_distance == 0.0,
            target_proposed=None,
            parse_status="parsed" if raw_distance is not None else "invalid",
        )
        raw["normalized_center_distance"] = raw_distance
        if raw_distance is None:
            raw["point"] = None
        marks = _prediction(index, "marks", correct=True, target_proposed=True)
        marks["normalized_center_distance"] = 0.0
        predictions.extend([raw, marks])
    return examples, predictions


def test_distance_threshold_curve_is_monotone_and_fails_closed_on_missing_distance() -> None:
    examples, predictions = _threshold_fixture()
    raw_records = [row for row in predictions if row["condition"] == "raw"]
    curve = distance_threshold_curve(raw_records, thresholds=(0.0, 0.01, 0.05, 0.2, 0.3))

    assert [point["threshold"] for point in curve] == [0.0, 0.01, 0.05, 0.2, 0.3]
    # Denominator is always every record; the unparseable record is retained, never within.
    assert {point["record_count"] for point in curve} == {6}
    assert {point["measured_count"] for point in curve} == {5}
    assert {point["missing_count"] for point in curve} == {1}
    assert [point["within_count"] for point in curve] == [1, 2, 3, 4, 5]
    # An exactly-equal observed distance passes its threshold.
    assert curve[2]["within_count"] == 3
    assert curve[-1]["accuracy"] == pytest.approx(5 / 6)
    within = [point["within_count"] for point in curve]
    assert within == sorted(within)
    assert max(within) < len(raw_records), "a missing distance must never reach 100%"
    del examples


@pytest.mark.parametrize(
    "thresholds",
    [(), (-0.1, 0.2), (0.1, 0.1), (0.2, 0.1), (0.0, float("inf"))],
)
def test_distance_threshold_curve_rejects_invalid_threshold_grids(thresholds: tuple) -> None:
    _, predictions = _threshold_fixture()
    with pytest.raises(ValueError):
        distance_threshold_curve(predictions, thresholds=thresholds)


def test_distance_threshold_report_breaks_down_every_target_size() -> None:
    examples, predictions = _threshold_fixture()
    result = analyze_predictions(
        examples=examples,
        predictions=predictions,
        error_reviews=_manual_reviews(examples, predictions),
        bootstrap_samples=200,
        bootstrap_seed=11,
    )
    section = result["distance_threshold_accuracy"]

    assert section["method"] == DISTANCE_THRESHOLD_METHOD
    assert section["is_scored_benchmark_metric"] is False
    assert section["missing_distance_counted_as_incorrect"] is True
    assert set(section["by_target_size"]) == {"small", "medium", "large"}
    for size, bucket in section["by_target_size"].items():
        assert bucket["example_count"] == 2
        ratio = bucket["target_area_ratio"]
        assert ratio["minimum"] == ratio["maximum"] == ratio["median"]
        # Strict accuracy in the breakdown must agree with the existing frozen slice.
        assert (
            bucket["conditions"]["raw"]["strict_accuracy"]
            == result["slices"]["target_size"][size]["raw_accuracy"]
        )
        for condition in ("raw", "marks"):
            curve = bucket["conditions"][condition]["curve"]
            assert [point["threshold"] for point in curve] == section["thresholds"]
            assert {point["record_count"] for point in curve} == {2}
    assert section["by_target_size"]["small"]["target_area_ratio"]["median"] == pytest.approx(
        30 / 8000
    )
    assert section["by_target_size"]["large"]["target_area_ratio"]["median"] == pytest.approx(
        400 / 8000
    )


def test_analysis_records_the_threshold_grid_and_keeps_gate_accuracy_strict() -> None:
    examples, predictions = _threshold_fixture()
    result = analyze_predictions(
        examples=examples,
        predictions=predictions,
        error_reviews=_manual_reviews(examples, predictions),
        bootstrap_samples=200,
        bootstrap_seed=11,
        distance_thresholds=(0.0, 0.05, 0.4),
    )
    config = result["analysis_config"]

    assert config["distance_thresholds"] == [0.0, 0.05, 0.4]
    assert config["distance_threshold_method"] == DISTANCE_THRESHOLD_METHOD
    assert result["distance_threshold_accuracy"]["thresholds"] == [0.0, 0.05, 0.4]
    # The gate-facing accuracy stays point-inside-box and is unmoved by the sweep.
    raw_curve = result["distance_threshold_accuracy"]["conditions"]["raw"]["curve"]
    assert result["conditions"]["raw"]["accuracy"] == pytest.approx(1 / 6)
    assert raw_curve[0]["accuracy"] == pytest.approx(1 / 6)
    assert raw_curve[-1]["accuracy"] == pytest.approx(5 / 6)
    assert result["per_example"][0]["target_area_ratio"] == pytest.approx(30 / 8000)


def _v2_prediction(
    index: int,
    condition: str,
    *,
    correct: bool,
    target_proposed: bool | None,
    parse_status: str = "parsed",
) -> dict:
    point = [20.0, 20.0] if correct else ([70.0, 60.0] if parse_status == "parsed" else None)
    parsed = {"x": int(point[0]), "y": int(point[1])} if point else None
    return {
        "schema_version": PREDICTION_SCHEMA_VERSION_V2,
        "protocol_version": PROTOCOL_VERSION,
        "prompt_version": PROMPT_VERSION_V2,
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
        "cache_key": f"cache-v2-{index}-{condition}",
        "raw_response": json.dumps(parsed) if parsed is not None else "not json",
        "parse_status": parse_status,
        "parse_error": None if parse_status == "parsed" else "invalid JSON",
        "parsed_prediction": parsed,
        "point": point,
        "mark_id": None,
        "correct": correct,
        "normalized_center_distance": 0.0 if correct else (0.5 if point else None),
        "target_proposed": target_proposed,
        "request_failure": None,
    }


def _v2_fixture() -> tuple[list[dict], list[dict]]:
    examples = [_example(index) for index in range(4)]
    outcomes = ((False, True), (True, False), (True, True), (False, False))
    predictions = []
    for index, (raw_correct, marks_correct) in enumerate(outcomes):
        predictions.append(
            _v2_prediction(
                index,
                "raw",
                correct=raw_correct,
                target_proposed=None,
                parse_status="invalid" if index == 3 else "parsed",
            )
        )
        predictions.append(
            _v2_prediction(
                index,
                "marks",
                correct=marks_correct,
                target_proposed=index != 3,
            )
        )
    return examples, predictions


def test_analysis_accepts_v2_prediction_records() -> None:
    examples, predictions = _v2_fixture()
    reviews = _manual_reviews(examples, predictions)
    result = analyze_predictions(
        examples=examples,
        predictions=predictions,
        error_reviews=reviews,
        bootstrap_samples=500,
        bootstrap_seed=7,
    )

    assert result["schema_version"] == ANALYSIS_SCHEMA_VERSION
    assert result["prompt_version"] == PROMPT_VERSION_V2
    assert result["conditions"]["raw"]["accuracy"] == 0.5
    assert result["conditions"]["marks"]["accuracy"] == 0.5
    assert result["paired"]["mcnemar_exact_p_value"] == 1.0
    assert len(result["per_example"]) == 4


def test_analysis_rejects_mixed_v1_v2_prediction_schema_versions() -> None:
    examples = [_example(index) for index in range(4)]
    _, v1_predictions = _fixture()
    _, v2_predictions = _v2_fixture()
    mixed = v1_predictions[:4] + v2_predictions[4:]
    reviews = _manual_reviews(examples, v1_predictions)
    with pytest.raises(ValueError, match="prediction schema versions must not be mixed"):
        analyze_predictions(
            examples=examples,
            predictions=mixed,
            error_reviews=reviews,
            bootstrap_samples=10,
        )
