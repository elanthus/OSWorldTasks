from __future__ import annotations

import asyncio
import base64
import dataclasses
import importlib
import io
import json
import logging
import re
import secrets
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.requests import Request

from pixelgym.platform.contracts import ArtifactRef
from pixelgym.platform.control_store import ControlStore, TransitionError, VerifiedPrincipal
from pixelgym.platform.deployment_smoke import DeploymentSmokeError
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.immutable_store import ImmutableStoreError, LocalImmutableStore
from pixelgym.platform.mlflow_tracking import TrackingRunView
from pixelgym.platform.operational_log import (
    OPERATIONAL_RECORD_SCHEMA_VERSION,
    ImmutableOperationalLog,
    MemoryOperationalLog,
    OperationalLogError,
    OperationalRecord,
    normalize_provider_metadata,
)
from pixelgym.platform.policy import build_policy_manifest, prompt_template
from pixelgym.platform.service import (
    API_SCHEMA_VERSION,
    MAX_ENCODED_IMAGE_CHARS,
    MAX_REQUEST_BODY_BYTES,
    LoadedPolicy,
    PolicyRuntime,
    ProviderFailure,
    _operational_context,
    create_serving_app,
)
from pixelgym.platform.source_provenance import SOURCE_PROVENANCE_SCHEMA_VERSION, SourceProvenance
from pixelgym.platform.web import create_control_app
from pixelgym.platform.web.app import DEPLOYMENT_AUDIT_WINDOW, RUNS_PAGE_SIZE
from scripts.capture_platform_api import _safe_body
from scripts.export_platform_evidence import export_evidence


def _image(width: int = 100, height: int = 80, image_format: str = "PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format=image_format)
    return output.getvalue()


class ServingFake:
    def __init__(
        self,
        raw: str = '{"x":20,"y":30}',
        failure: str | None = None,
        *,
        provider_request_id: object = "request-1",
        latency_ms: object = 5.0,
        usage: object = {"input_tokens": 1},
    ) -> None:
        self.raw = raw
        self.failure = failure
        self.provider_request_id = provider_request_id
        self.latency_ms = latency_ms
        self.usage = usage
        self.calls = 0

    def ground(self, **request):
        self.calls += 1
        if self.failure:
            raise ProviderFailure(self.failure, "private provider detail")
        return self.raw, self.provider_request_id, self.latency_ms, self.usage


class BlockingServingFake(ServingFake):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def ground(self, **request):
        self.started.set()
        assert self.release.wait(timeout=1)
        return super().ground(**request)


def _loaded(policy, provider) -> LoadedPolicy:
    return LoadedPolicy(policy, "deployment-1", "candidate-1", provider)


def _serving_app(runtime: PolicyRuntime):
    return create_serving_app(runtime, operational_log=MemoryOperationalLog())


def test_serving_contract_and_identity_headers(policy_factory) -> None:
    provider = ServingFake()
    client = TestClient(_serving_app(PolicyRuntime(_loaded(policy_factory(), provider))))
    response = client.post(
        "/api/v1/ground",
        json={
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "image/png",
            "target": "Click the Company name field",
        },
    )
    assert response.status_code == 200
    assert response.json()["schema_version"] == API_SCHEMA_VERSION
    assert response.json()["prediction"] == {"x": 20, "y": 30}
    assert response.headers["x-pixelgym-policy-id"] == policy_factory().policy_id
    assert response.headers["x-pixelgym-deployment-id"] == "deployment-1"
    assert provider.calls == 1


def test_operational_record_is_redacted_immutable_and_identifies_served_policy(
    policy_factory,
) -> None:
    provider = ServingFake(usage={"input_tokens": 1, "output_tokens": 0})
    log = MemoryOperationalLog()
    policy = policy_factory()
    response = TestClient(
        create_serving_app(PolicyRuntime(_loaded(policy, provider)), operational_log=log)
    ).post(
        "/api/v1/ground",
        json={
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "image/png",
            "target": "private target must not be retained",
        },
    )
    request_id = response.headers["x-pixelgym-request-id"]
    record = log.get(request_id)
    assert record is not None
    assert record.policy_id == policy.policy_id
    assert record.deployment_id == "deployment-1"
    assert record.exact_policy_version == "candidate-1"
    assert record.terminal_status == "completed"
    assert record.http_status == 200
    assert record.provider_request_id == "request-1"
    assert record.usage == {"input_tokens": 1, "output_tokens": 0}
    assert record.latency_ms >= 0 and record.occurred_at.endswith("+00:00")
    assert "target" not in record.to_dict() and "image" not in record.to_dict()
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.terminal_status = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record.usage["input_tokens"] = 99  # type: ignore[index]


def test_operational_log_distinguishes_absent_usage_and_failure_classes(policy_factory) -> None:
    payload = {
        "image_base64": base64.b64encode(_image()).decode(),
        "media_type": "image/png",
        "target": "target",
    }
    no_usage_log = MemoryOperationalLog()
    no_usage = TestClient(
        create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake(usage=None))),
            operational_log=no_usage_log,
        )
    ).post("/api/v1/ground", json=payload)
    assert no_usage_log.get(no_usage.headers["x-pixelgym-request-id"]).usage is None

    bad_metadata_log = MemoryOperationalLog()
    malformed = TestClient(
        create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake(latency_ms=float("nan")))),
            operational_log=bad_metadata_log,
        )
    ).post("/api/v1/ground", json=payload)
    malformed_record = bad_metadata_log.get(malformed.headers["x-pixelgym-request-id"])
    assert malformed.status_code == 502
    assert (
        malformed_record is not None
        and malformed_record.terminal_status == "provider_metadata_invalid"
    )

    failed_log = MemoryOperationalLog()
    failed = TestClient(
        create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake(failure="timeout"))),
            operational_log=failed_log,
        )
    ).post("/api/v1/ground", json=payload)
    failed_record = failed_log.get(failed.headers["x-pixelgym-request-id"])
    assert failed.status_code == 504
    assert failed_record is not None and failed_record.terminal_status == "provider_timeout"


@pytest.mark.parametrize(
    ("request_id", "latency_ms", "usage"),
    [
        ("", 1.0, {}),
        ("request-1", True, {}),
        ("request-1", -1.0, {}),
        ("request-1", 1.0, []),
        ("request-1", 1.0, {"InvalidKey": 1}),
        ("request-1", 1.0, {"input_tokens": True}),
    ],
)
def test_provider_metadata_normalizer_rejects_malformed_values(
    request_id: object, latency_ms: object, usage: object
) -> None:
    with pytest.raises(ValueError, match="provider"):
        normalize_provider_metadata(request_id, latency_ms, usage)


def test_provider_metadata_replace_revalidates_and_remains_immutable() -> None:
    metadata = normalize_provider_metadata("request-1", 1, {"input_tokens": 1})

    changed = dataclasses.replace(metadata, latency_ms=2)

    assert changed.latency_ms == 2.0
    assert changed.usage == {"input_tokens": 1}
    with pytest.raises(ValueError, match="request ID"):
        dataclasses.replace(metadata, request_id="")
    with pytest.raises(TypeError):
        changed.usage["input_tokens"] = 2  # type: ignore[index]


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_request_id": ""},
        {"provider_latency_ms": float("nan")},
        {"usage": {"InvalidKey": 1}},
        {"provider_request_id": None, "provider_latency_ms": 1.0},
    ],
)
def test_operational_record_read_boundary_rejects_malformed_provider_metadata(
    changes: dict[str, object],
) -> None:
    payload = {
        "schema_version": OPERATIONAL_RECORD_SCHEMA_VERSION,
        "request_id": "srv-" + "1" * 32,
        "occurred_at": "2026-08-15T00:00:00+00:00",
        "policy_id": "policy",
        "deployment_id": "deployment",
        "exact_policy_version": "candidate",
        "terminal_status": "completed",
        "http_status": 200,
        "latency_ms": 1.0,
        "provider_latency_ms": 1.0,
        "provider_request_id": "request-1",
        "usage": {"input_tokens": 1},
        **changes,
    }

    with pytest.raises(ValueError, match="provider"):
        OperationalRecord.from_dict(payload)


def test_operational_record_read_boundary_names_missing_provider_request_id() -> None:
    payload = {
        "schema_version": OPERATIONAL_RECORD_SCHEMA_VERSION,
        "request_id": "srv-" + "1" * 32,
        "occurred_at": "2026-08-15T00:00:00+00:00",
        "policy_id": "policy",
        "deployment_id": "deployment",
        "exact_policy_version": "candidate",
        "terminal_status": "completed",
        "http_status": 200,
        "latency_ms": 1.0,
        "provider_latency_ms": None,
        "usage": None,
    }

    with pytest.raises(ValueError, match="missing required field: provider_request_id"):
        OperationalRecord.from_dict(payload)


def test_immutable_operational_log_surfaces_missing_provider_request_id() -> None:
    class StoredRecordWithoutProviderRequestId:
        def get_reference(self, logical_key):
            return object()

        def get_verified(self, reference):
            return b'{"provider_latency_ms":null,"usage":null}'

    log = ImmutableOperationalLog(StoredRecordWithoutProviderRequestId())

    with pytest.raises(OperationalLogError, match="missing required field: provider_request_id"):
        log.get("srv-" + "1" * 32)


def test_operational_log_captures_rejected_and_invalid_output_requests(policy_factory) -> None:
    log = MemoryOperationalLog()
    client = TestClient(
        create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake("not-json"))), operational_log=log
        )
    )
    invalid_input = client.post(
        "/api/v1/ground",
        json={"image_base64": "bad", "media_type": "image/png", "target": "target"},
    )
    rejected = log.get(invalid_input.headers["x-pixelgym-request-id"])
    assert invalid_input.status_code == 400
    assert rejected is not None and rejected.terminal_status == "request_rejected"
    assert rejected.policy_id == policy_factory().policy_id

    invalid_output = client.post(
        "/api/v1/ground",
        json={
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "image/png",
            "target": "target",
        },
    )
    record = log.get(invalid_output.headers["x-pixelgym-request-id"])
    assert invalid_output.status_code == 200
    assert record is not None and record.terminal_status == "invalid_output"


def test_immutable_operational_log_retrieves_verified_record_and_detects_tampering(
    tmp_path: Path, policy_factory
) -> None:
    log = ImmutableOperationalLog(LocalImmutableStore(tmp_path / "immutable"))
    client = TestClient(
        create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake())), operational_log=log
        )
    )
    response = client.post(
        "/api/v1/ground",
        json={
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "image/png",
            "target": "target",
        },
    )
    request_id = response.headers["x-pixelgym-request-id"]
    assert log.get(request_id) is not None
    record_path = (
        tmp_path / "immutable" / "objects" / "serving-operational-records" / f"{request_id}.json"
    )
    record_path.write_text("{}\n")
    with pytest.raises(OperationalLogError, match="retrieve verified"):
        log.get(request_id)


def test_unavailable_operational_storage_fails_closed(policy_factory) -> None:
    class FailingLog:
        def append(self, record) -> None:
            raise OperationalLogError("unavailable")

        def get(self, request_id):
            return None

    response = TestClient(
        create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake())), operational_log=FailingLog()
        )
    ).post(
        "/api/v1/ground",
        json={
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "image/png",
            "target": "target",
        },
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "serving audit storage is unavailable"}


def test_unexpected_handler_failure_logs_redacted_traceback(policy_factory, caplog) -> None:
    class ExplodingProvider(ServingFake):
        def ground(self, **request):
            raise RuntimeError(f"private provider and request payload: {request['target']}")

    secret_target = "redaction-contract-token-7f0f"
    with caplog.at_level(logging.ERROR, logger="pixelgym.platform.service"):
        response = TestClient(
            create_serving_app(
                PolicyRuntime(_loaded(policy_factory(), ExplodingProvider())),
                operational_log=MemoryOperationalLog(),
            )
        ).post(
            "/api/v1/ground",
            json={
                "image_base64": base64.b64encode(_image()).decode(),
                "media_type": "image/png",
                "target": secret_target,
            },
        )
    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    assert secret_target not in response.text
    assert "type=RuntimeError" in caplog.text
    assert "test_service_web.py" in caplog.text
    assert "private provider and request payload" not in caplog.text
    assert secret_target not in caplog.text


def test_cancelled_request_is_audited_without_masking_cancellation(policy_factory) -> None:
    class CapturingLog:
        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail
            self.records = []

        def append(self, record) -> None:
            self.records.append(record)
            if self.fail:
                raise OperationalLogError("unavailable")

        def get(self, request_id):
            return None

    def middleware_for(log: CapturingLog):
        app = create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake())), operational_log=log
        )
        return app.user_middleware[0].kwargs["dispatch"]

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/ground",
            "raw_path": b"/api/v1/ground",
            "query_string": b"",
            "headers": [(b"host", b"test")],
            "client": ("test", 1234),
            "server": ("test", 80),
        }
    )

    async def cancelled_call_next(_: Request):
        raise asyncio.CancelledError()

    for failing_audit in (False, True):
        log = CapturingLog(fail=failing_audit)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(middleware_for(log)(request, cancelled_call_next))
        assert len(log.records) == 1
        record = log.records[0]
        assert record.terminal_status == "cancelled"
        assert record.http_status == 499
        assert _operational_context.get() is None


def test_missing_response_raises_after_auditing_and_context_cleanup(policy_factory) -> None:
    class CapturingLog:
        def __init__(self) -> None:
            self.records = []

        def append(self, record) -> None:
            self.records.append(record)

        def get(self, request_id):
            return None

    app = create_serving_app(
        PolicyRuntime(_loaded(policy_factory(), ServingFake())), operational_log=CapturingLog()
    )
    middleware = app.user_middleware[0].kwargs["dispatch"]
    log = app.state.operational_log
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/ground",
            "raw_path": b"/api/v1/ground",
            "query_string": b"",
            "headers": [(b"host", b"test")],
            "client": ("test", 1234),
            "server": ("test", 80),
        }
    )

    async def missing_response(_: Request):
        return None  # Exercise the defensive middleware boundary.

    with pytest.raises(RuntimeError, match="serving handler returned no response"):
        asyncio.run(middleware(request, missing_response))
    assert len(log.records) == 1
    assert log.records[0].http_status == 499
    assert _operational_context.get() is None


def test_cancelled_in_flight_request_completes_audit_through_base_http_middleware(
    policy_factory,
) -> None:
    async def exercise() -> tuple[MemoryOperationalLog, asyncio.CancelledError]:
        provider = BlockingServingFake()
        log = MemoryOperationalLog()
        app = create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), provider)), operational_log=log
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            request = asyncio.create_task(
                client.post(
                    "/api/v1/ground",
                    json={
                        "image_base64": base64.b64encode(_image()).decode(),
                        "media_type": "image/png",
                        "target": "target",
                    },
                )
            )
            assert await asyncio.to_thread(provider.started.wait, 1)
            request.cancel()
            provider.release.set()
            with pytest.raises(asyncio.CancelledError) as cancelled:
                await request
        return log, cancelled.value

    log, cancelled = asyncio.run(exercise())
    assert isinstance(cancelled, asyncio.CancelledError)
    assert len(log._records) == 1
    record = next(iter(log._records.values()))
    assert record.terminal_status == "cancelled"
    assert record.http_status == 499


def test_operational_append_runs_off_the_event_loop_and_keeps_request_context(
    policy_factory,
) -> None:
    class ContextInspectingLog:
        def __init__(self, event_loop_thread: int) -> None:
            self.event_loop_thread = event_loop_thread
            self.append_thread: int | None = None
            self.context_request_id: str | None = None

        def append(self, record) -> None:
            self.append_thread = threading.get_ident()
            context = _operational_context.get()
            self.context_request_id = context.request_id if context is not None else None

        def get(self, request_id):
            return None

    async def exercise() -> tuple[ContextInspectingLog, httpx.Response, int]:
        event_loop_thread = threading.get_ident()
        log = ContextInspectingLog(event_loop_thread)
        app = create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake())), operational_log=log
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/ground",
                json={
                    "image_base64": base64.b64encode(_image()).decode(),
                    "media_type": "image/png",
                    "target": "target",
                },
            )
        return log, response, event_loop_thread

    log, response, event_loop_thread = asyncio.run(exercise())
    assert response.status_code == 200
    assert log.append_thread is not None and log.append_thread != event_loop_thread
    assert log.context_request_id == response.headers["x-pixelgym-request-id"]


def test_operational_audit_bulkhead_limits_blocking_appends(policy_factory, monkeypatch) -> None:
    class BlockingLog:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()
            self._lock = threading.Lock()
            self.active = 0
            self.maximum_active = 0
            self.records = []

        def append(self, record) -> None:
            with self._lock:
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
                self.entered.set()
            assert self.release.wait(timeout=1)
            with self._lock:
                self.records.append(record)
                self.active -= 1

        def get(self, request_id):
            return None

    async def exercise() -> tuple[list[httpx.Response], BlockingLog]:
        log = BlockingLog()
        second_audit_attempted = asyncio.Event()
        audit_attempts = 0
        original_run_sync = anyio.to_thread.run_sync

        async def observed_run_sync(function, *args, **kwargs):
            nonlocal audit_attempts
            if function == log.append:
                audit_attempts += 1
                if audit_attempts == 2:
                    second_audit_attempted.set()
            return await original_run_sync(function, *args, **kwargs)

        monkeypatch.setattr(anyio.to_thread, "run_sync", observed_run_sync)
        app = create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake())),
            operational_log=log,
            operational_audit_concurrency=1,
        )
        transport = httpx.ASGITransport(app=app)
        payload = {
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "image/png",
            "target": "target",
        }
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.post("/api/v1/ground", json=payload))
            assert await asyncio.to_thread(log.entered.wait, 1)
            second = asyncio.create_task(client.post("/api/v1/ground", json=payload))
            await asyncio.wait_for(second_audit_attempted.wait(), timeout=1)
            assert log.maximum_active == 1 and not second.done()
            log.release.set()
            return await asyncio.gather(first, second), log

    responses, log = asyncio.run(exercise())
    assert [response.status_code for response in responses] == [200, 200]
    assert log.maximum_active == 1
    assert len(log.records) == 2
    assert len({record.request_id for record in log.records}) == 2


def test_operational_audit_bulkhead_requires_positive_limit(policy_factory) -> None:
    with pytest.raises(ValueError, match="concurrency"):
        create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake())),
            operational_log=MemoryOperationalLog(),
            operational_audit_concurrency=0,
        )


def test_immutable_operational_log_does_not_serialize_distinct_request_writes(
    tmp_path: Path,
) -> None:
    class RendezvousStore:
        def __init__(self) -> None:
            self.delegate = LocalImmutableStore(tmp_path / "immutable")
            self.put_barrier = threading.Barrier(2)

        def put_once(self, logical_key, data, *, media_type):
            self.put_barrier.wait(timeout=1)
            return self.delegate.put_once(logical_key, data, media_type=media_type)

        def get_verified(self, reference):
            return self.delegate.get_verified(reference)

        def get_reference(self, logical_key):
            return self.delegate.get_reference(logical_key)

    def record(index: int) -> OperationalRecord:
        return OperationalRecord(
            schema_version=OPERATIONAL_RECORD_SCHEMA_VERSION,
            request_id=f"srv-{index:032x}",
            occurred_at="2026-08-15T00:00:00+00:00",
            policy_id="policy",
            deployment_id="deployment",
            exact_policy_version="candidate",
            terminal_status="completed",
            http_status=200,
            latency_ms=1.0,
            provider_metadata=None,
        )

    log = ImmutableOperationalLog(RendezvousStore())
    errors: list[OperationalLogError] = []

    def append(item: OperationalRecord) -> None:
        try:
            log.append(item)
        except OperationalLogError as exc:  # pragma: no cover - assertion below checks this path.
            errors.append(exc)

    first = threading.Thread(target=append, args=(record(1),))
    second = threading.Thread(target=append, args=(record(2),))
    first.start()
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive() and not second.is_alive()
    assert not errors


def test_no_active_deployment_is_operationally_recorded() -> None:
    log = MemoryOperationalLog()
    response = TestClient(create_serving_app(PolicyRuntime(), operational_log=log)).post(
        "/api/v1/ground",
        json={
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "image/png",
            "target": "target",
        },
    )
    record = log.get(response.headers["x-pixelgym-request-id"])
    assert response.status_code == 503
    assert record is not None and record.terminal_status == "no_active_deployment"
    assert record.policy_id is None and record.deployment_id is None


@pytest.mark.parametrize(
    "payload",
    [
        {"image_base64": "not-base64", "media_type": "image/png", "target": "target"},
        {
            "image_base64": base64.b64encode(b"not-image").decode(),
            "media_type": "image/png",
            "target": "target",
        },
        {
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "text/plain",
            "target": "target",
        },
        {
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "image/png",
            "target": "   ",
        },
        {
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "image/png",
            "target": "target",
            "extra": "forbidden",
        },
    ],
)
def test_invalid_requests_fail_before_provider(policy_factory, payload: dict) -> None:
    provider = ServingFake()
    client = TestClient(_serving_app(PolicyRuntime(_loaded(policy_factory(), provider))))
    assert client.post("/api/v1/ground", json=payload).status_code in {400, 415, 422}
    assert provider.calls == 0


def test_encoded_image_limit_rejects_before_base64_decode(
    policy_factory, monkeypatch
) -> None:
    from pixelgym.platform import service as service_module

    provider = ServingFake()
    payload = {
        "image_base64": "A" * (MAX_ENCODED_IMAGE_CHARS + 1),
        "media_type": "image/png",
        "target": "target",
    }
    decode_called = False

    def unexpected_decode(*args, **kwargs):
        nonlocal decode_called
        decode_called = True
        raise AssertionError("oversized encoded input must not be decoded")

    monkeypatch.setattr(service_module.base64, "b64decode", unexpected_decode)
    client = TestClient(_serving_app(PolicyRuntime(_loaded(policy_factory(), provider))))

    response = client.post("/api/v1/ground", json=payload)

    assert response.status_code == 422
    assert decode_called is False
    assert provider.calls == 0


def test_declared_oversized_body_is_rejected_before_json_parsing(policy_factory) -> None:
    provider = ServingFake()
    log = MemoryOperationalLog()
    client = TestClient(
        create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), provider)), operational_log=log
        )
    )

    response = client.post(
        "/api/v1/ground",
        content=b"{}",
        headers={
            "content-type": "application/json",
            "content-length": str(MAX_REQUEST_BODY_BYTES + 1),
        },
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "request body exceeds the byte limit"}
    assert provider.calls == 0
    record = log.get(response.headers["x-pixelgym-request-id"])
    assert record is not None and record.terminal_status == "request_rejected"


def test_streamed_body_limit_does_not_require_content_length(policy_factory) -> None:
    async def exercise() -> tuple[httpx.Response, ServingFake]:
        provider = ServingFake()
        app = _serving_app(PolicyRuntime(_loaded(policy_factory(), provider)))
        transport = httpx.ASGITransport(app=app)
        shared_chunk = b"x" * (1024 * 1024)

        async def chunks():
            yield b'{"image_base64":"'
            for _ in range(8):
                yield shared_chunk
            yield b'","media_type":"image/png","target":"target"}'

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/ground",
                content=chunks(),
                headers={"content-type": "application/json"},
            )
        return response, provider

    response, provider = asyncio.run(exercise())

    assert response.status_code == 413
    assert provider.calls == 0


def test_bad_dimensions_and_media_mismatch_fail_before_provider(policy_factory) -> None:
    provider = ServingFake()
    client = TestClient(_serving_app(PolicyRuntime(_loaded(policy_factory(), provider))))
    too_wide = client.post(
        "/api/v1/ground",
        json={
            "image_base64": base64.b64encode(_image(4097, 1)).decode(),
            "media_type": "image/png",
            "target": "target",
        },
    )
    mismatch = client.post(
        "/api/v1/ground",
        json={
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "image/jpeg",
            "target": "target",
        },
    )
    assert too_wide.status_code == 400 and mismatch.status_code == 415
    assert provider.calls == 0


def test_parser_and_provider_failures_are_explicit_without_retry(policy_factory) -> None:
    invalid = ServingFake("not-json")
    client = TestClient(_serving_app(PolicyRuntime(_loaded(policy_factory(), invalid))))
    payload = {
        "image_base64": base64.b64encode(_image()).decode(),
        "media_type": "image/png",
        "target": "target",
    }
    response = client.post("/api/v1/ground", json=payload)
    assert response.status_code == 200 and response.json()["parse_status"] == "invalid"
    assert invalid.calls == 1
    timeout = ServingFake(failure="timeout")
    response = TestClient(_serving_app(PolicyRuntime(_loaded(policy_factory(), timeout)))).post(
        "/api/v1/ground", json=payload
    )
    assert response.status_code == 504
    assert "private provider detail" not in response.text
    assert timeout.calls == 1


def test_no_active_policy_is_not_ready() -> None:
    client = TestClient(_serving_app(PolicyRuntime()))
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503


def test_bootstrap_import_is_side_effect_free_and_factory_uses_explicit_migration(
    tmp_path: Path, repository_root: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "PIXELGYM_REPOSITORY_ROOT",
        "PIXELGYM_CONTROL_DB",
        "PIXELGYM_IMMUTABLE_ROOT",
        "PIXELGYM_IMMUTABLE_BUCKET",
        "PIXELGYM_CSRF_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    sys.modules.pop("pixelgym.platform.bootstrap", None)
    module = importlib.import_module("pixelgym.platform.bootstrap")
    assert not (tmp_path / ".cache").exists()

    monkeypatch.setenv("PIXELGYM_REPOSITORY_ROOT", str(repository_root))
    monkeypatch.setenv("PIXELGYM_CONTROL_DB", str(tmp_path / "state/control.db"))
    monkeypatch.setenv("PIXELGYM_IMMUTABLE_ROOT", str(tmp_path / "immutable"))
    monkeypatch.setenv("PIXELGYM_CSRF_SECRET", "test-secret-at-least-sixteen")
    (tmp_path / "state").mkdir()
    migrated = ControlStore(tmp_path / "state/control.db", reviewer_identity="local-reviewer")
    migrated.migrate()
    restore_called = False
    original_restore = module.DeploymentCoordinator.restore_active

    def restore_active(coordinator):
        nonlocal restore_called
        restore_called = True
        return original_restore(coordinator)

    monkeypatch.setattr(module.DeploymentCoordinator, "restore_active", restore_active)
    client = TestClient(module.create_app())

    assert restore_called
    assert client.get("/").status_code == 200
    assert client.get("/health/live").status_code == 200
    assert (tmp_path / "state/control.db").is_file()


def test_bootstrap_factory_fails_clearly_before_explicit_migration(
    tmp_path: Path, repository_root: Path, monkeypatch
) -> None:
    from pixelgym.platform import bootstrap

    monkeypatch.setenv("PIXELGYM_REPOSITORY_ROOT", str(repository_root))
    monkeypatch.setenv("PIXELGYM_CONTROL_DB", str(tmp_path / "state/control.db"))
    monkeypatch.setenv("PIXELGYM_CSRF_SECRET", "test-secret-at-least-sixteen")
    monkeypatch.delenv("PIXELGYM_IMMUTABLE_BUCKET", raising=False)

    with pytest.raises(RuntimeError, match="not migrated"):
        bootstrap.create_app()


def test_bootstrap_factory_requires_explicit_csrf_secret(
    tmp_path: Path, repository_root: Path, monkeypatch
) -> None:
    from pixelgym.platform import bootstrap

    monkeypatch.setenv("PIXELGYM_REPOSITORY_ROOT", str(repository_root))
    monkeypatch.setenv("PIXELGYM_CONTROL_DB", str(tmp_path / "control.db"))
    monkeypatch.delenv("PIXELGYM_CSRF_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="CSRF_SECRET"):
        bootstrap.create_app()


def test_bootstrap_tracking_initialization_fails_open_with_reconciliation(
    tmp_path: Path, repository_root: Path, monkeypatch
) -> None:
    from pixelgym.platform import bootstrap

    database = tmp_path / "state/control.db"
    database.parent.mkdir()
    control = ControlStore(database, reviewer_identity="local-reviewer")
    control.migrate()
    monkeypatch.setenv("PIXELGYM_REPOSITORY_ROOT", str(repository_root))
    monkeypatch.setenv("PIXELGYM_CONTROL_DB", str(database))
    monkeypatch.setenv("PIXELGYM_IMMUTABLE_ROOT", str(tmp_path / "immutable"))
    monkeypatch.setenv("PIXELGYM_CSRF_SECRET", "test-secret-at-least-sixteen")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://unavailable.invalid")
    monkeypatch.setattr(
        bootstrap,
        "MlflowTracking",
        lambda _uri: (_ for _ in ()).throw(ConnectionError("tracking unavailable")),
    )

    app = bootstrap.create_app()

    assert TestClient(app).get("/").status_code == 200
    event = control.audit_events()[-1]
    assert event["event_type"] == "tracking.reconciliation_required"
    assert event["details"]["operation"] == "initialize_tracking"


def test_bootstrap_records_background_reconciliation_failure(
    tmp_path: Path, repository_root: Path, monkeypatch
) -> None:
    from pixelgym.platform import bootstrap

    database = tmp_path / "state/control.db"
    database.parent.mkdir()
    control = ControlStore(database, reviewer_identity="local-reviewer")
    control.migrate()
    monkeypatch.setenv("PIXELGYM_REPOSITORY_ROOT", str(repository_root))
    monkeypatch.setenv("PIXELGYM_CONTROL_DB", str(database))
    monkeypatch.setenv("PIXELGYM_IMMUTABLE_ROOT", str(tmp_path / "immutable"))
    monkeypatch.setenv("PIXELGYM_CSRF_SECRET", "test-secret-at-least-sixteen")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://tracking.invalid")
    monkeypatch.setattr(bootstrap, "MlflowTracking", lambda _uri: object())
    monkeypatch.setattr(
        bootstrap.DeploymentCoordinator,
        "reconcile_tracking",
        lambda _coordinator: (_ for _ in ()).throw(ConnectionError("mirror unavailable")),
    )

    app = bootstrap.create_app()
    app.state.tracking_reconciliation_thread.join(timeout=1)

    assert not app.state.tracking_reconciliation_thread.is_alive()
    event = control.audit_events()[-1]
    assert event["event_type"] == "tracking.reconciliation_required"
    assert event["details"]["operation"] == "startup_reconciliation"
    assert event["details"]["error"] == "ConnectionError: mirror unavailable"


def test_worker_registration_and_cancellation_intent_are_atomic(
    tmp_path: Path, repository_root: Path, monkeypatch
) -> None:
    from pixelgym.platform import bootstrap

    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    payload = {
        "dataset": "day3-frozen-v1",
        "prompt_version": "2",
        "model": "day3-replay-revised-v2",
        "condition": "raw",
        "maximum_calls": "100",
        "price_catalog": "pixelgym-demo-prices-v1",
    }
    submission_id = control.submit(payload)
    process_lock = threading.Lock()
    processes = {}
    cancelled = set()
    popen_started = threading.Event()
    release_popen = threading.Event()
    process_waiting = threading.Event()
    release_process = threading.Event()
    cancellation_committed = threading.Event()
    cancellation_recorded = threading.Event()
    cancellation_result: list[bool] = []

    class Process:
        returncode = 0

        def wait(self):
            process_waiting.set()
            assert release_process.wait(timeout=1)
            return self.returncode

    def popen(*args, **kwargs):
        popen_started.set()
        assert release_popen.wait(timeout=1)
        return Process()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", popen)
    worker = threading.Thread(
        target=bootstrap._run_flow,
        args=(repository_root, control, submission_id, payload),
        kwargs={
            "processes": processes,
            "process_lock": process_lock,
            "cancelled_submissions": cancelled,
        },
    )
    worker.start()
    assert popen_started.wait(timeout=1)
    acquired_during_popen = process_lock.acquire(blocking=False)
    if acquired_during_popen:
        process_lock.release()
    assert not acquired_during_popen

    def cancel() -> None:
        control.cancel_submission(
            submission_id, actor="local-reviewer", reason="cancel during worker startup"
        )
        cancellation_committed.set()
        cancellation_result.append(
            bootstrap._record_cancellation_intent(
                control,
                submission_id,
                process_lock=process_lock,
                cancelled_submissions=cancelled,
            )
        )
        cancellation_recorded.set()

    cancellation = threading.Thread(target=cancel)
    cancellation.start()
    assert cancellation_committed.wait(timeout=1)
    assert not cancellation_recorded.is_set()
    release_popen.set()
    assert process_waiting.wait(timeout=1)
    assert cancellation_recorded.wait(timeout=1)
    assert cancellation_result == [True]
    assert submission_id in processes
    assert submission_id in cancelled
    release_process.set()
    worker.join(timeout=1)
    cancellation.join(timeout=1)
    assert not worker.is_alive()
    assert not cancellation.is_alive()
    assert control.get_submission(submission_id)["status"] == "Cancelled"
    assert submission_id not in processes
    assert submission_id not in cancelled
    with pytest.raises(ValueError, match="unknown submission status"):
        control.mark_submission(submission_id, "Cancelled")
    assert not bootstrap._record_cancellation_intent(
        control,
        "submission-missing",
        process_lock=process_lock,
        cancelled_submissions=cancelled,
    )


def test_cancel_waiting_for_process_lock_does_not_block_serving(tmp_path: Path) -> None:
    from pixelgym.platform import bootstrap

    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    payload = {
        "dataset": "day3-frozen-v1",
        "prompt_version": "2",
        "model": "day3-replay-revised-v2",
        "condition": "raw",
        "maximum_calls": "100",
        "price_catalog": "pixelgym-demo-prices-v1",
    }
    submission_id = control.submit(payload)
    process_lock = threading.Lock()
    cancelled_submissions: set[str] = set()
    lock_held = threading.Event()
    release_lock = threading.Event()
    callback_started = threading.Event()
    callback_calls: list[str] = []

    def hold_process_lock() -> None:
        with process_lock:
            lock_held.set()
            assert release_lock.wait(timeout=2)

    def cancel_callback(cancelled_id: str) -> bool:
        callback_calls.append(cancelled_id)
        callback_started.set()
        return bootstrap._record_cancellation_intent(
            control,
            cancelled_id,
            process_lock=process_lock,
            cancelled_submissions=cancelled_submissions,
        )

    app = create_control_app(
        control,
        csrf_secret="test-secret-at-least-sixteen",
        cancel_callback=cancel_callback,
    )
    app.mount(
        "/",
        create_serving_app(
            PolicyRuntime(),
            operational_log=MemoryOperationalLog(),
        ),
    )

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        token = _csrf(client.get(f"/submissions/{submission_id}").text)
        cancellation_form = {"csrf_token": token, "reason": "stop concurrent fixture"}

        def request_cancel() -> httpx.Response:
            return client.post(
                f"/submissions/{submission_id}/cancel",
                data=cancellation_form,
                follow_redirects=False,
            )

        with ThreadPoolExecutor(max_workers=3) as executor:
            holder = executor.submit(hold_process_lock)
            assert lock_held.wait(timeout=1)
            cancellation = executor.submit(request_cancel)
            assert callback_started.wait(timeout=1)
            assert not cancellation.done()
            serving = executor.submit(client.get, "/health/live")
            try:
                serving_response = serving.result(timeout=1)
                assert serving_response.status_code == 200
            finally:
                release_lock.set()

            cancel_response = cancellation.result(timeout=1)
            holder.result(timeout=1)
            assert cancel_response.status_code == 303

        repeated = client.post(
            f"/submissions/{submission_id}/cancel",
            data=cancellation_form,
            follow_redirects=False,
        )

    assert repeated.status_code == 303
    assert callback_calls == [submission_id]
    assert control.get_submission(submission_id)["status"] == "Cancelled"
    cancellation_events = [
        event for event in control.audit_events() if event["event_type"] == "submission.cancelled"
    ]
    assert len(cancellation_events) == 1
    assert cancellation_events[0]["subject_id"] == submission_id
    assert cancellation_events[0]["details"] == {
        "previous_status": "Submitted",
        "reason": "stop concurrent fixture",
    }


def test_serving_bootstrap_disables_s3_retries_only_for_bounded_audit_writes(
    tmp_path: Path, monkeypatch
) -> None:
    from pixelgym.platform import bootstrap

    captured: dict[str, object] = {}

    class CapturedS3Store:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setenv("PIXELGYM_IMMUTABLE_BUCKET", "immutable")
    monkeypatch.setattr(bootstrap, "S3ImmutableStore", CapturedS3Store)

    bootstrap._build_immutable_store(tmp_path)

    assert captured["retry_max_attempts"] == 1


def _assembled_platform_app(tmp_path: Path, repository_root: Path, monkeypatch):
    from pixelgym.platform import bootstrap

    database = tmp_path / "state/control.db"
    database.parent.mkdir()
    control = ControlStore(database, reviewer_identity="local-reviewer")
    control.migrate()
    monkeypatch.setenv("PIXELGYM_REPOSITORY_ROOT", str(repository_root))
    monkeypatch.setenv("PIXELGYM_CONTROL_DB", str(database))
    monkeypatch.setenv("PIXELGYM_IMMUTABLE_ROOT", str(tmp_path / "immutable"))
    monkeypatch.setenv("PIXELGYM_CSRF_SECRET", "test-secret-at-least-sixteen")
    return bootstrap.create_app(), control


def _approved_candidate(control: ControlStore, policy, summary, report):
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )
    control.approve(
        candidate.candidate_id,
        actor="local-reviewer",
        reason="reviewed",
        gate_report_sha256=candidate.gate_report_sha256,
    )
    return candidate


def test_assembled_app_deploys_only_the_isolated_smoke_tested_runtime(
    tmp_path: Path, repository_root: Path, monkeypatch, passing_evidence, policy_factory
) -> None:
    app, control = _assembled_platform_app(tmp_path, repository_root, monkeypatch)
    policy, summary, report = passing_evidence
    candidate = _approved_candidate(control, policy, summary, report)

    deployed = app.state.deployment_coordinator.deploy(
        candidate.candidate_id, actor=VerifiedPrincipal("synthetic-demo"), reason="first deploy"
    )

    runtime = app.state.policy_runtime.loaded
    assert runtime is not None
    assert runtime.manifest.policy_id == candidate.policy.policy_id
    assert runtime.deployment_id == deployed.deployment_id
    assert runtime.exact_policy_version == candidate.candidate_id
    response = TestClient(app).get("/api/v1/policy")
    assert response.json()["policy_id"] == candidate.policy.policy_id
    assert response.json()["deployment_id"] == deployed.deployment_id

    rollback_policy = policy_factory(1)
    rollback_summary = dataclasses.replace(
        summary, run_id="run-rollback", policy_id=rollback_policy.policy_id
    )
    rollback_report = dataclasses.replace(
        report, run_id="run-rollback", policy_id=rollback_policy.policy_id
    )
    rollback_candidate = _approved_candidate(
        control, rollback_policy, rollback_summary, rollback_report
    )
    second = app.state.deployment_coordinator.deploy(
        rollback_candidate.candidate_id,
        actor=VerifiedPrincipal("synthetic-demo"),
        reason="second deploy",
    )
    restored = app.state.deployment_coordinator.rollback(
        actor=VerifiedPrincipal("synthetic-demo"), reason="deterministic rollback"
    )
    runtime = app.state.policy_runtime.loaded
    assert runtime is not None
    assert restored.candidate_id == candidate.candidate_id
    assert runtime.manifest.policy_id == candidate.policy.policy_id
    assert runtime.deployment_id == restored.deployment_id
    assert second.deployment_id != restored.deployment_id
    # The rejected source remains in append-only history but is not eligible again.
    deployment_page = TestClient(app).get("/deployment")
    assert "Rollback to previous approved version" not in deployment_page.text
    with pytest.raises(TransitionError, match="no eligible known-good"):
        app.state.deployment_coordinator.rollback(
            actor=VerifiedPrincipal("synthetic-demo"), reason="repeat deterministic rollback"
        )


@pytest.mark.parametrize("failure", ["provider", "invalid-output", "identity"])
def test_assembled_app_pre_activation_failures_preserve_active_pointer_and_runtime(
    tmp_path: Path, repository_root: Path, monkeypatch, passing_evidence, failure: str
) -> None:
    app, control = _assembled_platform_app(tmp_path, repository_root, monkeypatch)
    policy, summary, report = passing_evidence
    first = _approved_candidate(control, policy, summary, report)
    active = app.state.deployment_coordinator.deploy(
        first.candidate_id, actor=VerifiedPrincipal("synthetic-demo"), reason="first deploy"
    )
    # Rebuild a valid, distinct policy instead of mutating identity fields.
    from pixelgym.platform.policy import build_policy_manifest, prompt_template

    prompt_version = 3 if failure == "identity" else policy.prompt_version
    prompt = (
        "identity-mismatch fixture"
        if failure == "identity"
        else prompt_template(prompt_version) + " second"
    )
    second_policy = build_policy_manifest(
        provider=policy.provider,
        model=policy.model + "-second",
        prompt_name=policy.prompt_name,
        prompt_version=prompt_version,
        prompt=prompt,
        condition=policy.condition,
        parameters=policy.parameters,
        parser_version=policy.parser_version,
        scorer_version=policy.scorer_version,
        overlay_version=policy.overlay_version,
        target_semantics=policy.target_semantics,
        source_provenance=__import__(
            "pixelgym.platform.source_provenance", fromlist=["SourceProvenance"]
        ).SourceProvenance(
            "pixelgym-source-provenance-v1",
            policy.code_revision,
            policy.source_tree_sha256,
            policy.code_state,
            "git-build-inputs-v1",
        ),
        dependency_lock_sha256=policy.dependency_lock_sha256,
    )
    second_summary = dataclasses.replace(summary, run_id="run-2", policy_id=second_policy.policy_id)
    second_report = dataclasses.replace(report, run_id="run-2", policy_id=second_policy.policy_id)
    second = _approved_candidate(control, second_policy, second_summary, second_report)
    smoke = app.state.deployment_coordinator.load_and_smoke
    if failure == "provider":
        smoke.provider = ServingFake(failure="timeout")
    elif failure == "invalid-output":
        smoke.provider = ServingFake("not-json")

    with pytest.raises((DeploymentSmokeError, TransitionError)):
        app.state.deployment_coordinator.deploy(
            second.candidate_id,
            actor=VerifiedPrincipal("synthetic-demo"),
            reason="must not activate",
        )

    assert control.active()[0] == active
    runtime = app.state.policy_runtime.loaded
    assert runtime is not None
    assert runtime.deployment_id == active.deployment_id
    assert runtime.manifest.policy_id == first.policy.policy_id


def _csrf(text: str) -> str:
    return re.search(r'<meta name="csrf-token" content="([0-9a-f]+)">', text).group(1)


MUTATING_ROUTES = (
    "submit_experiment",
    "cancel_submission",
    "approve_form",
    "approve_api",
    "deploy_form",
    "rollback_form",
)
MUTATING_FORM_ROUTES = tuple(route for route in MUTATING_ROUTES if route != "approve_api")


def test_csrf_matrix_covers_every_mutating_route(tmp_path: Path) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    app = create_control_app(control, csrf_secret=secrets.token_urlsafe(32))

    mutating_endpoints = {
        route.endpoint.__name__
        for route in app.routes
        if {"POST", "PUT", "PATCH", "DELETE"}.intersection(
            getattr(route, "methods", ()) or ()
        )
    }

    assert mutating_endpoints == set(MUTATING_ROUTES)


class MutationProbe:
    def __init__(self) -> None:
        self.calls = {route: 0 for route in MUTATING_ROUTES}
        self.provider_calls = 0
        self.actors: list[str] = []

    def submit(self, _submission_id: str, _payload: dict[str, str]) -> None:
        self.calls["submit_experiment"] += 1

    def cancel(self, _submission_id: str) -> bool:
        self.calls["cancel_submission"] += 1
        return True

    def mirror_candidate_status(self, *_args, **_kwargs) -> None:
        self.provider_calls += 1

    def deploy(self, *_args, **_kwargs) -> None:
        self.calls["deploy_form"] += 1
        self.actors.append(_kwargs["actor"])

    def rollback(self, *_args, **_kwargs) -> None:
        self.calls["rollback_form"] += 1
        self.actors.append(_kwargs["actor"])


def _control_state(control: ControlStore, probe: MutationProbe) -> dict[str, object]:
    candidates = control.list_candidates()
    submissions = control.list_submissions()
    approvals = control.approval_events()
    deployments = control.deployment_history()
    audit = control.audit_events()
    return {
        "candidate_count": len(candidates),
        "candidate_state": tuple(
            (item.candidate_id, item.state.value, item.version) for item in candidates
        ),
        "approval_count": len(approvals),
        "approvals": approvals,
        "deployment_count": len(deployments),
        "deployments": deployments,
        "active": control.active(),
        "submission_count": len(submissions),
        "submissions": submissions,
        "worker_count": probe.calls["submit_experiment"],
        "audit_count": len(audit),
        "audit": audit,
        "route_calls": dict(probe.calls),
        "provider_calls": probe.provider_calls,
        "probe_actors": tuple(probe.actors),
    }


def _mutating_route_context(
    tmp_path: Path,
    passing_evidence,
    route: str,
    *,
    verified_proxy: bool = False,
) -> tuple[ControlStore, MutationProbe, TestClient, str, dict[str, str], bool]:
    policy, summary, report = passing_evidence
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    probe = MutationProbe()
    fields: dict[str, str]
    is_api = route == "approve_api"

    if route == "submit_experiment":
        path = "/experiments"
        fields = {
            "dataset": "day3-frozen-v1",
            "prompt_version": "2",
            "model": "day3-replay-revised-v2",
            "condition": "raw",
            "maximum_calls": "100",
            "price_catalog": "pixelgym-demo-prices-v1",
        }
    elif route == "cancel_submission":
        submission_id = control.submit(
            {
                "dataset": "day3-frozen-v1",
                "prompt_version": "2",
                "model": "day3-replay-revised-v2",
                "condition": "raw",
                "maximum_calls": "100",
                "price_catalog": "pixelgym-demo-prices-v1",
            }
        )
        path = f"/submissions/{submission_id}/cancel"
        fields = {"reason": "cancel fixture"}
    elif route in {"approve_form", "approve_api"}:
        candidate = control.register_candidate(
            source_run_id=summary.run_id,
            policy=policy,
            gate_report=report,
            artifacts=[],
        )
        prefix = "/api" if is_api else ""
        path = f"{prefix}/candidates/{candidate.candidate_id}/approve"
        fields = {"reason": "approve fixture"}
    elif route == "deploy_form":
        candidate = _approved_candidate(control, policy, summary, report)
        path = f"/candidates/{candidate.candidate_id}/deploy"
        fields = {
            "reason": "deploy fixture",
            "expected_deployment_id": "",
            "expected_generation": "0",
        }
    else:
        assert route == "rollback_form"
        path = "/rollback"
        fields = {
            "reason": "rollback fixture",
            "expected_deployment_id": "deployment-current",
            "expected_generation": "2",
        }

    app = create_control_app(
            control,
            coordinator=probe,
            csrf_secret=secrets.token_urlsafe(32),
            bind_address="0.0.0.0" if verified_proxy else "127.0.0.1",
            trusted_proxy_addresses=("10.10.0.0/24",) if verified_proxy else (),
            submit_callback=probe.submit,
            cancel_callback=probe.cancel,
            tracking=probe,
        )
    client = TestClient(
        app,
        client=("10.10.0.5", 50000) if verified_proxy else ("127.0.0.1", 50000),
    )
    return control, probe, client, path, fields, is_api


def _post_mutating_route(
    client: TestClient,
    path: str,
    fields: dict[str, str],
    *,
    is_api: bool,
    token: str | None,
    principal: str | None = None,
):
    principal_headers = {} if principal is None else {"X-Forwarded-User": principal}
    if is_api:
        header_token: str | bytes | None = token
        if token is not None and not token.isascii():
            header_token = token.encode("latin-1")
        headers = dict(principal_headers)
        if header_token is not None:
            headers["X-CSRF-Token"] = header_token
        return client.post(path, json=fields, headers=headers, follow_redirects=False)
    data = dict(fields)
    if token is not None:
        data["csrf_token"] = token
    return client.post(path, data=data, headers=principal_headers, follow_redirects=False)


def _submission_fields() -> dict[str, str]:
    return {
        "dataset": "day3-frozen-v1",
        "prompt_version": "2",
        "model": "day3-replay-revised-v2",
        "condition": "raw",
        "maximum_calls": "100",
        "price_catalog": "pixelgym-demo-prices-v1",
    }


def _proxy_client(control: ControlStore, *, client_address: str = "10.10.0.5") -> TestClient:
    return TestClient(
        create_control_app(
            control,
            csrf_secret="test-secret-at-least-sixteen",
            bind_address="0.0.0.0",
            trusted_proxy_addresses=("10.10.0.0/24",),
        ),
        client=(client_address, 50000),
    )


def test_allowlisted_proxy_header_attributes_submission_to_verified_principal(
    tmp_path: Path,
) -> None:
    control = ControlStore(tmp_path / "control.db")
    control.migrate()
    client = _proxy_client(control)
    headers = {"X-Forwarded-User": "reviewer.alice"}
    token = _csrf(client.get("/", headers=headers).text)

    response = client.post(
        "/experiments",
        data={**_submission_fields(), "csrf_token": token},
        headers=headers,
        follow_redirects=False,
    )

    assert response.status_code == 303
    created = [event for event in control.audit_events() if event["event_type"] == "submission.created"]
    assert len(created) == 1
    assert created[0]["actor"] == "reviewer.alice"


@pytest.mark.parametrize("route", MUTATING_ROUTES)
def test_every_mutating_route_uses_the_request_principal(
    tmp_path: Path, passing_evidence, route: str
) -> None:
    route_root = tmp_path / route
    route_root.mkdir()
    control, probe, client, path, fields, is_api = _mutating_route_context(
        route_root,
        passing_evidence,
        route,
        verified_proxy=True,
    )
    principal = f"reviewer.{route}"
    token = _csrf(client.get("/", headers={"X-Forwarded-User": principal}).text)

    response = _post_mutating_route(
        client,
        path,
        fields,
        is_api=is_api,
        token=token,
        principal=principal,
    )

    assert response.status_code == (200 if is_api else 303)
    if route in {"deploy_form", "rollback_form"}:
        assert probe.actors == [principal]
    else:
        assert control.audit_events()[-1]["actor"] == principal


def test_untrusted_proxy_header_is_rejected_before_nonloopback_mutation(tmp_path: Path) -> None:
    control = ControlStore(tmp_path / "control.db")
    control.migrate()
    client = _proxy_client(control, client_address="198.51.100.9")
    token = _csrf(client.get("/").text)
    before = control.audit_events()

    response = client.post(
        "/experiments",
        data={**_submission_fields(), "csrf_token": token},
        headers={"X-Forwarded-User": "spoofed-reviewer"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "verified reviewer principal is required"}
    assert control.audit_events() == before
    assert control.list_submissions() == []


@pytest.mark.parametrize("principal", (None, "reviewer alice", "-reviewer"))
def test_trusted_proxy_missing_or_invalid_principal_is_rejected_before_mutation(
    tmp_path: Path, principal: str | None
) -> None:
    control = ControlStore(tmp_path / "control.db")
    control.migrate()
    client = _proxy_client(control)
    token = _csrf(client.get("/").text)
    headers = {} if principal is None else {"X-Forwarded-User": principal}

    response = client.post(
        "/experiments",
        data={**_submission_fields(), "csrf_token": token},
        headers=headers,
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert control.audit_events() == []
    assert control.list_submissions() == []


def test_actor_named_form_and_json_fields_cannot_override_request_principal(
    tmp_path: Path, passing_evidence
) -> None:
    (tmp_path / "form").mkdir()
    control, probe, form_client, form_path, form_fields, _ = _mutating_route_context(
        tmp_path / "form", passing_evidence, "submit_experiment"
    )
    token = _csrf(form_client.get("/").text)
    before = _control_state(control, probe)

    form_response = form_client.post(
        form_path,
        data={**form_fields, "csrf_token": token, "actor": "spoofed-reviewer"},
        follow_redirects=False,
    )

    assert form_response.status_code == 422
    assert _control_state(control, probe) == before

    (tmp_path / "api").mkdir()
    api_control, api_probe, api_client, api_path, api_fields, _ = _mutating_route_context(
        tmp_path / "api", passing_evidence, "approve_api"
    )
    api_token = _csrf(api_client.get("/").text)
    api_before = _control_state(api_control, api_probe)
    api_response = api_client.post(
        api_path,
        json={**api_fields, "actor": "spoofed-reviewer"},
        headers={"X-CSRF-Token": api_token},
        follow_redirects=False,
    )

    assert api_response.status_code == 422
    assert _control_state(api_control, api_probe) == api_before


def test_loopback_without_header_renders_and_audits_synthetic_demo_identity(tmp_path: Path) -> None:
    control = ControlStore(tmp_path / "control.db")
    control.migrate()
    client = TestClient(
        create_control_app(control, csrf_secret="test-secret-at-least-sixteen"),
        client=("127.0.0.1", 50000),
    )
    page = client.get("/")

    response = client.post(
        "/experiments",
        data={**_submission_fields(), "csrf_token": _csrf(page.text)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Reviewer: synthetic-demo (synthetic demo)" in page.text
    assert control.audit_events()[-1]["actor"] == "synthetic-demo"


def test_two_verified_principals_produce_distinct_attributed_events(tmp_path: Path) -> None:
    control = ControlStore(tmp_path / "control.db")
    control.migrate()
    client = _proxy_client(control)
    alice_headers = {"X-Forwarded-User": "reviewer.alice"}
    token = _csrf(client.get("/", headers=alice_headers).text)
    submitted = client.post(
        "/experiments",
        data={**_submission_fields(), "csrf_token": token},
        headers=alice_headers,
        follow_redirects=False,
    )
    submission_path = submitted.headers["location"]
    bob_headers = {"X-Forwarded-User": "reviewer.bob"}
    cancel_token = _csrf(client.get(submission_path, headers=bob_headers).text)

    cancelled = client.post(
        f"{submission_path}/cancel",
        data={"csrf_token": cancel_token, "reason": "reviewer handoff"},
        headers=bob_headers,
        follow_redirects=False,
    )

    assert cancelled.status_code == 303
    reviewer_events = [
        event for event in control.audit_events() if event["actor"].startswith("reviewer.")
    ]
    assert [(event["event_type"], event["actor"]) for event in reviewer_events] == [
        ("submission.created", "reviewer.alice"),
        ("submission.cancelled", "reviewer.bob"),
    ]


def test_principal_middleware_does_not_log_or_audit_unrelated_header_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    control = ControlStore(tmp_path / "control.db")
    control.migrate()
    client = _proxy_client(control)
    headers = {
        "X-Forwarded-User": "reviewer.alice",
        "X-Unrelated-Context": "private-context-value",
    }
    token = _csrf(client.get("/", headers=headers).text)
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/experiments",
            data={**_submission_fields(), "csrf_token": token},
            headers=headers,
            follow_redirects=False,
        )

    encoded_audit = json.dumps(control.audit_events(), sort_keys=True)
    assert response.status_code == 303
    assert "reviewer.alice" in encoded_audit
    assert "private-context-value" not in encoded_audit
    assert "private-context-value" not in caplog.text


def test_nonloopback_bootstrap_without_proxy_allowlist_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pixelgym.platform import bootstrap

    monkeypatch.setenv("PIXELGYM_BIND_ADDRESS", "0.0.0.0")
    monkeypatch.delenv("PIXELGYM_TRUSTED_PROXY_ADDRESSES", raising=False)

    with pytest.raises(RuntimeError, match="non-loopback.*TRUSTED_PROXY"):
        bootstrap.create_app()


def test_prechange_local_reviewer_audit_row_remains_readable_and_legacy_labeled(
    tmp_path: Path,
) -> None:
    control = ControlStore(tmp_path / "control.db")
    control.migrate()
    with control.transaction() as connection:
        control._audit(
            connection,
            "candidate.approved",
            "local-reviewer",
            "candidate-legacy",
            {"reason": "pre-change fixture"},
        )
    client = TestClient(
        create_control_app(control, csrf_secret="test-secret-at-least-sixteen"),
        client=("127.0.0.1", 50000),
    )

    response = client.get("/deployment/audit")

    assert response.status_code == 200
    assert "local-reviewer (legacy synthetic)" in response.text
    assert control.audit_events()[0]["actor"] == "local-reviewer"


@pytest.mark.parametrize(
    "unicode_token",
    (
        "\N{LATIN SMALL LETTER E WITH ACUTE}" * 64,
        "\N{FULLWIDTH DIGIT ZERO}" * 64,
        "\N{ZERO WIDTH SPACE}" * 64,
        "\N{COLLISION SYMBOL}" * 16,
        "a" * 63 + "\N{GREEK SMALL LETTER PI}",
    ),
)
def test_csrf_validation_rejects_arbitrary_unicode_without_raising(
    tmp_path: Path, passing_evidence, unicode_token: str
) -> None:
    control, probe, client, path, fields, is_api = _mutating_route_context(
        tmp_path, passing_evidence, "submit_experiment"
    )
    before = _control_state(control, probe)

    response = _post_mutating_route(
        client, path, fields, is_api=is_api, token=unicode_token
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert _control_state(control, probe) == before


@pytest.mark.parametrize("route", MUTATING_ROUTES)
@pytest.mark.parametrize(
    "token_case",
    ("missing", "empty", "malformed", "overlong", "wrong_ascii", "non_ascii", "stale_session"),
)
def test_every_mutating_route_returns_documented_csrf_rejection(
    tmp_path: Path, passing_evidence, route: str, token_case: str
) -> None:
    _control, _probe, client, path, fields, is_api = _mutating_route_context(
        tmp_path, passing_evidence, route
    )
    valid_token = _csrf(client.get("/").text)
    tokens = {
        "missing": None,
        "empty": "",
        "malformed": "not-hex",
        "overlong": "a" * 65,
        "wrong_ascii": ("0" if valid_token[0] != "0" else "1") + valid_token[1:],
        "non_ascii": "\N{LATIN SMALL LETTER E WITH ACUTE}" * 64,
        "stale_session": valid_token,
    }
    if token_case == "stale_session":
        client.cookies.clear()
        client.cookies.set("pixelgym_session", secrets.token_urlsafe(24))

    response = _post_mutating_route(
        client, path, fields, is_api=is_api, token=tokens[token_case]
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}


@pytest.mark.parametrize("route", MUTATING_ROUTES)
@pytest.mark.parametrize("token_case", ("missing", "wrong_ascii", "non_ascii", "stale_session"))
def test_csrf_rejection_precedes_all_control_and_external_mutation(
    tmp_path: Path, passing_evidence, route: str, token_case: str
) -> None:
    control, probe, client, path, fields, is_api = _mutating_route_context(
        tmp_path, passing_evidence, route
    )
    valid_token = _csrf(client.get("/").text)
    tokens = {
        "missing": None,
        "wrong_ascii": ("0" if valid_token[0] != "0" else "1") + valid_token[1:],
        "non_ascii": "\N{LATIN SMALL LETTER E WITH ACUTE}" * 64,
        "stale_session": valid_token,
    }
    if token_case == "stale_session":
        client.cookies.clear()
        client.cookies.set("pixelgym_session", secrets.token_urlsafe(24))
    before = _control_state(control, probe)

    response = _post_mutating_route(
        client, path, fields, is_api=is_api, token=tokens[token_case]
    )

    assert response.status_code == 403
    assert _control_state(control, probe) == before


@pytest.mark.parametrize("route", MUTATING_ROUTES)
def test_valid_csrf_token_permits_each_intended_action_exactly_once(
    tmp_path: Path, passing_evidence, route: str
) -> None:
    control, probe, client, path, fields, is_api = _mutating_route_context(
        tmp_path, passing_evidence, route
    )
    token = _csrf(client.get("/").text)
    before = _control_state(control, probe)

    response = _post_mutating_route(client, path, fields, is_api=is_api, token=token)

    assert response.status_code == (200 if is_api else 303)
    after = _control_state(control, probe)
    if route == "submit_experiment":
        assert after["submission_count"] == before["submission_count"] + 1
        assert after["audit_count"] == before["audit_count"] + 1
        assert probe.calls[route] == 1
    elif route == "cancel_submission":
        assert after["submission_count"] == before["submission_count"]
        assert after["submissions"][0]["status"] == "Cancelled"
        assert after["audit_count"] == before["audit_count"] + 1
        assert probe.calls[route] == 1
    elif route in {"approve_form", "approve_api"}:
        assert after["approval_count"] == before["approval_count"] + 1
        assert after["candidate_state"][0][1] == "Approved"
        assert after["audit_count"] == before["audit_count"] + 1
        assert probe.provider_calls == 1
    else:
        assert probe.calls[route] == 1


@pytest.mark.parametrize("route", MUTATING_FORM_ROUTES)
def test_mutating_form_routes_reject_duplicate_csrf_token_fields(
    tmp_path: Path, passing_evidence, route: str
) -> None:
    control, probe, client, path, fields, _is_api = _mutating_route_context(
        tmp_path, passing_evidence, route
    )
    token = _csrf(client.get("/").text)
    pairs = [*fields.items(), ("csrf_token", token), ("csrf_token", "0" * 64)]
    before = _control_state(control, probe)

    response = client.post(
        path,
        content=urlencode(pairs).encode("ascii"),
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert _control_state(control, probe) == before


def test_approve_api_rejects_duplicate_csrf_token_headers(
    tmp_path: Path, passing_evidence
) -> None:
    control, probe, client, path, fields, _is_api = _mutating_route_context(
        tmp_path, passing_evidence, "approve_api"
    )
    token = _csrf(client.get("/").text)
    before = _control_state(control, probe)

    response = client.post(
        path,
        json=fields,
        headers=[("X-CSRF-Token", token), ("X-CSRF-Token", "0" * 64)],
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert _control_state(control, probe) == before


def test_api_transcript_html_redaction_excludes_csrf_and_page_chrome() -> None:
    body = """<h1>Action blocked</h1><p>only an eligible candidate can be approved</p>
    <meta name="csrf-token" content="secret-token"><footer>private host</footer>"""
    assert _safe_body(body, "text/html") == {
        "title": "Action blocked",
        "detail": "only an eligible candidate can be approved",
    }


def test_web_submission_is_allowlisted_idempotent_and_synthetic_labeled(tmp_path: Path) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    scheduled: list[tuple[str, dict]] = []
    cancellation_states: list[str] = []
    client = TestClient(
        create_control_app(
            control,
            csrf_secret="test-secret-at-least-sixteen",
            submit_callback=lambda submission, payload: scheduled.append((submission, payload)),
            cancel_callback=lambda submission: (
                cancellation_states.append(control.get_submission(submission)["status"]) or True
            ),
        ),
        client=("127.0.0.1", 50000),
    )
    page = client.get("/")
    token = _csrf(page.text)
    form = {
        "csrf_token": token,
        "dataset": "day3-frozen-v1",
        "prompt_version": "2",
        "model": "day3-replay-revised-v2",
        "condition": "raw",
        "maximum_calls": "100",
        "price_catalog": "pixelgym-demo-prices-v1",
    }
    first = client.post("/experiments", data=form, follow_redirects=False)
    second = client.post("/experiments", data=form, follow_redirects=False)
    assert first.status_code == second.status_code == 303
    assert first.headers["location"] == second.headers["location"]
    assert first.headers["location"].startswith("/submissions/submission-")
    detail = client.get(first.headers["location"])
    assert "Submitted" in detail.text
    assert "Metaflow pathspec" in detail.text
    assert "MLflow run pending" in detail.text
    assert "Cancel experiment" in detail.text
    cancelled = client.post(
        f"{first.headers['location']}/cancel",
        data={"csrf_token": _csrf(detail.text), "reason": "stop fixture"},
        follow_redirects=True,
    )
    assert cancelled.status_code == 200
    assert "Cancelled" in cancelled.text
    assert "Cancellation is no longer available" in cancelled.text
    assert cancellation_states == ["Cancelled"]
    blocked = client.post("/experiments", data={**form, "model": "shell-command"})
    assert blocked.status_code == 422
    assert "DEMO PROVIDER" in page.text


def test_approval_mirrors_mlflow_tags_and_records_reconciliation_on_failure(
    tmp_path: Path, passing_evidence
) -> None:
    from pixelgym.platform.mlflow_tracking import TrackingMirrorError

    policy, summary, report = passing_evidence
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )

    class Mirror:
        def mirror_candidate_status(self, *args, **kwargs):
            raise TrackingMirrorError("MLflow unavailable")

    client = TestClient(
        create_control_app(
            control,
            csrf_secret="test-secret-at-least-sixteen",
            tracking=Mirror(),
        ),
        client=("127.0.0.1", 50000),
    )
    page = client.get(f"/candidates/{candidate.candidate_id}")
    response = client.post(
        f"/candidates/{candidate.candidate_id}/approve",
        data={"csrf_token": _csrf(page.text), "reason": "reviewed"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert control.get_candidate(candidate.candidate_id).state.value == "Approved"
    assert control.audit_events()[-1]["event_type"] == "tracking.reconciliation_required"


def test_empty_form_body_and_unknown_candidates_are_client_errors(tmp_path: Path) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    client = TestClient(
        create_control_app(control, csrf_secret="test-secret-at-least-sixteen"),
        client=("127.0.0.1", 50000),
    )

    empty = client.post(
        "/experiments",
        content=b"",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert empty.status_code == 422
    assert client.get("/candidates/nope").status_code == 404
    assert client.get("/compare?candidate=nope").status_code == 404


def test_compatible_run_api_uses_tracking_view_model(tmp_path: Path) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()

    class TrackingView:
        def search_compatible_runs(self, **filters):
            assert filters["primary_metric"] == "accuracy"
            assert filters["max_results"] == 20
            assert filters["timeout_seconds"] == 5.0
            return [
                TrackingRunView(
                    run_id="mlflow-run-1",
                    status="FINISHED",
                    params={
                        "dataset_fingerprint": filters["dataset_fingerprint"],
                        "scorer_version": filters["scorer_version"],
                        "target_semantics": filters["target_semantics"],
                    },
                    tags={"primary_metric": "accuracy"},
                    metrics={"accuracy": 1.0},
                    artifact_paths=("summary.json",),
                    dataset_inputs=(),
                )
            ]

    client = TestClient(
        create_control_app(
            control,
            csrf_secret="test-secret-at-least-sixteen",
            tracking=TrackingView(),
        )
    )
    response = client.get(
        "/api/tracking/runs/compatible",
        params={
            "dataset_fingerprint": "sha256:" + "a" * 64,
            "scorer_version": "scorer-v1",
            "target_semantics": "target-v1",
        },
    )

    assert response.status_code == 200
    assert response.json()["runs"][0]["run_id"] == "mlflow-run-1"


def test_failed_candidate_has_visible_reasons_and_no_approval_control(
    tmp_path: Path, passing_evidence, gate_policy
) -> None:
    policy, summary, _ = passing_evidence
    from dataclasses import replace

    summary = replace(summary, accuracy=0.5, correct_count=50)
    report = evaluate_gates(gate_policy, summary)
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    candidate = control.register_candidate(
        source_run_id=summary.run_id, policy=policy, gate_report=report, artifacts=[]
    )
    client = TestClient(
        create_control_app(control, csrf_secret="test-secret-at-least-sixteen"),
        client=("127.0.0.1", 50000),
    )
    page = client.get(f"/candidates/{candidate.candidate_id}")
    assert "Approval unavailable" in page.text
    assert "below the minimum" in page.text
    assert "Approve exact candidate" not in page.text
    assert "target_bbox" not in page.text and "expected answer" not in page.text
    token = _csrf(page.text)
    direct = client.post(
        f"/api/candidates/{candidate.candidate_id}/approve",
        json={"reason": "bypass"},
        headers={"X-CSRF-Token": token},
    )
    assert direct.status_code == 409


def test_missing_immutable_package_renders_as_blocked_deployment(
    tmp_path: Path, passing_evidence
) -> None:
    policy, summary, report = passing_evidence
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    candidate = _approved_candidate(control, policy, summary, report)

    class MissingPackageCoordinator:
        def deploy(self, *args, **kwargs):
            raise ImmutableStoreError("pinned immutable S3 object is missing")

    client = TestClient(
        create_control_app(
            control,
            coordinator=MissingPackageCoordinator(),
            csrf_secret="test-secret-at-least-sixteen",
        ),
        client=("127.0.0.1", 50000),
    )
    detail = client.get(f"/candidates/{candidate.candidate_id}")
    response = client.post(
        f"/candidates/{candidate.candidate_id}/deploy",
        data={
            "csrf_token": _csrf(detail.text),
            "reason": "must fail closed",
            "expected_deployment_id": "",
            "expected_generation": "0",
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "Action blocked" in response.text
    assert "pinned immutable S3 object is missing" in response.text
    assert control.active()[0] is None


def test_missing_gate_evidence_renders_as_blocked_instead_of_crashing(
    tmp_path: Path, passing_evidence
) -> None:
    from dataclasses import replace

    policy, summary, report = passing_evidence
    missing = replace(
        report,
        cost_usd_per_100=replace(report.cost_usd_per_100, observed=None, passed=False),
        provider_latency_p95_ms=replace(
            report.provider_latency_p95_ms, observed=None, passed=False
        ),
        overall_passed=False,
        reasons=("cost and latency evidence are missing",),
    )
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    candidate = control.register_candidate(
        source_run_id=summary.run_id, policy=policy, gate_report=missing, artifacts=[]
    )
    client = TestClient(create_control_app(control, csrf_secret="test-secret-at-least-sixteen"))
    runs = client.get("/runs")
    detail = client.get(f"/candidates/{candidate.candidate_id}")
    assert runs.status_code == detail.status_code == 200
    assert "missing" in runs.text and "missing" in detail.text
    assert "Approval unavailable" in detail.text


def test_compare_always_displays_accuracy_cost_latency_and_compatibility(
    tmp_path: Path, passing_evidence, policy_factory
) -> None:
    from dataclasses import replace

    policy, summary, report = passing_evidence
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    artifacts = [
        ArtifactRef(
            logical_key="raw-responses/a.json",
            uri="https://evidence.test/raw/a.json",
            version_id="v1",
            sha256="a" * 64,
            size=1,
            media_type="application/json",
            retention_status="locked",
        ),
        ArtifactRef(
            logical_key="runs/submission-a/predictions.jsonl",
            uri="https://evidence.test/runs/a/predictions.jsonl",
            version_id="v1",
            sha256="b" * 64,
            size=1,
            media_type="application/x-ndjson",
            retention_status="locked",
        ),
        ArtifactRef(
            logical_key="runs/submission-a/gate-report.json",
            uri="https://evidence.test/runs/a/gate-report.json",
            version_id="v1",
            sha256="c" * 64,
            size=1,
            media_type="application/json",
            retention_status="locked",
        ),
    ]
    first = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=artifacts,
        summary=summary,
    )
    second_policy = policy_factory(model="another-exact-model")
    second_report = replace(
        report,
        policy_id=second_policy.policy_id,
        run_id="run-2",
        accuracy=replace(report.accuracy, observed=0.9),
    )
    second_summary = replace(
        summary, run_id="run-2", policy_id=second_policy.policy_id, correct_count=90, accuracy=0.9
    )
    second = control.register_candidate(
        source_run_id="run-2",
        policy=second_policy,
        gate_report=second_report,
        artifacts=[],
        summary=second_summary,
    )
    client = TestClient(create_control_app(control, csrf_secret="test-secret-at-least-sixteen"))
    response = client.get(
        f"/compare?candidate={first.candidate_id}&candidate={second.candidate_id}"
    )
    assert "COMPATIBLE" in response.text
    assert "Cost / 100" in response.text
    assert "Provider p95" in response.text
    assert "Accuracy" in response.text
    assert "80.0%" in response.text
    assert "Δ +10.0 pp" in response.text
    assert "Raw-response index (1)" in response.text
    assert "Per-example errors" in response.text
    assert "Gate report" in response.text
    assert "MLflow run" in response.text
    assert "Prompt diff (recorded versions)" in response.text
    assert "Raw-response index unavailable" in response.text

    raw = client.get(f"/candidates/{first.candidate_id}/evidence/raw-responses")
    assert raw.status_code == 200
    assert "raw-responses/a.json" in raw.text
    assert "https://evidence.test/raw/a.json" in raw.text
    diff = client.get(
        f"/compare/prompt-diff?candidate={first.candidate_id}&candidate={second.candidate_id}"
    )
    assert diff.status_code == 200
    assert "no separate immutable prompt-diff artifact was recorded" in diff.text


def test_compare_blocks_promotion_for_different_primary_metrics(
    tmp_path: Path, passing_evidence, policy_factory
) -> None:
    from dataclasses import replace

    policy, summary, report = passing_evidence
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    first = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
        summary=summary,
    )
    incompatible_policy = policy_factory(model="different-primary-metric")
    incompatible_summary = replace(
        summary,
        run_id="run-primary-mismatch",
        policy_id=incompatible_policy.policy_id,
        primary_metric="mean_distance",
    )
    incompatible_report = replace(
        report,
        run_id=incompatible_summary.run_id,
        policy_id=incompatible_policy.policy_id,
    )
    incompatible = control.register_candidate(
        source_run_id=incompatible_summary.run_id,
        policy=incompatible_policy,
        gate_report=incompatible_report,
        artifacts=[],
        summary=incompatible_summary,
    )
    client = TestClient(create_control_app(control, csrf_secret="test-secret-at-least-sixteen"))
    blocked = client.get(
        f"/compare?candidate={first.candidate_id}&candidate={incompatible.candidate_id}"
    )
    assert "PROMOTION COMPARISON BLOCKED" in blocked.text
    assert "recorded primary metric" in blocked.text


def test_runs_render_recorded_badges_filters_summary_and_fixture_disclosure(
    tmp_path: Path, passing_evidence, policy_factory
) -> None:
    _, summary, report = passing_evidence
    from dataclasses import replace

    policy = policy_factory(
        model="recorded-real-model",
        provider="recorded-real-provider",
        code_state="dirty",
    )
    summary = replace(
        summary, policy_id=policy.policy_id, synthetic_provider=False, dirty_code=True
    )
    report = replace(report, policy_id=policy.policy_id)

    incomplete = replace(
        report,
        cost_usd_per_100=replace(report.cost_usd_per_100, observed=None, passed=False),
        completeness=replace(report.completeness, scored=99, unique=99, passed=False),
        overall_passed=False,
        reasons=("incomplete and unpriced",),
    )
    summary = replace(summary, invalid_count=3, unpriced_call_count=1)
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=incomplete,
        artifacts=[],
        summary=summary,
    )
    client = TestClient(create_control_app(control, csrf_secret="test-secret-at-least-sixteen"))
    runs = client.get(
        f"/runs?provider=recorded-real-provider&lifecycle=GateFailed&dataset={report.dataset_fingerprint.removeprefix('sha256:')[:8]}&code_revision={policy.code_revision[:8]}"
    )
    assert runs.status_code == 200
    assert "REAL PROVIDER" in runs.text
    assert "INCOMPLETE" in runs.text
    assert "DIRTY CODE" in runs.text
    assert "UNPRICED" in runs.text
    assert "invalid 3" in runs.text
    assert report.dataset_fingerprint.removeprefix("sha256:")[:12] in runs.text
    assert policy.code_revision[:12] in runs.text
    assert "Filter stored runs" in runs.text
    detail = client.get(f"/candidates/{candidate.candidate_id}")
    assert "Synthetic fixture disclosure" not in detail.text
    replay_policy, replay_summary, replay_report = passing_evidence
    replay = control.register_candidate(
        source_run_id="replay-run",
        policy=replay_policy,
        gate_report=replace(replay_report, run_id="replay-run"),
        artifacts=[],
        summary=replace(replay_summary, run_id="replay-run"),
    )
    detail = client.get(f"/candidates/{replay.candidate_id}")
    assert "Synthetic fixture disclosure" in detail.text
    assert 'condition == "marks"' in detail.text
    assert "relabeled" in detail.text
    assert detail.text.index('name="csrf-token"') < detail.text.index("</head>")


def _register_bulk_candidates(
    control: ControlStore,
    passing_evidence,
    *,
    oldest_provider: str | None = None,
) -> None:
    policy, _summary, report = passing_evidence
    for index in range(1_000):
        run_id = f"bulk-run-{index:04d}"
        unsigned = dataclasses.replace(
            policy,
            model=f"bulk-model-{index:04d}",
            provider=oldest_provider if oldest_provider and index < 10 else policy.provider,
            policy_id="",
        )
        candidate_policy = dataclasses.replace(
            unsigned,
            policy_id="sha256:"
            + sha256_bytes(canonical_json_bytes(unsigned.identity_dict())),
        )
        candidate_report = dataclasses.replace(
            report,
            run_id=run_id,
            policy_id=candidate_policy.policy_id,
        )
        control.register_candidate(
            source_run_id=run_id,
            policy=candidate_policy,
            gate_report=candidate_report,
            artifacts=[],
        )


def test_runs_filter_before_pagination_finds_oldest_provider_candidates(
    tmp_path: Path, passing_evidence
) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    _register_bulk_candidates(
        control,
        passing_evidence,
        oldest_provider="bulk-provider-x",
    )

    response = TestClient(
        create_control_app(control, csrf_secret="test-secret-at-least-sixteen")
    ).get("/runs?provider=bulk-provider-x")

    assert response.status_code == 200
    assert response.text.count('<tr><td><a href="/candidates/') == 10
    assert 'href="/runs?provider=bulk-provider-x&amp;page=2"' not in response.text


def test_runs_provider_options_include_providers_outside_first_page(
    tmp_path: Path, passing_evidence
) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    _register_bulk_candidates(
        control,
        passing_evidence,
        oldest_provider="bulk-provider-x",
    )

    response = TestClient(
        create_control_app(control, csrf_secret="test-secret-at-least-sixteen")
    ).get("/runs")

    assert response.status_code == 200
    provider_select = re.search(
        r'<select name="provider">(?P<options>.*?)</select>', response.text
    )
    assert provider_select is not None
    assert 'value="bulk-provider-x"' in provider_select.group("options")
    table_body = response.text.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert "bulk-provider-x" not in table_body


def test_runs_query_count_and_rendered_candidates_stay_bounded_with_large_ledger(
    tmp_path: Path, passing_evidence
) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    _register_bulk_candidates(control, passing_evidence)

    statements: list[str] = []
    control.connection.set_trace_callback(statements.append)
    try:
        response = TestClient(
            create_control_app(control, csrf_secret="test-secret-at-least-sixteen")
        ).get("/runs")
    finally:
        control.connection.set_trace_callback(None)

    selects = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    candidate_selects = [
        statement for statement in selects if "FROM candidates" in statement
    ]
    assert response.status_code == 200
    assert len(selects) == 3
    assert len(candidate_selects) == 2
    assert response.text.count('<tr><td><a href="/candidates/') == RUNS_PAGE_SIZE
    assert 'href="/runs?page=2"' in response.text


def test_deployment_window_links_to_history_and_export_retains_every_audit_event(
    tmp_path: Path,
) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    event_count = DEPLOYMENT_AUDIT_WINDOW + 5
    for index in range(event_count):
        with control.transaction() as connection:
            control._audit(
                connection,
                "test.event",
                "system",
                f"audit-subject-{index:02d}",
                {"index": index},
            )

    client = TestClient(
        create_control_app(control, csrf_secret="test-secret-at-least-sixteen")
    )
    deployment = client.get("/deployment")
    history = client.get("/deployment/audit")
    export_path = tmp_path / "export"
    export_evidence(control, export_path)
    exported = [
        json.loads(line)
        for line in (export_path / "demo-audit-events.jsonl").read_text().splitlines()
    ]

    assert deployment.status_code == history.status_code == 200
    assert deployment.text.count("<li><span>") == DEPLOYMENT_AUDIT_WINDOW
    assert f">audit-subject-{event_count - 1:02d}</p>" in deployment.text
    assert ">audit-subject-00</p>" not in deployment.text
    assert 'href="/deployment/audit"' in deployment.text
    assert "View full audit history" in deployment.text
    assert ">audit-subject-00</p>" in history.text
    assert len(exported) == event_count
    assert {event["subject_id"] for event in exported} == {
        f"audit-subject-{index:02d}" for index in range(event_count)
    }


def test_runs_filter_prompt_model_status_date_and_gate_result(
    tmp_path: Path, passing_evidence
) -> None:
    policy, summary, report = passing_evidence
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    submission_id = control.submit(
        {
            "dataset": "day3-frozen-v1",
            "prompt_version": str(policy.prompt_version),
            "model": policy.model,
            "condition": policy.condition,
            "maximum_calls": "100",
            "price_catalog": "pixelgym-demo-prices-v1",
        }
    )
    control.link_run(
        submission_id,
        metaflow_pathspec="GroundingEvaluationFlow/1",
        mlflow_run_id=summary.run_id,
    )
    control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
        summary=summary,
        submission_id=submission_id,
    )
    created_date = control.get_submission(submission_id)["created_at_utc"][:10]
    client = TestClient(create_control_app(control, csrf_secret="test-secret-at-least-sixteen"))

    matched = client.get(
        "/runs",
        params={
            "prompt_version": policy.prompt_version,
            "model": policy.model,
            "status": "Complete",
            "date_from": created_date,
            "date_to": created_date,
            "gate_result": "passed",
        },
    )
    assert policy.model in matched.text
    for filters in (
        {"prompt_version": policy.prompt_version + 1},
        {"model": "different-model"},
        {"status": "Failed"},
        {"date_from": "2999-01-01"},
        {"gate_result": "failed"},
    ):
        excluded = client.get("/runs", params=filters)
        assert excluded.status_code == 200
        assert "No evaluated candidates" in excluded.text


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "not-a-date"},
        {"date_from": "2026-02-30"},
        {"date_to": "2026-8-18"},
        {"gate_result": "unknown"},
    ],
)
def test_runs_reject_invalid_date_and_gate_filters(tmp_path: Path, params) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    response = TestClient(
        create_control_app(control, csrf_secret="test-secret-at-least-sixteen")
    ).get("/runs", params=params)
    assert response.status_code == 422


def test_compatible_run_api_rejects_unsafe_filters_and_maps_timeout(tmp_path: Path) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()

    class SlowTracking:
        calls = 0

        def search_compatible_runs(self, **filters):
            self.calls += 1
            raise TimeoutError("slow tracking")

    tracking = SlowTracking()
    client = TestClient(
        create_control_app(
            control,
            csrf_secret="test-secret-at-least-sixteen",
            tracking=tracking,
        )
    )
    base = {
        "dataset_fingerprint": "sha256:" + "a" * 64,
        "scorer_version": "scorer-v1",
        "target_semantics": "target-v1",
    }
    assert (
        client.get(
            "/api/tracking/runs/compatible",
            params={**base, "scorer_version": "unsafe'value"},
        ).status_code
        == 422
    )
    assert tracking.calls == 0
    assert client.get("/api/tracking/runs/compatible", params=base).status_code == 504
    assert tracking.calls == 1


def test_runs_render_safe_source_provenance_diagnostic(
    tmp_path: Path, passing_evidence, gate_policy
) -> None:
    _policy, summary, _report = passing_evidence
    policy = build_policy_manifest(
        provider="scripted-demo",
        model="day3-replay-revised-v2",
        prompt_name="pixelgym-grounding",
        prompt_version=2,
        prompt=prompt_template(2),
        condition="raw",
        parameters={"deterministic": True, "hidden_retries": 0},
        parser_version="pixelgym-grounding-parser-v1",
        scorer_version=gate_policy.required_scorer_version,
        overlay_version="none-raw-coordinate-policy",
        target_semantics=gate_policy.required_target_semantics,
        source_provenance=SourceProvenance(
            SOURCE_PROVENANCE_SCHEMA_VERSION,
            None,
            None,
            "unverifiable",
            "none",
            "manifest_malformed_json",
        ),
        dependency_lock_sha256="a" * 64,
    )
    summary = dataclasses.replace(
        summary,
        policy_id=policy.policy_id,
        code_state="unverifiable",
        code_provenance_verified=False,
    )
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=evaluate_gates(gate_policy, summary),
        artifacts=[],
        summary=summary,
    )
    client = TestClient(create_control_app(control, csrf_secret="test-secret-at-least-sixteen"))
    page = client.get("/runs")
    assert "PROVENANCE: MANIFEST MALFORMED JSON" in page.text
