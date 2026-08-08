"""Unit tests for the action contract (D1.5): types, the versioned key
allowlist, the fixed action space, and strict validation."""

from collections.abc import Mapping
from typing import Self

import pytest
from gymnasium import spaces

from pixelgym.actions import (
    ACTIVE_FIELDS,
    KEY_ALLOWLIST,
    ActionType,
    InvalidActionError,
    ValidatedAction,
    build_action_space,
    validate_action,
)


class _StatefulMapping(Mapping):
    """A `Mapping` double whose `__getitem__` for `sneaky_key` returns
    `safe_value` on its first read and `evil_value` on every read after
    that -- simulating a caller-controlled action object that could pass
    validation and then hand a dispatcher a different value, if the
    dispatcher re-read the mapping instead of using validation's result."""

    def __init__(self, base: dict, *, sneaky_key: str, safe_value: int, evil_value: int) -> None:
        self._base = dict(base)
        self._sneaky_key = sneaky_key
        self._safe_value = safe_value
        self._evil_value = evil_value
        self.reads_of_sneaky_key = 0

    def __getitem__(self, key):
        if key == self._sneaky_key:
            self.reads_of_sneaky_key += 1
            return self._safe_value if self.reads_of_sneaky_key == 1 else self._evil_value
        return self._base[key]

    def __iter__(self):
        return iter(self._base)

    def __len__(self) -> int:
        return len(self._base)


class _StatefulInt(int):
    """An `int` subclass whose stored/compared value is always `safe_value`
    (so `isinstance`/ordinary comparisons never see `evil_value`), but whose
    `__int__` returns `evil_value` from its second call onward -- simulating
    an accepted int-like action value that could leak an unvalidated result
    to a second, separate `int(...)` conversion."""

    def __new__(cls, safe_value: int, evil_value: int) -> Self:
        obj = super().__new__(cls, safe_value)
        obj._evil_value = evil_value
        obj.int_calls = 0
        return obj

    def __int__(self) -> int:
        self.int_calls += 1
        return int.__int__(self) if self.int_calls == 1 else self._evil_value


def test_action_type_values_are_stable():
    """These ints are the wire format of `action_type`; changing them
    changes what every previously-recorded action means."""
    assert ActionType.NOOP == 0
    assert ActionType.CLICK == 1
    assert ActionType.KEY == 2


def test_key_allowlist_contains_the_named_keys():
    for key in ("Tab", "Enter", "Backspace", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"):
        assert key in KEY_ALLOWLIST


def test_key_allowlist_contains_printable_characters():
    for char in ("a", "Z", "5", " ", "!", "_"):
        assert char in KEY_ALLOWLIST


def test_key_allowlist_excludes_bare_control_whitespace():
    """Tab/Enter are represented by their named keys, not the literal
    control characters -- a literal '\\t' or '\\n' index would be a second,
    redundant way to spell the same key."""
    for control_char in ("\t", "\n", "\r", "\x0b", "\x0c"):
        assert control_char not in KEY_ALLOWLIST


def test_key_allowlist_has_no_duplicate_entries():
    assert len(KEY_ALLOWLIST) == len(set(KEY_ALLOWLIST))


def test_active_fields_covers_every_action_type_exactly_once():
    assert set(ACTIVE_FIELDS) == set(ActionType)


def test_active_fields_noop_uses_nothing():
    assert ACTIVE_FIELDS[ActionType.NOOP] == frozenset()


def test_active_fields_click_uses_only_coordinates():
    assert ACTIVE_FIELDS[ActionType.CLICK] == frozenset({"x", "y"})


def test_active_fields_key_uses_only_the_key_index():
    assert ACTIVE_FIELDS[ActionType.KEY] == frozenset({"key"})


def test_build_action_space_rejects_non_positive_width():
    with pytest.raises(ValueError, match="width"):
        build_action_space(width=0, height=10)


def test_build_action_space_rejects_non_positive_height():
    with pytest.raises(ValueError, match="height"):
        build_action_space(width=10, height=-1)


def test_build_action_space_declares_all_four_fields():
    space = build_action_space(width=100, height=80)

    assert set(space.spaces) == {"action_type", "x", "y", "key"}
    assert space["action_type"].n == len(ActionType)
    assert space["x"].n == 100
    assert space["y"].n == 80
    assert space["key"].n == len(KEY_ALLOWLIST)


def test_validate_action_accepts_every_space_sample():
    space = build_action_space(width=100, height=80)
    rng_action = space.sample()

    validate_action(space, rng_action)  # must not raise


def test_validate_action_accepts_boundary_coordinates():
    space = build_action_space(width=100, height=80)

    validate_action(space, {"action_type": ActionType.CLICK, "x": 0, "y": 0, "key": 0})
    validate_action(space, {"action_type": ActionType.CLICK, "x": 99, "y": 79, "key": 0})


def test_validate_action_rejects_out_of_range_x():
    space = build_action_space(width=100, height=80)

    with pytest.raises(InvalidActionError):
        validate_action(space, {"action_type": ActionType.CLICK, "x": 100, "y": 0, "key": 0})


def test_validate_action_rejects_negative_coordinate():
    space = build_action_space(width=100, height=80)

    with pytest.raises(InvalidActionError):
        validate_action(space, {"action_type": ActionType.CLICK, "x": -1, "y": 0, "key": 0})


def test_validate_action_rejects_missing_field():
    space = build_action_space(width=100, height=80)

    with pytest.raises(InvalidActionError):
        validate_action(space, {"action_type": ActionType.NOOP, "x": 0, "y": 0})


def test_validate_action_rejects_unknown_extra_field():
    space = build_action_space(width=100, height=80)

    with pytest.raises(InvalidActionError):
        validate_action(
            space,
            {"action_type": ActionType.NOOP, "x": 0, "y": 0, "key": 0, "extra": 1},
        )


def test_validate_action_rejects_float_coordinate_instead_of_coercing():
    """A coordinate of 3.0 must be rejected outright, not truncated to 3 --
    validation never coerces types to make an action fit."""
    space = build_action_space(width=100, height=80)

    with pytest.raises(InvalidActionError):
        validate_action(space, {"action_type": ActionType.CLICK, "x": 3.0, "y": 0, "key": 0})


def test_validate_action_rejects_key_index_out_of_range():
    space = build_action_space(width=100, height=80)

    with pytest.raises(InvalidActionError):
        validate_action(
            space,
            {"action_type": ActionType.KEY, "x": 0, "y": 0, "key": len(KEY_ALLOWLIST)},
        )


def test_key_action_space_component_is_a_discrete_space():
    space = build_action_space(width=100, height=80)

    assert isinstance(space["key"], spaces.Discrete)


# -- validate_action returns a normalized, single-read snapshot -------------


def test_validate_action_returns_a_validated_action_snapshot():
    space = build_action_space(width=100, height=80)

    result = validate_action(space, {"action_type": ActionType.CLICK, "x": 5, "y": 6, "key": 0})

    assert result == ValidatedAction(action_type=1, x=5, y=6, key=0)
    assert type(result.x) is int
    assert type(result.action_type) is int


def test_validate_action_reads_a_stateful_mapping_field_exactly_once():
    """A `Mapping` that returns an in-range value on its first read and an
    out-of-range value on every later read must not be able to leak the
    later value: validate_action reads each field exactly once."""
    space = build_action_space(width=100, height=80)
    action = _StatefulMapping(
        {"action_type": ActionType.CLICK, "x": 0, "y": 0, "key": 0},
        sneaky_key="x",
        safe_value=5,
        evil_value=999,
    )

    result = validate_action(space, action)

    assert result.x == 5
    assert action.reads_of_sneaky_key == 1


def test_validate_action_converts_a_stateful_int_subclass_exactly_once():
    """An accepted `int` subclass whose `__int__` returns a different value
    on a second call must not be able to leak that second value: the
    returned value is int(...)'d exactly once."""
    space = build_action_space(width=100, height=80)
    sneaky_x = _StatefulInt(5, 999)
    action = {"action_type": ActionType.CLICK, "x": sneaky_x, "y": 0, "key": 0}

    result = validate_action(space, action)

    assert result.x == 5
    assert sneaky_x.int_calls == 1


def test_validate_action_rejects_a_stateful_mapping_whose_first_read_is_out_of_range():
    """If the *first* (and only) read is already out of range, the action
    must be rejected -- validation must not retry a second, more lenient
    read looking for a value that fits."""
    space = build_action_space(width=100, height=80)
    action = _StatefulMapping(
        {"action_type": ActionType.CLICK, "x": 0, "y": 0, "key": 0},
        sneaky_key="x",
        safe_value=999,
        evil_value=5,
    )

    with pytest.raises(InvalidActionError):
        validate_action(space, action)

    assert action.reads_of_sneaky_key == 1
