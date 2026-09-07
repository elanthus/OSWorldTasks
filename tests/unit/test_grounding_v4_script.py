"""Fast tests for the v4 pilot command-line defaults."""

from __future__ import annotations

from pathlib import Path

from legacy.grounding.scripts.run_grounding_v4_pilot import default_output_path


def test_v4_default_prediction_paths_are_provider_specific(tmp_path: Path) -> None:
    luna = default_output_path(tmp_path, "luna")
    mock = default_output_path(tmp_path, "mock")

    assert luna.name == "grounding-v4-pilot-predictions-luna.jsonl"
    assert mock.name == "grounding-v4-pilot-predictions-mock.jsonl"
    assert luna != mock
