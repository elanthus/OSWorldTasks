"""S4 HTTP contract tests for the isolated stateful-serving adapter."""

from __future__ import annotations

import base64
import io
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pixelgym.grounding.v5.contracts import sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.runner import ScriptedStatefulPolicy, ScriptedTransport
from pixelgym.platform.immutable_store import LocalImmutableStore
from pixelgym.platform.operational_log import MemoryOperationalLog
from pixelgym.platform.schema_validation import PlatformSchemas
from pixelgym.platform.service import PolicyRuntime, create_serving_app
from pixelgym.platform.serving_episode import (
    ServingEpisodeError,
    ServingEpisodeHost,
    SQLiteServingSessionStore,
)
from pixelgym.platform.stateful_contracts import (
    MAX_ENCODED_IMAGE_CHARS,
    SESSION_SCHEMA_VERSION,
    EvidenceClass,
    StatefulPolicyPackage,
)
from pixelgym.platform.stateful_service import (
    EpisodeOperationalLogError,
    ImmutableEpisodeOperationalLog,
    MemoryEpisodeOperationalLog,
    MemoryEpisodeSessionRegistry,
    create_episode_router,
)
from tests.unit.platform.stateful_fixtures import evidence, identity, v5_manifest

EPISODE_ID = "ep-" + "3" * 32
NOOP = {"action_type": 0, "x": 0, "y": 0, "key": 0}


def _package() -> StatefulPolicyPackage:
    return StatefulPolicyPackage.build(
        manifest=v5_manifest(),
        model_alias_disclosure=None,
        package_source_sha256="9" * 64,
        dependency_lock_sha256="8" * 64,
        max_steps=40,
        evidence_class=EvidenceClass.CALIBRATION,
        evidence=evidence(),
        code_revision="2" * 40,
        code_state="clean",
        source_tree_sha256="7" * 64,
        source_provenance_verified=True,
        source_provenance_failure_reason=None,
    )


def _host(root: Path, *, attempt_cap: int = 20) -> ServingEpisodeHost:
    package = _package()
    return ServingEpisodeHost(
        session_store=SQLiteServingSessionStore(root / "sessions.sqlite"),
        journal=V5AttemptJournal(root / "attempts.sqlite"),
        package=package,
        identity=replace(identity(), policy_id=package.policy_id),
        policy=ScriptedStatefulPolicy((NOOP,)),
        transport=ScriptedTransport(),
        deployment_attempt_cap=attempt_cap,
        episode_id_factory=lambda: EPISODE_ID,
    )


class FactoryProbe:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = 0
        self.hosts: list[ServingEpisodeHost] = []

    def __call__(self) -> ServingEpisodeHost:
        self.calls += 1
        host = _host(self.root / f"host-{self.calls}")
        self.hosts.append(host)
        return host


def _png(*, width: int = 1024, height: int = 768) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


def _screenshot(data: bytes, *, media_type: str = "image/png") -> dict[str, str]:
    return {
        "image_base64": base64.b64encode(data).decode("ascii"),
        "media_type": media_type,
    }


def _create_body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "task_instruction": "Complete the vendor form.",
        "screen_width": 1024,
        "screen_height": 768,
        "client_episode_ref": "client-1",
    }
    body.update(changes)
    return body


def _app(
    tmp_path: Path,
) -> tuple[TestClient, FactoryProbe, MemoryEpisodeOperationalLog]:
    factory = FactoryProbe(tmp_path)
    records = MemoryEpisodeOperationalLog()
    router = create_episode_router(
        host_factory=factory,
        session_registry=MemoryEpisodeSessionRegistry(),
        operational_log=records,
    )
    app = create_serving_app(
        PolicyRuntime(),
        operational_log=MemoryOperationalLog(),
        episode_router=router,
    )
    return TestClient(app), factory, records


def _assert_identity(response: Any, expected: dict[str, str]) -> None:
    assert response.headers["X-PixelGym-API-Version"] == SESSION_SCHEMA_VERSION
    assert response.headers["X-PixelGym-Policy-ID"] == expected["policy_id"]
    assert response.headers["X-PixelGym-Deployment-ID"] == expected["deployment_id"]
    assert response.headers["X-PixelGym-Exact-Policy-Version"] == expected["exact_policy_version"]
    assert response.headers["X-PixelGym-Evidence-Class"] == expected["evidence_class"]


def test_all_four_routes_mount_on_existing_service_and_emit_frozen_contracts(
    tmp_path: Path,
) -> None:
    client, factory, records = _app(tmp_path)
    schemas = PlatformSchemas()

    created = client.post("/api/v2/episodes", json=_create_body())
    assert created.status_code == 201
    schemas.validate("serving_create_response", created.json())
    ident = created.json()["identity"]
    _assert_identity(created, ident)
    assert factory.calls == 1

    initial = client.get(f"/api/v2/episodes/{EPISODE_ID}")
    assert initial.status_code == 200
    schemas.validate("serving_episode_status", initial.json())
    _assert_identity(initial, ident)
    assert initial.json()["open"] is True

    screen = _png()
    acted = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/act",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "screenshot": _screenshot(screen),
            "previous_intent_id": None,
            "previous_result": None,
        },
    )
    assert acted.status_code == 200
    schemas.validate("serving_act_response", acted.json())
    _assert_identity(acted, ident)
    intent_id = acted.json()["intent_id"]

    closed = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/close",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "final_screenshot": _screenshot(screen),
            "final_intent_id": intent_id,
            "final_result": {
                "reward": 1.0,
                "terminated": True,
                "truncated": False,
                "screenshot_sha256": "sha256:" + sha256_bytes(screen),
            },
        },
    )
    assert closed.status_code == 200
    schemas.validate("serving_close_response", closed.json())
    _assert_identity(closed, ident)
    assert closed.json()["terminal_classification"] == "terminated"
    assert closed.json()["steps"] == 1

    status = client.get(f"/api/v2/episodes/{EPISODE_ID}")
    assert status.status_code == 200
    assert status.json()["open"] is False
    assert status.json()["last_intent_status"] == "result_reported"

    assert sorted(records.records) == [
        f"serving-episode-records/{EPISODE_ID}/closed.json",
        f"serving-episode-records/{EPISODE_ID}/opened.json",
        f"serving-episode-records/{EPISODE_ID}/steps/0000.json",
    ]
    closed_record = records.records[f"serving-episode-records/{EPISODE_ID}/closed.json"]
    schemas.validate("episode_closed_record", closed_record.to_dict())
    encoded_records = repr([record.to_dict() for record in records.records.values()])
    assert base64.b64encode(screen).decode("ascii") not in encoded_records
    assert "policy_checkpoint" not in closed.json()
    assert "final_screenshot_object_key" not in closed.json()


@pytest.mark.parametrize(
    "body",
    [
        _create_body(extra="forbidden"),
        _create_body(screen_width=1023),
        _create_body(screen_width=True),
        _create_body(schema_version="pixelgym-serving-session-v1"),
    ],
)
def test_create_rejects_non_contract_input_before_invoking_host(
    tmp_path: Path, body: dict[str, Any]
) -> None:
    client, factory, records = _app(tmp_path)

    response = client.post("/api/v2/episodes", json=body)

    assert response.status_code == 422
    assert factory.calls == 0
    assert records.records == {}


def test_duplicate_json_keys_are_rejected_before_invoking_host(tmp_path: Path) -> None:
    client, factory, _ = _app(tmp_path)
    body = (
        '{"schema_version":"pixelgym-serving-session-v2",'
        '"task_instruction":"first","task_instruction":"second",'
        '"screen_width":1024,"screen_height":768,"client_episode_ref":"client-1"}'
    )

    response = client.post(
        "/api/v2/episodes", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_json"
    assert factory.calls == 0


def test_create_maps_the_deployment_cap_without_exposing_host_diagnostics(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path / "capped", attempt_cap=1)
    host.create_episode(task_instruction="prime cap", client_episode_ref="primer")
    host.act(episode_id=EPISODE_ID, screenshot=b"not retained")
    router = create_episode_router(
        host_factory=lambda: host,
        session_registry=MemoryEpisodeSessionRegistry(),
        operational_log=MemoryEpisodeOperationalLog(),
    )
    client = TestClient(
        create_serving_app(
            PolicyRuntime(),
            operational_log=MemoryOperationalLog(),
            episode_router=router,
        )
    )

    response = client.post("/api/v2/episodes", json=_create_body(client_episode_ref="next"))

    assert response.status_code == 429
    assert response.json() == {
        "detail": {
            "code": "deployment_attempt_cap_reached",
            "message": "deployment attempt cap prevents a new episode",
        }
    }
    _assert_identity(response, host.identity.to_dict())
    assert "refusing" not in response.text


def test_create_does_not_register_episode_when_open_record_cannot_be_written(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path / "host")
    registry = MemoryEpisodeSessionRegistry()

    class FailingOperationalLog(MemoryEpisodeOperationalLog):
        def append(self, record: object) -> None:
            del record
            raise EpisodeOperationalLogError("private storage failure")

    router = create_episode_router(
        host_factory=lambda: host,
        session_registry=registry,
        operational_log=FailingOperationalLog(),
    )
    client = TestClient(
        create_serving_app(
            PolicyRuntime(),
            operational_log=MemoryOperationalLog(),
            episode_router=router,
        )
    )

    response = client.post("/api/v2/episodes", json=_create_body())

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "operational_storage_unavailable",
            "message": "episode operational storage is unavailable",
        }
    }
    _assert_identity(response, host.identity.to_dict())
    assert registry.get(EPISODE_ID) is None
    assert host.get(EPISODE_ID).resume_phase.value == "closed"
    assert "private storage failure" not in response.text


@pytest.mark.parametrize(
    ("screenshot", "expected_status"),
    [
        (_screenshot(_png(width=1023)), 422),
        (_screenshot(_png(), media_type="image/jpeg"), 415),
        ({"image_base64": "not base64", "media_type": "image/png"}, 400),
        ({"image_base64": "aGk=", "media_type": "image/gif"}, 422),
    ],
)
def test_act_validates_screenshot_before_invoking_host(
    tmp_path: Path, screenshot: dict[str, str], expected_status: int
) -> None:
    client, factory, records = _app(tmp_path)
    assert client.post("/api/v2/episodes", json=_create_body()).status_code == 201
    host = factory.hosts[0]
    before = host.get(EPISODE_ID)

    response = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/act",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "screenshot": screenshot,
            "previous_intent_id": None,
            "previous_result": None,
        },
    )

    assert response.status_code == expected_status
    assert host.get(EPISODE_ID) == before
    assert not any("/steps/" in key for key in records.records)


def test_decoded_screenshot_byte_limit_accepts_equality_and_rejects_above(
    tmp_path: Path,
) -> None:
    client, _factory, _ = _app(tmp_path)
    assert client.post("/api/v2/episodes", json=_create_body()).status_code == 201
    image = _png()
    exact = image + b"\0" * (5 * 1024 * 1024 - len(image))

    accepted = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/act",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "screenshot": _screenshot(exact),
            "previous_intent_id": None,
            "previous_result": None,
        },
    )
    assert accepted.status_code == 200

    second_client, second_factory, _ = _app(tmp_path / "above")
    assert second_client.post("/api/v2/episodes", json=_create_body()).status_code == 201
    too_large = exact + b"\0"
    rejected = second_client.post(
        f"/api/v2/episodes/{EPISODE_ID}/act",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "screenshot": _screenshot(too_large),
            "previous_intent_id": None,
            "previous_result": None,
        },
    )
    assert rejected.status_code == 413
    assert second_factory.hosts[0].get(EPISODE_ID).revision == 0
    assert len(_screenshot(too_large)["image_base64"]) <= MAX_ENCODED_IMAGE_CHARS


def test_intent_errors_are_stable_identified_and_do_not_expose_host_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, factory, _ = _app(tmp_path)
    created = client.post("/api/v2/episodes", json=_create_body())
    ident = created.json()["identity"]
    screen = _png()

    first = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/act",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "screenshot": _screenshot(screen),
            "previous_intent_id": None,
            "previous_result": None,
        },
    )
    wrong = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/act",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "screenshot": _screenshot(screen),
            "previous_intent_id": "intent-" + "9" * 32,
            "previous_result": {
                "reward": 0.0,
                "terminated": False,
                "truncated": False,
                "screenshot_sha256": "sha256:" + sha256_bytes(screen),
            },
        },
    )
    assert wrong.status_code == 409
    assert wrong.json()["detail"]["code"] == "intent_reference_invalid"
    _assert_identity(wrong, ident)

    def sensitive_failure(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ServingEpisodeError("secret checkpoint and raw provider response")

    monkeypatch.setattr(factory.hosts[0], "act", sensitive_failure)
    failed = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/act",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "screenshot": _screenshot(screen),
            "previous_intent_id": first.json()["intent_id"],
            "previous_result": {
                "reward": 0.0,
                "terminated": False,
                "truncated": False,
                "screenshot_sha256": "sha256:" + sha256_bytes(screen),
            },
        },
    )
    assert failed.status_code == 503
    assert failed.json() == {
        "detail": {
            "code": "episode_host_unavailable",
            "message": "episode host is unavailable",
        }
    }
    assert "secret" not in failed.text
    assert "checkpoint" not in failed.text
    _assert_identity(failed, ident)


def test_close_requires_outstanding_intent_and_act_after_close_is_rejected(
    tmp_path: Path,
) -> None:
    client, _, records = _app(tmp_path)
    created = client.post("/api/v2/episodes", json=_create_body())
    ident = created.json()["identity"]
    screen = _png()
    first = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/act",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "screenshot": _screenshot(screen),
            "previous_intent_id": None,
            "previous_result": None,
        },
    )

    wrong_close = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/close",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "final_screenshot": _screenshot(screen),
            "final_intent_id": "intent-" + "9" * 32,
            "final_result": {
                "reward": 0.0,
                "terminated": False,
                "truncated": False,
                "screenshot_sha256": "sha256:" + sha256_bytes(screen),
            },
        },
    )
    assert wrong_close.status_code == 409
    assert records.screenshots == {}

    wrong_digest = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/close",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "final_screenshot": _screenshot(screen),
            "final_intent_id": first.json()["intent_id"],
            "final_result": {
                "reward": 0.0,
                "terminated": False,
                "truncated": False,
                "screenshot_sha256": "sha256:" + "0" * 64,
            },
        },
    )
    assert wrong_digest.status_code == 409
    assert records.screenshots == {}

    close_body: dict[str, Any] = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "final_screenshot": _screenshot(screen),
        "final_intent_id": first.json()["intent_id"],
        "final_result": {
            "reward": 0.0,
            "terminated": False,
            "truncated": False,
            "screenshot_sha256": "sha256:" + sha256_bytes(screen),
        },
    }
    closed = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/close",
        json=close_body,
    )
    assert closed.status_code == 200
    assert closed.json()["terminal_classification"] == "closed_by_caller"
    assert len(records.screenshots) == 1

    retried = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/close",
        json=close_body,
    )
    assert retried.status_code == 200
    assert retried.json() == closed.json()
    assert len(records.screenshots) == 1

    different_screen = screen + b"\0"
    different_retry = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/close",
        json={
            **close_body,
            "final_screenshot": _screenshot(different_screen),
            "final_result": {
                **close_body["final_result"],
                "screenshot_sha256": "sha256:" + sha256_bytes(different_screen),
            },
        },
    )
    assert different_retry.status_code == 409
    assert different_retry.json()["detail"]["code"] == "episode_ended"
    assert len(records.screenshots) == 1

    after_close = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/act",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "screenshot": _screenshot(screen),
            "previous_intent_id": first.json()["intent_id"],
            "previous_result": {
                "reward": 0.0,
                "terminated": False,
                "truncated": False,
                "screenshot_sha256": "sha256:" + sha256_bytes(screen),
            },
        },
    )
    assert after_close.status_code == 409
    assert after_close.json()["detail"]["code"] == "episode_ended"
    _assert_identity(after_close, ident)


def test_missing_required_null_fields_and_non_float_reward_are_rejected(
    tmp_path: Path,
) -> None:
    client, _, _ = _app(tmp_path)
    created = client.post("/api/v2/episodes", json=_create_body())
    assert created.status_code == 201
    ident = created.json()["identity"]
    screen = _png()

    missing = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/act",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "screenshot": _screenshot(screen),
        },
    )
    assert missing.status_code == 422
    _assert_identity(missing, ident)

    integer_reward = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/close",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "final_screenshot": _screenshot(screen),
            "final_intent_id": "intent-" + "1" * 32,
            "final_result": {
                "reward": 0,
                "terminated": False,
                "truncated": False,
                "screenshot_sha256": "sha256:" + sha256_bytes(screen),
            },
        },
    )
    assert integer_reward.status_code == 422
    _assert_identity(integer_reward, ident)


def test_immutable_episode_log_verifies_put_once_records_and_final_screenshot(
    tmp_path: Path,
) -> None:
    client_factory = FactoryProbe(tmp_path / "hosts")
    store = LocalImmutableStore(tmp_path / "immutable")
    log = ImmutableEpisodeOperationalLog(store)
    router = create_episode_router(
        host_factory=client_factory,
        session_registry=MemoryEpisodeSessionRegistry(),
        operational_log=log,
    )
    client = TestClient(
        create_serving_app(
            PolicyRuntime(),
            operational_log=MemoryOperationalLog(),
            episode_router=router,
        )
    )
    assert client.post("/api/v2/episodes", json=_create_body()).status_code == 201
    screen = _png()
    acted = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/act",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "screenshot": _screenshot(screen),
            "previous_intent_id": None,
            "previous_result": None,
        },
    )
    closed = client.post(
        f"/api/v2/episodes/{EPISODE_ID}/close",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "final_screenshot": _screenshot(screen),
            "final_intent_id": acted.json()["intent_id"],
            "final_result": {
                "reward": 0.0,
                "terminated": False,
                "truncated": True,
                "screenshot_sha256": "sha256:" + sha256_bytes(screen),
            },
        },
    )
    assert closed.status_code == 200

    for key in (
        f"serving-episode-records/{EPISODE_ID}/opened.json",
        f"serving-episode-records/{EPISODE_ID}/steps/0000.json",
        f"serving-episode-records/{EPISODE_ID}/closed.json",
    ):
        reference = store.get_reference(key)
        assert reference is not None
        assert store.get_verified(reference).endswith(b"\n")
    screenshot_keys = tuple((tmp_path / "immutable" / "metadata").rglob("*.png.metadata.json"))
    assert len(screenshot_keys) == 1
