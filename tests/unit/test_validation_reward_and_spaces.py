"""Fast FakeBackend coverage for the reward-timing and space-integrity validators."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.backends import fake as fake_backend_module
from pixelgym.validation import reward as reward_validation
from pixelgym.validation.reward import validate_reward_timing
from pixelgym.validation.spaces import validate_space_integrity

GOLDEN = Path(__file__).parent / "fixtures" / "golden_trajectory_seed7.json"


def _first_frame_only_renderer() -> Any:
    """Render the first frame for real, then reuse it.

    Reward comes only from the privileged evaluator over form state (invariant 6),
    never from pixels, so reusing one frame leaves every reward decision intact
    while removing per-step text rendering cost from the full prefix replay.
    """
    real_render = fake_backend_module.render.render
    cache: list[Any] = []

    def render(record: Any, form: Any, layout: Any) -> Any:
        if not cache:
            cache.append(real_render(record, form, layout))
        return cache[0]

    return render


@pytest.fixture(scope="module")
def reward_report() -> dict[str, Any]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fake_backend_module.render, "render", _first_frame_only_renderer())
        return validate_reward_timing(GOLDEN)


@pytest.fixture(scope="module")
def space_report() -> dict[str, Any]:
    return validate_space_integrity(GOLDEN, sampled_action_count=40)


def _records(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {record["name"]: record for record in report["records"]}


def test_reward_timing_report_passes_every_trajectory(reward_report):
    """Invariant 5: every scripted reward-timing trajectory meets its expected outcome."""
    summary = reward_report["summary"]
    assert summary["passed"] is True
    assert summary["failed_count"] == 0
    assert summary["passed_count"] == summary["trajectory_count"] == len(reward_report["records"])


def test_reward_is_zero_until_exact_submission_then_once(reward_report):
    """Invariant 5: no golden prefix rewards; the full trajectory rewards exactly once."""
    records = _records(reward_report)
    prefixes = [r for name, r in records.items() if name.startswith("golden-prefix-")]
    assert len(prefixes) == reward_report["summary"]["golden_prefix_count"]
    assert all(r["positive_reward_count"] == 0 for r in prefixes)

    complete = records["complete-golden-trajectory"]
    assert complete["positive_reward_count"] == 1
    assert complete["first_positive_reward_step"] == complete["terminal_step"]
    assert complete["terminal_step"] == complete["action_count"]


def test_near_misses_empty_and_stale_submissions_never_reward(reward_report):
    """Invariants 5 and 6: inexact, empty, unsubmitted or stale submissions earn nothing."""
    records = _records(reward_report)
    names = [
        "empty-submit",
        "correct-fields-without-submit",
        "wrong-or-stale-task-id-fixture",
        *(name for name in records if name.startswith("near-miss-")),
    ]
    assert (
        sum(name.startswith("near-miss-") for name in names)
        == (reward_report["summary"]["near_miss_count"])
    )
    for name in names:
        assert records[name]["observed_outcome"] == "no_reward", name


def test_step_after_success_is_rejected(reward_report):
    """Invariant 8: stepping after a successful episode raises."""
    duplicate = _records(reward_report)["duplicate-submit-after-success"]
    assert duplicate["post_success_step_rejected"] is True
    assert duplicate["positive_reward_count"] == 1


def test_truncation_is_not_termination(reward_report):
    """Invariant 7: hitting the step limit truncates without reward or termination."""
    timeout = _records(reward_report)["timeout-one-action-before-completion"]
    assert timeout["observed_outcome"] == "truncated_without_reward"
    assert timeout["terminal_step"] is None
    assert timeout["truncation_step"] == timeout["action_count"]


class _ScriptedEnv:
    """Replays fixed (reward, terminated, truncated) tuples to exercise the classifier."""

    def __init__(self, outcomes: Sequence[tuple[float, bool, bool]]) -> None:
        self.outcomes = list(outcomes)

    def step(self, _action: Any) -> tuple[None, float, bool, bool, dict[str, Any]]:
        reward, terminated, truncated = self.outcomes.pop(0)
        return None, reward, terminated, truncated, {}


@pytest.mark.parametrize(
    ("outcomes", "observed"),
    [
        ([(1.0, False, False), (1.0, True, False)], "invalid_reward_timeline"),
        ([(1.0, False, False), (0.0, False, False)], "invalid_reward_timeline"),
        ([(0.0, False, False), (0.0, False, True)], "truncated_without_reward"),
        ([(0.0, False, False), (0.0, False, False)], "no_reward"),
        ([(0.0, False, False), (1.0, True, False)], "terminal_reward"),
    ],
    ids=["double-reward", "reward-without-termination", "truncated", "none", "terminal"],
)
def test_reward_classifier_flags_broken_timelines(outcomes, observed):
    """Invariant 5: the validator itself detects a double or non-terminal reward."""
    record = reward_validation._execute(
        "scripted",
        _ScriptedEnv(outcomes),  # type: ignore[arg-type]
        [{}] * len(outcomes),
        expected_outcome="terminal_reward",
    )
    assert record["observed_outcome"] == observed
    assert record["passed"] is (observed == "terminal_reward")


def test_space_integrity_report_passes(space_report):
    """Space integrity: sampled observations stay in the observation space."""
    assert space_report["summary"]["passed"] is True
    assert space_report["gymnasium_checker_passed"] is True
    assert space_report["sampled_action_count"] == 40
    assert len(space_report["sampled_actions"]) == 40
    assert all(r["observation_contained"] for r in space_report["sampled_actions"])


def test_space_boundaries_are_accepted(space_report):
    """Invariant 2: corner clicks and first/last allowlist keys are valid actions."""
    names = {r["name"] for r in space_report["boundary_cases"]}
    assert names == {"top-left", "bottom-right", "first-key", "last-key"}
    assert all(r["accepted"] and r["observation_contained"] for r in space_report["boundary_cases"])
    assert len(KEY_ALLOWLIST) > 1


def test_invalid_actions_rejected_before_backend(space_report):
    """Invariant 4: every malformed action is rejected and never reaches the backend."""
    invalid = space_report["invalid_actions"]
    assert {r["name"] for r in invalid} >= {
        "x-equals-width",
        "key-equals-count",
        "float-x",
        "boolean-action-type",
        "not-a-mapping",
    }
    for record in invalid:
        assert record["rejected"] is True, record["name"]
        assert record["rejected_before_backend_execution"] is True, record["name"]


def test_post_episode_steps_rejected(space_report):
    """Invariants 7 and 8: stepping after termination or truncation raises."""
    assert space_report["post_terminal_step_rejected"] is True
    assert space_report["post_truncation_step_rejected"] is True
    assert space_report["summary"]["post_episode_calls_rejected"] is True


@pytest.mark.slow
def test_reward_timing_full_replay_with_real_rendering():
    """Invariant 5: the full validator, with real per-step rendering, passes every trajectory."""
    summary = validate_reward_timing(GOLDEN)["summary"]
    assert summary["passed"] is True
    assert summary["failed_count"] == 0
