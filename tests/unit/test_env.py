"""Unit tests for `PixelGuiEnv` (D1.5): spaces, reset, step, reward,
termination, truncation, the agent-facing info boundary, strict action
validation, and observation-space integrity.

Uses `pixelgym.backends.fake.FakeBackend` -- the reusable, in-package fake
backend (not a test-local double) -- for the environment-checker run and for
exercising the environment contract in general. A couple of small local
subclasses stand in only for backends that misbehave (wrong-shape/dtype
screenshots), since `FakeBackend` itself always returns a conforming frame.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Self

import numpy as np
import pytest
from gymnasium.utils.env_checker import check_env

from pixelgym.actions import KEY_ALLOWLIST, ActionType, build_action_space
from pixelgym.backends.base import Backend
from pixelgym.backends.fake import FakeBackend
from pixelgym.env import BackendContractError, PixelGuiEnv
from pixelgym.tasks.vendor_form import generator
from pixelgym.tasks.vendor_form.ui import WidgetId


class _StatefulMapping(Mapping):
    """A caller-controlled `Mapping` whose `__getitem__` for `sneaky_key`
    returns `safe_value` on the first read and an out-of-range
    `evil_value` on every later read -- reproduces the reported
    vulnerability where `validate_action` would validate a safe value but
    `_dispatch` would separately re-read the mapping and get a different,
    unvalidated one."""

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
    """An accepted `int` subclass (`isinstance(_, int)` is `True`, it is
    not `bool`) whose stored value is always `safe_value`, but whose
    `__int__` returns an out-of-range `evil_value` from the second call
    onward -- reproduces the reported vulnerability where validation's
    `int(value)` call and dispatch's separate `int(value)` call could see
    different results for the same object."""

    def __new__(cls, safe_value: int, evil_value: int) -> Self:
        obj = super().__new__(cls, safe_value)
        obj._evil_value = evil_value
        obj.int_calls = 0
        return obj

    def __int__(self) -> int:
        self.int_calls += 1
        return int.__int__(self) if self.int_calls == 1 else self._evil_value


class _WrongShapeBackend(FakeBackend):
    def screenshot(self) -> np.ndarray:
        return np.zeros((self.height + 1, self.width, 3), dtype=np.uint8)


class _WrongDtypeBackend(FakeBackend):
    def screenshot(self) -> np.ndarray:
        return np.zeros((self.height, self.width, 3), dtype=np.int16)


class _NonArrayScreenshotBackend(FakeBackend):
    def screenshot(self):  # not a numpy.ndarray at all
        return [[[0, 0, 0]] * self.width] * self.height


class _BadAfterFirstScreenshotBackend(FakeBackend):
    """Returns a conforming frame once (for reset), then a wrong-shaped one
    -- isolates step()'s own observation check from reset()'s."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._screenshot_calls = 0

    def screenshot(self) -> np.ndarray:
        self._screenshot_calls += 1
        if self._screenshot_calls == 1:
            return super().screenshot()
        return np.zeros((self.height + 1, self.width, 3), dtype=np.uint8)


def _noop() -> dict:
    return {"action_type": ActionType.NOOP, "x": 0, "y": 0, "key": 0}


def _click(x: int, y: int) -> dict:
    return {"action_type": ActionType.CLICK, "x": x, "y": y, "key": 0}


def _key(index: int) -> dict:
    return {"action_type": ActionType.KEY, "x": 0, "y": 0, "key": index}


# -- Spaces -------------------------------------------------------------


def test_observation_space_matches_backend_dimensions():
    env = PixelGuiEnv(FakeBackend(width=64, height=48))

    assert env.observation_space.shape == (48, 64, 3)
    assert env.observation_space.dtype == np.uint8


def test_action_space_matches_backend_dimensions():
    env = PixelGuiEnv(FakeBackend(width=64, height=48))

    assert env.action_space == build_action_space(64, 48)


def test_max_episode_steps_must_be_positive():
    with pytest.raises(ValueError, match="max_episode_steps"):
        PixelGuiEnv(FakeBackend(), max_episode_steps=0)


# -- The official Gymnasium environment checker, against the reusable fake --


def test_gymnasium_check_env_passes_against_the_reusable_fake_backend():
    # Generous max_episode_steps so the checker's rollout never truncates mid-check.
    env = PixelGuiEnv(FakeBackend(width=64, height=48), max_episode_steps=200)

    check_env(env, skip_render_check=True)


# -- reset() ---------------------------------------------------------------


def test_reset_returns_observation_within_the_declared_space():
    env = PixelGuiEnv(FakeBackend())

    observation, _info = env.reset(seed=7)

    assert observation in env.observation_space


def test_reset_info_carries_only_task_id():
    env = PixelGuiEnv(FakeBackend())

    _observation, info = env.reset(seed=7)

    assert set(info) == {"task_id"}


def test_reset_info_does_not_expose_the_seed():
    """The vendor-form generator is a public, deterministic function of the
    seed; handing back the seed would let an agent reconstruct every
    expected field value directly instead of reading the request card."""
    env = PixelGuiEnv(FakeBackend())

    _observation, info = env.reset(seed=7)

    assert "seed" not in info
    assert 7 not in info.values()


def test_reset_info_does_not_expose_expected_field_values():
    env = PixelGuiEnv(FakeBackend())

    _observation, info = env.reset(seed=7)

    expected_fields = generator.generate_task(7)["fields"]
    for value in expected_fields.values():
        assert value not in info.values()


def test_same_seed_produces_the_same_task_id_and_initial_observation():
    env = PixelGuiEnv(FakeBackend())

    obs1, info1 = env.reset(seed=7)
    obs2, info2 = env.reset(seed=7)

    assert info1["task_id"] == info2["task_id"]
    assert np.array_equal(obs1, obs2)


def test_different_seed_produces_a_different_task_id():
    env = PixelGuiEnv(FakeBackend())

    _obs1, info1 = env.reset(seed=1)
    _obs2, info2 = env.reset(seed=2)

    assert info1["task_id"] != info2["task_id"]


def test_different_seed_produces_a_different_initial_observation():
    """The task varies in pixels, not just in the privileged record -- the
    screenshot is the only channel an agent has to learn what to type."""
    env = PixelGuiEnv(FakeBackend())

    obs1, _info1 = env.reset(seed=1)
    obs2, _info2 = env.reset(seed=2)

    assert not np.array_equal(obs1, obs2)


def test_reset_without_a_seed_does_not_raise():
    env = PixelGuiEnv(FakeBackend())

    observation, info = env.reset()

    assert observation in env.observation_space
    assert "task_id" in info


# -- Action validation: canonical types only, before any backend call -------


def test_invalid_action_is_rejected_before_any_backend_call():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    malformed = {"action_type": ActionType.CLICK, "x": backend.width, "y": 0, "key": 0}
    with pytest.raises(ValueError):
        env.step(malformed)

    assert backend.click_calls == []
    assert backend.key_calls == []


@pytest.mark.parametrize("field", ["action_type", "x", "y", "key"])
def test_boolean_is_rejected_in_every_discrete_field(field):
    """`bool` is a Python `int` subclass, but `True`/`False` are not
    canonical action values in any field -- including inactive ones."""
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    action = _noop()
    action[field] = True

    with pytest.raises(ValueError):
        env.step(action)

    assert backend.click_calls == []
    assert backend.key_calls == []


@pytest.mark.parametrize("field", ["action_type", "x", "y", "key"])
def test_numpy_bool_is_rejected_in_every_discrete_field(field):
    """`np.bool_` is neither `np.integer` nor a Python `int`/`bool`
    subclass, but it's still worth pinning down explicitly alongside
    `bool`."""
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    action = _noop()
    action[field] = np.bool_(True)

    with pytest.raises(ValueError):
        env.step(action)

    assert backend.click_calls == []
    assert backend.key_calls == []


@pytest.mark.parametrize("field", ["action_type", "x", "y", "key"])
def test_zero_dimensional_numpy_array_is_rejected(field):
    """`Discrete.contains` treats a 0-d integer array as a valid scalar;
    `validate_action` must not -- only plain int/np.integer scalars."""
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    action = _noop()
    action[field] = np.array(0)

    with pytest.raises(ValueError):
        env.step(action)

    assert backend.click_calls == []
    assert backend.key_calls == []


@pytest.mark.parametrize("field", ["action_type", "x", "y", "key"])
def test_non_scalar_numpy_array_is_rejected(field):
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    action = _noop()
    action[field] = np.array([0, 0])

    with pytest.raises(ValueError):
        env.step(action)

    assert backend.click_calls == []
    assert backend.key_calls == []


@pytest.mark.parametrize("field", ["action_type", "x", "y", "key"])
def test_float_is_rejected(field):
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    action = _noop()
    action[field] = 0.0

    with pytest.raises(ValueError):
        env.step(action)

    assert backend.click_calls == []


@pytest.mark.parametrize("field", ["action_type", "x", "y", "key"])
def test_string_is_rejected(field):
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    action = _noop()
    action[field] = "0"

    with pytest.raises(ValueError):
        env.step(action)

    assert backend.click_calls == []


def test_missing_key_is_rejected():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    action = _noop()
    del action["key"]

    with pytest.raises(ValueError):
        env.step(action)

    assert backend.click_calls == []
    assert backend.key_calls == []


def test_extra_key_is_rejected():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    action = {**_noop(), "extra": 0}

    with pytest.raises(ValueError):
        env.step(action)

    assert backend.click_calls == []


def test_plain_python_int_action_is_accepted():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    env.step({"action_type": 1, "x": 5, "y": 6, "key": 0})  # CLICK == 1

    assert backend.click_calls == [(5, 6)]


def test_action_type_enum_value_is_accepted():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    env.step(_click(1, 2))

    assert backend.click_calls == [(1, 2)]


def test_numpy_integer_scalar_action_is_accepted():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    action = {
        "action_type": np.int64(ActionType.CLICK),
        "x": np.int32(3),
        "y": np.int32(4),
        "key": np.int64(0),
    }
    env.step(action)

    assert backend.click_calls == [(3, 4)]


def test_sampled_action_is_always_accepted():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    for _ in range(50):
        env.step(env.action_space.sample())  # must never raise


def test_noop_does_not_call_backend_click_or_key():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    env.step(_noop())

    assert backend.click_calls == []
    assert backend.key_calls == []


def test_click_action_dispatches_its_coordinates_to_the_backend():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    env.step(_click(10, 20))

    assert backend.click_calls == [(10, 20)]


def test_key_action_dispatches_the_allowlisted_key_string_to_the_backend():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    env.step(_key(3))

    assert backend.key_calls == [KEY_ALLOWLIST[3]]


def test_boundary_clicks_at_extreme_corners_are_accepted():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    env.step(_click(0, 0))
    env.step(_click(63, 47))

    assert backend.click_calls == [(0, 0), (63, 47)]


def test_out_of_range_click_coordinate_is_rejected():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    with pytest.raises(ValueError):
        env.step(_click(64, 0))

    assert backend.click_calls == []


# -- Validate-then-dispatch: no re-read of the caller's action after -------
# -- validation (regression coverage for the reported vulnerability) -------


def test_stateful_mapping_second_read_never_reaches_the_backend():
    """`validate_action` must read the caller's action exactly once per
    field. A mapping that answers safely on the first read of `x` and
    with an out-of-range value on every later read must never get its
    later value dispatched to the backend."""
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    action = _StatefulMapping(_click(0, 0), sneaky_key="x", safe_value=5, evil_value=999)

    env.step(action)  # 5 is in range; must not raise

    assert action.reads_of_sneaky_key == 1
    assert backend.click_calls == [(5, 0)]


def test_stateful_mapping_out_of_range_on_first_read_is_rejected():
    """The mirror case: if the single read is already out of range, the
    action is rejected outright -- there is no second, more lenient read
    to fall back to."""
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    action = _StatefulMapping(_click(0, 0), sneaky_key="x", safe_value=999, evil_value=5)

    with pytest.raises(ValueError):
        env.step(action)

    assert action.reads_of_sneaky_key == 1
    assert backend.click_calls == []


def test_stateful_int_subclass_second_conversion_never_reaches_the_backend():
    """An accepted `int` subclass (not `bool`, not an array) whose
    `__int__` returns a different, out-of-range value on a second call
    must not be able to leak that value to the backend: `int(...)` is
    called on any given value exactly once."""
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    sneaky_x = _StatefulInt(5, 999)

    env.step(_click(sneaky_x, 0))  # 5 is in range; must not raise

    assert sneaky_x.int_calls == 1
    assert backend.click_calls == [(5, 0)]


def test_stateful_int_subclass_out_of_range_on_first_conversion_is_rejected():
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    sneaky_x = _StatefulInt(999, 5)

    with pytest.raises(ValueError):
        env.step(_click(sneaky_x, 0))

    assert sneaky_x.int_calls == 1
    assert backend.click_calls == []


# -- Reward, termination, and truncation ------------------------------------


def test_reward_is_zero_with_no_submission():
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    _obs, reward, terminated, truncated, _info = env.step(_noop())

    assert reward == 0.0
    assert terminated is False
    assert truncated is False


def test_valid_submission_terminates_with_reward_one():
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    backend.install_submission(backend.current_fields())

    _obs, reward, terminated, truncated, _info = env.step(_noop())

    assert reward == 1.0
    assert terminated is True
    assert truncated is False


def test_incorrect_submission_does_not_terminate_or_reward():
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    fields = backend.current_fields()
    wrong_values = {**fields, next(iter(fields)): "wrong"}
    backend.install_submission(wrong_values)

    _obs, reward, terminated, _truncated, _info = env.step(_noop())

    assert reward == 0.0
    assert terminated is False


def test_step_limit_truncates_without_terminating():
    backend = FakeBackend()
    env = PixelGuiEnv(backend, max_episode_steps=1)
    env.reset(seed=7)

    _obs, reward, terminated, truncated, _info = env.step(_noop())

    assert reward == 0.0
    assert terminated is False
    assert truncated is True


def test_step_result_has_five_values_of_the_documented_types():
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    result = env.step(_noop())

    assert len(result) == 5
    observation, reward, terminated, truncated, info = result
    assert isinstance(observation, np.ndarray)
    assert isinstance(reward, float)
    assert isinstance(terminated, bool)
    assert isinstance(truncated, bool)
    assert isinstance(info, dict)


def test_step_observation_is_contained_in_the_declared_space():
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    observation, *_rest = env.step(_noop())

    assert observation in env.observation_space


def test_step_info_carries_only_task_id():
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    *_rest, info = env.step(_noop())

    assert set(info) == {"task_id"}


def test_step_info_does_not_expose_partial_evaluator_score():
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    fields = backend.current_fields()
    # A single-field near miss: evaluate() internally computes a nonzero,
    # non-one score for this -- that score must never reach `info`.
    backend.install_submission({**fields, next(iter(fields)): "wrong"})

    *_rest, info = env.step(_noop())

    assert "score" not in info
    assert "mismatched_fields" not in info
    assert "submitted" not in info


def test_step_info_does_not_expose_expected_field_values_on_success():
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    fields = backend.current_fields()
    backend.install_submission(fields)

    *_rest, info = env.step(_noop())

    for value in fields.values():
        assert value not in info.values()


# -- Reward through the action space only ----------------------------------
# The tests above install submissions through a privileged hook, which proves
# the reward *plumbing* but not that reward is reachable by an agent. These
# solve the form with nothing but CLICK and KEY.
#
# The authority on reward *timing* is the frozen golden trajectory replayed in
# `test_golden_trajectory.py`, which never consults privileged state. What the
# runtime-derived solver adds here is the two things a frozen recording cannot
# express: a form proven correct at the moment reward is withheld, and a
# deliberate near miss.


def test_reward_cannot_fire_a_second_time_for_a_standing_success():
    """The evaluator is a pure function of the submission history, so it keeps
    reporting success after the winning step. Termination is what stops that
    from paying out again."""
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    backend.install_submission(backend.current_fields())

    _obs, reward, terminated, _truncated, _info = env.step(_noop())

    assert (reward, terminated) == (1.0, True)
    # The winning submission is still on the privileged record: nothing was
    # consumed or cleared to make the one-shot reward work, so a second payout
    # is prevented by termination alone.
    assert len(backend.read_submissions()) == 1
    with pytest.raises(RuntimeError):
        env.step(_noop())


def test_correct_values_typed_but_never_submitted_receive_zero(dynamic_solve_actions):
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    rewards = [
        env.step(action)[1] for action in dynamic_solve_actions(backend, include_submit=False)
    ]

    assert set(rewards) == {0.0}
    assert backend.form.values() == backend.current_fields()  # the form *is* correct


def test_a_single_wrong_character_submitted_receives_zero(dynamic_solve_actions):
    """A near miss, not a malformed action: every keystroke is legal and the
    form is submitted for the right task -- one field is just off by one
    character."""
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    actions = dynamic_solve_actions(backend, include_submit=False)
    company_name = backend.layout.controls[WidgetId.COMPANY_NAME].center
    actions += [
        _click(*company_name),
        _key(KEY_ALLOWLIST.index("x")),  # one character too many
        _click(*backend.layout.controls[WidgetId.SUBMIT].center),
    ]

    rewards = [env.step(action)[1] for action in actions]

    assert set(rewards) == {0.0}
    submitted = backend.read_submissions()[0].values
    expected = backend.current_fields()
    assert submitted["company_name"] == expected["company_name"] + "x"
    assert {name for name in expected if submitted[name] != expected[name]} == {"company_name"}


def test_stepping_after_termination_raises():
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    backend.install_submission(backend.current_fields())
    env.step(_noop())  # terminates

    with pytest.raises(RuntimeError):
        env.step(_noop())


def test_stepping_after_truncation_raises():
    backend = FakeBackend()
    env = PixelGuiEnv(backend, max_episode_steps=1)
    env.reset(seed=7)
    env.step(_noop())  # truncates

    with pytest.raises(RuntimeError):
        env.step(_noop())


def test_stepping_before_reset_raises():
    env = PixelGuiEnv(FakeBackend())

    with pytest.raises(RuntimeError):
        env.step(_noop())


def test_reset_after_termination_starts_a_fresh_episode():
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    backend.install_submission(backend.current_fields())
    env.step(_noop())  # terminates episode one

    env.reset(seed=7)
    _obs, reward, terminated, _truncated, _info = env.step(_noop())

    assert reward == 0.0
    assert terminated is False


# -- Observation-space integrity: backend-contract errors -------------------


def test_reset_observation_is_contained_in_the_declared_space():
    env = PixelGuiEnv(FakeBackend())

    observation, _info = env.reset(seed=7)

    assert observation in env.observation_space


def test_wrong_shape_screenshot_is_rejected_not_reshaped():
    env = PixelGuiEnv(_WrongShapeBackend(width=64, height=48))

    with pytest.raises(BackendContractError, match="shape"):
        env.reset(seed=7)


def test_wrong_dtype_screenshot_is_rejected_not_cast():
    env = PixelGuiEnv(_WrongDtypeBackend(width=64, height=48))

    with pytest.raises(BackendContractError, match="dtype"):
        env.reset(seed=7)


def test_non_ndarray_screenshot_is_rejected():
    env = PixelGuiEnv(_NonArrayScreenshotBackend(width=64, height=48))

    with pytest.raises(BackendContractError, match="numpy.ndarray"):
        env.reset(seed=7)


def test_wrong_shape_screenshot_on_step_is_rejected():
    # Well-behaved for reset's screenshot, then misbehaves from the first
    # step onward, so this specifically exercises step()'s observation check.
    backend = _BadAfterFirstScreenshotBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    with pytest.raises(BackendContractError, match="shape"):
        env.step(_noop())


# -- close() -----------------------------------------------------------------


def test_close_delegates_to_the_backend():
    backend = FakeBackend()
    env = PixelGuiEnv(backend)

    env.close()

    assert backend.closed is True


# -- Backend protocol ----------------------------------------------------


def test_fake_backend_satisfies_the_backend_protocol():
    assert isinstance(FakeBackend(), Backend)
