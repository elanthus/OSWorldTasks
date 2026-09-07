"""Fast contract tests for capture-only v3 servers."""

from __future__ import annotations

from fastapi.testclient import TestClient

from legacy.grounding.v3_server import create_v3b_app, create_v3c_app
from pixelgym.tasks.vendor_form.ui import INCOMPLETE_SUBMISSION_MESSAGE


def test_v3b_invalid_submission_is_rejected_without_mutating_counter() -> None:
    with TestClient(create_v3b_app()) as client:
        reset = client.post("/api/reset", json={"seed": 20}).json()
        invalid = client.post(
            "/api/submit",
            json={
                "task_id": reset["task_id"],
                "company_name": "",
                "contact_email": "",
                "contact_phone": "",
                "tax_id": "",
                "country": "",
                "payment_terms": "",
                "expedited_onboarding": False,
            },
        )
        assert invalid.status_code == 422
        assert invalid.json() == {"detail": INCOMPLETE_SUBMISSION_MESSAGE}

        valid = client.post(
            "/api/submit",
            json={
                "task_id": reset["task_id"],
                "company_name": "Acme",
                "contact_email": "ops@example.com",
                "contact_phone": "+1 555 0100",
                "tax_id": "US-123",
                "country": "US",
                "payment_terms": "Net 30",
                "expedited_onboarding": False,
            },
        )
        assert valid.json() == {"submission_number": 1}


def test_v3c_task_id_is_hash_derived_and_seed_deterministic() -> None:
    with TestClient(create_v3c_app()) as client:
        first = client.post("/api/reset", json={"seed": 20}).json()
        repeated = client.post("/api/reset", json={"seed": 20}).json()
        other = client.post("/api/reset", json={"seed": 21}).json()

    assert first == repeated
    assert first["task_id"].startswith("v3c-")
    assert len(first["task_id"]) == len("v3c-") + 16
    assert first["task_id"] != other["task_id"]
