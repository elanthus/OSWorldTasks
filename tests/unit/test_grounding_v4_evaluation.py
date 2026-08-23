"""Fast tests for the v4 paid-call cap and input checks."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.evaluation import (
    PARSER_VERSION_V2,
    PREDICTION_SCHEMA_VERSION_V2,
    PROMPT_VERSION_V2,
    ResponseCache,
    cache_key,
    schema_for,
)
from pixelgym.grounding.providers import MockProvider
from pixelgym.grounding.schema import PROTOCOL_VERSION
from pixelgym.grounding.v4_evaluation import (
    planned_v4_calls,
    record_v4_evaluation,
    run_v4_evaluation,
    summarize_v4_evaluation,
)
from pixelgym.grounding.v4_protocol import V4_CONDITION_CALL_CAP, V4_PROTOCOL_VERSION


def _write_inputs(root: Path, *, overlay_count: int = 10) -> None:
    artifacts = root / "artifacts"
    artifacts.mkdir()
    examples = []
    overlays = []
    for index in range(10):
        example_id = f"v4-{index:02d}"
        examples.append(
            {
                "protocol_version": V4_PROTOCOL_VERSION,
                "example_id": example_id,
                "target": "Click the target",
                "screen_width": 1024,
                "screen_height": 768,
                "image_sha256": f"{index:064x}",
            }
        )
        if index < overlay_count:
            overlays.append(
                {
                    "protocol_version": V4_PROTOCOL_VERSION,
                    "example_id": example_id,
                    "marked_image_sha256": f"{index + 100:064x}",
                }
            )
    (artifacts / "grounding-v4-pilot-dataset.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in examples)
    )
    (artifacts / "grounding-v4-pilot-overlays.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in overlays)
    )


def test_planned_v4_calls_is_exactly_twenty_without_invoking_provider(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    provider = MockProvider()
    plan = planned_v4_calls(
        repository_root=tmp_path,
        provider=provider,
        cache=ResponseCache(tmp_path / "cache"),
    )
    assert plan == {
        "total_condition_records": V4_CONDITION_CALL_CAP,
        "cached_calls": 0,
        "new_calls": V4_CONDITION_CALL_CAP,
    }
    assert provider.call_count == 0


def test_v4_runner_refuses_cap_below_plan_before_invocation(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    provider = MockProvider()
    with pytest.raises(RuntimeError, match="needs 20 new calls but cap is 19"):
        run_v4_evaluation(
            repository_root=tmp_path,
            provider=provider,
            output_path=tmp_path / "predictions.jsonl",
            max_new_calls=19,
        )
    assert provider.call_count == 0


@pytest.mark.parametrize("cap", [-1, 21])
def test_v4_runner_rejects_caps_outside_human_gate(tmp_path: Path, cap: int) -> None:
    _write_inputs(tmp_path)
    with pytest.raises(ValueError, match="between 0 and 20"):
        run_v4_evaluation(
            repository_root=tmp_path,
            provider=MockProvider(),
            output_path=tmp_path / "predictions.jsonl",
            max_new_calls=cap,
        )


def test_v4_planner_rejects_incomplete_overlay_set(tmp_path: Path) -> None:
    _write_inputs(tmp_path, overlay_count=9)
    with pytest.raises(ValueError, match="exactly ten"):
        planned_v4_calls(
            repository_root=tmp_path,
            provider=MockProvider(),
            cache=ResponseCache(tmp_path / "cache"),
        )


def test_v4_protocol_changes_cache_identity(tmp_path: Path) -> None:
    provider = MockProvider()
    material = {
        "provider": provider,
        "condition": "raw",
        "prompt": "Locate the same target.",
        "image_sha256": "a" * 64,
        "schema": schema_for("raw", parser_version=PARSER_VERSION_V2),
        "prompt_version": PROMPT_VERSION_V2,
    }

    default_key = cache_key(**material, protocol_version=PROTOCOL_VERSION)
    v4_key = cache_key(**material, protocol_version=V4_PROTOCOL_VERSION)

    assert default_key != v4_key


def test_v4_evaluation_does_not_import_capture_instrumentation() -> None:
    source_path = Path(__file__).parents[2] / "pixelgym" / "grounding" / "v4_evaluation.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "pixelgym.grounding.calibration_v4" not in imported_modules


def test_fixture_contains_no_target_identity_in_overlays(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    rows: list[dict[str, Any]] = [
        json.loads(line)
        for line in (tmp_path / "artifacts" / "grounding-v4-pilot-overlays.jsonl")
        .read_text()
        .splitlines()
    ]
    assert all("target_id" not in row for row in rows)


def _write_predictions(
    root: Path,
    *,
    raw_correct: int = 9,
    marks_correct: int = 9,
    failed_index: int | None = None,
) -> Path:
    rows = []
    for index in range(10):
        for condition in ("raw", "marks"):
            is_failure = failed_index == index and condition == "raw"
            rows.append(
                {
                    "schema_version": PREDICTION_SCHEMA_VERSION_V2,
                    "protocol_version": V4_PROTOCOL_VERSION,
                    "prompt_version": PROMPT_VERSION_V2,
                    "example_id": f"v4-{index:02d}",
                    "condition": condition,
                    "provider": "codex-cli",
                    "model": "gpt-5.6-luna",
                    "parameters": {"reasoning_effort": "low", "temperature": None},
                    "timestamp_utc": f"2026-08-23T04:00:{index:02d}+00:00",
                    "parse_status": "request_failure" if is_failure else "parsed",
                    "correct": (
                        False
                        if is_failure
                        else index < (raw_correct if condition == "raw" else marks_correct)
                    ),
                    "target_proposed": True if condition == "marks" else None,
                }
            )
    path = root / "predictions.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def test_v4_results_route_saturated_conditions_to_multistep_design(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    predictions = _write_predictions(tmp_path)

    results = summarize_v4_evaluation(
        repository_root=tmp_path,
        predictions_path=predictions,
    )

    assert results["conditions"] == {
        "raw": {"correct_count": 9, "record_count": 10},
        "marks": {"correct_count": 9, "record_count": 10},
    }
    assert results["failures"] == {
        "request_failure_count": 0,
        "parse_failure_count": 0,
    }
    assert results["routing"]["decision"] == "design_multi_step_v4b"


@pytest.mark.parametrize("failure_status", ["request_failure", "invalid"])
def test_v4_results_route_any_failure_to_floor_audit(
    tmp_path: Path, failure_status: str
) -> None:
    _write_inputs(tmp_path)
    predictions = _write_predictions(tmp_path, failed_index=0)
    if failure_status == "invalid":
        rows = [json.loads(line) for line in predictions.read_text().splitlines()]
        rows[0]["parse_status"] = "invalid"
        predictions.write_text("".join(json.dumps(row) + "\n" for row in rows))

    results = summarize_v4_evaluation(
        repository_root=tmp_path,
        predictions_path=predictions,
    )

    expected_request_failures = int(failure_status == "request_failure")
    expected_parse_failures = int(failure_status == "invalid")
    assert results["failures"] == {
        "request_failure_count": expected_request_failures,
        "parse_failure_count": expected_parse_failures,
    }
    assert results["routing"]["decision"] == "floor_audit"


def test_v4_results_reject_duplicate_condition_record(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    predictions = _write_predictions(tmp_path)
    rows = [json.loads(line) for line in predictions.read_text().splitlines()]
    rows[-1] = rows[0]
    predictions.write_text("".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises(ValueError, match="duplicate"):
        summarize_v4_evaluation(
            repository_root=tmp_path,
            predictions_path=predictions,
        )


def test_record_v4_evaluation_updates_manifest_and_hashes_outputs(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    predictions = _write_predictions(tmp_path)
    manifest_path = tmp_path / "artifacts" / "grounding-v4-pilot-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "protocol_version": V4_PROTOCOL_VERSION,
                "primary_model": "gpt-5.6-luna",
                "primary_parameters": {"reasoning_effort": "low", "temperature": None},
                "status": "calibration_captured_evaluation_not_run",
                "model_calls_performed": 0,
                "decision_history": [],
                "outputs": {},
            }
        )
    )
    results_path = tmp_path / "artifacts" / "results.json"

    first = record_v4_evaluation(
        repository_root=tmp_path,
        predictions_path=predictions,
        results_path=results_path,
        manifest_path=manifest_path,
    )
    second = record_v4_evaluation(
        repository_root=tmp_path,
        predictions_path=predictions,
        results_path=results_path,
        manifest_path=manifest_path,
    )

    assert second == first
    manifest = json.loads(manifest_path.read_text())
    assert manifest["model_calls_performed"] == 20
    assert manifest["status"] == "calibration_evaluated_v4b_design_required"
    assert len(manifest["decision_history"]) == 1
    assert manifest["decision_history"][0]["route"] == "design_multi_step_v4b"
    assert manifest["outputs"]["predictions_luna"]["sha256"]
    assert manifest["outputs"]["results_luna"]["sha256"]


def test_record_v4_evaluation_rejects_external_result_before_writing(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    predictions = _write_predictions(tmp_path)
    manifest_path = tmp_path / "artifacts" / "grounding-v4-pilot-manifest.json"
    manifest_path.write_text("{}")
    external_results = tmp_path.parent / f"{tmp_path.name}-outside-results.json"

    with pytest.raises(ValueError, match="results path must be inside"):
        record_v4_evaluation(
            repository_root=tmp_path,
            predictions_path=predictions,
            results_path=external_results,
            manifest_path=manifest_path,
        )

    assert not external_results.exists()


def test_record_v4_evaluation_rejects_parent_traversal_before_writing(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path)
    predictions = _write_predictions(tmp_path)
    manifest_path = tmp_path / "artifacts" / "grounding-v4-pilot-manifest.json"
    manifest_path.write_text("{}")
    external_results = tmp_path / ".." / f"{tmp_path.name}-traversal-results.json"

    with pytest.raises(ValueError, match="results path must be inside"):
        record_v4_evaluation(
            repository_root=tmp_path,
            predictions_path=predictions,
            results_path=external_results,
            manifest_path=manifest_path,
        )

    assert not external_results.resolve().exists()


def test_record_v4_evaluation_validates_manifest_before_writing_result(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path)
    predictions = _write_predictions(tmp_path)
    manifest_path = tmp_path / "artifacts" / "grounding-v4-pilot-manifest.json"
    manifest_path.write_text(json.dumps({"protocol_version": "wrong"}))
    results_path = tmp_path / "artifacts" / "results.json"

    with pytest.raises(ValueError, match="manifest protocol"):
        record_v4_evaluation(
            repository_root=tmp_path,
            predictions_path=predictions,
            results_path=results_path,
            manifest_path=manifest_path,
        )

    assert not results_path.exists()


def test_record_v4_evaluation_validates_outputs_before_writing_result(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path)
    predictions = _write_predictions(tmp_path)
    manifest_path = tmp_path / "artifacts" / "grounding-v4-pilot-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "protocol_version": V4_PROTOCOL_VERSION,
                "primary_model": "gpt-5.6-luna",
                "primary_parameters": {"reasoning_effort": "low", "temperature": None},
                "decision_history": [],
            }
        )
    )
    results_path = tmp_path / "artifacts" / "results.json"

    with pytest.raises(TypeError, match="manifest outputs"):
        record_v4_evaluation(
            repository_root=tmp_path,
            predictions_path=predictions,
            results_path=results_path,
            manifest_path=manifest_path,
        )

    assert not results_path.exists()
