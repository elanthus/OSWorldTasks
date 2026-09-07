"""Bounded versioned grounding inference API pinned to one approved policy."""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import logging
import math
import time
import traceback
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, Protocol

import anyio
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.utils import is_body_allowed_for_status_code
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pixelgym.grounding.evaluation import parse_prediction
from pixelgym.platform.contracts import PolicyManifest
from pixelgym.platform.operational_log import (
    OPERATIONAL_RECORD_SCHEMA_VERSION,
    OperationalLog,
    OperationalRecord,
    ProviderMetadata,
    normalize_provider_metadata,
)
from pixelgym.platform.policy import render_prompt

logger = logging.getLogger(__name__)

API_SCHEMA_VERSION = "pixelgym-grounding-api-v1"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_ENCODED_IMAGE_CHARS = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
# Allow bounded JSON syntax and the other model fields without making the image allowance fuzzy.
MAX_REQUEST_BODY_BYTES = MAX_ENCODED_IMAGE_CHARS + 16 * 1024
MAX_DIMENSION = 4096
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 30.0
DEFAULT_PROVIDER_CONCURRENCY = 4
DEFAULT_PROVIDER_QUEUE_TIMEOUT_SECONDS = 0.25
DEFAULT_MAX_PROVIDER_OUTPUT_BYTES = 64 * 1024
ALLOWED_MEDIA_TYPES = {"image/png", "image/jpeg"}


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
        prompt: str,
    ) -> tuple[str | None, str, float | None, dict[str, Any] | None]:
        """Return raw final text, request ID, latency, and usage without hidden retry.

        ``prompt`` is rendered by pixelgym.platform.policy.render_prompt entirely from
        the candidate's own packaged ``prompt_template_text`` -- the frozen bytes
        recorded in the policy package -- with no dependency on whatever prompt code is
        currently running. Evaluation's request material calls the same render_prompt.
        """


@dataclass(frozen=True)
class LoadedPolicy:
    manifest: PolicyManifest
    deployment_id: str
    exact_policy_version: str
    provider: ServingProvider


class GroundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    image_base64: str = Field(min_length=1, max_length=MAX_ENCODED_IMAGE_CHARS)
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
        # Production activation is sanctioned only through DeploymentCoordinator; direct use is smoke-only.
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
    provider_metadata: ProviderMetadata | None = None
    provider_output_bytes: int | None = None


_operational_context: ContextVar[_OperationalContext | None] = ContextVar(
    "pixelgym_operational_context", default=None
)


class _RequestBodyLimitMiddleware:
    """Reject oversized grounding bodies before Starlette buffers or parses them."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != "/api/v1/ground":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = 0
            if declared_length > self.max_body_bytes:
                await self._reject(scope, receive, send)
                return

        buffered = bytearray()
        disconnected = False
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                break
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(buffered) + len(chunk) > self.max_body_bytes:
                await self._reject(scope, receive, send)
                return
            buffered.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def receive_buffered() -> Message:
            nonlocal replayed
            if replayed or disconnected:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": bytes(buffered), "more_body": False}

        await self.app(scope, receive_buffered, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = _error_response(413, "request body exceeds the byte limit")
        await response(scope, receive, send)


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


def _identity(context: _OperationalContext | None = None) -> dict[str, str] | None:
    if context is None:
        context = _operational_context.get()
    if (
        context is None
        or context.policy_id is None
        or context.deployment_id is None
        or context.exact_policy_version is None
    ):
        return None
    return {
        "api_version": API_SCHEMA_VERSION,
        "policy_id": context.policy_id,
        "deployment_id": context.deployment_id,
        "exact_policy_version": context.exact_policy_version,
    }


def _attach_identity_headers(response: Response, context: _OperationalContext) -> None:
    identity = _identity(context)
    if identity is None:
        return
    response.headers["X-PixelGym-API-Version"] = identity["api_version"]
    response.headers["X-PixelGym-Policy-ID"] = identity["policy_id"]
    response.headers["X-PixelGym-Deployment-ID"] = identity["deployment_id"]
    response.headers["X-PixelGym-Exact-Policy-Version"] = identity[
        "exact_policy_version"
    ]


def _error_response(
    status_code: int,
    detail: object,
    *,
    headers: Mapping[str, str] | None = None,
) -> Response:
    response_headers = dict(headers) if headers is not None else None
    if not is_body_allowed_for_status_code(status_code):
        return Response(status_code=status_code, headers=response_headers)
    content: dict[str, object] = {"detail": detail}
    identity = _identity()
    if identity is not None:
        content["identity"] = identity
    return JSONResponse(
        status_code=status_code, content=jsonable_encoder(content), headers=response_headers
    )


def _release_provider_capacity(
    limiter: anyio.CapacityLimiter,
    borrower: object,
) -> None:
    limiter.release_on_behalf_of(borrower)


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


def create_serving_app(
    runtime: PolicyRuntime,
    *,
    operational_log: OperationalLog,
    operational_audit_concurrency: int = 4,
    provider_timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
    provider_concurrency: int = DEFAULT_PROVIDER_CONCURRENCY,
    provider_queue_timeout_seconds: float = DEFAULT_PROVIDER_QUEUE_TIMEOUT_SECONDS,
    max_provider_output_bytes: int = DEFAULT_MAX_PROVIDER_OUTPUT_BYTES,
) -> FastAPI:
    if operational_audit_concurrency <= 0:
        raise ValueError("operational audit concurrency must be positive")
    if not math.isfinite(provider_timeout_seconds) or provider_timeout_seconds <= 0:
        raise ValueError("provider timeout must be finite and positive")
    if provider_concurrency <= 0:
        raise ValueError("provider concurrency must be positive")
    if (
        not math.isfinite(provider_queue_timeout_seconds)
        or provider_queue_timeout_seconds < 0
    ):
        raise ValueError("provider queue timeout must be finite and nonnegative")
    if max_provider_output_bytes <= 0:
        raise ValueError("maximum provider output bytes must be positive")
    # Audit I/O may wait for its configured storage deadline, but it must not occupy Starlette's
    # shared worker capacity while it does. Waiting here preserves the rule that no response is
    # released before its immutable evidence is verified.
    audit_limiter = anyio.CapacityLimiter(operational_audit_concurrency)
    # Provider admission and execution are deliberately separate from audit I/O. A dedicated
    # executor prevents unrelated Starlette worker traffic from consuming provider capacity.
    provider_limiter = anyio.CapacityLimiter(provider_concurrency)
    provider_executor = ThreadPoolExecutor(
        max_workers=provider_concurrency,
        thread_name_prefix="pixelgym-provider",
    )

    @asynccontextmanager
    async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # Abandoned/timed-out calls are never cancelled (see the timeout handling below), so
            # this releases the pool without waiting for them and without a hidden retry.
            provider_executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="PixelGym Grounding API", docs_url=None, redoc_url=None, lifespan=_lifespan
    )
    app.state.operational_log = operational_log
    app.state.provider_executor = provider_executor
    # Install this before the audit middleware below so the audit wrapper remains outermost and
    # records body-limit rejections without allowing request parsing to occur first.
    app.add_middleware(_RequestBodyLimitMiddleware, max_body_bytes=MAX_REQUEST_BODY_BYTES)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        del request
        return _error_response(exc.status_code, exc.detail, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> Response:
        del request
        safe_errors = [
            {key: error[key] for key in ("type", "loc", "msg") if key in error}
            for error in exc.errors()
        ]
        return _error_response(422, safe_errors)

    @app.middleware("http")
    async def record_ground_operation(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
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
        except Exception as exc:  # noqa: BLE001 - convert unknown handler errors into a redacted record.
            # Keep traceback locations for operators without formatting source lines or the
            # exception value, either of which could contain request/provider-derived text.
            locations = " <- ".join(
                f"{frame.filename}:{frame.lineno} in {frame.name}"
                for frame in traceback.extract_tb(exc.__traceback__)
            )
            logger.error(
                "unexpected serving handler failure type=%s traceback=%s",
                type(exc).__name__,
                locations,
            )
            _set_terminal_status("internal_error")
            response = _error_response(500, "internal server error")
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
                provider_metadata=context.provider_metadata,
                provider_output_bytes=context.provider_output_bytes,
            )
            try:
                # Immutable logging deliberately performs two store operations (put-once, then
                # verified read-back).  Both may perform remote or filesystem I/O, so keep them
                # out of the event loop. Starlette propagates the current ContextVars into this
                # worker call, which preserves request-scoped logging state for implementations
                # that consume it.
                # Starlette's BaseHTTPMiddleware runs under an AnyIO cancellation scope.  Once
                # disconnected, it can re-deliver cancellation at every await; shield the one
                # required audit append so it has a chance to finish before propagating the
                # original cancellation out of this middleware.
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(
                        operational_log.append, record, limiter=audit_limiter
                    )
            except Exception:  # noqa: BLE001 - an unrecorded serving result is never safe to return.
                # Never return an apparently successful inference that lacks its required evidence.
                # During cancellation there is no response to replace; preserving the original
                # cancellation is more truthful than turning a disconnected request into a 503.
                if response is not None:
                    response = _error_response(503, "serving audit storage is unavailable")
            finally:
                _operational_context.reset(token)
        # ``call_next`` is required to return a response, and cancellations re-raise above.
        # Keep this defensive check explicit: Python optimization must not remove a
        # correctness boundary in the serving path.
        if response is None:
            raise RuntimeError("serving handler returned no response")
        _attach_identity_headers(response, context)
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
    async def ground(request: GroundRequest, response: Response) -> GroundResponse:
        # Base64 decoding and Image.verify() are CPU-bound; keep them off the shared event loop
        # so a large screenshot cannot stall /health/live or other requests behind it.
        image, width, height = await anyio.to_thread.run_sync(_decode_and_validate, request)
        target = request.target.strip()
        if not target:
            raise HTTPException(422, "target must contain non-whitespace text")
        if runtime.loaded is None:
            _set_terminal_status("no_active_deployment")
            raise HTTPException(503, "no approved policy is loaded")
        loaded = runtime.loaded
        _set_identity(loaded)
        try:
            rendered_prompt = render_prompt(loaded.manifest, target=target, width=width, height=height)
        except ValueError as exc:
            # Deploy/rollback/restore already verify renderer binding before traffic can
            # reach an active deployment; this is defense-in-depth, not the expected path.
            _set_terminal_status("renderer_binding_invalid")
            raise HTTPException(500, "active policy package failed to render a request prompt") from exc
        borrower = object()
        provider_admitted = False
        if provider_queue_timeout_seconds == 0:
            try:
                provider_limiter.acquire_on_behalf_of_nowait(borrower)
                provider_admitted = True
            except anyio.WouldBlock:
                provider_admitted = False
        else:
            with anyio.move_on_after(provider_queue_timeout_seconds):
                await provider_limiter.acquire_on_behalf_of(borrower)
                provider_admitted = True
        if not provider_admitted:
            _set_terminal_status("provider_concurrency_saturated")
            raise HTTPException(503, "provider concurrency limit is saturated")

        loop = asyncio.get_running_loop()
        provider_call = partial(
            loaded.provider.ground,
            image_bytes=image,
            media_type=request.media_type,
            target=target,
            policy=loaded.manifest,
            prompt=rendered_prompt,
        )

        def _on_provider_future_done(_: Future[Any]) -> None:
            try:
                loop.call_soon_threadsafe(
                    _release_provider_capacity, provider_limiter, borrower
                )
            except RuntimeError:
                # The event loop already closed (interpreter/app shutdown); the executor is
                # being torn down too, so the limiter borrower is abandoned along with it.
                pass

        try:
            # Run inside a copy of the caller's context so request-scoped ContextVars reach the
            # provider thread, matching the audit path's propagation via anyio.to_thread.run_sync.
            provider_future: Future[
                tuple[str | None, str, float | None, dict[str, Any] | None]
            ] = provider_executor.submit(copy_context().run, provider_call)
        except BaseException:
            provider_limiter.release_on_behalf_of(borrower)
            raise
        provider_future.add_done_callback(_on_provider_future_done)
        try:
            with anyio.move_on_after(provider_timeout_seconds) as timeout_scope:
                raw, provider_request_id, provider_latency_ms, usage = await asyncio.shield(
                    asyncio.wrap_future(provider_future)
                )
        except ProviderFailure as exc:
            _set_terminal_status(f"provider_{exc.code}")
            status = 504 if exc.code == "timeout" else 429 if exc.code == "rate_limit" else 502
            raise HTTPException(status, f"provider request failed: {exc.code}") from exc
        if timeout_scope.cancel_called:
            # The service's own deadline fired; the call is abandoned, never cancelled or
            # retried, and stays distinct from a provider-reported ``ProviderFailure(timeout)``.
            _set_terminal_status("provider_timeout_enforced")
            raise HTTPException(504, "provider request timed out")
        output_bytes = len(raw.encode("utf-8")) if raw is not None else None
        context = _operational_context.get()
        if context is not None:
            context.provider_output_bytes = output_bytes
        if output_bytes is not None and output_bytes > max_provider_output_bytes:
            _set_terminal_status("provider_output_too_large")
            raise HTTPException(502, "provider output exceeds the byte limit")
        try:
            provider_metadata = normalize_provider_metadata(
                provider_request_id, provider_latency_ms, usage
            )
        except ValueError as exc:
            _set_terminal_status("provider_metadata_invalid")
            raise HTTPException(502, "provider returned malformed operational metadata") from exc
        if context is not None:
            context.provider_metadata = provider_metadata
        if runtime.loaded is not loaded:
            _set_terminal_status("runtime_mismatch")
            raise HTTPException(503, "active deployment changed during request")
        parsed = parse_prediction(raw, condition="raw", width=width, height=height, marks=[])
        _set_terminal_status("completed" if parsed.status == "parsed" else "invalid_output")
        response.headers["X-PixelGym-API-Version"] = API_SCHEMA_VERSION
        response.headers["X-PixelGym-Policy-ID"] = loaded.manifest.policy_id
        response.headers["X-PixelGym-Deployment-ID"] = loaded.deployment_id
        response.headers["X-PixelGym-Exact-Policy-Version"] = loaded.exact_policy_version
        return GroundResponse(
            schema_version=API_SCHEMA_VERSION,
            prediction=parsed.parsed_prediction,
            parse_status=parsed.status,
            parse_error=parsed.error,
            policy_id=loaded.manifest.policy_id,
            deployment_id=loaded.deployment_id,
            exact_policy_version=loaded.exact_policy_version,
            provider_request_id=provider_metadata.request_id,
        )

    return app
