"""Capped evaluation planning and execution for the frozen v4 pilot inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from legacy.grounding.v4_protocol import V4_CONDITION_CALL_CAP, V4_PROTOCOL_VERSION
from pixelgym.grounding.evaluation import (
    PARSER_VERSION_V2,
    PREDICTION_SCHEMA_VERSION_V2,
    PROMPT_VERSION_V2,
    Condition,
    ResponseCache,
    cache_key,
    evaluate_one,
    prompt_for,
    schema_for,
)
from pixelgym.grounding.providers import GroundingProvider
from pixelgym.serialization import canonical_json_text, load_jsonl

V4_CONDITIONS: tuple[Condition, Condition] = ("raw", "marks")
V4_RESULTS_SCHEMA_VERSION = "pixelgym-grounding-v4-results-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_v4_inputs(
    repository_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    examples = load_jsonl(repository_root / "artifacts" / "grounding-v4-pilot-dataset.jsonl")
    overlays = load_jsonl(repository_root / "artifacts" / "grounding-v4-pilot-overlays.jsonl")
    if len(examples) != 10 or len(overlays) != 10:
        raise ValueError("v4 evaluation requires exactly ten examples and overlays")
    by_example = {row["example_id"]: row for row in overlays}
    if len(by_example) != len(overlays):
        raise ValueError("v4 overlay metadata contains duplicate example IDs")
    if {row["example_id"] for row in examples} != set(by_example):
        raise ValueError("v4 dataset and overlay example IDs do not match")
    if any(row.get("protocol_version") != V4_PROTOCOL_VERSION for row in examples + overlays):
        raise ValueError("v4 input protocol version does not match")
    return examples, by_example


def planned_v4_calls(
    *,
    repository_root: Path,
    provider: GroundingProvider,
    cache: ResponseCache,
) -> dict[str, int]:
    examples, overlays = load_v4_inputs(repository_root)
    total = 0
    cached = 0
    for example in examples:
        overlay = overlays[example["example_id"]]
        for condition in V4_CONDITIONS:
            prompt = prompt_for(example, condition, prompt_version=PROMPT_VERSION_V2)
            schema = schema_for(condition, parser_version=PARSER_VERSION_V2)
            image_sha256 = (
                example["image_sha256"] if condition == "raw" else overlay["marked_image_sha256"]
            )
            key = cache_key(
                provider=provider,
                condition=condition,
                prompt=prompt,
                image_sha256=image_sha256,
                schema=schema,
                prompt_version=PROMPT_VERSION_V2,
                protocol_version=V4_PROTOCOL_VERSION,
            )
            total += 1
            cached += cache.get(key) is not None
    if total != V4_CONDITION_CALL_CAP:
        raise ValueError("v4 pilot must contain exactly twenty condition records")
    return {
        "total_condition_records": total,
        "cached_calls": cached,
        "new_calls": total - cached,
    }


def run_v4_evaluation(
    *,
    repository_root: Path,
    provider: GroundingProvider,
    output_path: Path,
    max_new_calls: int,
    cache_directory: Path | None = None,
) -> dict[str, Any]:
    if max_new_calls < 0 or max_new_calls > V4_CONDITION_CALL_CAP:
        raise ValueError("v4 max_new_calls must be between 0 and 20")
    cache = ResponseCache(
        cache_directory or repository_root / ".cache" / "grounding-v4" / "responses"
    )
    plan = planned_v4_calls(repository_root=repository_root, provider=provider, cache=cache)
    if plan["new_calls"] > max_new_calls:
        raise RuntimeError(
            f"v4 evaluation needs {plan['new_calls']} new calls but cap is {max_new_calls}"
        )
    examples, overlays = load_v4_inputs(repository_root)
    records = []
    cache_hits = 0
    for example in examples:
        overlay = overlays[example["example_id"]]
        for condition in V4_CONDITIONS:
            record, cache_hit = evaluate_one(
                repository_root=repository_root,
                example=example,
                overlay=overlay,
                condition=condition,
                provider=provider,
                cache=cache,
                prompt_version=PROMPT_VERSION_V2,
                parser_version=PARSER_VERSION_V2,
                prediction_schema_version=PREDICTION_SCHEMA_VERSION_V2,
                protocol_version=V4_PROTOCOL_VERSION,
            )
            records.append(record)
            cache_hits += cache_hit
    encoded = "".join(canonical_json_text(row) + "\n" for row in records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_file() and output_path.read_text() != encoded:
        raise ValueError("refusing to overwrite different immutable v4 predictions")
    output_path.write_text(encoded, encoding="utf-8")
    return {
        "protocol_version": V4_PROTOCOL_VERSION,
        "prompt_version": PROMPT_VERSION_V2,
        "prediction_schema_version": PREDICTION_SCHEMA_VERSION_V2,
        "provider": provider.name,
        "model": provider.model,
        "example_count": len(examples),
        "condition_record_count": len(records),
        "cache_hits": cache_hits,
        "new_calls": len(records) - cache_hits,
        "output_path": output_path.relative_to(repository_root).as_posix()
        if output_path.is_relative_to(repository_root)
        else str(output_path),
    }


def summarize_v4_evaluation(
    *, repository_root: Path, predictions_path: Path
) -> dict[str, Any]:
    """Validate and summarize stored v4 predictions without invoking a provider."""
    examples, _ = load_v4_inputs(repository_root)
    predictions = load_jsonl(predictions_path)
    if len(predictions) != V4_CONDITION_CALL_CAP:
        raise ValueError("v4 results require exactly twenty condition records")

    expected_keys = {
        (example["example_id"], condition)
        for example in examples
        for condition in V4_CONDITIONS
    }
    actual_keys = [(row.get("example_id"), row.get("condition")) for row in predictions]
    if len(set(actual_keys)) != len(actual_keys):
        raise ValueError("v4 predictions contain duplicate example/condition records")
    if set(actual_keys) != expected_keys:
        raise ValueError("v4 predictions do not match the frozen example/condition grid")

    required_constants = {
        "schema_version": PREDICTION_SCHEMA_VERSION_V2,
        "protocol_version": V4_PROTOCOL_VERSION,
        "prompt_version": PROMPT_VERSION_V2,
    }
    for field, expected in required_constants.items():
        if any(row.get(field) != expected for row in predictions):
            raise ValueError(f"v4 prediction {field} does not match {expected!r}")
    for field in ("provider", "model", "parameters"):
        encodings = {canonical_json_text(row.get(field)) for row in predictions}
        if len(encodings) != 1:
            raise ValueError(f"v4 prediction {field} must be constant")

    allowed_parse_statuses = {"parsed", "invalid", "request_failure"}
    if any(row.get("parse_status") not in allowed_parse_statuses for row in predictions):
        raise ValueError("v4 predictions contain an unknown parse status")
    if any(type(row.get("correct")) is not bool for row in predictions):
        raise TypeError("v4 prediction correct values must be booleans")
    if any(
        not isinstance(row.get("timestamp_utc"), str) or not row["timestamp_utc"]
        for row in predictions
    ):
        raise ValueError("v4 prediction timestamps must be nonempty strings")
    raw_records = [row for row in predictions if row["condition"] == "raw"]
    marks_records = [row for row in predictions if row["condition"] == "marks"]
    raw_correct = sum(row.get("correct") is True for row in raw_records)
    marks_correct = sum(row.get("correct") is True for row in marks_records)
    request_failures = sum(
        row["parse_status"] == "request_failure" for row in predictions
    )
    parse_failures = sum(row["parse_status"] == "invalid" for row in predictions)
    proposal_covered = sum(row.get("target_proposed") is True for row in marks_records)

    raw_by_id = {row["example_id"]: row for row in raw_records}
    marks_by_id = {row["example_id"]: row for row in marks_records}
    both_correct = sum(
        raw_by_id[example_id].get("correct") is True
        and marks_by_id[example_id].get("correct") is True
        for example_id in raw_by_id
    )
    both_incorrect = sum(
        raw_by_id[example_id].get("correct") is not True
        and marks_by_id[example_id].get("correct") is not True
        for example_id in raw_by_id
    )
    raw_only = sum(
        raw_by_id[example_id].get("correct") is True
        and marks_by_id[example_id].get("correct") is not True
        for example_id in raw_by_id
    )
    marks_only = sum(
        raw_by_id[example_id].get("correct") is not True
        and marks_by_id[example_id].get("correct") is True
        for example_id in raw_by_id
    )

    if proposal_covered != len(marks_records):
        route = "floor_audit"
        rationale = "marks proposal coverage was below 100%"
    elif request_failures or parse_failures or raw_correct < 5 or marks_correct < 5:
        route = "floor_audit"
        rationale = "failure observed or at least one condition scored below 50%"
    elif raw_correct >= 9 or marks_correct >= 9:
        route = "design_multi_step_v4b"
        rationale = "at least one condition scored above 85%"
    elif 5 <= raw_correct <= 7 and 6 <= marks_correct <= 8:
        route = "freeze_v4_request_haiku"
        rationale = "both conditions landed in the preregistered calibration bands"
    else:
        route = "human_review"
        rationale = "result does not match a preregistered automatic routing cell"

    timestamps = [
        row["timestamp_utc"]
        for row in predictions
        if isinstance(row.get("timestamp_utc"), str) and row["timestamp_utc"]
    ]
    return {
        "schema_version": V4_RESULTS_SCHEMA_VERSION,
        "protocol_version": V4_PROTOCOL_VERSION,
        "prompt_version": PROMPT_VERSION_V2,
        "prediction_schema_version": PREDICTION_SCHEMA_VERSION_V2,
        "provider": predictions[0]["provider"],
        "model": predictions[0]["model"],
        "parameters": predictions[0]["parameters"],
        "collection": {
            "example_count": len(examples),
            "condition_record_count": len(predictions),
            "first_timestamp_utc": min(timestamps) if timestamps else None,
            "last_timestamp_utc": max(timestamps) if timestamps else None,
        },
        "conditions": {
            "raw": {"correct_count": raw_correct, "record_count": len(raw_records)},
            "marks": {"correct_count": marks_correct, "record_count": len(marks_records)},
        },
        "failures": {
            "request_failure_count": request_failures,
            "parse_failure_count": parse_failures,
        },
        "paired": {
            "both_correct_count": both_correct,
            "raw_only_correct_count": raw_only,
            "marks_only_correct_count": marks_only,
            "both_incorrect_count": both_incorrect,
        },
        "set_of_marks": {
            "proposal_covered_count": proposal_covered,
            "proposal_total_count": len(marks_records),
            "conditional_selection_correct_count": sum(
                row.get("target_proposed") is True and row.get("correct") is True
                for row in marks_records
            ),
            "conditional_selection_total_count": proposal_covered,
        },
        "routing": {"decision": route, "rationale": rationale},
        "predictions": {
            "path": predictions_path.relative_to(repository_root).as_posix()
            if predictions_path.is_relative_to(repository_root)
            else str(predictions_path),
            "sha256": _sha256(predictions_path),
        },
    }


def record_v4_evaluation(
    *,
    repository_root: Path,
    predictions_path: Path,
    results_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Write deterministic results and update the v4 evidence manifest."""
    repository_root = repository_root.resolve()
    predictions_path = predictions_path.resolve()
    results_path = results_path.resolve()
    manifest_path = manifest_path.resolve()
    evidence_paths = {
        "predictions": predictions_path,
        "results": results_path,
        "manifest": manifest_path,
    }
    for label, path in evidence_paths.items():
        if not path.is_relative_to(repository_root):
            raise ValueError(f"v4 {label} path must be inside the repository")

    results = summarize_v4_evaluation(
        repository_root=repository_root,
        predictions_path=predictions_path,
    )
    encoded_results = json.dumps(results, indent=2, sort_keys=True) + "\n"
    if results_path.is_file() and results_path.read_text(encoding="utf-8") != encoded_results:
        raise ValueError("refusing to overwrite different immutable v4 results")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol_version") != V4_PROTOCOL_VERSION:
        raise ValueError("v4 manifest protocol version does not match")
    if manifest.get("primary_model") != results["model"]:
        raise ValueError("v4 manifest primary model does not match predictions")
    if manifest.get("primary_parameters") != results["parameters"]:
        raise ValueError("v4 manifest primary parameters do not match predictions")
    route = results["routing"]["decision"]
    route_metadata = {
        "design_multi_step_v4b": (
            "calibration_evaluated_v4b_design_required",
            "single-step v4 saturated; design a separate multi-step v4b pilot",
        ),
        "floor_audit": (
            "calibration_evaluated_floor_audit_required",
            "perform a floor audit before changing task difficulty",
        ),
        "freeze_v4_request_haiku": (
            "calibration_evaluated_haiku_approval_required",
            "freeze v4 and request separate approval for the Haiku ceiling check",
        ),
        "human_review": (
            "calibration_evaluated_human_review_required",
            "request human review of the unmatched routing cell",
        ),
    }
    status, decision_text = route_metadata[route]
    decision = {
        "calls": results["collection"]["condition_record_count"],
        "decision": decision_text,
        "escalation": results["routing"]["rationale"],
        "evaluation_window_utc": [
            results["collection"]["first_timestamp_utc"],
            results["collection"]["last_timestamp_utc"],
        ],
        "marks_accuracy": (
            f'{results["conditions"]["marks"]["correct_count"]}/'
            f'{results["conditions"]["marks"]["record_count"]}'
        ),
        "model": results["model"],
        "parameters": results["parameters"],
        "parse_failures": results["failures"]["parse_failure_count"],
        "predictions_path": results["predictions"]["path"],
        "provider": results["provider"],
        "raw_accuracy": (
            f'{results["conditions"]["raw"]["correct_count"]}/'
            f'{results["conditions"]["raw"]["record_count"]}'
        ),
        "request_failures": results["failures"]["request_failure_count"],
        "results_path": results_path.relative_to(repository_root).as_posix(),
        "route": results["routing"]["decision"],
    }
    history = manifest.get("decision_history")
    if not isinstance(history, list):
        raise TypeError("v4 manifest decision history must be a list")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise TypeError("v4 manifest outputs must be an object")
    if history and history[-1] != decision:
        raise ValueError("refusing to replace different v4 decision history")

    # All validation that can fail deterministically is complete before either
    # evidence file is changed, so a rejected manifest cannot leave an orphan result.
    results_path.write_text(encoded_results, encoding="utf-8")
    if not history:
        history.append(decision)

    manifest["status"] = status
    manifest["model_calls_performed"] = results["collection"]["condition_record_count"]
    outputs["predictions_luna"] = results["predictions"]
    outputs["results_luna"] = {
        "path": results_path.relative_to(repository_root).as_posix(),
        "sha256": _sha256(results_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return results
