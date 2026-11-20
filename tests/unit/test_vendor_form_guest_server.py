"""Fast, in-process tests for the dependency-free OSWorld guest service."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from pixelgym.tasks.vendor_form import generator
from pixelgym.tasks.vendor_form.app.guest_server import (
    GuestTaskState,
    PayloadValidationError,
    TaskConflictError,
)
from pixelgym.tasks.vendor_form.app.server import create_app


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

    assert (
        first
        == second
        == {
            "task_id": task["task_id"],
            "seed": 7,
            "requires_reload": True,
        }
    )
    assert state.privileged_state()["submissions"] == []


def test_reset_response_keys_match_fastapi_contract(state):
    fastapi_response = TestClient(create_app()).post("/api/reset", json={"seed": 7})

    assert fastapi_response.status_code == 200
    assert set(state.reset(7)) == set(fastapi_response.json())


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


def test_input_acknowledgements_match_fastapi_contract(state):
    task = generator.generate_task(7)
    assert state.inputs_acknowledged() == 0

    def ack(page, count, task_id=task["task_id"]):
        return state.acknowledge_input({"task_id": task_id, "page": page, "count": count})

    with pytest.raises(TaskConflictError):
        ack(1, 1)  # no page has reported ready
    page = state.mark_page_ready(task["task_id"])

    assert ack(page, 1) == {"acknowledged": 1}
    for bad_page, bad_count in ((page, 1), (page, 3), (page + 1, 2)):
        with pytest.raises(TaskConflictError):
            ack(bad_page, bad_count)
    with pytest.raises(TaskConflictError):
        ack(page, 2, task_id="vf-stale")
    with pytest.raises(PayloadValidationError):
        ack(page, True)
    with pytest.raises(PayloadValidationError):
        state.acknowledge_input({"task_id": task["task_id"], "count": 2})
    assert state.inputs_acknowledged() == 1

    # Same seed, same task_id: only the page token separates the old page from the new one.
    state.reset(7)
    new_page = state.mark_page_ready(task["task_id"])
    assert new_page != page
    with pytest.raises(TaskConflictError):
        ack(page, 1)
    assert ack(new_page, 1) == {"acknowledged": 1}
    assert set(state.privileged_state()) == {"task", "submissions"}


def test_page_ready_http_response_carries_the_page_token():
    fastapi_page = TestClient(create_app())
    fastapi_page.post("/api/reset", json={"seed": 7})
    task_id = fastapi_page.get("/api/task").json()["task_id"]
    fastapi_ready = fastapi_page.post("/api/page-ready", json={"task_id": task_id}).json()

    guest = GuestTaskState(generator.generate_task(7))
    guest.reset(7)

    assert set(fastapi_ready) == {"ready", "page"}
    assert fastapi_ready["page"] == guest.mark_page_ready(task_id)
