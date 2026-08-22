"""Paired raw-coordinate and set-of-marks grounding evaluation pipeline."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pixelgym.grounding.providers import GroundingProvider, ProviderResponse
from pixelgym.grounding.schema import PROTOCOL_VERSION
from pixelgym.serialization import canonical_json_text, load_jsonl

Condition = Literal["raw", "marks"]
PROMPT_VERSION = "pixelgym-grounding-prompt-v1"
PROMPT_VERSION_V2 = "pixelgym-grounding-prompt-v2"
PARSER_VERSION_V1 = "pixelgym-grounding-parser-v1"
PARSER_VERSION_V2 = "pixelgym-grounding-parser-v2"
PREDICTION_SCHEMA_VERSION = "pixelgym-grounding-prediction-v1"
PREDICTION_SCHEMA_VERSION_V2 = "pixelgym-grounding-prediction-v2"

RAW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
    "required": ["x", "y"],
    "additionalProperties": False,
}
MARKS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"mark_id": {"type": "integer", "minimum": 1}},
    "required": ["mark_id"],
    "additionalProperties": False,
}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_for(
    example: dict[str, Any], condition: Condition, *, prompt_version: str = PROMPT_VERSION
) -> str:
    if prompt_version not in (PROMPT_VERSION, PROMPT_VERSION_V2):
        raise ValueError(f"unknown prompt version {prompt_version!r}")
    common = (
        "Locate the requested control in the attached screenshot. "
        f"Target: {example['target']}. "
        f"The screenshot is {example['screen_width']} pixels wide and "
        f"{example['screen_height']} pixels high. "
    )
    if condition == "raw":
        return common + (
            "Return only a JSON object with integer x and y screenshot-pixel coordinates. "
            "The origin is the upper-left. Do not explain your answer and do not use tools."
        )
    if condition == "marks":
        if prompt_version == PROMPT_VERSION:
            return common + (
                "Every candidate control is outlined and has a visible numbered badge. "
                "Return only a JSON object with the integer mark_id of the requested control. "
                "Do not explain your answer and do not use tools."
            )
        return common + (
            "Every candidate control is outlined and has a visible numbered badge to help "
            "you locate controls. "
            "Return only a JSON object with integer x and y screenshot-pixel coordinates "
            "of the requested control. "
            "The origin is the upper-left. Do not explain your answer and do not use tools."
        )
    raise ValueError(f"unknown condition {condition!r}")


def schema_for(condition: Condition, *, parser_version: str = PARSER_VERSION_V1) -> dict[str, Any]:
    if parser_version not in (PARSER_VERSION_V1, PARSER_VERSION_V2):
        raise ValueError(f"unknown parser version {parser_version!r}")
    if condition == "raw":
        return RAW_SCHEMA
    if condition == "marks":
        return RAW_SCHEMA if parser_version == PARSER_VERSION_V2 else MARKS_SCHEMA
    raise ValueError(f"unknown condition {condition!r}")


def cache_key(
    *,
    provider: GroundingProvider,
    condition: Condition,
    prompt: str,
    image_sha256: str,
    schema: dict[str, Any],
    prompt_version: str = PROMPT_VERSION,
    protocol_version: str = PROTOCOL_VERSION,
) -> str:
    material = {
        "provider": provider.name,
        "model": provider.model,
        "parameters": provider.parameters,
        "protocol_version": protocol_version,
        "prompt_version": prompt_version,
        "condition": condition,
        "prompt": prompt,
        "image_sha256": image_sha256,
        "schema": schema,
    }
    return _sha256_text(canonical_json_text(material))


class ResponseCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> ProviderResponse | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        value = json.loads(path.read_text())
        if value.get("cache_key") != key:
            raise ValueError("cached response key does not match its filename")
        return ProviderResponse.from_cache_dict(value["provider_response"])

    def put(self, key: str, response: ProviderResponse) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(key)
        value = {"cache_key": key, "provider_response": response.to_cache_dict()}
        encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
        if path.is_file():
            if path.read_text() != encoded:
                raise ValueError("refusing to replace a different cached response")
            return
        temporary = path.with_suffix(".tmp")
        temporary.write_text(encoded)
        temporary.replace(path)


@dataclass(frozen=True)
class ParsedPrediction:
    status: str
    parsed_prediction: dict[str, int] | None
    point: list[float] | None
    mark_id: int | None
    error: str | None


def _parse_point(
    value: dict[str, Any], *, width: int, height: int, keys_error: str
) -> ParsedPrediction:
    if set(value) != {"x", "y"}:
        return ParsedPrediction("invalid", None, None, None, keys_error)
    x, y = value["x"], value["y"]
    if type(x) is not int or type(y) is not int:
        return ParsedPrediction("invalid", None, None, None, "x and y must be integers")
    if not (0 <= x < width and 0 <= y < height):
        return ParsedPrediction("invalid", value, None, None, "point lies outside screenshot")
    return ParsedPrediction("parsed", value, [float(x), float(y)], None, None)


def parse_prediction(
    raw_response: str | None,
    *,
    condition: Condition,
    width: int,
    height: int,
    marks: list[dict[str, Any]],
    parser_version: str = PARSER_VERSION_V1,
) -> ParsedPrediction:
    if parser_version not in (PARSER_VERSION_V1, PARSER_VERSION_V2):
        raise ValueError(f"unknown parser version {parser_version!r}")
    if raw_response is None:
        return ParsedPrediction("invalid", None, None, None, "response text is missing")
    try:
        value = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        return ParsedPrediction("invalid", None, None, None, f"invalid JSON: {exc.msg}")
    if not isinstance(value, dict):
        return ParsedPrediction("invalid", None, None, None, "response must be a JSON object")
    if condition == "raw":
        return _parse_point(
            value, width=width, height=height, keys_error="raw response keys must be x and y"
        )
    if condition == "marks":
        if parser_version == PARSER_VERSION_V2:
            return _parse_point(
                value,
                width=width,
                height=height,
                keys_error="marks response keys must be x and y",
            )
        if set(value) != {"mark_id"}:
            return ParsedPrediction(
                "invalid", None, None, None, "marks response key must be mark_id"
            )
        mark_id = value["mark_id"]
        if type(mark_id) is not int:
            return ParsedPrediction("invalid", None, None, None, "mark_id must be an integer")
        match = next((mark for mark in marks if mark["mark_id"] == mark_id), None)
        if match is None:
            return ParsedPrediction("invalid", value, None, mark_id, "mark_id does not exist")
        x0, y0, x1, y1 = match["bbox"]
        return ParsedPrediction(
            "parsed",
            value,
            [(x0 + x1) / 2, (y0 + y1) / 2],
            mark_id,
            None,
        )
    raise ValueError(f"unknown condition {condition!r}")


def score_point(
    point: list[float] | None, bbox: list[int], *, width: int, height: int
) -> tuple[bool, float | None]:
    if point is None:
        return False, None
    x, y = point
    x0, y0, x1, y1 = bbox
    correct = x0 <= x < x1 and y0 <= y < y1
    center_x, center_y = (x0 + x1) / 2, (y0 + y1) / 2
    distance = math.hypot(x - center_x, y - center_y) / math.hypot(width, height)
    return correct, distance


def evaluate_one(
    *,
    repository_root: Path,
    example: dict[str, Any],
    overlay: dict[str, Any],
    condition: Condition,
    provider: GroundingProvider,
    cache: ResponseCache,
    prompt_version: str = PROMPT_VERSION,
    parser_version: str = PARSER_VERSION_V1,
    prediction_schema_version: str = PREDICTION_SCHEMA_VERSION,
    protocol_version: str = PROTOCOL_VERSION,
) -> tuple[dict[str, Any], bool]:
    prompt = prompt_for(example, condition, prompt_version=prompt_version)
    schema = schema_for(condition, parser_version=parser_version)
    if condition == "raw":
        image_relative = example["image_path"]
        image_sha256 = example["image_sha256"]
    else:
        image_relative = overlay["marked_image_path"]
        image_sha256 = overlay["marked_image_sha256"]
    image_path = repository_root / image_relative
    if hashlib.sha256(image_path.read_bytes()).hexdigest() != image_sha256:
        raise ValueError("evaluation image digest does not match frozen metadata")
    key = cache_key(
        provider=provider,
        condition=condition,
        prompt=prompt,
        image_sha256=image_sha256,
        schema=schema,
        prompt_version=prompt_version,
        protocol_version=protocol_version,
    )
    response = cache.get(key)
    cache_hit = response is not None
    if response is None:
        response = provider.invoke(image_path=image_path, prompt=prompt, schema=schema)
        cache.put(key, response)
    if response.request_failure is not None:
        parsed = ParsedPrediction("request_failure", None, None, None, response.request_failure)
    else:
        parsed = parse_prediction(
            response.raw_response,
            condition=condition,
            width=example["screen_width"],
            height=example["screen_height"],
            marks=overlay["marks"],
            parser_version=parser_version,
        )
    correct, distance = score_point(
        parsed.point,
        example["bbox"],
        width=example["screen_width"],
        height=example["screen_height"],
    )
    record = {
        "schema_version": prediction_schema_version,
        "protocol_version": protocol_version,
        "prompt_version": prompt_version,
        "example_id": example["example_id"],
        "condition": condition,
        "provider": provider.name,
        "model": provider.model,
        "parameters": provider.parameters,
        "timestamp_utc": response.timestamp_utc,
        "latency_ms": response.latency_ms,
        "usage": response.usage,
        "provider_metadata": response.provider_metadata,
        "image_path": image_relative,
        "image_sha256": image_sha256,
        "prompt_sha256": _sha256_text(prompt),
        "cache_key": key,
        "raw_response": response.raw_response,
        "parse_status": parsed.status,
        "parse_error": parsed.error,
        "parsed_prediction": parsed.parsed_prediction,
        "point": parsed.point,
        "mark_id": parsed.mark_id,
        "correct": correct,
        "normalized_center_distance": distance,
        "target_proposed": overlay["target_proposed"] if condition == "marks" else None,
        "request_failure": response.request_failure,
    }
    return record, cache_hit


def pilot_example_ids(examples: list[dict[str, Any]]) -> list[str]:
    ordered = sorted(examples, key=lambda row: row["example_id"])
    if len(ordered) != 100:
        raise ValueError("pilot selection requires the frozen 100-example dataset")
    selected = []
    for target_index in range(10):
        selected.append(ordered[target_index * 11]["example_id"])
    return selected


def _load_inputs(repository_root: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    examples = load_jsonl(repository_root / "artifacts" / "grounding-dataset.jsonl")
    overlays = load_jsonl(repository_root / "artifacts" / "grounding-overlays.jsonl")
    by_example = {row["example_id"]: row for row in overlays}
    if len(by_example) != len(overlays):
        raise ValueError("overlay metadata contains duplicate example IDs")
    if {row["example_id"] for row in examples} != set(by_example):
        raise ValueError("dataset and overlay example IDs do not match")
    return examples, by_example


def planned_new_calls(
    *,
    repository_root: Path,
    provider: GroundingProvider,
    cache: ResponseCache,
    pilot: bool,
    prompt_version: str = PROMPT_VERSION,
    parser_version: str = PARSER_VERSION_V1,
) -> dict[str, int]:
    examples, overlays = _load_inputs(repository_root)
    if pilot:
        selected = set(pilot_example_ids(examples))
        examples = [row for row in examples if row["example_id"] in selected]
    total = 0
    cached = 0
    for example in examples:
        overlay = overlays[example["example_id"]]
        for condition in ("raw", "marks"):
            prompt = prompt_for(example, condition, prompt_version=prompt_version)
            schema = schema_for(condition, parser_version=parser_version)
            image_sha = (
                example["image_sha256"] if condition == "raw" else overlay["marked_image_sha256"]
            )
            key = cache_key(
                provider=provider,
                condition=condition,
                prompt=prompt,
                image_sha256=image_sha,
                schema=schema,
                prompt_version=prompt_version,
            )
            total += 1
            cached += cache.get(key) is not None
    return {"total_condition_records": total, "cached_calls": cached, "new_calls": total - cached}


def run_evaluation(
    *,
    repository_root: Path,
    provider: GroundingProvider,
    output_path: Path,
    pilot: bool,
    max_new_calls: int,
    cache_directory: Path | None = None,
    prompt_version: str = PROMPT_VERSION,
    parser_version: str = PARSER_VERSION_V1,
    prediction_schema_version: str = PREDICTION_SCHEMA_VERSION,
) -> dict[str, Any]:
    cache = ResponseCache(cache_directory or repository_root / ".cache" / "grounding" / "responses")
    plan = planned_new_calls(
        repository_root=repository_root,
        provider=provider,
        cache=cache,
        pilot=pilot,
        prompt_version=prompt_version,
        parser_version=parser_version,
    )
    if plan["new_calls"] > max_new_calls:
        raise RuntimeError(
            f"evaluation needs {plan['new_calls']} new calls but cap is {max_new_calls}"
        )
    examples, overlays = _load_inputs(repository_root)
    if pilot:
        selected = set(pilot_example_ids(examples))
        examples = [row for row in examples if row["example_id"] in selected]
    records = []
    cache_hits = 0
    for example in examples:
        overlay = overlays[example["example_id"]]
        for condition in ("raw", "marks"):
            record, cache_hit = evaluate_one(
                repository_root=repository_root,
                example=example,
                overlay=overlay,
                condition=condition,
                provider=provider,
                cache=cache,
                prompt_version=prompt_version,
                parser_version=parser_version,
                prediction_schema_version=prediction_schema_version,
            )
            records.append(record)
            cache_hits += cache_hit
    encoded = "".join(canonical_json_text(row) + "\n" for row in records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_file() and output_path.read_text() != encoded:
        raise ValueError("refusing to overwrite a different immutable prediction file")
    output_path.write_text(encoded)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "prompt_version": prompt_version,
        "prediction_schema_version": prediction_schema_version,
        "provider": provider.name,
        "model": provider.model,
        "pilot": pilot,
        "example_count": len(examples),
        "condition_record_count": len(records),
        "cache_hits": cache_hits,
        "new_calls": len(records) - cache_hits,
        "output_path": output_path.relative_to(repository_root).as_posix()
        if output_path.is_relative_to(repository_root)
        else str(output_path),
    }
