"""Privileged, host-side evaluator.

`evaluate` consumes only the `TaskSpec` the environment reset to and the
`Submission` history read from the privileged backend state (``/api/state``
in the vendor-form app). Its signature has no parameter through which
screenshot pixels, UI state, focus position, action history, or an
agent-declared "done" could enter -- there is no DONE action, and this
function does not accept one. Reward integrity rests entirely on this
boundary: the environment may turn `EvaluationResult.success` into reward,
and nothing else.

`evaluate` is a pure function of its arguments. It does not track whether
it has "already" returned success for a task, so calling it twice with the
same inputs returns an identical result -- the one-shot reward semantics
(fire exactly once) are the environment's responsibility, not this
function's.

Selection among multiple submissions is keyed by `Submission.submitted_at_step`
(the backend's monotonically increasing submission number), never by the
order `submissions` happens to be passed in. `TaskSpec` and `Submission`
validate their own invariants at construction (non-empty expected fields,
a positive `max_episode_steps`, a positive `submitted_at_step`); this
module additionally rejects, at evaluation time, an ambiguous privileged
history where two submissions eligible for the same task and seed share a
`submitted_at_step` -- see `evaluate` below.
"""

from __future__ import annotations

from collections.abc import Sequence

from pixelgym.task_spec import EvaluationResult, Submission, TaskSpec

_MISSING = object()


def _matches(expected: object, actual: object) -> bool:
    """Type-strict equality. Python considers ``True == 1`` and
    ``False == 0``, but a normalized field's type is part of its value, so
    a submitted int must never stand in for an expected bool (or vice
    versa). Comparing ``type(...) is type(...)`` -- rather than
    ``isinstance`` -- is what catches this: ``isinstance(True, int)`` is
    ``True``, but ``type(True) is int`` is ``False``.
    """
    return type(actual) is type(expected) and actual == expected


def evaluate(task: TaskSpec, submissions: Sequence[Submission]) -> EvaluationResult:
    """Score `submissions` against `task`.

    Only ``final`` submissions are considered -- a draft/non-final record
    is not evidence of completion, even if its values are correct.

    `task_id_matches` reports whether any final submission carries
    `task.task_id`, independent of its seed. Evaluation itself additionally
    requires `task.seed` to match before a submission is eligible to
    succeed, so a same-task_id submission recorded under the wrong seed
    reports `task_id_matches=True` but can never succeed.

    Among the eligible submissions (final, matching both `task.task_id` and
    `task.seed`), the one actually scored is the one with the greatest
    `submitted_at_step` -- selection is by that step number, never by
    position in `submissions`. Two eligible submissions sharing a
    `submitted_at_step` describe an ambiguous privileged history (the
    number is meant to uniquely identify a submission); rather than
    silently resolving the tie by list order, this raises `ValueError`.

    Success requires an exact match: every expected field present with the
    expected value, and no extra field names beyond `task.expected_fields`.
    "Expected value" is type-strict, not just ``==`` -- Python considers
    ``True == 1`` and ``False == 0``, but a submitted int must never stand
    in for an expected bool (or vice versa); see `_matches`. Extra field
    names are reported in `mismatched_fields` (so they block success) but
    are not counted against `score`, which is the fraction of expected
    fields whose value is correct and therefore always falls within
    ``[0.0, 1.0]``.
    """
    final_submissions = [s for s in submissions if s.final]
    submitted = bool(final_submissions)
    task_id_matches = any(s.task_id == task.task_id for s in final_submissions)

    eligible = [s for s in final_submissions if s.task_id == task.task_id and s.seed == task.seed]

    steps = [s.submitted_at_step for s in eligible]
    if len(steps) != len(set(steps)):
        raise ValueError(
            f"ambiguous submission history for task {task.task_id!r}: multiple final "
            "submissions eligible for the same task and seed share a submitted_at_step"
        )

    if not eligible:
        return EvaluationResult(
            success=False,
            score=0.0,
            submitted=submitted,
            mismatched_fields=tuple(sorted(task.expected_fields)),
            task_id_matches=task_id_matches,
        )

    latest = max(eligible, key=lambda s: s.submitted_at_step)

    mismatched: set[str] = set()
    correct = 0
    for name, expected in task.expected_fields.items():
        if _matches(expected, latest.values.get(name, _MISSING)):
            correct += 1
        else:
            mismatched.add(name)
    mismatched.update(set(latest.values) - set(task.expected_fields))

    return EvaluationResult(
        success=not mismatched,
        score=correct / len(task.expected_fields),
        submitted=submitted,
        mismatched_fields=tuple(sorted(mismatched)),
        task_id_matches=task_id_matches,
    )
