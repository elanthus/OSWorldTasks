"""Submission value normalization, shared by the real app and the fake backend.

Both the FastAPI service (`pixelgym.tasks.vendor_form.app.server`) and the
in-process fake backend (`pixelgym.backends.fake`) record submissions that the
same privileged evaluator scores. If they normalized differently, a trajectory
that succeeds against the fake could fail against the real app for reasons that
have nothing to do with the agent -- so the rule lives here once rather than
being restated in each.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def normalize_submitted_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Strip surrounding whitespace from string values; pass others through.

    Non-string values (notably the ``expedited_onboarding`` bool) are left
    exactly as-is: the evaluator compares types strictly, so coercing here
    would change what counts as a correct answer.
    """
    return {
        key: value.strip() if isinstance(value, str) else value for key, value in values.items()
    }
