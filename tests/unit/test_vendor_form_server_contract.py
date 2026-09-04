"""Shared HTTP contract tests for the local and OSWorld guest task servers."""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from typing import Any, Protocol

import httpx
import pytest
from fastapi.testclient import TestClient

from pixelgym.tasks.vendor_form import generator
from pixelgym.tasks.vendor_form.app.guest_server import GuestTaskState, VendorFormHTTPServer
from pixelgym.tasks.vendor_form.app.server import create_app

_SEED = 7
_PUBLIC_TASK_KEYS = {"task_id", "schema_version", "fields", "options"}
_EXPECTED_VALUE_KEYS = {
    "answers",
    "expected_fields",
    "expected_submission",
    "expected_values",
    "seed",
}


class _Response(Protocol):
    status_code: int

    def json(self) -> Any: ...


class _Client(Protocol):
    def get(self, url: str) -> _Response: ...

    def post(self, url: str, *, json: Any) -> _Response: ...


@pytest.fixture
def server_clients() -> Iterator[Mapping[str, _Client]]:
    guest_server = VendorFormHTTPServer(
        ("127.0.0.1", 0), GuestTaskState(generator.generate_task(_SEED))
    )
    guest_thread = threading.Thread(
        target=guest_server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    guest_thread.start()
    guest_port = guest_server.server_address[1]

    try:
        with (
            TestClient(create_app()) as fastapi_client,
            httpx.Client(
                base_url=f"http://127.0.0.1:{guest_port}", trust_env=False
            ) as guest_client,
        ):
            yield {"fastapi": fastapi_client, "guest": guest_client}
    finally:
        guest_server.shutdown()
        guest_thread.join(timeout=2)
        guest_server.server_close()
        assert not guest_thread.is_alive()


def _responses(
    clients: Mapping[str, _Client], method: str, path: str, *, json: Any | None = None
) -> dict[str, _Response]:
    if method == "GET":
        return {name: client.get(path) for name, client in clients.items()}
    return {name: client.post(path, json=json) for name, client in clients.items()}


def _assert_matching_status(
    responses: Mapping[str, _Response], expected_status: int
) -> None:
    statuses = {name: response.status_code for name, response in responses.items()}
    assert len(set(statuses.values())) == 1, statuses
    assert next(iter(statuses.values())) == expected_status


def _reset(clients: Mapping[str, _Client]) -> dict[str, dict[str, Any]]:
    responses = _responses(clients, "POST", "/api/reset", json={"seed": _SEED})
    _assert_matching_status(responses, 200)
    return {name: response.json() for name, response in responses.items()}


def _public_tasks(clients: Mapping[str, _Client]) -> dict[str, dict[str, Any]]:
    responses = _responses(clients, "GET", "/api/task")
    _assert_matching_status(responses, 200)
    return {name: response.json() for name, response in responses.items()}


def _submission(task: Mapping[str, Any]) -> dict[str, Any]:
    return {"task_id": task["task_id"], **task["fields"]}


def _valid(payload: dict[str, Any]) -> None:
    return None


def _coerced_bool_string(payload: dict[str, Any]) -> None:
    payload["expedited_onboarding"] = "true"


def _coerced_bool_int(payload: dict[str, Any]) -> None:
    payload["expedited_onboarding"] = 1


def _coerced_bool_form(payload: dict[str, Any]) -> None:
    payload["expedited_onboarding"] = "on"


def _extra_key(payload: dict[str, Any]) -> None:
    payload["unexpected"] = "value"


def _missing_key(payload: dict[str, Any]) -> None:
    del payload["tax_id"]


def _wrong_task_id(payload: dict[str, Any]) -> None:
    payload["task_id"] = "vf-stale"


@pytest.mark.parametrize(
    ("mutate_payload", "expected_status"),
    [
        pytest.param(_valid, 200, id="valid"),
        pytest.param(_coerced_bool_string, 422, id="bool-string-true"),
        pytest.param(_coerced_bool_int, 422, id="bool-int-one"),
        pytest.param(_coerced_bool_form, 422, id="bool-string-on"),
        pytest.param(_extra_key, 422, id="extra-key"),
        pytest.param(_missing_key, 422, id="missing-key"),
        pytest.param(_wrong_task_id, 409, id="wrong-task-id"),
    ],
)
def test_submit_contract_matches_between_servers(
    server_clients: Mapping[str, _Client],
    mutate_payload: Any,
    expected_status: int,
) -> None:
    _reset(server_clients)
    tasks = _public_tasks(server_clients)
    assert tasks["fastapi"] == tasks["guest"]

    responses: dict[str, _Response] = {}
    for name, client in server_clients.items():
        payload = _submission(tasks[name])
        mutate_payload(payload)
        responses[name] = client.post("/api/submit", json=payload)

    _assert_matching_status(responses, expected_status)
    if expected_status == 200:
        assert {name: response.json() for name, response in responses.items()} == {
            "fastapi": {"submission_number": 1},
            "guest": {"submission_number": 1},
        }
    else:
        states = _responses(server_clients, "GET", "/api/state")
        assert all(response.json()["submissions"] == [] for response in states.values())


def test_public_task_contract_matches_and_exposes_only_request_card_values(
    server_clients: Mapping[str, _Client],
) -> None:
    _reset(server_clients)

    tasks = _public_tasks(server_clients)

    assert tasks["fastapi"] == tasks["guest"]
    for task in tasks.values():
        assert set(task) == _PUBLIC_TASK_KEYS
        assert _EXPECTED_VALUE_KEYS.isdisjoint(task)
        assert set(task["fields"]) == set(generator.FIELD_NAMES)
        assert set(task["options"]) == {"country", "payment_terms"}


def test_page_ready_contract_matches_before_and_after_reset(
    server_clients: Mapping[str, _Client],
) -> None:
    reset_payloads = _reset(server_clients)
    mark_responses = {
        name: client.post(
            "/api/page-ready", json={"task_id": reset_payloads[name]["task_id"]}
        )
        for name, client in server_clients.items()
    }
    _assert_matching_status(mark_responses, 200)

    before_reset = _responses(server_clients, "GET", "/api/page-ready")
    _assert_matching_status(before_reset, 200)
    assert all(response.json() == {"ready": True} for response in before_reset.values())

    _reset(server_clients)
    after_reset = _responses(server_clients, "GET", "/api/page-ready")
    _assert_matching_status(after_reset, 200)
    assert all(response.json() == {"ready": False} for response in after_reset.values())
