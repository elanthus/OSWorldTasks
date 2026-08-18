"""Typed contract tests for TaskSpec, Submission, and EvaluationResult."""

import dataclasses

import pytest

from pixelgym.task_spec import EvaluationResult, Submission, TaskSpec

_FIELDS = {"company_name": "Blue Harbor Supply Co.", "country": "Canada"}


def _task(**overrides) -> TaskSpec:
    defaults = {
        "task_id": "vf-abc123",
        "seed": 7,
        "instruction": "Fill out the form exactly as shown and submit.",
        "expected_fields": _FIELDS,
        "max_episode_steps": 200,
        "app_url": "http://127.0.0.1:8000/",
    }
    defaults.update(overrides)
    return TaskSpec(**defaults)


def test_task_spec_expected_fields_is_immutable():
    task = _task()

    with pytest.raises(TypeError):
        task.expected_fields["country"] = "tampered"


def test_task_spec_rejects_attribute_mutation():
    task = _task()

    with pytest.raises(dataclasses.FrozenInstanceError):
        task.task_id = "tampered"


def test_task_spec_expected_fields_is_decoupled_from_caller_supplied_dict():
    fields = dict(_FIELDS)
    task = _task(expected_fields=fields)
    fields["country"] = "tampered after construction"

    assert task.expected_fields["country"] == "Canada"


def test_task_spec_from_generated_builds_expected_shape():
    record = {
        "task_id": "vf-abc123",
        "seed": 7,
        "fields": dict(_FIELDS),
        "options": {"country": ["Canada", "Brazil"]},
    }

    task = TaskSpec.from_generated(
        record,
        instruction="Fill out the form.",
        app_url="http://127.0.0.1:8000/",
        max_episode_steps=200,
    )

    assert task.task_id == "vf-abc123"
    assert task.seed == 7
    assert task.expected_fields == _FIELDS
    assert task.max_episode_steps == 200
    assert task.app_url == "http://127.0.0.1:8000/"


def test_task_spec_rejects_empty_expected_fields():
    with pytest.raises(ValueError, match="expected_fields"):
        _task(expected_fields={})


def test_task_spec_rejects_non_positive_max_episode_steps():
    with pytest.raises(ValueError, match="max_episode_steps"):
        _task(max_episode_steps=0)


def test_task_spec_rejects_negative_max_episode_steps():
    with pytest.raises(ValueError, match="max_episode_steps"):
        _task(max_episode_steps=-5)


def test_task_spec_rejects_non_integer_max_episode_steps():
    with pytest.raises(TypeError, match="max_episode_steps"):
        _task(max_episode_steps=200.5)


def test_task_spec_rejects_boolean_seed():
    """True == 1 in Python, but a Boolean seed must never be accepted --
    not even though bool is a subclass of int."""
    with pytest.raises(TypeError, match="seed"):
        _task(seed=True)


def test_task_spec_accepts_ordinary_integer_seed():
    task = _task(seed=7)

    assert task.seed == 7


def test_submission_values_is_immutable():
    submission = Submission(task_id="vf-abc123", seed=7, values=dict(_FIELDS), submitted_at_step=1)

    with pytest.raises(TypeError):
        submission.values["country"] = "tampered"


def test_submission_defaults_to_final():
    submission = Submission(task_id="vf-abc123", seed=7, values=dict(_FIELDS), submitted_at_step=1)

    assert submission.final is True


def test_submission_from_record_round_trips_server_state_shape():
    record = {
        "task_id": "vf-abc123",
        "seed": 7,
        "values": dict(_FIELDS),
        "submitted_at_step": 2,
        "final": True,
    }

    submission = Submission.from_record(record)

    assert submission.task_id == "vf-abc123"
    assert submission.seed == 7
    assert submission.values == _FIELDS
    assert submission.submitted_at_step == 2
    assert submission.final is True


def test_submission_rejects_non_positive_submitted_at_step():
    with pytest.raises(ValueError, match="submitted_at_step"):
        Submission(task_id="vf-abc123", seed=7, values=dict(_FIELDS), submitted_at_step=0)


def test_submission_rejects_negative_submitted_at_step():
    with pytest.raises(ValueError, match="submitted_at_step"):
        Submission(task_id="vf-abc123", seed=7, values=dict(_FIELDS), submitted_at_step=-1)


def test_submission_rejects_non_integer_submitted_at_step():
    with pytest.raises(TypeError, match="submitted_at_step"):
        Submission(task_id="vf-abc123", seed=7, values=dict(_FIELDS), submitted_at_step=1.5)


def test_submission_rejects_boolean_seed():
    """Even though True == 1, a Boolean submission seed must never be
    accepted -- not even when it would compare equal to a legitimate
    integer task seed of 1."""
    with pytest.raises(TypeError, match="seed"):
        Submission(task_id="vf-abc123", seed=True, values=dict(_FIELDS), submitted_at_step=1)


def test_submission_accepts_ordinary_integer_seed():
    submission = Submission(task_id="vf-abc123", seed=7, values=dict(_FIELDS), submitted_at_step=1)

    assert submission.seed == 7


def test_evaluation_result_holds_structured_evidence_not_just_a_float():
    result = EvaluationResult(
        success=True,
        score=1.0,
        submitted=True,
        mismatched_fields=(),
        task_id_matches=True,
    )

    assert result.success is True
    assert result.mismatched_fields == ()
