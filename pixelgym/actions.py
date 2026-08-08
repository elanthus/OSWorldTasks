"""Action types (NOOP, CLICK, KEY) and the versioned key allowlist (D1.5).

The action space is a *fixed* ``spaces.Dict`` -- every action carries all
four fields (``action_type``, ``x``, ``y``, ``key``) regardless of which
type it is, so ``action_space.sample()`` always produces something callers
can pass to `PixelGuiEnv.step`. Only some fields are read by the environment
for a given `ActionType`; see `ACTIVE_FIELDS`. The other fields must still be
present and within their declared space (the Dict is fixed-shape), but their
values are ignored.

Validation is strict and does **not** rely solely on `spaces.Dict.contains`:
Gymnasium's `Discrete.contains` accepts `True`/`False` (`bool` is a Python
`int` subclass) and zero-dimensional NumPy arrays, neither of which is a
canonical action value here. `validate_action` instead checks, per field,
that the value is a plain Python `int` or a NumPy integer *scalar*
(`np.integer`, never `np.ndarray` -- not even 0-d) and in range, and that
the action has exactly the four declared keys. It never clips a coordinate
into range or coerces a value's type to make an action fit.

`validate_action` reads each field (`action[name]`) and normalizes it
(`int(...)`) *exactly once*, returning the result as an immutable
`ValidatedAction` -- concrete Python `int`s only. Callers must dispatch
that snapshot and never re-read the original `action`: a caller-controlled
`Mapping` whose `__getitem__` returns a different value on a second call,
or an accepted `int` subclass whose `__int__` returns a different value on
a second call, cannot smuggle an out-of-range value past validation this
way, because there is no second read or conversion for either to exploit.
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

import numpy as np
from gymnasium import spaces


class ActionType(IntEnum):
    """Discrete `action_type` values, in the order `build_action_space` uses."""

    NOOP = 0
    CLICK = 1
    KEY = 2


KEY_ALLOWLIST_VERSION = 1

_NAMED_KEYS: tuple[str, ...] = (
    "Tab",
    "Enter",
    "Backspace",
    "ArrowLeft",
    "ArrowRight",
    "ArrowUp",
    "ArrowDown",
)

# string.printable is digits + ascii_letters + punctuation + " \t\n\r\x0b\x0c".
# The whitespace control characters other than the space itself are dropped
# here because they have dedicated named keys above (Tab, Enter) or no
# meaningful on-screen effect (vertical tab, form feed) for this task.
_PRINTABLE_CHARACTERS: tuple[str, ...] = tuple(
    ch for ch in string.printable if ch not in "\t\n\r\x0b\x0c"
)

KEY_ALLOWLIST: tuple[str, ...] = _PRINTABLE_CHARACTERS + _NAMED_KEYS
"""Version `KEY_ALLOWLIST_VERSION` of the KEY action's allowlist. The `key`
action field is an index into this tuple; index order is fixed so that a
given index always means the same key within one allowlist version. Bumping
the allowlist (adding/removing/reordering keys) requires bumping
`KEY_ALLOWLIST_VERSION`, since it changes what every existing index means."""

ACTIVE_FIELDS: Mapping[ActionType, frozenset[str]] = {
    ActionType.NOOP: frozenset(),
    ActionType.CLICK: frozenset({"x", "y"}),
    ActionType.KEY: frozenset({"key"}),
}
"""Which action fields a given `ActionType` actually reads. The other fields
of the Dict action are still required to be present and space-valid (fixed
shape), but the environment ignores their values."""


class InvalidActionError(ValueError):
    """An action was rejected before it reached the backend."""


_REQUIRED_KEYS: frozenset[str] = frozenset({"action_type", "x", "y", "key"})


@dataclass(frozen=True)
class ValidatedAction:
    """The normalized result of `validate_action`: four concrete Python
    `int` values, each read from the caller's action and converted exactly
    once. This -- never the original caller-supplied mapping -- is what
    `PixelGuiEnv` dispatches to the backend."""

    action_type: int
    x: int
    y: int
    key: int


def build_action_space(width: int, height: int) -> spaces.Dict:
    """The fixed action `spaces.Dict` for a `width` x `height` screen.

    - `action_type`: `spaces.Discrete(len(ActionType))` -- NOOP, CLICK, or KEY.
    - `x`, `y`: `spaces.Discrete(width)` / `spaces.Discrete(height)` -- integer
      coordinates in `[0, width)` / `[0, height)`, active only for CLICK.
    - `key`: `spaces.Discrete(len(KEY_ALLOWLIST))` -- index into `KEY_ALLOWLIST`,
      active only for KEY.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"width and height must be positive, got width={width}, height={height}")
    return spaces.Dict(
        {
            "action_type": spaces.Discrete(len(ActionType)),
            "x": spaces.Discrete(width),
            "y": spaces.Discrete(height),
            "key": spaces.Discrete(len(KEY_ALLOWLIST)),
        }
    )


def _is_canonical_integral_scalar(value: object) -> bool:
    """True only for a plain Python `int` or a NumPy integer *scalar*.

    Deliberately narrower than "whatever `Discrete.contains` accepts":
    `bool` is excluded even though it is a Python `int` subclass (`True`/
    `False` are not canonical action values), and any `np.ndarray` -- even
    zero-dimensional -- is excluded even though `Discrete.contains` treats a
    0-d integer array as a valid scalar. `action_space.sample()` and normal
    callers passing plain `int`/`ActionType`/`np.int64` values are accepted.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, np.integer)


def _validate_field(name: str, value: object, size: int) -> int:
    """Validate one already-read field value and return its normalized
    `int`. Calls `int(value)` exactly once; the range check and the
    returned value both reuse that single normalized result rather than
    re-deriving it from `value` a second time."""
    if not _is_canonical_integral_scalar(value):
        raise InvalidActionError(
            f"action[{name!r}] must be a canonical int (not bool, float, str, or "
            f"array), got {value!r} of type {type(value).__name__}"
        )
    normalized = int(value)
    if not (0 <= normalized < size):
        raise InvalidActionError(f"action[{name!r}]={normalized!r} is out of range [0, {size})")
    return normalized


def validate_action(action_space: spaces.Dict, action: Any) -> ValidatedAction:
    """Validate `action` and return the normalized `ValidatedAction`
    snapshot -- the only thing that may ever be dispatched to a backend.

    `action` must be a mapping with exactly the four declared keys, each
    holding a canonical, in-range integral scalar (see
    `_is_canonical_integral_scalar`) -- including fields the action's type
    does not use (see `ACTIVE_FIELDS`); every field is validated regardless
    of `action_type`, before any backend method is invoked. Never clips or
    coerces -- an out-of-range, wrong-typed, or malformed action is rejected
    outright, not repaired.

    Each field is read from `action` and converted to `int` exactly once.
    A caller must dispatch the returned `ValidatedAction`, not `action`
    itself: re-reading `action` after this call reopens the door to a
    stateful `Mapping.__getitem__` or a stateful `int.__int__` returning a
    different, unvalidated value on a later call.
    """
    if not isinstance(action, Mapping):
        raise InvalidActionError(
            f"action must be a mapping with exactly the keys {sorted(_REQUIRED_KEYS)}, "
            f"got {action!r} of type {type(action).__name__}"
        )
    if set(action) != _REQUIRED_KEYS:
        raise InvalidActionError(
            f"action must have exactly the keys {sorted(_REQUIRED_KEYS)}, got "
            f"{sorted(action)}"
        )

    normalized = {
        name: _validate_field(name, action[name], action_space[name].n)
        for name in ("action_type", "x", "y", "key")
    }
    return ValidatedAction(**normalized)
