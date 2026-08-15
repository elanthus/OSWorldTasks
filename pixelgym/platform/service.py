"""Bounded versioned grounding inference API pinned to one approved policy."""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import math
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from pixelgym.grounding.evaluation import parse_prediction
from pixelgym.platform.contracts import PolicyManifest
from pixelgym.platform.operational_log import (
    OPERATIONAL_RECORD_SCHEMA_VERSION,
    OperationalLog,
    OperationalRecord,
)

API_SCHEMA_VERSION = "pixelgym-grounding-api-v1"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_DIMENSION = 4096
ALLOWED_MEDIA_TYPES = {"image/png", "image/jpeg"}
_PROVIDER_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_USAGE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ProviderFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ServingProvider(Protocol):
    def ground(
        self,
        *,
        image_bytes: bytes,
        media_type: str,
        target: str,
        policy: PolicyManifest,
    ) -> tuple[str | None, str, float | None, dict[str, Any] | None]:
        """Return raw final text, request ID, latency, and usage without hidden retry."""


@dataclass(frozen=True)
class LoadedPolicy:
    manifest: PolicyManifest
    deployment_id: str
    exact_policy_version: str
    provider: ServingProvider
    approved: bool
    gate_passed: bool


class GroundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    image_base64: str = Field(min_length=1)
    media_type: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=500)


class GroundResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str
    prediction: dict[str, int] | None
    parse_status: str
    parse_error: str | None
    policy_id: str
    deployment_id: str
    exact_policy_version: str
    provider_request_id: str


class PolicyRuntime:
    def __init__(self, loaded: LoadedPolicy | None = None) -> None:
        self.loaded: LoadedPolicy | None = None
        if loaded is not None:
            self.activate(loaded)

    def activate(self, loaded: LoadedPolicy) -> None:
        if not loaded.approved or not loaded.gate_passed:
            raise ValueError("unapproved or gate-failed policies cannot become ready")
        if loaded.manifest.condition != "raw":
            raise ValueError("serving v1 supports raw-coordinate policies only")
        self.loaded = loaded


@dataclass
class _OperationalContext:
    request_id: str
    occurred_at: str
    policy_id: str | None = None
    deployment_id: str | None = None
    exact_policy_version: str | None = None
    terminal_status: str | None = None
    provider_latency_ms: float | None = None
    provider_request_id: str | None = None
    usage: dict[str, int | float] | None = None


_operational_context: ContextVar[_OperationalContext | None] = ContextVar(
    "pixelgym_operational_context", default=None
)


def _set_identity(loaded: LoadedPolicy) -> None:
    context = _operational_context.get()
    if context is not None:
        context.policy_id = loaded.manifest.policy_id
        context.deployment_id = loaded.deployment_id
        context.exact_policy_version = loaded.exact_policy_version


def _set_terminal_status(status: str) -> None:
    context = _operational_context.get()
    if context is not None:
        context.terminal_status = status


def _normalize_provider_metadata(
    request_id: object, latency_ms: object, usage: object
) -> tuple[str, float | None, dict[str, int | float] | None]:
    if not isinstance(request_id, str) or not _PROVIDER_REQUEST_ID.fullmatch(request_id):
        raise ValueError("provider request ID is malformed")
    if latency_ms is not None and (
        isinstance(latency_ms, bool)
        or not isinstance(latency_ms, (int, float))
        or not math.isfinite(latency_ms)
        or latency_ms < 0
    ):
        raise ValueError("provider latency is malformed")
    if usage is None:
        normalized_usage = None
    elif not isinstance(usage, dict):
        raise ValueError("provider usage is malformed")
    else:
        normalized_usage = {}
        for key, value in usage.items():
            if (
                not isinstance(key, str)
                or not _USAGE_KEY.fullmatch(key)
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("provider usage is malformed")
            normalized_usage[key] = value
    return request_id, float(latency_ms) if latency_ms is not None else None, normalized_usage


def _decode_and_validate(request: GroundRequest) -> tuple[bytes, int, int]:
    if request.media_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(415, "unsupported screenshot media type")
    try:
        data = base64.b64decode(request.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "image_base64 is not valid base64") from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "screenshot exceeds the request byte limit")
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            actual_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(400, "screenshot is not a valid supported image") from exc
    expected_format = "PNG" if request.media_type == "image/png" else "JPEG"
    if actual_format != expected_format:
        raise HTTPException(415, "declared media type does not match screenshot bytes")
    if width <= 0 or height <= 0 or width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise HTTPException(400, "screenshot dimensions are outside the supported bounds")
    return data, width, height


def create_serving_app(runtime: PolicyRuntime, *, operational_log: OperationalLog) -> FastAPI:
    app = FastAPI(title="PixelGym Grounding API", docs_url=None, redoc_url=None)
    app.state.operational_log = operational_log

    @app.middleware("http")
    async def record_ground_operation(request: Request, call_next):
        if request.url.path != "/api/v1/ground":
            return await call_next(request)
        context = _OperationalContext(
            request_id=f"srv-{uuid.uuid4().hex}",
            occurred_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        )
        started = time.perf_counter()
        token = _operational_context.set(context)
        if runtime.loaded is not None:
            # Validation failures still identify the traffic policy selected at receipt time.
            _set_identity(runtime.loaded)
        # A client disconnect can cancel the task while ``call_next`` is still running.  Keep
        # this optional until a response really exists so the audit path cannot replace that
        # cancellation with an UnboundLocalError.
        response: Response | None = None
        try:
            response = await call_next(request)
        except asyncio.CancelledError:
            # No HTTP response was produced.  499 is the conventional client-disconnect status
            # for an operational record; it is never sent because cancellation still propagates.
            _set_terminal_status("cancelled")
            raise
        except Exception:  # noqa: BLE001 - convert unknown handler errors into a redacted record.
            _set_terminal_status("internal_error")
            response = JSONResponse(status_code=500, content={"detail": "internal server error"})
        finally:
            latency_ms = (time.perf_counter() - started) * 1000
            http_status = response.status_code if response is not None else 499
            status = context.terminal_status or (
                "completed" if 200 <= http_status < 300 else "request_rejected"
            )
            record = OperationalRecord(
                schema_version=OPERATIONAL_RECORD_SCHEMA_VERSION,
                request_id=context.request_id,
                occurred_at=context.occurred_at,
                policy_id=context.policy_id,
                deployment_id=context.deployment_id,
                exact_policy_version=context.exact_policy_version,
                terminal_status=status,
                http_status=http_status,
                latency_ms=latency_ms,
                provider_latency_ms=context.provider_latency_ms,
                provider_request_id=context.provider_request_id,
                usage=context.usage,
            )
            try:
                # Immutable logging deliberately performs two store operations (put-once, then
                # verified read-back).  Both may perform remote or filesystem I/O, so keep them
                # out of the event loop.  Starlette propagates the current ContextVars into this
                # worker call, which preserves request-scoped logging state for implementations
                # that consume it.
                await run_in_threadpool(operational_log.append, record)
            except Exception:  # noqa: BLE001 - an unrecorded serving result is never safe to return.
                # Never return an apparently successful inference that lacks its required evidence.
                # During cancellation there is no response to replace; preserving the original
                # cancellation is more truthful than turning a disconnected request into a 503.
                if response is not None:
                    response = JSONResponse(
                        status_code=503, content={"detail": "serving audit storage is unavailable"}
                    )
            finally:
                _operational_context.reset(token)
        assert response is not None  # Exceptions (including cancellation) do not reach this line.
        response.headers["X-PixelGym-Request-ID"] = context.request_id
        return response

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    def ready(response: Response) -> dict[str, str]:
        if runtime.loaded is None:
            response.status_code = 503
            return {"status": "not-ready"}
        return {"status": "ready", "policy_id": runtime.loaded.manifest.policy_id}

    @app.get("/api/v1/policy")
    def policy() -> dict[str, Any]:
        if runtime.loaded is None:
            raise HTTPException(503, "no approved policy is loaded")
        loaded = runtime.loaded
        return {
            "schema_version": API_SCHEMA_VERSION,
            "policy_id": loaded.manifest.policy_id,
            "deployment_id": loaded.deployment_id,
            "exact_policy_version": loaded.exact_policy_version,
            "provider": loaded.manifest.provider,
            "model": loaded.manifest.model,
            "prompt_version": loaded.manifest.prompt_version,
        }

    @app.post("/api/v1/ground", response_model=GroundResponse)
    def ground(request: GroundRequest, response: Response) -> GroundResponse:
        image, width, height = _decode_and_validate(request)
        target = request.target.strip()
        if not target:
            raise HTTPException(422, "target must contain non-whitespace text")
        if runtime.loaded is None:
            _set_terminal_status("no_active_deployment")
            raise HTTPException(503, "no approved policy is loaded")
        loaded = runtime.loaded
        _set_identity(loaded)
        try:
            raw, provider_request_id, provider_latency_ms, usage = loaded.provider.ground(
                image_bytes=image,
                media_type=request.media_type,
                target=target,
                policy=loaded.manifest,
            )
        except ProviderFailure as exc:
            _set_terminal_status(f"provider_{exc.code}")
            status = 504 if exc.code == "timeout" else 429 if exc.code == "rate_limit" else 502
            raise HTTPException(status, f"provider request failed: {exc.code}") from exc
        try:
            provider_request_id, provider_latency_ms, usage = _normalize_provider_metadata(
                provider_request_id, provider_latency_ms, usage
            )
        except ValueError as exc:
            _set_terminal_status("provider_metadata_invalid")
            raise HTTPException(502, "provider returned malformed operational metadata") from exc
        context = _operational_context.get()
        if context is not None:
            context.provider_request_id = provider_request_id
            context.provider_latency_ms = provider_latency_ms
            context.usage = usage
        if runtime.loaded is not loaded:
            _set_terminal_status("runtime_mismatch")
            raise HTTPException(503, "active deployment changed during request")
        parsed = parse_prediction(raw, condition="raw", width=width, height=height, marks=[])
        _set_terminal_status("completed" if parsed.status == "parsed" else "invalid_output")
        response.headers["X-PixelGym-API-Version"] = API_SCHEMA_VERSION
        response.headers["X-PixelGym-Policy-ID"] = loaded.manifest.policy_id
        response.headers["X-PixelGym-Deployment-ID"] = loaded.deployment_id
        return GroundResponse(
            schema_version=API_SCHEMA_VERSION,
            prediction=parsed.parsed_prediction,
            parse_status=parsed.status,
            parse_error=parsed.error,
            policy_id=loaded.manifest.policy_id,
            deployment_id=loaded.deployment_id,
            exact_policy_version=loaded.exact_policy_version,
            provider_request_id=provider_request_id,
        )

    return app
