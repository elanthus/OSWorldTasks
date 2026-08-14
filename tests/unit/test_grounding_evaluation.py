from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from pixelgym.grounding.evaluation import (
    MARKS_SCHEMA,
    RAW_SCHEMA,
    ResponseCache,
    cache_key,
    parse_prediction,
    pilot_example_ids,
    prompt_for,
    run_evaluation,
)
from pixelgym.grounding.providers import MockProvider


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


def test_pilot_selection_is_independent_of_jsonl_row_order() -> None:
    examples = [{"example_id": f"example-{index:04d}"} for index in range(100)]

    assert pilot_example_ids(examples) == pilot_example_ids(list(reversed(examples)))
    assert pilot_example_ids(examples) == [
        f"example-{index:04d}" for index in range(0, 100, 11)
    ]
