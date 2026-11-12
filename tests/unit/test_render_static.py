"""The static-layer renderer must be byte-identical to the frozen single-pass renderer.

`render.render` draws the state-independent screen once per task (`render_static`) and redraws
only the dynamic widgets per frame. That is a speed optimisation, not a visual change, so every
frame is compared byte-for-byte with `tests/support/reference_render.render_reference`, the
renderer as it stood before the optimisation.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.backends.fake import FakeBackend
from pixelgym.tasks.vendor_form import render
from tests.support.reference_render import render_reference

_GOLDEN = Path(__file__).parent / "fixtures/golden_trajectory_seed7.json"
_SIZES = [(1024, 768), (640, 480), (320, 240), (64, 48)]


def _assert_identical(backend: FakeBackend) -> None:
    fast = backend.screenshot()
    reference = render_reference(backend.task_record, backend.form, backend.layout)
    assert fast.dtype == reference.dtype and fast.shape == reference.shape
    assert np.array_equal(fast, reference)


def _apply(backend: FakeBackend, action: tuple[str, object]) -> None:
    kind, argument = action
    if kind == "click":
        x, y = argument  # type: ignore[misc]
        backend.click(x, y)
    else:
        backend.key(argument)  # type: ignore[arg-type]


@pytest.mark.parametrize(("width", "height"), _SIZES)
def test_golden_trajectory_frames_are_byte_identical(width: int, height: int) -> None:
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    backend = FakeBackend(width=width, height=height)
    backend.reset(golden["seed"])
    _assert_identical(backend)
    for action in golden["actions"]:
        if action["action_type"] == 1:
            x = min(action["x"] * width // 1024, width - 1)
            y = min(action["y"] * height // 768, height - 1)
            backend.click(x, y)
        elif action["action_type"] == 2:
            backend.key(KEY_ALLOWLIST[action["key"]])
        _assert_identical(backend)


def _actions(width: int, height: int) -> st.SearchStrategy[list[tuple[str, object]]]:
    clicks = st.tuples(st.integers(0, width - 1), st.integers(0, height - 1)).map(
        lambda point: ("click", point)
    )
    keys = st.one_of(
        st.sampled_from(("Tab", "Enter", "Backspace", "ArrowDown", "ArrowUp", " ")),
        st.sampled_from(KEY_ALLOWLIST),
    ).map(lambda key: ("key", key))
    return st.lists(st.one_of(clicks, keys, keys), min_size=1, max_size=60)


@settings(max_examples=150)
@given(
    size=st.sampled_from(_SIZES),
    seed=st.integers(0, 10_000),
    data=st.data(),
)
def test_random_states_are_byte_identical(
    size: tuple[int, int], seed: int, data: st.DataObject
) -> None:
    width, height = size
    backend = FakeBackend(width=width, height=height)
    backend.reset(seed)
    for action in data.draw(_actions(width, height)):
        _apply(backend, action)
        _assert_identical(backend)


def test_long_typed_values_and_every_status_are_byte_identical() -> None:
    backend = FakeBackend()
    backend.reset(3)
    layout = backend.layout
    backend.click(*layout.controls[render.WidgetId.SUBMIT].center)  # incomplete status
    _assert_identical(backend)
    backend.install_form_values({"company_name": "W" * 300, "tax_id": "x" * 90})
    _assert_identical(backend)
    backend.install_form_values({name: value for name, value in backend.current_fields().items()})
    backend.click(*layout.controls[render.WidgetId.SUBMIT].center)  # "Submitted."
    assert backend.form.status == "Submitted."
    _assert_identical(backend)


def test_static_layer_is_not_mutated_by_rendering() -> None:
    backend = FakeBackend()
    record = dict(backend.reset(5))
    static = render.render_static(record, backend.layout)
    assert static is not None
    before = np.asarray(static).copy()
    backend.install_form_values({"company_name": "Mutation probe"})
    render.render(record, backend.form, backend.layout, static=static)
    assert np.array_equal(np.asarray(static), before)


# The disjointness guard is conservative: below 256x192 it falls back to the single pass even
# where the layers happen not to collide. Both paths are byte-identical; only speed differs.
@pytest.mark.parametrize(
    ("width", "height", "fast"),
    [(1024, 768, True), (320, 240, True), (256, 192, True), (128, 96, False), (64, 48, False)],
)
def test_static_layer_is_used_only_where_layers_are_disjoint(
    width: int, height: int, fast: bool
) -> None:
    backend = FakeBackend(width=width, height=height)
    backend.reset(7)

    assert (render.render_static(backend.task_record, backend.layout) is not None) is fast
