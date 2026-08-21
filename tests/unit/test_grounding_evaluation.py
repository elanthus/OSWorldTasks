from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from pixelgym.grounding.evaluation import (
    MARKS_SCHEMA,
    PARSER_VERSION_V1,
    PARSER_VERSION_V2,
    PREDICTION_SCHEMA_VERSION,
    PREDICTION_SCHEMA_VERSION_V2,
    PROMPT_VERSION,
    PROMPT_VERSION_V2,
    RAW_SCHEMA,
    ResponseCache,
    cache_key,
    evaluate_one,
    parse_prediction,
    pilot_example_ids,
    prompt_for,
    run_evaluation,
)
from pixelgym.grounding.providers import MockProvider
from pixelgym.grounding.schema import PROTOCOL_VERSION


def _example() -> dict:
    return {
        "example_id": "vendor-form-0001-company-name",
        "image_path": "artifacts/raw.png",
        "image_sha256": "a" * 64,
        "target": "Click the Company name field",
        "target_id": "company_name",
        "bbox": [10, 10, 30, 30],
        "screen_width": 100,
        "screen_height": 80,
    }


def _marks() -> list[dict]:
    return [
        {
            "mark_id": 1,
            "semantic_id": "company_name",
            "element_type": "text_input",
            "visible_label": "Company name",
            "bbox": [10, 10, 30, 30],
            "badge_bbox": [32, 10, 40, 20],
        }
    ]


def test_prompts_share_target_and_dimensions_without_privileged_annotations() -> None:
    example = _example()
    raw = prompt_for(example, "raw")
    marks = prompt_for(example, "marks")

    for prompt in (raw, marks):
        assert example["target"] in prompt
        assert "100 pixels wide" in prompt
        assert "80 pixels high" in prompt
        assert "company_name" not in prompt
        assert "[10, 10, 30, 30]" not in prompt
    assert "mark_id" not in raw
    assert "mark_id" in marks


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("not json", "invalid JSON"),
        ('{"x":1}', "keys"),
        ('{"x":true,"y":2}', "integers"),
        ('{"x":100,"y":2}', "outside"),
        ('{"x":1,"y":2,"extra":3}', "keys"),
    ],
)
def test_raw_parser_scores_failures_without_repair(raw: str, error: str) -> None:
    parsed = parse_prediction(raw, condition="raw", width=100, height=80, marks=[])
    assert parsed.status == "invalid"
    assert error in parsed.error
    assert parsed.point is None


def test_mark_parser_maps_valid_id_to_candidate_center_and_rejects_unknown_id() -> None:
    parsed = parse_prediction(
        '{"mark_id":1}', condition="marks", width=100, height=80, marks=_marks()
    )
    assert parsed.status == "parsed"
    assert parsed.point == [20.0, 20.0]
    assert parsed.mark_id == 1

    invalid = parse_prediction(
        '{"mark_id":99}', condition="marks", width=100, height=80, marks=_marks()
    )
    assert invalid.status == "invalid"
    assert invalid.point is None


def test_v2_marks_prompt_asks_for_coordinates_and_keeps_marks_as_an_aid() -> None:
    example = _example()
    marks_v2 = prompt_for(example, "marks", prompt_version=PROMPT_VERSION_V2)

    assert example["target"] in marks_v2
    assert "100 pixels wide" in marks_v2
    assert "80 pixels high" in marks_v2
    assert "badge" in marks_v2
    assert "x and y" in marks_v2
    assert "mark_id" not in marks_v2
    assert "company_name" not in marks_v2
    assert "[10, 10, 30, 30]" not in marks_v2
    # The raw prompt is byte-identical across prompt versions.
    assert prompt_for(example, "raw", prompt_version=PROMPT_VERSION_V2) == prompt_for(
        example, "raw"
    )
    # The default stays the frozen v1 prompt, byte for byte.
    assert prompt_for(example, "marks") == prompt_for(
        example, "marks", prompt_version=PROMPT_VERSION
    )
    assert prompt_for(example, "marks") != marks_v2
    with pytest.raises(ValueError):
        prompt_for(example, "marks", prompt_version="pixelgym-grounding-prompt-v99")


def test_v2_marks_parser_validates_the_point_contract() -> None:
    parsed = parse_prediction(
        '{"x":15,"y":12}',
        condition="marks",
        width=100,
        height=80,
        marks=_marks(),
        parser_version=PARSER_VERSION_V2,
    )
    assert parsed.status == "parsed"
    assert parsed.point == [15.0, 12.0]
    assert parsed.mark_id is None

    for raw, error in [
        ('{"mark_id":1}', "keys"),
        ('{"x":true,"y":2}', "integers"),
        ('{"x":100,"y":2}', "outside"),
    ]:
        invalid = parse_prediction(
            raw,
            condition="marks",
            width=100,
            height=80,
            marks=_marks(),
            parser_version=PARSER_VERSION_V2,
        )
        assert invalid.status == "invalid"
        assert error in invalid.error
        assert invalid.point is None

    # The default stays the frozen v1 mark-id contract.
    legacy = parse_prediction(
        '{"mark_id":1}',
        condition="marks",
        width=100,
        height=80,
        marks=_marks(),
        parser_version=PARSER_VERSION_V1,
    )
    assert legacy.status == "parsed"
    assert legacy.point == [20.0, 20.0]
    with pytest.raises(ValueError):
        parse_prediction(
            '{"x":1,"y":2}',
            condition="marks",
            width=100,
            height=80,
            marks=_marks(),
            parser_version="pixelgym-grounding-parser-v99",
        )


def test_v2_marks_response_schema_is_the_point_schema() -> None:
    from pixelgym.grounding.evaluation import schema_for

    assert schema_for("marks", parser_version=PARSER_VERSION_V2) == RAW_SCHEMA
    assert schema_for("marks") == MARKS_SCHEMA
    assert schema_for("marks", parser_version=PARSER_VERSION_V1) == MARKS_SCHEMA
    assert schema_for("raw", parser_version=PARSER_VERSION_V2) == RAW_SCHEMA
    with pytest.raises(ValueError):
        schema_for("marks", parser_version="pixelgym-grounding-parser-v99")


def test_cache_key_separates_prompt_versions_so_v1_responses_cannot_be_reused() -> None:
    provider = MockProvider()
    v1_key = cache_key(
        provider=provider,
        condition="marks",
        prompt="prompt",
        image_sha256="a" * 64,
        schema=RAW_SCHEMA,
    )
    assert v1_key == cache_key(
        provider=provider,
        condition="marks",
        prompt="prompt",
        image_sha256="a" * 64,
        schema=RAW_SCHEMA,
        prompt_version=PROMPT_VERSION,
    )
    assert v1_key != cache_key(
        provider=provider,
        condition="marks",
        prompt="prompt",
        image_sha256="a" * 64,
        schema=RAW_SCHEMA,
        prompt_version=PROMPT_VERSION_V2,
    )


def test_cache_key_covers_condition_model_prompt_image_schema_and_parameters() -> None:
    provider = MockProvider()
    base = cache_key(
        provider=provider,
        condition="raw",
        prompt="prompt",
        image_sha256="a" * 64,
        schema=RAW_SCHEMA,
    )
    assert base != cache_key(
        provider=provider,
        condition="marks",
        prompt="prompt",
        image_sha256="a" * 64,
        schema=MARKS_SCHEMA,
    )
    assert base != cache_key(
        provider=provider,
        condition="raw",
        prompt="changed",
        image_sha256="a" * 64,
        schema=RAW_SCHEMA,
    )
    assert base != cache_key(
        provider=provider,
        condition="raw",
        prompt="prompt",
        image_sha256="b" * 64,
        schema=RAW_SCHEMA,
    )


def _write_mock_inputs(root: Path, count: int = 100) -> None:
    artifact_dir = root / "artifacts"
    artifact_dir.mkdir()
    raw_dir = artifact_dir / "grounding" / "images" / "raw"
    marks_dir = artifact_dir / "grounding" / "images" / "marks"
    raw_dir.mkdir(parents=True)
    marks_dir.mkdir(parents=True)
    examples = []
    overlays = []
    for index in range(count):
        raw_path = raw_dir / f"{index:04d}.png"
        marked_path = marks_dir / f"{index:04d}.png"
        Image.new("RGB", (100, 80), "white").save(raw_path)
        Image.new("RGB", (100, 80), "white").save(marked_path)
        raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        marks_sha = hashlib.sha256(marked_path.read_bytes()).hexdigest()
        example_id = f"example-{index:04d}"
        examples.append(
            {
                "example_id": example_id,
                "image_path": raw_path.relative_to(root).as_posix(),
                "image_sha256": raw_sha,
                "target": f"Click target {index}",
                "target_id": f"target-{index}",
                "bbox": [10, 10, 30, 30],
                "screen_width": 100,
                "screen_height": 80,
            }
        )
        overlays.append(
            {
                "example_id": example_id,
                "marked_image_path": marked_path.relative_to(root).as_posix(),
                "marked_image_sha256": marks_sha,
                "marks": [
                    {
                        "mark_id": 1,
                        "semantic_id": f"target-{index}",
                        "element_type": "button",
                        "visible_label": "target",
                        "bbox": [10, 10, 30, 30],
                        "badge_bbox": [32, 10, 40, 20],
                    }
                ],
                "target_proposed": True,
            }
        )
    (artifact_dir / "grounding-dataset.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in examples)
    )
    (artifact_dir / "grounding-overlays.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in overlays)
    )


def test_mock_pipeline_caches_both_conditions_and_makes_no_duplicate_calls(tmp_path: Path) -> None:
    _write_mock_inputs(tmp_path)
    provider = MockProvider()
    output = tmp_path / "pilot.jsonl"
    cache_dir = tmp_path / "cache"

    first = run_evaluation(
        repository_root=tmp_path,
        provider=provider,
        output_path=output,
        pilot=True,
        max_new_calls=20,
        cache_directory=cache_dir,
    )
    second = run_evaluation(
        repository_root=tmp_path,
        provider=provider,
        output_path=output,
        pilot=True,
        max_new_calls=0,
        cache_directory=cache_dir,
    )

    assert first["condition_record_count"] == 20
    assert first["new_calls"] == 20
    assert second["new_calls"] == 0
    assert second["cache_hits"] == 20
    assert provider.call_count == 20
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert [record["condition"] for record in records[:4]] == ["raw", "marks", "raw", "marks"]
    assert all(record["parse_status"] == "parsed" for record in records)


def test_call_cap_blocks_provider_before_first_request(tmp_path: Path) -> None:
    _write_mock_inputs(tmp_path)
    provider = MockProvider()
    with pytest.raises(RuntimeError, match="cap"):
        run_evaluation(
            repository_root=tmp_path,
            provider=provider,
            output_path=tmp_path / "pilot.jsonl",
            pilot=True,
            max_new_calls=19,
            cache_directory=tmp_path / "cache",
        )
    assert provider.call_count == 0


def test_response_cache_refuses_to_replace_different_content(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    provider = MockProvider()
    response = provider.invoke(image_path=tmp_path / "unused", prompt="", schema=RAW_SCHEMA)
    cache.put("a" * 64, response)
    different = provider.invoke(image_path=tmp_path / "unused", prompt="", schema=RAW_SCHEMA)
    with pytest.raises(ValueError, match="replace"):
        cache.put("a" * 64, different)


def test_v3a_evaluate_one_stamps_prediction_schema_version_v2(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.png"
    marks_path = tmp_path / "marks.png"
    Image.new("RGB", (100, 80), "white").save(raw_path)
    Image.new("RGB", (100, 80), "white").save(marks_path)
    raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    marks_sha = hashlib.sha256(marks_path.read_bytes()).hexdigest()
    example = {
        **_example(),
        "image_path": raw_path.relative_to(tmp_path).as_posix(),
        "image_sha256": raw_sha,
    }
    overlay = {
        "example_id": example["example_id"],
        "marked_image_path": marks_path.relative_to(tmp_path).as_posix(),
        "marked_image_sha256": marks_sha,
        "marks": _marks(),
        "target_proposed": True,
    }
    provider = MockProvider()
    cache = ResponseCache(tmp_path / "cache")
    for condition in ("raw", "marks"):
        record, _ = evaluate_one(
            repository_root=tmp_path,
            example=example,
            overlay=overlay,
            condition=condition,
            provider=provider,
            cache=cache,
            prompt_version=PROMPT_VERSION_V2,
            parser_version=PARSER_VERSION_V2,
            prediction_schema_version=PREDICTION_SCHEMA_VERSION_V2,
        )
        assert record["schema_version"] == PREDICTION_SCHEMA_VERSION_V2
        assert record["prompt_version"] == PROMPT_VERSION_V2
        assert record["protocol_version"] == PROTOCOL_VERSION

    # Default stays v1.
    record_v1, _ = evaluate_one(
        repository_root=tmp_path,
        example=example,
        overlay=overlay,
        condition="raw",
        provider=provider,
        cache=cache,
    )
    assert record_v1["schema_version"] == PREDICTION_SCHEMA_VERSION
    assert record_v1["prompt_version"] == PROMPT_VERSION


def test_v3a_runner_stamps_v2_records_and_separates_cache(tmp_path: Path) -> None:
    _write_mock_inputs(tmp_path)
    provider = MockProvider()
    output_v1 = tmp_path / "v1.jsonl"
    output_v3a = tmp_path / "v3a.jsonl"
    cache_dir = tmp_path / "cache"

    run_evaluation(
        repository_root=tmp_path,
        provider=provider,
        output_path=output_v1,
        pilot=True,
        max_new_calls=20,
        cache_directory=cache_dir,
    )
    run_evaluation(
        repository_root=tmp_path,
        provider=provider,
        output_path=output_v3a,
        pilot=True,
        max_new_calls=20,
        cache_directory=cache_dir,
        prompt_version=PROMPT_VERSION_V2,
        parser_version=PARSER_VERSION_V2,
        prediction_schema_version=PREDICTION_SCHEMA_VERSION_V2,
    )
    v1_records = [json.loads(line) for line in output_v1.read_text().splitlines()]
    v3a_records = [json.loads(line) for line in output_v3a.read_text().splitlines()]

    assert all(r["schema_version"] == PREDICTION_SCHEMA_VERSION for r in v1_records)
    assert all(r["schema_version"] == PREDICTION_SCHEMA_VERSION_V2 for r in v3a_records)
    assert all(r["prompt_version"] == PROMPT_VERSION_V2 for r in v3a_records)
    # v3a calls are not cached by v1 — different prompt/schema produce different cache keys.
    assert provider.call_count == 40


def test_pilot_selection_is_independent_of_jsonl_row_order() -> None:
    examples = [{"example_id": f"example-{index:04d}"} for index in range(100)]

    assert pilot_example_ids(examples) == pilot_example_ids(list(reversed(examples)))
    assert pilot_example_ids(examples) == [
        f"example-{index:04d}" for index in range(0, 100, 11)
    ]
