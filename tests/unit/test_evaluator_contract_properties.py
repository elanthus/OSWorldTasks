"""Generated contract tests for privileged evaluation and reward isolation."""

from __future__ import annotations

import string
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from pixelgym.actions import ActionType
from pixelgym.env import PixelGuiEnv
from pixelgym.evaluator import evaluate
from pixelgym.task_spec import Submission, TaskSpec
from pixelgym.tasks.vendor_form.normalization import normalize_submitted_values

_FIELD_NAMES = st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=10)
_FIELD_VALUES = st.one_of(
    st.text(alphabet=string.ascii_letters + string.digits + " -_", max_size=16),
    st.integers(min_value=-1_000, max_value=1_000),
    st.booleans(),
)
_EXPECTED_FIELDS = st.dictionaries(_FIELD_NAMES, _FIELD_VALUES, min_size=1, max_size=5)


def _task(expected_fields: Mapping[str, Any], *, seed: int = 7) -> TaskSpec:
    return TaskSpec(
        task_id="property-task",
        seed=seed,
        instruction="Submit the exact generated values.",
        expected_fields=expected_fields,
        max_episode_steps=10,
        app_url="property://evaluator",
    )


def _submission(
    values: Mapping[str, Any],
    *,
    task_id: str = "property-task",
    seed: int = 7,
    step: int = 1,
) -> Submission:
    return Submission(
        task_id=task_id,
        seed=seed,
        values=values,
        submitted_at_step=step,
        final=True,
    )


def _one_wrong_value(expected_fields: Mapping[str, Any]) -> dict[str, Any]:
    wrong = dict(expected_fields)
    name = next(iter(wrong))
    value = wrong[name]
    if type(value) is bool:
        wrong[name] = int(value)
    elif type(value) is int:
        wrong[name] = bool(value)
    else:
        wrong[name] = f"{value}!"
    return wrong


def _wrong_type(value: Any) -> Any:
    if type(value) is bool:
        return int(value)
    if type(value) is int:
        return bool(value)
    return value.encode()


class _RewardIsolationBackend:
    """Backend with arbitrary pixels/history but fixed privileged state."""

    width = 2
    height = 2
    app_url = "property://evaluator"

    def __init__(self, pixels: bytes, action_history: Sequence[str]) -> None:
        self._frame = np.frombuffer(pixels, dtype=np.uint8).reshape(self.height, self.width, 3)
        self.action_history = list(action_history)
        self._seed = 0

    def reset(self, seed: int) -> Mapping[str, Any]:
        self._seed = seed
        return {
            "task_id": "property-task",
            "seed": seed,
            "fields": {"answer": "exact"},
        }

    def screenshot(self) -> np.ndarray:
        return self._frame.copy()

    def noop(self) -> None:
        self.action_history.append("noop")

    def click(self, x: int, y: int) -> None:
        self.action_history.append(f"click:{x}:{y}")

    def key(self, key: str) -> None:
        self.action_history.append(f"key:{key}")

    def read_submissions(self) -> Sequence[Submission]:
        return [_submission({"answer": "exact"}, seed=self._seed)]

    def close(self) -> None:
        return None


@given(expected_fields=_EXPECTED_FIELDS, latest_is_exact=st.booleans(), reverse=st.booleans())
def test_only_the_greatest_submission_step_determines_success(
    expected_fields: dict[str, Any], latest_is_exact: bool, reverse: bool
) -> None:
    task = _task(expected_fields)
    exact = dict(expected_fields)
    wrong = _one_wrong_value(expected_fields)
    older_values, latest_values = (wrong, exact) if latest_is_exact else (exact, wrong)
    submissions = [
        _submission(older_values, step=1),
        _submission(latest_values, step=2),
    ]
    if reverse:
        submissions.reverse()

    result = evaluate(task, submissions)

    assert result.success is latest_is_exact
    assert result.score == (
        1.0 if latest_is_exact else (len(expected_fields) - 1) / len(expected_fields)
    )


@given(
    expected_fields=_EXPECTED_FIELDS, mutation=st.sampled_from(("missing", "extra", "wrong_type"))
)
def test_missing_extra_and_wrong_typed_values_cannot_succeed(
    expected_fields: dict[str, Any], mutation: str
) -> None:
    values = dict(expected_fields)
    expected_mismatch: str
    if mutation == "missing":
        expected_mismatch = next(iter(values))
        del values[expected_mismatch]
    elif mutation == "extra":
        expected_mismatch = "unexpected"
        while expected_mismatch in values:
            expected_mismatch += "_"
        values[expected_mismatch] = "extra"
    else:
        expected_mismatch = next(iter(values))
        values[expected_mismatch] = _wrong_type(values[expected_mismatch])

    result = evaluate(_task(expected_fields), [_submission(values)])

    assert result.success is False
    assert expected_mismatch in result.mismatched_fields


@given(
    expected_fields=_EXPECTED_FIELDS,
    seed=st.integers(min_value=-(2**31), max_value=2**31 - 1),
    stale_dimension=st.sampled_from(("task_id", "seed")),
)
def test_stale_task_identity_cannot_succeed(
    expected_fields: dict[str, Any], seed: int, stale_dimension: str
) -> None:
    submission = _submission(
        expected_fields,
        task_id="stale-task" if stale_dimension == "task_id" else "property-task",
        seed=seed + 1 if stale_dimension == "seed" else seed,
    )

    result = evaluate(_task(expected_fields, seed=seed), [submission])

    assert result.success is False
    assert result.score == 0.0
    assert result.task_id_matches is (stale_dimension == "seed")


@given(
    value=st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=20),
    leading=st.lists(st.sampled_from(tuple(" \t\n\r\v\f")), min_size=1, max_size=4),
    trailing=st.lists(st.sampled_from(tuple(" \t\n\r\v\f")), min_size=1, max_size=4),
)
def test_whitespace_is_normalized_before_exact_evaluation(
    value: str, leading: list[str], trailing: list[str]
) -> None:
    expected = {"answer": value, "flag": True}
    raw = {"answer": "".join(leading) + value + "".join(trailing), "flag": True}
    task = _task(expected)

    normalized_result = evaluate(task, [_submission(normalize_submitted_values(raw))])
    raw_result = evaluate(task, [_submission(raw)])

    assert normalized_result.success is True
    assert raw_result.success is False
    assert normalize_submitted_values(raw)["flag"] is True


@given(
    first_pixels=st.binary(min_size=12, max_size=12),
    second_pixels=st.binary(min_size=12, max_size=12),
    first_history=st.lists(st.text(max_size=8), max_size=6),
    second_history=st.lists(st.text(max_size=8), max_size=6),
)
def test_reward_is_independent_of_pixels_and_action_history(
    first_pixels: bytes,
    second_pixels: bytes,
    first_history: list[str],
    second_history: list[str],
) -> None:
    outcomes = []
    for pixels, history in (
        (first_pixels, first_history),
        (second_pixels, second_history),
    ):
        env = PixelGuiEnv(_RewardIsolationBackend(pixels, history), max_episode_steps=2)
        env.reset(seed=7)
        _observation, reward, terminated, truncated, _info = env.step(
            {"action_type": ActionType.NOOP, "x": 0, "y": 0, "key": 0}
        )
        outcomes.append((reward, terminated, truncated))

    assert outcomes == [(1.0, True, False), (1.0, True, False)]
