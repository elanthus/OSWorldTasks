"""Fast tests for the v4 paid-call cap and input checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.calibration_v4 import V4_PROTOCOL_VERSION
from pixelgym.grounding.evaluation import ResponseCache
from pixelgym.grounding.providers import MockProvider
from pixelgym.grounding.v4_evaluation import planned_v4_calls, run_v4_evaluation


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
    assert plan == {"total_condition_records": 20, "cached_calls": 0, "new_calls": 20}
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
    _write_inputs(tmp_path)
    provider = MockProvider()
    cache = ResponseCache(tmp_path / "cache")
    first = planned_v4_calls(repository_root=tmp_path, provider=provider, cache=cache)
    assert first["new_calls"] == 20
    assert list((tmp_path / "cache").glob("*.json")) == []


def test_fixture_contains_no_target_identity_in_overlays(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    rows: list[dict[str, Any]] = [
        json.loads(line)
        for line in (tmp_path / "artifacts" / "grounding-v4-pilot-overlays.jsonl")
        .read_text()
        .splitlines()
    ]
    assert all("target_id" not in row for row in rows)
