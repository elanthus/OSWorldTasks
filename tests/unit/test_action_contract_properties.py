"""Generated contract tests for strict action validation and dispatch."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from pixelgym.actions import (
    KEY_ALLOWLIST,
    ActionType,
    InvalidActionError,
    ValidatedAction,
    build_action_space,
    validate_action,
)
from pixelgym.env import PixelGuiEnv
from pixelgym.task_spec import Submission

_FIELDS = ("action_type", "x", "y", "key")
_SCALAR_KINDS = ("int", "int_subclass", "int8", "int16", "int32", "int64")


class _IntSubclass(int):
    """Representative accepted subclass; bool is tested separately."""


def _integral_scalar(value: int, kind: str) -> int | np.integer[Any]:
    constructors = {
        "int": int,
        "int_subclass": _IntSubclass,
        "int8": np.int8,
        "int16": np.int16,
        "int32": np.int32,
        "int64": np.int64,
    }
    return constructors[kind](value)


@st.composite
def _valid_action_cases(draw):
    width = draw(st.integers(min_value=1, max_value=1_000))
    height = draw(st.integers(min_value=1, max_value=1_000))
    raw = {
        "action_type": draw(st.integers(min_value=0, max_value=len(ActionType) - 1)),
        "x": draw(st.integers(min_value=0, max_value=width - 1)),
        "y": draw(st.integers(min_value=0, max_value=height - 1)),
        "key": draw(st.integers(min_value=0, max_value=len(KEY_ALLOWLIST) - 1)),
    }
    action = {}
    for name, value in raw.items():
        compatible_kinds = tuple(
            kind for kind in _SCALAR_KINDS if kind != "int8" or value <= np.iinfo(np.int8).max
        )
        action[name] = _integral_scalar(value, draw(st.sampled_from(compatible_kinds)))
    return width, height, action, raw


@st.composite
def _invalid_actions(draw):
    action: dict[str, Any] = {"action_type": 0, "x": 0, "y": 0, "key": 0}
    variant = draw(st.sampled_from(("out_of_range", "wrong_type", "missing", "extra")))
    if variant == "out_of_range":
        field = draw(st.sampled_from(_FIELDS))
        upper_bounds = {"action_type": len(ActionType), "x": 8, "y": 6, "key": len(KEY_ALLOWLIST)}
        action[field] = draw(st.sampled_from((-1, upper_bounds[field])))
    elif variant == "wrong_type":
        field = draw(st.sampled_from(_FIELDS))
        action[field] = draw(
            st.one_of(
                st.booleans(),
                st.none(),
                st.floats(allow_nan=True, allow_infinity=True),
                st.text(max_size=5),
                st.binary(max_size=5),
                st.lists(st.integers(), max_size=2),
                st.builds(np.array, st.integers(min_value=-2, max_value=2)),
            )
        )
    elif variant == "missing":
        del action[draw(st.sampled_from(_FIELDS))]
    else:
        action["extra"] = draw(st.integers())
    return action


class _BackendSpy:
    """Minimal backend that records every call after reset."""

    width = 8
    height = 6
    app_url = "spy://action-contract"

    def __init__(self) -> None:
        self.calls: list[object] = []

    def reset(self, seed: int) -> Mapping[str, Any]:
        self.calls.append(("reset", seed))
        return {"task_id": f"spy-{seed}", "seed": seed, "fields": {"answer": "exact"}}

    def screenshot(self) -> np.ndarray:
        self.calls.append("screenshot")
        return np.zeros((self.height, self.width, 3), dtype=np.uint8)

    def noop(self) -> None:
        self.calls.append("noop")

    def click(self, x: int, y: int) -> None:
        self.calls.append(("click", x, y))

    def key(self, key: str) -> None:
        self.calls.append(("key", key))

    def read_submissions(self) -> Sequence[Submission]:
        self.calls.append("read_submissions")
        return []

    def close(self) -> None:
        self.calls.append("close")


@given(case=_valid_action_cases())
def test_exact_valid_shape_and_integral_scalar_types_are_accepted(case) -> None:
    width, height, action, raw = case

    validated = validate_action(build_action_space(width, height), action)

    assert validated == ValidatedAction(**raw)
    assert all(type(getattr(validated, field)) is int for field in _FIELDS)


@given(
    width=st.integers(min_value=1, max_value=1_000),
    height=st.integers(min_value=1, max_value=1_000),
    x_boundary=st.sampled_from(("below", "first", "last", "above")),
    y_boundary=st.sampled_from(("below", "first", "last", "above")),
)
def test_coordinate_boundaries_match_half_open_screen_bounds(
    width: int, height: int, x_boundary: str, y_boundary: str
) -> None:
    candidates_x = {"below": -1, "first": 0, "last": width - 1, "above": width}
    candidates_y = {"below": -1, "first": 0, "last": height - 1, "above": height}
    x = candidates_x[x_boundary]
    y = candidates_y[y_boundary]
    action = {"action_type": ActionType.CLICK, "x": x, "y": y, "key": 0}

    if 0 <= x < width and 0 <= y < height:
        assert validate_action(build_action_space(width, height), action).x == x
    else:
        with pytest.raises(InvalidActionError):
            validate_action(build_action_space(width, height), action)


@given(index=st.integers(min_value=-2, max_value=len(KEY_ALLOWLIST) + 1))
def test_key_index_acceptance_matches_allowlist_bounds(index: int) -> None:
    action = {"action_type": ActionType.KEY, "x": 0, "y": 0, "key": index}

    if 0 <= index < len(KEY_ALLOWLIST):
        assert validate_action(build_action_space(8, 6), action).key == index
    else:
        with pytest.raises(InvalidActionError):
            validate_action(build_action_space(8, 6), action)


@given(field=st.sampled_from(_FIELDS), value=st.booleans())
def test_bool_is_rejected_even_though_it_is_an_int_subclass(field: str, value: bool) -> None:
    action = {"action_type": 0, "x": 0, "y": 0, "key": 0}
    action[field] = value

    with pytest.raises(InvalidActionError):
        validate_action(build_action_space(8, 6), action)


@given(
    action=st.one_of(
        st.none(),
        st.integers(),
        st.text(max_size=5),
        st.lists(st.integers(), max_size=4),
        st.tuples(st.integers(), st.integers()),
    )
)
def test_non_mapping_shapes_are_rejected(action: object) -> None:
    with pytest.raises(InvalidActionError):
        validate_action(build_action_space(8, 6), action)


@given(field=st.sampled_from(_FIELDS))
def test_non_bool_int_subclasses_are_accepted_and_normalized(field: str) -> None:
    action = {"action_type": 0, "x": 0, "y": 0, "key": 0}
    action[field] = _IntSubclass(0)

    validated = validate_action(build_action_space(8, 6), action)

    assert type(getattr(validated, field)) is int
    assert getattr(validated, field) == 0


@given(action=_invalid_actions())
def test_every_invalid_action_is_rejected_before_any_backend_call(action: dict[str, Any]) -> None:
    backend = _BackendSpy()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    backend.calls.clear()

    with pytest.raises(InvalidActionError):
        env.step(action)

    assert backend.calls == []


@given(case=_valid_action_cases(), field=st.sampled_from(_FIELDS))
def test_validated_snapshot_is_immutable_and_detached_from_input(case, field: str) -> None:
    width, height, action, _raw = case
    validated = validate_action(build_action_space(width, height), action)
    before = dataclasses.astuple(validated)

    action[field] = int(action[field]) + 10_000

    assert dataclasses.astuple(validated) == before
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(validated, field, 0)
