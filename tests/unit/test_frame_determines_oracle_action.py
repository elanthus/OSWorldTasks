"""The current frame must determine the oracle's next action (no hidden state on the oracle path).

A policy that acts on the current frame alone can only solve the task if no frame is shown before
two different oracle actions. Under renderer v1 that failed about four times per episode: a
trailing space in a text field had no ink, so "Acme" and "Acme " rendered identically while the
oracle typed different next characters, and the oracle's Enter on the country select left the
frame unchanged before a click. Renderer v2 draws a static caret after the last character of the
focused text field, and the oracle no longer presses Enter on the select.

The check collects (frame digest, oracle action) for every oracle step on at least 200 seeds and
asserts that no frame digest maps to two different actions, within an episode or across episodes.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

import pytest

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.tasks.vendor_form import render
from pixelgym.tasks.vendor_form.ui import WidgetId
from tests.support.golden_solver import build_dynamic_solve_actions

SEEDS = range(200)


def _frame_digest(backend: FakeBackend) -> str:
    return hashlib.sha256(backend.screenshot().tobytes()).hexdigest()


def _apply(backend: FakeBackend, action: dict[str, int]) -> None:
    if action["action_type"] == ActionType.CLICK:
        backend.click(action["x"], action["y"])
    elif action["action_type"] == ActionType.KEY:
        backend.key(KEY_ALLOWLIST[action["key"]])


def _frame_to_actions(width: int, height: int, seeds: range) -> dict[str, set[tuple[int, ...]]]:
    backend = FakeBackend(width=width, height=height)
    seen: dict[str, set[tuple[int, ...]]] = defaultdict(set)
    for seed in seeds:
        backend.reset(seed)
        for action in build_dynamic_solve_actions(backend):
            key = (int(action["action_type"]), action["x"], action["y"], action["key"])
            seen[_frame_digest(backend)].add(key)
            _apply(backend, action)
        assert len(backend.read_submissions()) == 1
    return seen


@pytest.mark.parametrize(("width", "height", "seeds"), [(512, 384, SEEDS), (1024, 768, range(40))])
def test_no_frame_precedes_two_different_oracle_actions(
    width: int, height: int, seeds: range
) -> None:
    seen = _frame_to_actions(width, height, seeds)
    conflicts = {digest: actions for digest, actions in seen.items() if len(actions) > 1}
    assert conflicts == {}, f"{len(conflicts)} frames precede more than one oracle action"


def test_a_trailing_space_changes_the_frame() -> None:
    backend = FakeBackend()
    backend.reset(0)
    backend.click(*backend.layout.controls[WidgetId.COMPANY_NAME].center)
    for character in "Acme":
        backend.key(character)
    before = _frame_digest(backend)

    backend.key(" ")

    assert backend.form.text[WidgetId.COMPANY_NAME] == "Acme "
    assert _frame_digest(backend) != before


def test_a_trailing_space_changes_the_frame_when_the_value_overflows_the_field() -> None:
    backend = FakeBackend()
    backend.reset(0)
    backend.click(*backend.layout.controls[WidgetId.COMPANY_NAME].center)
    for character in "w" * 80:
        backend.key(character)
    before = _frame_digest(backend)

    backend.key(" ")

    assert _frame_digest(backend) != before


def test_the_caret_follows_focus_and_is_drawn_only_in_the_focused_text_field() -> None:
    backend = FakeBackend()
    backend.reset(0)
    backend.click(*backend.layout.controls[WidgetId.COMPANY_NAME].center)
    focused_empty = _frame_digest(backend)
    backend.click(*backend.layout.controls[WidgetId.COUNTRY].center)
    backend.click(*backend.layout.controls[WidgetId.COMPANY_NAME].center)

    assert _frame_digest(backend) == focused_empty  # same state, same frame
    assert render.RENDERER_VERSION == "pixelgym-vendor-form-fake-renderer-v2"


def test_negative_control_without_the_caret_the_frame_hides_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the caret removed, the same check finds conflicts, so the test above can fail."""

    def without_caret(painter: object, rect: object, text: str) -> None:
        painter.text_in(rect, text, font=painter.body, keep_tail=True)  # type: ignore[attr-defined]

    monkeypatch.setattr(render._Painter, "text_with_caret", without_caret)
    seen = _frame_to_actions(512, 384, range(5))

    assert any(len(actions) > 1 for actions in seen.values())
