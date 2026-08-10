"""Fast, in-process tests for the dependency-free OSWorld guest service."""

from __future__ import annotations

import json

import pytest

from pixelgym.tasks.vendor_form import generator
from pixelgym.tasks.vendor_form.app.guest_server import GuestTaskState


@pytest.fixture
def state() -> GuestTaskState:
    return GuestTaskState(generator.generate_task(7))


def _submission(task: dict, **overrides):
    payload = {"task_id": task["task_id"], **task["fields"]}
    payload.update(overrides)
    return payload


def test_reset_is_idempotent_and_clears_submissions(state):
    task = generator.generate_task(7)
    state.submit(_submission(task))

    first = state.reset(7)
    second = state.reset(7)

    assert first == second == {"task_id": task["task_id"], "seed": 7}
    assert state.privileged_state()["submissions"] == []


def test_page_ready_marker_is_task_bound_and_reset_to_false(state):
    task = generator.generate_task(7)
    assert state.page_ready() is False

    with pytest.raises(ValueError, match="task_id"):
        state.mark_page_ready("vf-stale")
    state.mark_page_ready(task["task_id"])
    assert state.page_ready() is True

    state.reset(7)
    assert state.page_ready() is False


def test_reset_rejects_a_different_seed_without_changing_state(state):
    task = generator.generate_task(7)
    state.submit(_submission(task))
    before = state.privileged_state()

    with pytest.raises(ValueError, match="seed"):
        state.reset(8)

    assert state.privileged_state() == before


def test_submission_is_atomic_normalized_and_monotonic(state):
    task = generator.generate_task(7)
    payload = _submission(task, company_name=f"  {task['fields']['company_name']}  ")

    assert state.submit(payload) == {"submission_number": 1}
    assert state.submit(payload) == {"submission_number": 2}

    records = state.privileged_state()["submissions"]
    assert [record["submitted_at_step"] for record in records] == [1, 2]
    assert records[-1]["values"]["company_name"] == task["fields"]["company_name"]
    assert records[-1]["final"] is True


def test_wrong_task_id_and_partial_payload_are_rejected_without_partial_write(state):
    task = generator.generate_task(7)
    with pytest.raises(ValueError, match="task_id"):
        state.submit(_submission(task, task_id="vf-stale"))
    partial = _submission(task)
    partial.pop("tax_id")
    with pytest.raises(ValueError, match="keys"):
        state.submit(partial)

    assert state.privileged_state()["submissions"] == []


def test_privileged_state_is_a_deep_copy(state):
    task = generator.generate_task(7)
    state.submit(_submission(task))

    leaked = state.privileged_state()
    leaked["task"]["fields"]["country"] = "changed"
    leaked["submissions"].clear()

    assert state.privileged_state() == json.loads(
        generator.canonical_json(
            {
                "task": task,
                "submissions": [
                    {
                        "task_id": task["task_id"],
                        "seed": 7,
                        "values": task["fields"],
                        "submitted_at_step": 1,
                        "final": True,
                    }
                ],
            }
        )
    )
