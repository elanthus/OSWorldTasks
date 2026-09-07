"""Shared fixtures for the fast unit suite.

The privileged golden-trajectory solver lives in `tests/support/golden_solver.py`
(it is also imported by `scripts/golden_trajectory.py`); this module only wires
it up as pytest fixtures and loads the committed fixture file.

**The solver is explicitly not the golden trajectory.** It reads the expected
values off the backend at runtime, which the checked-in golden trajectory is
forbidden to do. The golden trajectory is a frozen list of literal
actions plus its recorded reward timeline in
`fixtures/golden_trajectory_seed7.json`, replayed by `test_golden_trajectory.py`,
which is the authority on reward timing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import settings

from tests.support.golden_solver import (
    build_dynamic_solve_actions,
    build_golden_actions,
    click_action,
    key_action,
    noop_action,
)

__all__ = [
    "build_dynamic_solve_actions",
    "build_golden_actions",
    "click_action",
    "key_action",
    "noop_action",
]

GOLDEN_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_trajectory_seed7.json"

# CI selects this profile explicitly. It makes generated cases repeatable across
# runs and prevents Hypothesis from reading or writing its normal example database.
settings.register_profile(
    "ci",
    settings(max_examples=50, derandomize=True, deadline=None, database=None),
)


@pytest.fixture
def dynamic_solve_actions():
    """The runtime-derived solver. For reward-timing guarantees, prefer the
    frozen golden trajectory in `test_golden_trajectory.py`."""
    return build_dynamic_solve_actions


@pytest.fixture
def golden_trajectory() -> dict:
    """The checked-in seed-7 golden trajectory, parsed from JSON.

    Loaded as plain data: the actions are literal `int` values, so replaying
    them cannot consult the generator, the layout, or the backend's task.
    """
    return json.loads(GOLDEN_FIXTURE_PATH.read_text())
