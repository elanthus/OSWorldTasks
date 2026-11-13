"""Shared pieces of the fake-versus-Chromium differential comparison (test support only).

Used by the opt-in test `tests/integration/test_vendor_form_differential.py`, which asserts there
is no divergence, and by `scripts/run_browser_differential_evidence.py`, which records every
generated sequence and every divergence as structured evidence.
"""

from __future__ import annotations

from typing import Any

from hypothesis import strategies as st

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.backends.fake import FakeBackend
from pixelgym.tasks.vendor_form import generator
from pixelgym.tasks.vendor_form.ui import DESIGN_HEIGHT, DESIGN_WIDTH, Layout, Rect, layout_for

SEEDS = (7, 11, 23, 101)
NAMED_KEYS = ("Tab", "Enter", "Backspace", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", " ")
LETTER_KEYS = ("a", "b", "c", "B", "N", "X", "1", "@")

Action = tuple[str, Any]


def _rect_points(rect: Rect) -> list[tuple[int, int]]:
    cx, cy = rect.center
    inside = [
        (cx, cy),
        (rect.x + 4, cy),
        (rect.right - 5, cy),
        (rect.x, rect.y),
        (rect.right - 1, rect.bottom - 1),
    ]
    outside = [(rect.x - 1, cy), (rect.right, cy), (cx, rect.y - 1), (cx, rect.bottom)]
    return inside + outside


def click_points(layout: Layout) -> list[tuple[int, int]]:
    rects = [
        *layout.controls.values(),
        *layout.labels.values(),
        *layout.payment_options,
        *layout.request_values.values(),
        *layout.request_labels.values(),
        layout.status,
        layout.form_heading,
        layout.request_heading,
    ]
    points = {point for rect in rects for point in _rect_points(rect)}
    return sorted((x, y) for x, y in points if 0 <= x < DESIGN_WIDTH and 0 <= y < DESIGN_HEIGHT)


# Every seed shares one layout (the payment-option count is fixed by the generator).
LAYOUT = layout_for(generator.generate_task(SEEDS[0]), DESIGN_WIDTH, DESIGN_HEIGHT)


def actions_strategy(max_actions: int) -> st.SearchStrategy[list[Action]]:
    clicks = st.one_of(
        st.sampled_from(click_points(LAYOUT)),
        st.sampled_from(click_points(LAYOUT)),
        st.tuples(st.integers(0, DESIGN_WIDTH - 1), st.integers(0, DESIGN_HEIGHT - 1)),
    ).map(lambda point: ("click", point))
    keys = st.one_of(
        st.sampled_from(NAMED_KEYS),
        st.sampled_from(NAMED_KEYS),
        st.sampled_from(LETTER_KEYS),
        st.sampled_from(KEY_ALLOWLIST),
    ).map(lambda key: ("key", key))
    return st.lists(st.one_of(clicks, keys), min_size=1, max_size=max_actions)


def fake_state(fake: FakeBackend) -> dict[str, Any]:
    form = fake.form
    return {
        "values": form.values(),
        "focus": None if form.focus is None else form.focus.value,
        "payment_focus_index": form.payment_focus_index,
        "status": form.status,
    }


def apply(backend: Any, action: Action) -> None:
    kind, argument = action
    if kind == "click":
        backend.click(*argument)
    else:
        backend.key(argument)


def compare(real: Any, seed: int, actions: list[Action]) -> dict[str, Any] | None:
    """Run `actions` on a fresh fake and on `real`; return the first divergence, or None.

    After every action the fake's form state and submissions are compared with the browser's
    privileged DOM read and server state. A divergence record names the action index and both
    sides; the run stops there, because later states would compound the first difference.
    """

    fake = FakeBackend()
    fake_task, real_task = dict(fake.reset(seed)), dict(real.reset(seed))
    if fake_task != real_task:
        return {"index": None, "kind": "task", "fake": fake_task, "browser": real_task}
    for index, action in enumerate(actions):
        apply(fake, action)
        apply(real, action)
        expected, actual = fake_state(fake), real.read_form_state()
        if actual != expected:
            return {"index": index, "kind": "form_state", "fake": expected, "browser": actual}
        fake_submissions = [_record(s) for s in fake.read_submissions()]
        real_submissions = [_record(s) for s in real.read_submissions()]
        if real_submissions != fake_submissions:
            return {
                "index": index,
                "kind": "submissions",
                "fake": fake_submissions,
                "browser": real_submissions,
            }
    return None


def _record(submission: Any) -> dict[str, Any]:
    return {
        "task_id": submission.task_id,
        "seed": submission.seed,
        "values": dict(submission.values),
        "submitted_at_step": submission.submitted_at_step,
        "final": submission.final,
    }


def action_to_json(action: Action) -> list[Any]:
    kind, argument = action
    return [kind, list(argument) if kind == "click" else argument]
