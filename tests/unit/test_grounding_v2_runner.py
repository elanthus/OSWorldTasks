"""Protocol v2 wiring for the grounding evaluation runner.

Every test here runs with network sockets forbidden and uses the deterministic mock
provider: the crossed v2 dataset loads and validates, the v2 pilot scores through the
unchanged v1 parser and scoring rule, plan-only drafts the owner freeze without a single
provider call, and the default v1 path keeps its frozen inputs and output locations.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import socket
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from PIL import Image

from pixelgym.grounding.evaluation import (
    PARSER_VERSION_V1,
    PREDICTION_SCHEMA_VERSION,
    PROMPT_VERSION,
    RAW_SCHEMA,
    _load_inputs,
    cache_key,
    parse_prediction,
    pilot_example_ids,
    pilot_example_ids_v2,
    prompt_for,
    run_evaluation,
    score_point,
)
from pixelgym.grounding.freeze_v2 import (
    DEFAULT_FREEZE_DRAFT_PATH,
    FREEZE_DRAFT_SCHEMA_VERSION,
    build_v2_freeze_draft,
)
from pixelgym.grounding.protocols import V1_PROTOCOL, V2_PROTOCOL, protocol_for
from pixelgym.grounding.providers import MockProvider
from pixelgym.grounding.schema import PROTOCOL_VERSION as V1_PROTOCOL_VERSION
from pixelgym.grounding.schema import SCREEN_STATES, TARGET_SPECS
from pixelgym.grounding.schema_v2 import PROTOCOL_VERSION as V2_PROTOCOL_VERSION

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "run_grounding_evaluation.py"
V2_METADATA_FILES = (
    "artifacts/grounding-v2-dataset.jsonl",
    "artifacts/grounding-v2-candidates.jsonl",
    "artifacts/grounding-v2-overlays.jsonl",
    "artifacts/grounding-v2-manifest.json",
)
EXPECTED_V2_PILOT_IDS = [
    "vendor-form-v2-0001-company-name",
    "vendor-form-v2-0002-contact-email",
    "vendor-form-v2-0003-contact-phone",
    "vendor-form-v2-0004-tax-id",
    "vendor-form-v2-0005-country",
    "vendor-form-v2-0026-payment-terms-net-15",
    "vendor-form-v2-0027-payment-terms-net-30",
    "vendor-form-v2-0028-payment-terms-net-45",
    "vendor-form-v2-0029-expedited-onboarding",
    "vendor-form-v2-0030-submit",
]


@pytest.fixture(autouse=True)
def _forbid_network_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("grounding v2 runner opened a network socket")

    monkeypatch.setattr(socket, "socket", forbidden)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_grounding_evaluation_script", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_v2_metadata(root: Path) -> Path:
    for relative in V2_METADATA_FILES:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPOSITORY_ROOT / relative, destination)
    return root


def _write_v1_mock_inputs(root: Path, count: int = 100) -> None:
    artifact_dir = root / "artifacts"
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
        example_id = f"example-{index:04d}"
        bbox = [10, 10, 30, 30]
        examples.append(
            {
                "example_id": example_id,
                "image_path": raw_path.relative_to(root).as_posix(),
                "image_sha256": _sha256(raw_path),
                "target": f"Click target {index}",
                "target_id": f"target-{index}",
                "bbox": bbox,
                "screen_width": 100,
                "screen_height": 80,
            }
        )
        overlays.append(
            {
                "example_id": example_id,
                "marked_image_path": marked_path.relative_to(root).as_posix(),
                "marked_image_sha256": _sha256(marked_path),
                "marks": [
                    {
                        "mark_id": 1,
                        "semantic_id": f"target-{index}",
                        "element_type": "button",
                        "visible_label": "target",
                        "bbox": bbox,
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


def test_protocol_selector_defaults_to_v1_and_keeps_frozen_v1_locations() -> None:
    script = _load_script()
    args = script.parse_args(["--provider", "mock", "--pilot", "--max-new-calls", "0"])

    assert args.protocol == "v1"
    assert protocol_for("v1") is V1_PROTOCOL
    assert protocol_for("v2") is V2_PROTOCOL
    assert V1_PROTOCOL.protocol_version == V1_PROTOCOL_VERSION == "pixelgym-grounding-v1"
    assert V1_PROTOCOL.dataset_path == "artifacts/grounding-dataset.jsonl"
    assert V1_PROTOCOL.overlays_path == "artifacts/grounding-overlays.jsonl"
    assert V1_PROTOCOL.pilot_predictions_path == "artifacts/day-3/pilot/pilot-predictions.jsonl"
    assert V1_PROTOCOL.full_predictions_path == "artifacts/grounding-predictions.jsonl"
    assert V1_PROTOCOL.cache_directory == ".cache/grounding/responses"
    assert V2_PROTOCOL.protocol_version == V2_PROTOCOL_VERSION == "pixelgym-grounding-v2"
    v1_paths = {
        V1_PROTOCOL.dataset_path,
        V1_PROTOCOL.overlays_path,
        V1_PROTOCOL.candidates_path,
        V1_PROTOCOL.pilot_predictions_path,
        V1_PROTOCOL.full_predictions_path,
        V1_PROTOCOL.cache_directory,
    }
    v2_paths = {
        V2_PROTOCOL.dataset_path,
        V2_PROTOCOL.overlays_path,
        V2_PROTOCOL.candidates_path,
        V2_PROTOCOL.pilot_predictions_path,
        V2_PROTOCOL.full_predictions_path,
        V2_PROTOCOL.cache_directory,
    }
    assert v1_paths.isdisjoint(v2_paths)
    with pytest.raises(ValueError, match="unknown grounding protocol"):
        protocol_for("v3")


def test_v2_inputs_load_and_validate_the_crossed_allocation() -> None:
    examples, overlays = _load_inputs(REPOSITORY_ROOT, V2_PROTOCOL)

    assert len(examples) == 100
    assert set(overlays) == {row["example_id"] for row in examples}
    assert {row["protocol_version"] for row in examples} == {V2_PROTOCOL_VERSION}
    cells = Counter((row["target_id"], row["screen_state"]) for row in examples)
    assert len(cells) == 50
    assert set(cells.values()) == {2}
    targets = Counter(row["target_id"] for row in examples)
    assert set(targets) == {target.semantic_id for target in TARGET_SPECS}
    assert set(targets.values()) == {10}
    states = Counter(row["screen_state"] for row in examples)
    assert set(states) == set(SCREEN_STATES)
    assert set(states.values()) == {20}
    assert len({row["image_path"] for row in examples}) == 100


def test_v2_inputs_reject_a_broken_join(tmp_path: Path) -> None:
    root = _copy_v2_metadata(tmp_path)
    overlays_path = root / V2_PROTOCOL.overlays_path
    lines = overlays_path.read_text().splitlines(keepends=True)
    overlays_path.write_text("".join(lines[:-1]))

    with pytest.raises(ValueError, match="exactly 100 joined examples"):
        _load_inputs(root, V2_PROTOCOL)


def test_v2_pilot_selection_covers_every_target_once_and_every_state_twice() -> None:
    examples, _ = _load_inputs(REPOSITORY_ROOT, V2_PROTOCOL)
    by_id = {row["example_id"]: row for row in examples}

    selected = pilot_example_ids(examples, protocol=V2_PROTOCOL)

    assert selected == EXPECTED_V2_PILOT_IDS
    assert selected == pilot_example_ids(list(reversed(examples)), protocol=V2_PROTOCOL)
    assert [by_id[item]["target_id"] for item in selected] == [
        target.semantic_id for target in TARGET_SPECS
    ]
    assert Counter(by_id[item]["screen_state"] for item in selected) == dict.fromkeys(
        SCREEN_STATES, 2
    )
    assert {by_id[item]["task_seed"] for item in selected} == {0, 5}
    # The frozen v1 index rule is not reusable on the crossed ids: it repeats targets.
    v1_rule_targets = Counter(by_id[item]["target_id"] for item in pilot_example_ids(examples))
    assert max(v1_rule_targets.values()) > 1
    with pytest.raises(ValueError, match="100-example crossed dataset"):
        pilot_example_ids_v2(examples[:-1])


def test_v2_pilot_scores_through_the_unchanged_v1_parser_and_scoring_rule(
    tmp_path: Path,
) -> None:
    provider = MockProvider()
    output = tmp_path / "v2-pilot-predictions.jsonl"

    summary = run_evaluation(
        repository_root=REPOSITORY_ROOT,
        provider=provider,
        output_path=output,
        pilot=True,
        max_new_calls=20,
        cache_directory=tmp_path / "cache",
        protocol=V2_PROTOCOL,
    )

    assert summary["protocol_version"] == V2_PROTOCOL_VERSION
    assert summary["prompt_version"] == PROMPT_VERSION
    assert summary["prediction_schema_version"] == PREDICTION_SCHEMA_VERSION
    assert summary["example_count"] == 10
    assert summary["condition_record_count"] == 20
    assert summary["new_calls"] == 20
    assert provider.call_count == 20
    records = [json.loads(line) for line in output.read_text().splitlines()]
    examples, overlays = _load_inputs(REPOSITORY_ROOT, V2_PROTOCOL)
    by_id = {row["example_id"]: row for row in examples}
    assert sorted({row["example_id"] for row in records}) == sorted(EXPECTED_V2_PILOT_IDS)
    assert {row["protocol_version"] for row in records} == {V2_PROTOCOL_VERSION}
    assert {row["prompt_version"] for row in records} == {PROMPT_VERSION}
    assert {row["schema_version"] for row in records} == {PREDICTION_SCHEMA_VERSION}
    assert {row["parse_status"] for row in records} == {"parsed"}
    assert "parser_version" not in records[0]
    for record in records:
        example = by_id[record["example_id"]]
        overlay = overlays[record["example_id"]]
        expected = parse_prediction(
            record["raw_response"],
            condition=record["condition"],
            width=example["screen_width"],
            height=example["screen_height"],
            marks=overlay["marks"],
            parser_version=PARSER_VERSION_V1,
        )
        correct, distance = score_point(
            expected.point,
            example["bbox"],
            width=example["screen_width"],
            height=example["screen_height"],
        )
        assert record["point"] == expected.point
        assert record["mark_id"] == expected.mark_id
        assert record["correct"] is correct
        assert record["normalized_center_distance"] == distance
        assert (
            record["prompt_sha256"]
            == hashlib.sha256(prompt_for(example, record["condition"]).encode("utf-8")).hexdigest()
        )
        if record["condition"] == "raw":
            assert record["correct"] is False
            assert record["target_proposed"] is None
        else:
            assert record["correct"] is (overlay["target_mark_id"] == 1)
            assert record["target_proposed"] is True
    first = records[0]
    assert first["cache_key"] != cache_key(
        provider=provider,
        condition="raw",
        prompt=prompt_for(by_id[first["example_id"]], "raw"),
        image_sha256=first["image_sha256"],
        schema=RAW_SCHEMA,
    )
    assert first["cache_key"] == cache_key(
        provider=provider,
        condition="raw",
        prompt=prompt_for(by_id[first["example_id"]], "raw"),
        image_sha256=first["image_sha256"],
        schema=RAW_SCHEMA,
        protocol_version=V2_PROTOCOL_VERSION,
    )


def test_v2_refuses_to_write_frozen_v1_result_paths(tmp_path: Path) -> None:
    for relative in (V1_PROTOCOL.pilot_predictions_path, V1_PROTOCOL.full_predictions_path):
        frozen = REPOSITORY_ROOT / relative
        digest_before = _sha256(frozen)
        provider = MockProvider()
        with pytest.raises(ValueError, match="frozen v1 result path"):
            run_evaluation(
                repository_root=REPOSITORY_ROOT,
                provider=provider,
                output_path=frozen,
                pilot=True,
                max_new_calls=200,
                cache_directory=tmp_path / "cache",
                protocol=V2_PROTOCOL,
            )
        assert provider.call_count == 0
        assert _sha256(frozen) == digest_before


def test_plan_only_v2_writes_the_freeze_draft_without_any_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _copy_v2_metadata(tmp_path)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("plan-only must not invoke the provider")

    monkeypatch.setattr(MockProvider, "invoke", boom)
    script = _load_script()
    argv = ["--provider", "mock", "--protocol", "v2", "--pilot", "--max-new-calls", "0"]

    script.main([*argv, "--plan-only"], repository_root=root)

    result = json.loads(capsys.readouterr().out)
    assert result["total_condition_records"] == 20
    assert result["new_calls"] == 20
    assert result["cached_calls"] == 0
    assert result["freeze_draft"]["written"] is True
    assert result["freeze_draft"]["model_calls_performed"] == 0
    assert result["freeze_draft"]["freeze_draft_path"] == DEFAULT_FREEZE_DRAFT_PATH
    draft_path = root / DEFAULT_FREEZE_DRAFT_PATH
    draft = json.loads(draft_path.read_text())
    manifest = json.loads((root / "artifacts" / "grounding-v2-manifest.json").read_text())
    assert draft["schema_version"] == FREEZE_DRAFT_SCHEMA_VERSION
    assert draft["protocol_version"] == V2_PROTOCOL_VERSION
    for label in ("dataset", "candidates", "overlays"):
        assert draft["inputs"][label] == manifest["outputs"][label]
        assert draft["inputs"][label]["sha256"] == _sha256(root / draft["inputs"][label]["path"])
    assert draft["pilot"]["example_ids"] == EXPECTED_V2_PILOT_IDS
    assert draft["pilot"]["condition_call_count"] == 20
    assert "t - (t mod 5)" in draft["pilot"]["selection_rule"]
    assert draft["condition_call_cap"] == {
        "pilot_new_calls": 20,
        "full_new_calls_after_pilot": 180,
        "total": 200,
    }
    assert draft["paired_condition_call_count"] == 200
    for field in ("provider", "model", "prompt_version", "parameters"):
        assert draft[field] is None
    assert draft["approval"] == {"owner": None, "approved_utc": None, "pilot_approved": None}
    assert draft["model_calls_performed"] == 0
    assert draft["output_paths"]["full_predictions"] == "artifacts/grounding-v2-predictions.jsonl"
    assert not (root / "artifacts" / "grounding-predictions.jsonl").exists()
    assert not (root / ".cache").exists()

    # An existing (owner-edited) draft is never overwritten.
    draft["provider"] = "owner-filled"
    owner_text = json.dumps(draft, indent=2, sort_keys=True) + "\n"
    draft_path.write_text(owner_text)
    script.main([*argv, "--plan-only"], repository_root=root)
    second = json.loads(capsys.readouterr().out)
    assert second["freeze_draft"]["written"] is False
    assert second["freeze_draft"]["existing_file_preserved"] is True
    assert draft_path.read_text() == owner_text


def test_freeze_draft_rejects_inputs_that_drift_from_the_manifest(tmp_path: Path) -> None:
    root = _copy_v2_metadata(tmp_path)
    dataset = root / V2_PROTOCOL.dataset_path
    dataset.write_text(dataset.read_text() + "\n")

    with pytest.raises(ValueError, match="dataset digest does not match"):
        build_v2_freeze_draft(root)


def test_freeze_draft_refuses_a_manifest_that_reports_model_calls(tmp_path: Path) -> None:
    root = _copy_v2_metadata(tmp_path)
    manifest_path = root / "artifacts" / "grounding-v2-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["model_calls_performed"] = 1
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="reports model calls"):
        build_v2_freeze_draft(root)


def test_default_v1_runner_path_reads_v1_inputs_and_writes_v1_locations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_v1_mock_inputs(tmp_path)
    script = _load_script()

    script.main(
        ["--provider", "mock", "--pilot", "--max-new-calls", "0", "--plan-only"],
        repository_root=tmp_path,
    )
    plan = json.loads(capsys.readouterr().out)
    assert plan == {"cached_calls": 0, "new_calls": 20, "total_condition_records": 20}
    assert not (tmp_path / DEFAULT_FREEZE_DRAFT_PATH).exists()

    script.main(
        ["--provider", "mock", "--pilot", "--max-new-calls", "20"], repository_root=tmp_path
    )
    pilot_summary = json.loads(capsys.readouterr().out)
    pilot_output = tmp_path / "artifacts" / "day-3" / "pilot" / "pilot-predictions.jsonl"
    assert pilot_summary["protocol_version"] == V1_PROTOCOL_VERSION
    assert pilot_summary["output_path"] == "artifacts/day-3/pilot/pilot-predictions.jsonl"
    pilot_records = [json.loads(line) for line in pilot_output.read_text().splitlines()]
    assert len(pilot_records) == 20
    assert {row["protocol_version"] for row in pilot_records} == {V1_PROTOCOL_VERSION}
    assert len(list((tmp_path / ".cache" / "grounding" / "responses").glob("*.json"))) == 20

    script.main(
        ["--provider", "mock", "--full", "--max-new-calls", "180"], repository_root=tmp_path
    )
    full_summary = json.loads(capsys.readouterr().out)
    assert full_summary["output_path"] == "artifacts/grounding-predictions.jsonl"
    assert full_summary["cache_hits"] == 20
    assert full_summary["new_calls"] == 180
    full_output = tmp_path / "artifacts" / "grounding-predictions.jsonl"
    assert len(full_output.read_text().splitlines()) == 200
    assert not (tmp_path / "artifacts" / "grounding-v2-predictions.jsonl").exists()
