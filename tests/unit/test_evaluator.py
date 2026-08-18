"""Threat-model tests for the privileged evaluator boundary.

The evaluator must never be able to observe screenshot pixels, a UI success
banner, or an agent's self-declared "done" -- its only inputs are the
TaskSpec the environment reset to and the Submission history read from the
privileged backend state.
"""

import inspect

import pytest

from pixelgym.evaluator import evaluate
from pixelgym.task_spec import Submission, TaskSpec

_EXPECTED = {
    "company_name": "Blue Harbor Supply Co.",
    "contact_email": "onboarding@blue-harbor-supply-co.example",
    "country": "Canada",
}


def _task(**overrides) -> TaskSpec:
    defaults = {
        "task_id": "vf-current",
        "seed": 7,
        "instruction": "Fill out the form exactly as shown and submit.",
        "expected_fields": _EXPECTED,
        "max_episode_steps": 200,
        "app_url": "http://127.0.0.1:8000/",
    }
    defaults.update(overrides)
    return TaskSpec(**defaults)


def _submission(**overrides) -> Submission:
    defaults = {
        "task_id": "vf-current",
        "seed": 7,
        "values": dict(_EXPECTED),
        "submitted_at_step": 1,
        "final": True,
    }
    defaults.update(overrides)
    return Submission(**defaults)


def test_no_submission_is_not_success():
    task = _task()

    result = evaluate(task, [])

    assert result.success is False
    assert result.submitted is False
    assert result.task_id_matches is False
    assert set(result.mismatched_fields) == set(_EXPECTED)


def test_exact_correct_submission_succeeds():
    task = _task()

    result = evaluate(task, [_submission()])

    assert result.success is True
    assert result.score == 1.0
    assert result.submitted is True
    assert result.task_id_matches is True
    assert result.mismatched_fields == ()


def test_single_field_near_miss_is_not_success():
    task = _task()
    submission = _submission(values={**_EXPECTED, "country": "Brazil"})

    result = evaluate(task, [submission])

    assert result.success is False
    assert result.mismatched_fields == ("country",)
    assert 0.0 < result.score < 1.0


def test_all_expected_values_correct_plus_an_unexpected_field_is_not_success():
    """Success requires an exact field-name match. An extra, unrequested
    field must block success and be named in mismatched_fields, but must
    not push score below the fraction of expected fields that were
    actually correct (score is based on expected fields only)."""
    task = _task()
    submission = _submission(values={**_EXPECTED, "extra_field": "surprise"})

    result = evaluate(task, [submission])

    assert result.success is False
    assert "extra_field" in result.mismatched_fields
    assert result.score == 1.0


def test_boolean_expected_true_rejects_submitted_integer_one():
    """True == 1 in Python, but a submitted int must never stand in for
    an expected bool -- a field's type is part of its normalized value."""
    task = _task(expected_fields={**_EXPECTED, "expedited_onboarding": True})
    submission = _submission(values={**_EXPECTED, "expedited_onboarding": 1})

    result = evaluate(task, [submission])

    assert result.success is False
    assert "expedited_onboarding" in result.mismatched_fields


def test_boolean_expected_false_rejects_submitted_integer_zero():
    """False == 0 in Python, but the same type-strictness applies in the
    other direction."""
    task = _task(expected_fields={**_EXPECTED, "expedited_onboarding": False})
    submission = _submission(values={**_EXPECTED, "expedited_onboarding": 0})

    result = evaluate(task, [submission])

    assert result.success is False
    assert "expedited_onboarding" in result.mismatched_fields


def test_correctly_typed_boolean_field_and_integer_seed_still_succeed():
    """Regression guard: type-strict comparison must not break the
    ordinary, correctly-typed case."""
    task = _task(seed=7, expected_fields={**_EXPECTED, "expedited_onboarding": True})
    submission = _submission(seed=7, values={**_EXPECTED, "expedited_onboarding": True})

    result = evaluate(task, [submission])

    assert result.success is True
    assert result.score == 1.0


def test_stale_submission_from_a_previous_episode_is_not_success():
    """A submission recorded under a prior episode's task_id must never be
    treated as evidence for the current one, even if its values happen to
    look identical."""
    task = _task(task_id="vf-current")
    stale = _submission(task_id="vf-previous-episode", values=dict(_EXPECTED))

    result = evaluate(task, [stale])

    assert result.success is False
    assert result.submitted is True
    assert result.task_id_matches is False
    assert set(result.mismatched_fields) == set(_EXPECTED)


def test_submission_for_a_different_task_seed_is_not_success():
    """A same-task_id submission recorded under the wrong seed must not
    succeed, but task_id_matches reports task_id equality only -- it is
    independent of the seed check that gates success."""
    task = _task(seed=7)
    wrong_seed = _submission(seed=8)

    result = evaluate(task, [wrong_seed])

    assert result.success is False
    assert result.task_id_matches is True


def test_correct_values_present_but_never_submitted_is_not_success():
    """The evaluator only ever sees the Submission history from the
    privileged backend; it has no channel to a live, unsubmitted form
    state, so 'the right values are sitting in the fields' cannot succeed."""
    task = _task()

    result = evaluate(task, [])

    assert result.submitted is False
    assert result.success is False


def test_non_final_submission_is_not_treated_as_agent_declared_completion():
    """There is no DONE action and evaluate() takes no 'agent says it's
    finished' flag; a draft/non-final submission record must not succeed
    just because its values happen to be correct."""
    task = _task()
    draft = _submission(final=False)

    result = evaluate(task, [draft])

    assert result.success is False
    assert result.submitted is False


def test_duplicate_submission_of_correct_values_still_evaluates_to_success():
    """Reward firing only once is the environment's job; the
    evaluator itself must stay a pure function of its inputs."""
    task = _task()
    submissions = [_submission(submitted_at_step=1), _submission(submitted_at_step=2)]

    result = evaluate(task, submissions)

    assert result.success is True


def test_latest_matching_submission_determines_the_result():
    """A resubmission overwrites the standing evaluation, in either
    direction -- correct-then-wrong must not stay 'succeeded'."""
    task = _task()
    submissions = [
        _submission(submitted_at_step=1, values=dict(_EXPECTED)),
        _submission(submitted_at_step=2, values={**_EXPECTED, "country": "Brazil"}),
    ]

    result = evaluate(task, submissions)

    assert result.success is False
    assert result.mismatched_fields == ("country",)


def test_latest_submission_is_selected_by_submitted_at_step_not_list_order():
    """Selection must key off submitted_at_step, never input list position:
    put the higher (wrong) step first and the lower (correct) step last,
    and confirm the higher step still wins."""
    task = _task()
    submissions = [
        _submission(submitted_at_step=2, values={**_EXPECTED, "country": "Brazil"}),
        _submission(submitted_at_step=1, values=dict(_EXPECTED)),
    ]

    result = evaluate(task, submissions)

    assert result.success is False
    assert result.mismatched_fields == ("country",)


def test_duplicate_submitted_at_step_among_matching_submissions_is_rejected():
    """submitted_at_step is meant to be a unique, monotonically increasing
    submission number (see task_spec.py). Two eligible (final, same
    task_id and seed) submissions sharing one describe an ambiguous
    privileged history -- reject it rather than silently picking a winner
    by list order."""
    task = _task()
    submissions = [
        _submission(submitted_at_step=1, values=dict(_EXPECTED)),
        _submission(submitted_at_step=1, values={**_EXPECTED, "country": "Brazil"}),
    ]

    with pytest.raises(ValueError, match="submitted_at_step"):
        evaluate(task, submissions)


def test_evaluate_is_pure_and_repeatable():
    task = _task()
    submissions = [_submission()]

    first = evaluate(task, submissions)
    second = evaluate(task, submissions)

    assert first == second


def test_partially_written_submission_missing_fields_is_not_success():
    """If privileged state is read mid-write and a submission is missing
    some expected keys, treat the missing keys as mismatches rather than
    crashing or silently succeeding."""
    task = _task()
    partial = _submission(values={"company_name": _EXPECTED["company_name"]})

    result = evaluate(task, [partial])

    assert result.success is False
    assert "contact_email" in result.mismatched_fields
    assert "country" in result.mismatched_fields


def test_evaluator_never_accepts_screenshot_or_pixel_data():
    """The evaluator boundary: its only inputs are TaskSpec and Submission
    objects derived from privileged backend state. Assert the signature
    carries no parameter through which pixels or UI state could enter."""
    params = set(inspect.signature(evaluate).parameters)

    assert params == {"task", "submissions"}
