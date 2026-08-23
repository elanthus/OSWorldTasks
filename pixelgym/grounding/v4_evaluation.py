"""Capped evaluation planning and execution for the frozen v4 pilot inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
from pixelgym.grounding.v4_protocol import V4_CONDITION_CALL_CAP, V4_PROTOCOL_VERSION
from pixelgym.serialization import canonical_json_text, load_jsonl

V4_CONDITIONS: tuple[Condition, Condition] = ("raw", "marks")


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
