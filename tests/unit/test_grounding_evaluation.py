from __future__ import annotations

import errno
import hashlib
import json
import os
from functools import partial
from pathlib import Path

import pytest
from PIL import Image

from pixelgym.grounding import evaluation
from pixelgym.grounding.evaluation import (
    MARKS_SCHEMA,
    PARSER_VERSION_V1,
    PARSER_VERSION_V2,
    PREDICTION_SCHEMA_VERSION,
    PREDICTION_SCHEMA_VERSION_V2,
    PREDICTION_SCHEMA_VERSION_V3,
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


def test_cli_refuses_existing_predictions_before_any_provider_call(tmp_path, monkeypatch):
    from scripts import run_grounding_evaluation as cli

    _write_mock_inputs(tmp_path)
    output = tmp_path / "artifacts/grounding-predictions.jsonl"
    frozen = b'{"provider":"historical"}\n'
    output.write_bytes(frozen)
    provider = MockProvider()
    monkeypatch.setattr(cli, "MockProvider", lambda: provider)
    with pytest.raises(ValueError, match="--output"):
        cli.main(["--provider", "mock", "--full", "--max-new-calls", "200"], tmp_path)
    assert provider.call_count == 0
    assert output.read_bytes() == frozen


@pytest.fixture
def run_one_example(tmp_path):
    _write_mock_inputs(tmp_path, count=1)
    return partial(
        run_evaluation,
        repository_root=tmp_path,
        pilot=False,
        max_new_calls=2,
        cache_directory=tmp_path / "cache",
    )


def test_unsupported_hard_links_fail_before_provider_dispatch(
    tmp_path, monkeypatch, run_one_example
):
    directory = tmp_path / "outputs"
    directory.mkdir()
    provider = MockProvider()

    def unsupported(*args):
        raise OSError(errno.ENOTSUP, "hard links unavailable")

    monkeypatch.setattr(os, "link", unsupported)
    with pytest.raises(OSError, match="hard links unavailable"):
        run_one_example(provider=provider, output_path=directory / "predictions.jsonl")
    assert provider.call_count == 0
    assert list(directory.iterdir()) == []


def test_maximum_length_output_name_can_publish_cached_predictions(tmp_path, run_one_example):
    original = tmp_path / "original.jsonl"
    run_one_example(provider=MockProvider(), output_path=original)
    name_max = os.pathconf(tmp_path, "PC_NAME_MAX") if hasattr(os, "pathconf") else 255
    output = tmp_path / ("a" * name_max)
    replay = MockProvider()
    assert run_one_example(provider=replay, output_path=output)["cache_hits"] == 2
    assert replay.call_count == 0
    assert output.read_bytes() == original.read_bytes()


def test_existing_directory_is_rejected_before_provider_dispatch(tmp_path, run_one_example):
    output = tmp_path / "predictions"
    output.mkdir()
    provider = MockProvider()
    with pytest.raises(ValueError, match="prediction file"):
        run_one_example(provider=provider, output_path=output)
    assert provider.call_count == 0
    assert list(output.iterdir()) == []


def test_publication_racing_reservation_keeps_winner_and_makes_no_calls(
    tmp_path, monkeypatch, run_one_example
):
    output = tmp_path / "predictions.jsonl"
    winner = b"competing predictions\n"
    original_open = Path.open

    def publish_before_open(path, mode="r", *args, **kwargs):
        if mode == "x" and path.parent == tmp_path:
            output.write_bytes(winner)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", publish_before_open)
    provider = MockProvider()
    with pytest.raises(FileExistsError):
        run_one_example(provider=provider, output_path=output)
    assert provider.call_count == 0
    assert output.read_bytes() == winner


def test_cache_entry_lost_after_planning_never_dispatches_on_replay(
    tmp_path, monkeypatch, run_one_example
):
    output = tmp_path / "predictions.jsonl"
    run_one_example(provider=MockProvider(), output_path=output)
    original = output.read_bytes()
    plan = evaluation.planned_new_calls

    def plan_then_evict(**kwargs):
        result = plan(**kwargs)
        assert result["new_calls"] == 0
        next((tmp_path / "cache").glob("*.json")).unlink()
        return result

    monkeypatch.setattr(evaluation, "planned_new_calls", plan_then_evict)
    replay = MockProvider()
    with pytest.raises(RuntimeError, match="cache miss"):
        run_one_example(provider=replay, output_path=output)
    assert replay.call_count == 0
    assert output.read_bytes() == original


def test_warm_cache_replay_preserves_mismatched_output(tmp_path, run_one_example):
    output = tmp_path / "predictions.jsonl"
    run_one_example(provider=MockProvider(), output_path=output)
    different = b"other predictions\r\n"
    output.write_bytes(different)
    replay = MockProvider()
    with pytest.raises(ValueError, match="different immutable prediction"):
        run_one_example(provider=replay, output_path=output)
    assert replay.call_count == 0
    assert output.read_bytes() == different


def test_interrupted_prediction_write_stays_hidden_and_retries_from_cache(
    tmp_path, monkeypatch, run_one_example
):
    directory = tmp_path / "outputs"
    directory.mkdir()
    output = directory / "predictions.jsonl"
    original_open = Path.open

    def open_with_failing_write(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        if path.parent == directory and mode in ("w", "x"):
            write = stream.write

            def write_prefix(text):
                write(text[:16])
                stream.flush()
                assert not output.exists()
                raise OSError("interrupted prediction write")

            stream.write = write_prefix
        return stream

    provider = MockProvider()
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", open_with_failing_write)
        with pytest.raises(OSError, match="interrupted prediction write"):
            run_one_example(provider=provider, output_path=output)
    assert provider.call_count == 2
    assert list(directory.iterdir()) == []
    replay = MockProvider()
    assert run_one_example(provider=replay, output_path=output)["cache_hits"] == 2
    assert replay.call_count == 0
    assert [json.loads(row)["condition"] for row in output.read_text().splitlines()] == [
        "raw",
        "marks",
    ]


def test_concurrent_run_for_same_predictions_stops_before_dispatch(tmp_path):
    _write_mock_inputs(tmp_path)
    output = tmp_path / "pilot.jsonl"
    contender = MockProvider()

    def run(provider):
        return run_evaluation(
            repository_root=tmp_path,
            provider=provider,
            output_path=output,
            pilot=True,
            max_new_calls=20,
        )

    class StartSecondRun(MockProvider):
        def invoke(self, **kwargs):
            if self.call_count == 0:
                with pytest.raises(FileExistsError):
                    run(contender)
            return super().invoke(**kwargs)

    assert run(StartSecondRun())["new_calls"] == 20
    assert contender.call_count == 0


def test_interrupted_collection_can_resume_from_partial_cache(tmp_path):
    _write_mock_inputs(tmp_path)
    output = tmp_path / "pilot.jsonl"

    class StopAfterOneResponse(MockProvider):
        def invoke(self, **kwargs):
            if self.call_count == 1:
                raise TimeoutError("provider interrupted")
            return super().invoke(**kwargs)

    def run(provider):
        return run_evaluation(
            repository_root=tmp_path,
            provider=provider,
            output_path=output,
            pilot=True,
            max_new_calls=20,
        )

    with pytest.raises(TimeoutError, match="provider interrupted"):
        run(StopAfterOneResponse())
    assert not output.exists()
    provider = MockProvider()
    assert run(provider)["cache_hits"] == 1
    assert provider.call_count == 19
    assert len(output.read_text().splitlines()) == 20


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


def test_v3_prediction_records_include_parser_version(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.png"
    Image.new("RGB", (100, 80), "white").save(raw_path)
    example = {
        **_example(),
        "image_path": "raw.png",
        "image_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }
    record, _ = evaluate_one(
        repository_root=tmp_path,
        example=example,
        overlay={"marks": [], "target_proposed": True},
        condition="raw",
        provider=MockProvider(),
        cache=ResponseCache(tmp_path / "cache"),
        prompt_version=PROMPT_VERSION_V2,
        parser_version=PARSER_VERSION_V2,
        prediction_schema_version=PREDICTION_SCHEMA_VERSION_V3,
    )
    assert record["parser_version"] == PARSER_VERSION_V2


def test_runner_rejects_incompatible_versions_before_planning_or_provider_call(
    tmp_path: Path,
) -> None:
    _write_mock_inputs(tmp_path)
    provider = MockProvider()
    with pytest.raises(ValueError, match="versions are incompatible"):
        run_evaluation(
            repository_root=tmp_path,
            provider=provider,
            output_path=tmp_path / "predictions.jsonl",
            pilot=True,
            max_new_calls=20,
            prompt_version=PROMPT_VERSION_V2,
            parser_version=PARSER_VERSION_V1,
            prediction_schema_version=PREDICTION_SCHEMA_VERSION_V2,
        )
    assert provider.call_count == 0


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
    assert pilot_example_ids(examples) == [f"example-{index:04d}" for index in range(0, 100, 11)]
