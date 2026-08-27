from __future__ import annotations

from pixelgym.grounding.v5.diagnostics import maximum_stage_index


def test_maximum_stage_index_ignores_missing_and_non_integer_values() -> None:
    diagnostics = [
        {"event": "missing"},
        {"event": "string", "stage_index": "9"},
        {"event": "boolean", "stage_index": True},
        {"event": "lower", "stage_index": 1},
        {"event": "higher", "stage_index": 3},
    ]

    assert maximum_stage_index(diagnostics) == 3
    assert maximum_stage_index([{"event": "missing"}]) == 0
