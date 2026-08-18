"""Replay of the checked-in seed-7 golden trajectory.

The trajectory is a frozen list of literal `NOOP`/`CLICK`/`KEY` dictionaries in
`fixtures/golden_trajectory_seed7.json`, together with the
`(step, action_type, reward, terminated, truncated)` timeline it is required to
produce. Replay is deliberately blind: no replay test reads
`backend.current_fields()`, `backend.form`, `backend.layout`, or
`pixelgym.tasks.vendor_form.generator`, so the reward timeline they assert
cannot be an artifact of the test knowing the answer. The environment sees
exactly what an agent would send it, and the privileged evaluator is the only
thing that decides success.

The recorded timeline is checked two ways, and both matter:

- replay must equal what the fixture records (drift detection), and
- what the fixture records must independently be zeros-then-exactly-one with a
  single terminating final step (correctness).

Only the first would make the fixture a snapshot of whatever the code does, so
regenerating it after a reward-timing bug would silently bless the bug.

The last section of this file is the exception, and is not a replay: it holds
the seed-11 solvability check and the drift guard that regenerates seed 7 from
the runtime-derived solver. Those two do consult privileged state -- that is
their whole job -- and they assert nothing about the golden reward timeline.
"""

from __future__ import annotations

import hashlib

import pytest

from pixelgym.actions import KEY_ALLOWLIST_VERSION, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.env import DEFAULT_INSTRUCTION, DEFAULT_MAX_EPISODE_STEPS, PixelGuiEnv
from pixelgym.evaluator import evaluate
from pixelgym.task_spec import TaskSpec
from pixelgym.tasks.vendor_form import generator

SEED = 7


@pytest.fixture
def env() -> PixelGuiEnv:
    return PixelGuiEnv(FakeBackend())


def _replay(env: PixelGuiEnv, actions: list[dict]) -> list[tuple]:
    return [env.step(action) for action in actions]


# -- The fixture is only meaningful against the contract it was recorded under --


def test_fixture_matches_the_current_action_space_contract(env, golden_trajectory):
    """`key` is an index into a versioned allowlist and `x`/`y` are absolute
    pixels. If either the allowlist version or the screen size moved, the
    recorded actions no longer mean what they meant when they were recorded,
    and the replay below would be testing nothing."""
    env.reset(seed=SEED)

    assert golden_trajectory["seed"] == SEED
    assert golden_trajectory["key_allowlist_version"] == KEY_ALLOWLIST_VERSION
    height, width, _channels = env.observation_space.shape
    assert golden_trajectory["screen"] == {"width": width, "height": height}


def test_fixture_identifies_the_task_it_was_recorded_against(env, golden_trajectory):
    """The recorded `task_id` and full spec hash pin the trajectory to one
    generated task. A generator change that altered seed 7's task would
    otherwise leave a fixture aimed at a task that no longer exists."""
    _observation, info = env.reset(seed=SEED)

    assert info["task_id"] == golden_trajectory["task_id"]

    record = generator.generate_task(SEED)
    body = {key: value for key, value in record.items() if key != "task_id"}
    digest = hashlib.sha256(generator.canonical_json(body).encode("utf-8")).hexdigest()
    assert golden_trajectory["task_spec_sha256"] == digest


def test_fixture_holds_only_literal_public_actions(golden_trajectory):
    actions = golden_trajectory["actions"]

    assert len(actions) == golden_trajectory["action_count"]
    assert {type(action) for action in actions} == {dict}
    for action in actions:
        assert set(action) == {"action_type", "x", "y", "key"}
        assert {type(value) for value in action.values()} == {int}
        assert action["action_type"] in {int(member) for member in ActionType}


def test_fixture_uses_every_action_type_the_space_declares(golden_trajectory):
    used = {action["action_type"] for action in golden_trajectory["actions"]}

    assert used == {int(member) for member in ActionType}


# -- The recorded timeline, checked independently of any replay -------------


def test_recorded_timeline_is_zeros_then_exactly_one(golden_trajectory):
    """Assert the reward contract against the fixture, independently of replay.

    This keeps the fixture from becoming a snapshot of whatever the environment
    currently does.
    """
    timeline = golden_trajectory["timeline"]

    rewards = [step["reward"] for step in timeline]

    assert rewards == [0.0] * (len(timeline) - 1) + [1.0]


def test_recorded_timeline_ends_the_episode_exactly_once_and_by_terminating(golden_trajectory):
    timeline = golden_trajectory["timeline"]

    assert all(not step["terminated"] and not step["truncated"] for step in timeline[:-1])
    assert timeline[-1]["terminated"] is True
    assert timeline[-1]["truncated"] is False


def test_recorded_timeline_covers_every_action_in_order(golden_trajectory):
    timeline = golden_trajectory["timeline"]
    actions = golden_trajectory["actions"]

    assert [step["step"] for step in timeline] == list(range(1, len(actions) + 1))
    assert [step["action_type"] for step in timeline] == [
        ActionType(action["action_type"]).name for action in actions
    ]


# -- Blind replay must reproduce the recorded timeline ----------------------


def test_blind_replay_reproduces_the_recorded_timeline_exactly(env, golden_trajectory):
    env.reset(seed=SEED)
    actions = golden_trajectory["actions"]

    observed = [
        {
            "step": number,
            "action_type": ActionType(action["action_type"]).name,
            "reward": reward,
            "terminated": terminated,
            "truncated": truncated,
        }
        for number, (action, (_obs, reward, terminated, truncated, _info)) in enumerate(
            zip(actions, _replay(env, actions), strict=True), start=1
        )
    ]

    assert observed == golden_trajectory["timeline"]


def test_final_submit_returns_one_and_terminates(env, golden_trajectory):
    env.reset(seed=SEED)

    _obs, reward, terminated, truncated, _info = _replay(env, golden_trajectory["actions"])[-1]

    assert reward == 1.0
    assert terminated is True
    assert truncated is False


def test_stepping_after_the_trajectory_raises_the_documented_error(env, golden_trajectory):
    env.reset(seed=SEED)
    actions = golden_trajectory["actions"]
    _replay(env, actions)

    with pytest.raises(RuntimeError, match="reset"):
        env.step(actions[0])


def test_replaying_the_trajectory_twice_reproduces_it_exactly(env, golden_trajectory):
    """A second reset to the same seed must reproduce the exact timeline."""
    actions = golden_trajectory["actions"]

    env.reset(seed=SEED)
    first = [
        (reward, terminated, truncated)
        for _o, reward, terminated, truncated, _i in _replay(env, actions)
    ]
    env.reset(seed=SEED)
    second = [
        (reward, terminated, truncated)
        for _o, reward, terminated, truncated, _i in _replay(env, actions)
    ]

    assert first == second


def test_the_trajectory_without_its_final_submit_never_pays_out(env, golden_trajectory):
    """The whole form is filled in correctly and the only thing missing is the
    Submit click. Correct on-screen values are not completion."""
    env.reset(seed=SEED)
    actions = golden_trajectory["actions"][:-1]

    results = _replay(env, actions)

    assert {reward for _o, reward, *_rest in results} == {0.0}
    assert all(not terminated for _o, _r, terminated, _t, _i in results)


# -- Reward and the privileged evaluator must agree -------------------------
# The actions below still come only from the frozen fixture -- nothing here
# selects or modifies an action based on privileged state. What is privileged
# is the *assertion*: reward is only trustworthy if the host-side evaluator
# independently agrees about when success happened and on what basis.


def _evaluator_task(backend) -> TaskSpec:
    """The host-side TaskSpec the evaluator scores against. Rebuilt from the
    public generator and the env module's documented defaults, which is what
    `PixelGuiEnv(backend)` is constructed with."""
    return TaskSpec.from_generated(
        generator.generate_task(SEED),
        instruction=DEFAULT_INSTRUCTION,
        app_url=backend.app_url,
        max_episode_steps=DEFAULT_MAX_EPISODE_STEPS,
    )


def test_the_evaluator_reports_failure_at_every_step_before_the_last(golden_trajectory):
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=SEED)
    task = _evaluator_task(backend)
    actions = golden_trajectory["actions"]

    successes = []
    for action in actions[:-1]:
        env.step(action)
        successes.append(evaluate(task, backend.read_submissions()).success)

    assert successes == [False] * (len(actions) - 1)


def test_the_final_step_pays_out_and_the_evaluator_agrees_why(golden_trajectory):
    """Reward `1.0` on its own proves nothing. It has to be backed by an
    evaluator that saw a submission, matched the task, and found no mismatched
    field -- the same evidence a reward-hacking audit would ask for."""
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=SEED)
    task = _evaluator_task(backend)

    for action in golden_trajectory["actions"][:-1]:
        env.step(action)
    _obs, reward, terminated, truncated, _info = env.step(golden_trajectory["actions"][-1])
    result = evaluate(task, backend.read_submissions())

    assert (reward, terminated, truncated) == (1.0, True, False)
    assert result.submitted is True
    assert result.success is True
    assert result.task_id_matches is True
    assert result.mismatched_fields == ()


def test_the_trajectory_records_exactly_one_submission(env, golden_trajectory):
    """More than one would mean the trajectory pressed Submit more than once,
    which changes what "reward fires exactly once" is actually evidence of."""
    backend = env.backend
    env.reset(seed=SEED)

    _replay(env, golden_trajectory["actions"])

    assert len(backend.read_submissions()) == 1


# -- Not replays: the dynamic solver's distinct coverage ---------------------
# Everything below consults privileged state on purpose. Nothing below asserts
# anything about the golden reward timeline.


def test_the_dynamic_solver_also_solves_a_different_seed(dynamic_solve_actions):
    """The golden fixture is frozen coordinates and keystrokes, so it can only
    ever cover seed 7. This is the check that the form is solvable in general
    rather than by a lucky recording."""
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=11)

    rewards = [env.step(action)[1] for action in dynamic_solve_actions(backend)]

    assert rewards == [0.0] * (len(rewards) - 1) + [1.0]


def test_the_golden_fixture_still_matches_what_the_solver_produces(golden_trajectory):
    """Drift guard: if the UI layout or the task generator moves, this fails
    with the regeneration recipe rather than leaving a stale fixture to fail
    obscurely somewhere else.

    `build_golden_actions` is the exact recipe the fixture was generated from,
    leading `NOOP` included, so the comparison is action-for-action with no
    filtering to hide a difference behind.
    """
    # Imported here rather than at module scope: keeping the solver out of this
    # file's import list is part of what makes the replay tests above visibly
    # blind to privileged state.
    from tests.support.golden_solver import build_golden_actions

    backend = FakeBackend()
    backend.reset(SEED)

    regenerated = build_golden_actions(backend)

    assert golden_trajectory["actions"] == regenerated, (
        "tests/unit/fixtures/golden_trajectory_seed7.json is stale; regenerate it with "
        "`python scripts/golden_trajectory.py check`, which prints the diff and the recipe"
    )
