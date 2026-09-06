"""The privileged solver that *generates* golden-trajectory candidates.

This module reads a `FakeBackend`'s expected field values at call time. It is
therefore the opposite of the golden trajectory itself, whose replay must be
blind to expected values:

    generation (here, privileged)  ->  candidate JSON  ->  blind replay (tests)

`build_dynamic_solve_actions` derives the public-action sequence that fills the
vendor form correctly for whatever task a `FakeBackend` currently holds.
`CLICK` and `KEY` are the only action types it emits, so reward stays reachable
through the action space rather than through a privileged test hook.

`build_golden_actions` defines the committed seed-7 fixture's exact recipe: the
solver's actions with one leading `NOOP`, which makes replay exercise all three
declared action types. Candidate generation and the committed fixture must agree
action-for-action, so ``python scripts/golden_trajectory.py check`` can diff them
directly.

Why the solver is kept even though the frozen fixture is the reward-timing
authority:

- It solves *any* seed, so the form's solvability is not a claim about seed 7
  alone -- a fixture of frozen coordinates and keystrokes can only ever cover
  the one task it was generated for.
- It is how the seed-7 fixture is regenerated when the UI layout changes.

It lives in the test tree, not in `pixelgym`, and nothing in the package
imports it. `scripts/golden_trajectory.py` does, which is why `tests/` is a
package -- but that script is a development tool and is not distributed either
(`[tool.setuptools.packages.find]` includes only `pixelgym*`).
"""

from __future__ import annotations

from typing import Any

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.tasks.vendor_form.ui import TEXT_WIDGETS, WidgetId

_KEY_INDEX: dict[str, int] = {key: index for index, key in enumerate(KEY_ALLOWLIST)}


def click_action(x: int, y: int) -> dict[str, Any]:
    return {"action_type": ActionType.CLICK, "x": x, "y": y, "key": 0}


def key_action(key: str) -> dict[str, Any]:
    if key not in _KEY_INDEX:
        raise AssertionError(f"{key!r} is not in the versioned key allowlist")
    return {"action_type": ActionType.KEY, "x": 0, "y": 0, "key": _KEY_INDEX[key]}


def noop_action() -> dict[str, Any]:
    return {"action_type": ActionType.NOOP, "x": 0, "y": 0, "key": 0}


def _click_widget(backend: FakeBackend, widget: WidgetId) -> dict[str, Any]:
    return click_action(*backend.layout.controls[widget].center)


def build_dynamic_solve_actions(backend: FakeBackend, *, include_submit: bool = True) -> list[dict]:
    """Actions that fill `backend`'s current task correctly and submit it.

    Derived from the backend's privileged state at call time -- not a golden
    trajectory. The interaction is the one a person would perform: click a
    field, type it, focus the country control and select by allowlisted
    type-ahead plus Enter, pick the radio, tick the box if the request card
    says Yes, press Submit. Native country-popup rows deliberately have no
    transferable geometry contract.
    """
    fields = backend.current_fields()
    layout = backend.layout
    actions: list[dict] = []

    for widget in TEXT_WIDGETS:
        actions.append(_click_widget(backend, widget))
        actions.extend(key_action(character) for character in fields[widget.value])

    # Native select-popup rows do not expose portable geometry. Focus the
    # control, use the countries' unique initial letters for type-ahead, then
    # commit with Enter; key_action verifies both keys are allowlisted.
    actions.append(_click_widget(backend, WidgetId.COUNTRY))
    country_index = backend.form.country_options.index(fields["country"])
    country = backend.form.country_options[country_index]
    actions.extend((key_action(country[0].lower()), key_action("Enter")))

    payment_index = backend.form.payment_options.index(fields["payment_terms"])
    actions.append(click_action(*layout.payment_options[payment_index].center))

    # The checkbox starts unchecked, so it is only clicked when it must end up
    # checked -- clicking it unconditionally would toggle it the wrong way.
    if fields["expedited_onboarding"]:
        actions.append(_click_widget(backend, WidgetId.EXPEDITED_ONBOARDING))

    if include_submit:
        actions.append(_click_widget(backend, WidgetId.SUBMIT))
    return actions


def build_golden_actions(backend: FakeBackend) -> list[dict[str, int]]:
    """The full golden action list for `backend`'s current task, as plain ints.

    One leading `NOOP` (so replay covers every action type the space declares)
    followed by the solver's `CLICK`/`KEY` sequence. This is the canonical
    recipe: what this returns is what the committed fixture must contain.
    """
    actions = [noop_action(), *build_dynamic_solve_actions(backend)]
    return [
        {
            "action_type": int(action["action_type"]),
            "x": int(action["x"]),
            "y": int(action["y"]),
            "key": int(action["key"]),
        }
        for action in actions
    ]
