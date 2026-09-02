"""Safe aggregation helpers for privileged v5 diagnostic evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def maximum_stage_index(diagnostics: Sequence[Mapping[str, Any]]) -> int:
    """Return the largest plain-integer stage index without coercing malformed evidence."""

    return max(
        (
            diagnostic["stage_index"]
            for diagnostic in diagnostics
            if type(diagnostic.get("stage_index")) is int
        ),
        default=0,
    )
