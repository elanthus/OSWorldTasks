"""Tests for the vendor-onboarding FastAPI service.

Uses FastAPI's in-process TestClient — no sockets, no network, no wall clock.
"""

import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pixelgym.tasks.vendor_form.app.server import VendorFormState, create_app

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _client() -> TestClient:
    return TestClient(create_app())


def _submit_payload(task: dict, **overrides) -> dict:
    """Build a /api/submit body: the task's fields plus the task_id it was read from."""
    payload = {**task["fields"], "task_id": task["task_id"]}
    payload.update(overrides)
    return payload


def test_ready_sentinel_follows_render_and_exact_font_loads():
    app_source = (
        REPOSITORY_ROOT / "pixelgym/tasks/vendor_form/app/static/app.js"
    ).read_text(encoding="utf-8")

    render_positions = [
        app_source.index("renderRequestCard(task.fields);"),
        app_source.index("populateCountryOptions(task.options.country);"),
        app_source.index("populatePaymentTermsOptions(task.options.payment_terms);"),
    ]
    font_load_position = app_source.index("document.fonts.load(")
    ready_position = app_source.index('dataset.pixelgymReady = "true"')

    assert max(render_positions) < font_load_position < ready_position


def test_reset_is_idempotent_for_same_seed():
    client = _client()

    first = client.post("/api/reset", json={"seed": 7}).json()
    second = client.post("/api/reset", json={"seed": 7}).json()

    assert first == second


def test_reset_installs_a_new_task_and_clears_prior_submissions():
    client = _client()
    client.post("/api/reset", json={"seed": 7})
    task = client.get("/api/task").json()
    client.post("/api/submit", json=_submit_payload(task))
    assert client.get("/api/state").json()["submissions"] != []

    client.post("/api/reset", json={"seed": 7})

    state = client.get("/api/state").json()
    assert state["submissions"] == []
    assert state["task"]["task_id"] == task["task_id"]


def test_reset_with_different_seed_changes_the_task():
    client = _client()
    first = client.post("/api/reset", json={"seed": 1}).json()
    second = client.post("/api/reset", json={"seed": 2}).json()

    assert first["task_id"] != second["task_id"]


def test_public_task_view_includes_attestable_schema_and_seed():
    client = _client()
    client.post("/api/reset", json={"seed": 7})

    task = client.get("/api/task").json()

    assert task["schema_version"] == 1
    assert task["seed"] == 7


def test_page_ready_marker_is_task_bound_and_reset_to_false():
    client = _client()
    active = client.post("/api/reset", json={"seed": 7}).json()
    assert client.get("/api/page-ready").json() == {"ready": False}

    stale = client.post("/api/page-ready", json={"task_id": "vf-stale"})
    marked = client.post("/api/page-ready", json={"task_id": active["task_id"]})

    assert stale.status_code == 409
    assert marked.json() == {"ready": True}
    assert client.get("/api/page-ready").json() == {"ready": True}
    client.post("/api/reset", json={"seed": 7})
    assert client.get("/api/page-ready").json() == {"ready": False}


def test_request_card_endpoint_exposes_desired_values_without_secrecy():
    client = _client()
    reset_resp = client.post("/api/reset", json={"seed": 11}).json()

    task_resp = client.get("/api/task").json()

    assert task_resp["task_id"] == reset_resp["task_id"]
    assert set(task_resp["fields"]) == {
        "company_name",
        "contact_email",
        "contact_phone",
        "tax_id",
        "country",
        "payment_terms",
        "expedited_onboarding",
    }
    assert task_resp["options"]["country"]
    assert task_resp["options"]["payment_terms"]


def test_get_task_before_reset_is_rejected():
    client = _client()

    resp = client.get("/api/task")

    assert resp.status_code == 409


def test_submit_before_reset_is_rejected():
    client = _client()

    resp = client.post(
        "/api/submit",
        json={
            "company_name": "x",
            "contact_email": "x@x.example",
            "contact_phone": "+1-555-000-0000",
            "tax_id": "TAX-00000000",
            "country": "Canada",
            "payment_terms": "Net 30",
            "expedited_onboarding": False,
            "task_id": "vf-0000000000000000",
        },
    )

    assert resp.status_code == 409


def test_submit_records_an_immutable_submission_event():
    client = _client()
    reset_resp = client.post("/api/reset", json={"seed": 5}).json()
    task = client.get("/api/task").json()

    resp = client.post("/api/submit", json=_submit_payload(task))

    assert resp.status_code == 200
    assert resp.json() == {"submission_number": 1}

    state = client.get("/api/state").json()
    submission = state["submissions"][0]
    assert submission["task_id"] == reset_resp["task_id"]
    assert submission["seed"] == 5
    assert submission["values"] == task["fields"]
    assert submission["submitted_at_step"] == 1


def test_repeated_submissions_increment_and_do_not_replace_prior_ones():
    client = _client()
    client.post("/api/reset", json={"seed": 5})
    task = client.get("/api/task").json()

    client.post("/api/submit", json=_submit_payload(task))
    client.post("/api/submit", json=_submit_payload(task))

    submissions = client.get("/api/state").json()["submissions"]
    assert [s["submitted_at_step"] for s in submissions] == [1, 2]


def test_submit_normalizes_surrounding_whitespace():
    client = _client()
    client.post("/api/reset", json={"seed": 5})
    task = client.get("/api/task").json()
    padded_name = f"  {task['fields']['company_name']}  "

    client.post("/api/submit", json=_submit_payload(task, company_name=padded_name))

    submission = client.get("/api/state").json()["submissions"][0]
    assert submission["values"]["company_name"] == task["fields"]["company_name"]


def test_stale_submission_for_a_previous_task_is_rejected_not_relabeled():
    """load seed 1, reset seed 2, then submit the seed-1 view: must be rejected, never
    silently stamped with the active seed-2 task_id."""
    client = _client()
    client.post("/api/reset", json={"seed": 1})
    stale_view = client.get("/api/task").json()

    active = client.post("/api/reset", json={"seed": 2}).json()

    resp = client.post("/api/submit", json=_submit_payload(stale_view))

    assert resp.status_code == 409
    state = client.get("/api/state").json()
    assert state["submissions"] == []
    assert state["task"]["task_id"] == active["task_id"]


def test_submit_with_unknown_task_id_is_rejected():
    client = _client()
    client.post("/api/reset", json={"seed": 5})
    task = client.get("/api/task").json()

    resp = client.post("/api/submit", json=_submit_payload(task, task_id="vf-does-not-exist"))

    assert resp.status_code == 409
    assert client.get("/api/state").json()["submissions"] == []


def test_submission_record_rejects_attribute_mutation():
    state = VendorFormState()
    task = state.reset(5)

    record = state.submit(task["task_id"], dict(task["fields"]))

    with pytest.raises(dataclasses.FrozenInstanceError):
        record.task_id = "tampered"


def test_submission_record_values_reject_item_mutation():
    state = VendorFormState()
    task = state.reset(5)

    record = state.submit(task["task_id"], dict(task["fields"]))

    with pytest.raises(TypeError):
        record.values["company_name"] = "tampered"


def test_mutating_the_caller_supplied_values_dict_after_submit_does_not_affect_stored_state():
    state = VendorFormState()
    task = state.reset(5)
    values = dict(task["fields"])

    record = state.submit(task["task_id"], values)
    values["company_name"] = "tampered after the call"

    assert state.submissions[0] is record
    assert record.values["company_name"] == task["fields"]["company_name"]


def test_root_route_serves_html_and_does_not_link_the_privileged_state_endpoint():
    client = _client()
    client.post("/api/reset", json={"seed": 5})

    resp = client.get("/")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "/api/state" not in resp.text


def test_privileged_state_endpoint_returns_full_task_and_submissions():
    client = _client()
    client.post("/api/reset", json={"seed": 9})

    state = client.get("/api/state").json()

    assert state["task"]["seed"] == 9
    assert state["submissions"] == []
