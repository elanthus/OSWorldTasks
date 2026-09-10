"""Strict HTTP adapter for caller-owned stateful serving episodes.

This module is deliberately independent of the v1 ``PolicyRuntime``.  S4 supplies only the
versioned wire boundary, an injectable host factory/session registry, and immutable operational
storage.  Deployment selection and control-plane activation remain S6 concerns.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal, Protocol

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from pixelgym.grounding.v5.contracts import SCREEN_HEIGHT, SCREEN_WIDTH, sha256_bytes
from pixelgym.platform.fingerprints import canonical_json_bytes
from pixelgym.platform.immutable_store import ImmutableStore
from pixelgym.platform.serving_episode import (
    DeploymentAttemptCapError,
    EpisodeEndedError,
    EpisodeNotFoundError,
    IntentReferenceError,
    ServingActResult,
    ServingEpisodeError,
    SessionConflictError,
)
from pixelgym.platform.stateful_contracts import (
    MAX_ENCODED_IMAGE_CHARS,
    MAX_TASK_INSTRUCTION_CHARS,
    SESSION_SCHEMA_VERSION,
    EpisodeClosedRecord,
    EpisodeOpenedRecord,
    EpisodeSessionState,
    EpisodeStepRecord,
    ReportedResult,
    ServingIdentity,
    StatefulPolicyPackage,
    episode_id_is_valid,
)

logger = logging.getLogger(__name__)

CREATE_BODY_LIMIT = 16 * 1024
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024
MAX_CLIENT_EPISODE_REF_CHARS = 200
SCREENSHOT_BODY_LIMIT = MAX_ENCODED_IMAGE_CHARS + 16 * 1024
_CLIENT_REF_PATTERN = r"^[A-Za-z0-9._:-]+$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ScreenshotBody(_StrictModel):
    image_base64: str = Field(min_length=1, max_length=MAX_ENCODED_IMAGE_CHARS)
    media_type: Literal["image/png", "image/jpeg"]


class ReportedResultBody(_StrictModel):
    reward: float
    terminated: bool
    truncated: bool
    screenshot_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("reward", mode="before")
    @classmethod
    def validate_reward(cls, value: object) -> object:
        if type(value) is not float or value not in (0.0, 1.0):
            raise ValueError("reward must be the float 0.0 or 1.0")
        return value

    @model_validator(mode="after")
    def validate_terminal_flags(self) -> ReportedResultBody:
        if self.terminated and self.truncated:
            raise ValueError("terminated and truncated cannot both be true")
        return self

    def to_contract(self) -> ReportedResult:
        return ReportedResult(**self.model_dump())


class CreateEpisodeBody(_StrictModel):
    schema_version: Literal["pixelgym-serving-session-v2"]
    task_instruction: str = Field(min_length=1, max_length=MAX_TASK_INSTRUCTION_CHARS)
    screen_width: int = Field(ge=1)
    screen_height: int = Field(ge=1)
    client_episode_ref: str = Field(
        min_length=1,
        max_length=MAX_CLIENT_EPISODE_REF_CHARS,
        pattern=_CLIENT_REF_PATTERN,
    )


class ActEpisodeBody(_StrictModel):
    schema_version: Literal["pixelgym-serving-session-v2"]
    screenshot: ScreenshotBody
    previous_intent_id: str | None = Field(pattern=r"^intent-[0-9a-f]{32}$")
    previous_result: ReportedResultBody | None

    @model_validator(mode="after")
    def validate_previous_result_pair(self) -> ActEpisodeBody:
        if (self.previous_intent_id is None) != (self.previous_result is None):
            raise ValueError("previous_intent_id and previous_result must be supplied together")
        return self


class CloseEpisodeBody(_StrictModel):
    schema_version: Literal["pixelgym-serving-session-v2"]
    final_screenshot: ScreenshotBody | None
    final_intent_id: str | None = Field(pattern=r"^intent-[0-9a-f]{32}$")
    final_result: ReportedResultBody | None

    @model_validator(mode="after")
    def validate_final_result_pair(self) -> CloseEpisodeBody:
        if (self.final_intent_id is None) != (self.final_result is None):
            raise ValueError("final_intent_id and final_result must be supplied together")
        if self.final_result is not None and self.final_screenshot is None:
            raise ValueError("a final result requires a final screenshot")
        return self


class EpisodeHost(Protocol):
    package: StatefulPolicyPackage
    identity: ServingIdentity

    def create_episode(
        self, *, task_instruction: str, client_episode_ref: str
    ) -> EpisodeSessionState: ...

    def get(self, episode_id: str) -> EpisodeSessionState: ...

    def act(
        self,
        *,
        episode_id: str,
        screenshot: bytes,
        previous_intent_id: str | None = None,
        previous_result: ReportedResult | None = None,
    ) -> ServingActResult: ...

    def step_record(self, episode_id: str, step_index: int) -> EpisodeStepRecord: ...

    def close_episode(
        self,
        *,
        episode_id: str,
        final_intent_id: str | None,
        final_result: ReportedResult | None,
        final_screenshot_sha256: str | None,
        final_screenshot_object_key: str | None,
    ) -> EpisodeClosedRecord: ...


EpisodeHostFactory = Callable[[], EpisodeHost]


@dataclass
class RegisteredEpisode:
    host: EpisodeHost
    identity: ServingIdentity
    screen: Mapping[str, int]
    lock: anyio.Lock = field(default_factory=anyio.Lock)


class EpisodeSessionRegistry(Protocol):
    def register(self, episode_id: str, registration: RegisteredEpisode) -> None: ...

    def get(self, episode_id: str) -> RegisteredEpisode | None: ...


class MemoryEpisodeSessionRegistry:
    """Explicit in-memory registry for contract tests and no-cost smoke use."""

    def __init__(self) -> None:
        self._episodes: dict[str, RegisteredEpisode] = {}
        self._lock = threading.RLock()

    def register(self, episode_id: str, registration: RegisteredEpisode) -> None:
        with self._lock:
            if episode_id in self._episodes:
                raise SessionConflictError("episode is already registered")
            self._episodes[episode_id] = registration

    def get(self, episode_id: str) -> RegisteredEpisode | None:
        with self._lock:
            return self._episodes.get(episode_id)


EpisodeRecord = EpisodeOpenedRecord | EpisodeStepRecord | EpisodeClosedRecord


@dataclass(frozen=True)
class StoredScreenshot:
    sha256: str
    object_key: str


class EpisodeOperationalLogError(RuntimeError):
    pass


class EpisodeOperationalLog(Protocol):
    def append(self, record: EpisodeRecord) -> None: ...

    def store_final_screenshot(
        self, *, episode_id: str, data: bytes, media_type: str
    ) -> StoredScreenshot: ...


def _screenshot_reference(
    *, episode_id: str, data: bytes, media_type: str
) -> StoredScreenshot:
    digest = sha256_bytes(data)
    extension = "png" if media_type == "image/png" else "jpg"
    return StoredScreenshot(
        sha256="sha256:" + digest,
        object_key=f"serving-final-screenshots/{episode_id}/{digest}.{extension}",
    )


def _record_key(record: EpisodeRecord) -> str:
    prefix = f"serving-episode-records/{record.episode_id}"
    if isinstance(record, EpisodeOpenedRecord):
        return f"{prefix}/opened.json"
    if isinstance(record, EpisodeStepRecord):
        return f"{prefix}/steps/{record.step_index:04d}.json"
    return f"{prefix}/closed.json"


class ImmutableEpisodeOperationalLog:
    """Put-once episode records and final screenshots with verified read-back."""

    def __init__(self, store: ImmutableStore) -> None:
        self.store = store

    def append(self, record: EpisodeRecord) -> None:
        data = canonical_json_bytes(record.to_dict()) + b"\n"
        try:
            reference = self.store.put_once(
                _record_key(record), data, media_type="application/json"
            )
            if self.store.get_verified(reference) != data:
                raise EpisodeOperationalLogError(
                    "episode operational record verification returned changed bytes"
                )
        except EpisodeOperationalLogError:
            raise
        except Exception as exc:
            raise EpisodeOperationalLogError(
                "failed to durably record serving episode operation"
            ) from exc

    def store_final_screenshot(
        self, *, episode_id: str, data: bytes, media_type: str
    ) -> StoredScreenshot:
        stored = _screenshot_reference(
            episode_id=episode_id, data=data, media_type=media_type
        )
        try:
            reference = self.store.put_once(stored.object_key, data, media_type=media_type)
            if self.store.get_verified(reference) != data:
                raise EpisodeOperationalLogError(
                    "final screenshot verification returned changed bytes"
                )
        except EpisodeOperationalLogError:
            raise
        except Exception as exc:
            raise EpisodeOperationalLogError(
                "failed to durably store the final screenshot"
            ) from exc
        return stored


class MemoryEpisodeOperationalLog:
    """Put-once in-memory implementation for contract tests."""

    def __init__(self) -> None:
        self.records: dict[str, EpisodeRecord] = {}
        self.screenshots: dict[str, tuple[bytes, str]] = {}
        self._lock = threading.RLock()

    def append(self, record: EpisodeRecord) -> None:
        key = _record_key(record)
        with self._lock:
            existing = self.records.get(key)
            if existing is not None and existing != record:
                raise EpisodeOperationalLogError("conflicting episode operational record")
            self.records[key] = record

    def store_final_screenshot(
        self, *, episode_id: str, data: bytes, media_type: str
    ) -> StoredScreenshot:
        stored = _screenshot_reference(
            episode_id=episode_id, data=data, media_type=media_type
        )
        with self._lock:
            existing = self.screenshots.get(stored.object_key)
            candidate = (data, media_type)
            if existing is not None and existing != candidate:
                raise EpisodeOperationalLogError("conflicting final screenshot")
            self.screenshots[stored.object_key] = candidate
        return stored


class _ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def _read_bounded_body(request: Request, limit: int) -> bytes:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise _ApiError(415, "unsupported_media_type", "content type must be application/json")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise _ApiError(400, "invalid_request", "content length is malformed") from exc
        if declared > limit:
            raise _ApiError(413, "request_too_large", "request body exceeds the byte limit")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise _ApiError(413, "request_too_large", "request body exceeds the byte limit")
        body.extend(chunk)
    if not body:
        raise _ApiError(400, "invalid_json", "request body must be a JSON object")
    return bytes(body)


async def _parse_body[RequestModel: BaseModel](
    request: Request, model: type[RequestModel], *, limit: int
) -> RequestModel:
    body = await _read_bounded_body(request, limit)

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant is forbidden: {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON object key")
            value[key] = item
        return value

    try:
        value = json.loads(
            body,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _ApiError(400, "invalid_json", "request body must be valid JSON") from exc
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise _ApiError(422, "invalid_request", "request violates the frozen API schema") from exc


def _decode_screenshot(
    screenshot: ScreenshotBody, *, expected_width: int, expected_height: int
) -> bytes:
    try:
        data = base64.b64decode(screenshot.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _ApiError(400, "invalid_screenshot", "screenshot is not valid base64") from exc
    if len(data) > MAX_SCREENSHOT_BYTES:
        raise _ApiError(413, "screenshot_too_large", "screenshot exceeds the byte limit")
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            actual_format = image.format
            if width > SCREEN_WIDTH or height > SCREEN_HEIGHT:
                raise _ApiError(
                    422,
                    "screenshot_dimensions_mismatch",
                    "screenshot dimensions do not match the frozen policy",
                )
            image.verify()
    except _ApiError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise _ApiError(400, "invalid_screenshot", "screenshot is not a valid image") from exc
    expected_format = "PNG" if screenshot.media_type == "image/png" else "JPEG"
    if actual_format != expected_format:
        raise _ApiError(
            415,
            "screenshot_media_type_mismatch",
            "declared media type does not match screenshot bytes",
        )
    if (width, height) != (expected_width, expected_height):
        raise _ApiError(
            422,
            "screenshot_dimensions_mismatch",
            "screenshot dimensions do not match the frozen policy",
        )
    return data


def _identity_headers(identity: ServingIdentity) -> dict[str, str]:
    return {
        "X-PixelGym-API-Version": SESSION_SCHEMA_VERSION,
        "X-PixelGym-Policy-ID": identity.policy_id,
        "X-PixelGym-Deployment-ID": identity.deployment_id,
        "X-PixelGym-Exact-Policy-Version": identity.exact_policy_version,
        "X-PixelGym-Evidence-Class": identity.evidence_class.value,
    }


def _json_response(
    payload: Mapping[str, Any], *, status_code: int = 200, identity: ServingIdentity | None = None
) -> JSONResponse:
    return JSONResponse(
        dict(payload),
        status_code=status_code,
        headers=None if identity is None else _identity_headers(identity),
    )


def _error_response(error: _ApiError, identity: ServingIdentity | None = None) -> JSONResponse:
    return _json_response(
        {"detail": {"code": error.code, "message": error.message}},
        status_code=error.status_code,
        identity=identity,
    )


def _map_host_error(exc: Exception) -> _ApiError:
    if isinstance(exc, EpisodeNotFoundError):
        return _ApiError(404, "episode_not_found", "episode was not found")
    if isinstance(exc, EpisodeEndedError):
        return _ApiError(409, "episode_ended", "episode is already over")
    if isinstance(exc, IntentReferenceError):
        return _ApiError(409, "intent_reference_invalid", "intent reference is invalid")
    if isinstance(exc, SessionConflictError):
        return _ApiError(409, "session_conflict", "episode session changed concurrently")
    if isinstance(exc, DeploymentAttemptCapError):
        return _ApiError(
            429,
            "deployment_attempt_cap_reached",
            "deployment attempt cap prevents a new episode",
        )
    if isinstance(exc, ServingEpisodeError):
        return _ApiError(503, "episode_host_unavailable", "episode host is unavailable")
    if isinstance(exc, EpisodeOperationalLogError):
        return _ApiError(
            503,
            "operational_storage_unavailable",
            "episode operational storage is unavailable",
        )
    if isinstance(exc, ValueError):
        return _ApiError(422, "invalid_request", "request was rejected by the episode host")
    if isinstance(exc, RuntimeError):
        return _ApiError(503, "episode_host_unavailable", "episode host is unavailable")
    logger.error("unexpected stateful serving failure type=%s", type(exc).__name__)
    return _ApiError(500, "internal_error", "internal server error")


def _registration_or_error(registry: EpisodeSessionRegistry, episode_id: str) -> RegisteredEpisode:
    if not episode_id_is_valid(episode_id):
        raise _ApiError(422, "invalid_episode_id", "episode id is malformed")
    registration = registry.get(episode_id)
    if registration is None:
        raise _ApiError(404, "episode_not_found", "episode was not found")
    return registration


def _close_response(record: EpisodeClosedRecord) -> dict[str, Any]:
    return {
        "schema_version": SESSION_SCHEMA_VERSION,
        "episode_id": record.episode_id,
        "identity": record.identity.to_dict(),
        "terminal_classification": record.terminal_classification.value,
        "steps": record.steps,
        "model_attempts": record.model_attempts,
        "provider_control_requests": record.provider_control_requests,
        "usage": None if record.usage is None else dict(record.usage),
        "attributed_cost_usd": record.attributed_cost_usd,
        "final_screenshot_sha256": record.final_screenshot_sha256,
    }


def create_episode_router(
    *,
    host_factory: EpisodeHostFactory,
    session_registry: EpisodeSessionRegistry,
    operational_log: EpisodeOperationalLog,
) -> APIRouter:
    """Build the S4 router without selecting or activating a deployment."""

    router = APIRouter(prefix="/api/v2")

    @router.post("/episodes")
    async def create_episode(request: Request) -> JSONResponse:
        identity: ServingIdentity | None = None
        try:
            body = await _parse_body(request, CreateEpisodeBody, limit=CREATE_BODY_LIMIT)
            if (body.screen_width, body.screen_height) != (SCREEN_WIDTH, SCREEN_HEIGHT):
                raise _ApiError(
                    422,
                    "screen_dimensions_mismatch",
                    "screen dimensions do not match the frozen policy",
                )
            host = await anyio.to_thread.run_sync(host_factory)
            identity = host.identity
            state = await anyio.to_thread.run_sync(
                partial(
                    host.create_episode,
                    task_instruction=body.task_instruction,
                    client_episode_ref=body.client_episode_ref,
                )
            )
            identity = state.identity
            registration = RegisteredEpisode(
                host=host, identity=state.identity, screen=dict(state.screen)
            )
            opened = EpisodeOpenedRecord(
                episode_id=state.episode_id,
                client_episode_ref=state.client_episode_ref,
                identity=state.identity,
                opened_at_utc=state.created_at_utc,
                task_instruction_sha256=state.task_instruction_sha256,
                screen=dict(state.screen),
                max_steps=state.max_steps,
                deployment_attempt_cap=state.deployment_attempt_cap,
            )
            try:
                await anyio.to_thread.run_sync(operational_log.append, opened)
            except Exception:
                try:
                    await anyio.to_thread.run_sync(
                        partial(
                            host.close_episode,
                            episode_id=state.episode_id,
                            final_intent_id=None,
                            final_result=None,
                            final_screenshot_sha256=None,
                            final_screenshot_object_key=None,
                        )
                    )
                except Exception as cleanup_exc:  # noqa: BLE001 - closed error surface.
                    logger.error(
                        "failed to compensate episode creation type=%s",
                        type(cleanup_exc).__name__,
                    )
                raise
            await anyio.to_thread.run_sync(
                session_registry.register, state.episode_id, registration
            )
            payload = {
                "schema_version": SESSION_SCHEMA_VERSION,
                "episode_id": state.episode_id,
                "identity": state.identity.to_dict(),
                "max_steps": state.max_steps,
                "max_model_attempts_per_action": state.max_model_attempts_per_action,
                "action_schema_version": host.package.action_schema_version,
                "key_allowlist_version": host.package.key_allowlist_version,
            }
            return _json_response(payload, status_code=201, identity=state.identity)
        except _ApiError as exc:
            return _error_response(exc)
        except Exception as exc:  # noqa: BLE001 - error mapping is intentionally closed.
            return _error_response(_map_host_error(exc), identity)

    @router.post("/episodes/{episode_id}/act")
    async def act(episode_id: str, request: Request) -> JSONResponse:
        registration: RegisteredEpisode | None = None
        try:
            registration = _registration_or_error(session_registry, episode_id)
            body = await _parse_body(request, ActEpisodeBody, limit=SCREENSHOT_BODY_LIMIT)
            screenshot = await anyio.to_thread.run_sync(
                partial(
                    _decode_screenshot,
                    body.screenshot,
                    expected_width=registration.screen["width"],
                    expected_height=registration.screen["height"],
                )
            )
            result_contract = (
                None if body.previous_result is None else body.previous_result.to_contract()
            )
            async with registration.lock:
                result = await anyio.to_thread.run_sync(
                    partial(
                        registration.host.act,
                        episode_id=episode_id,
                        screenshot=screenshot,
                        previous_intent_id=body.previous_intent_id,
                        previous_result=result_contract,
                    )
                )
                record = await anyio.to_thread.run_sync(
                    registration.host.step_record, episode_id, result.step_index
                )
                await anyio.to_thread.run_sync(operational_log.append, record)
            return _json_response(result.to_dict(), identity=registration.identity)
        except _ApiError as exc:
            return _error_response(exc, None if registration is None else registration.identity)
        except Exception as exc:  # noqa: BLE001 - error mapping is intentionally closed.
            return _error_response(
                _map_host_error(exc),
                None if registration is None else registration.identity,
            )

    @router.post("/episodes/{episode_id}/close")
    async def close_episode(episode_id: str, request: Request) -> JSONResponse:
        registration: RegisteredEpisode | None = None
        try:
            registration = _registration_or_error(session_registry, episode_id)
            body = await _parse_body(request, CloseEpisodeBody, limit=SCREENSHOT_BODY_LIMIT)
            final_bytes = None
            if body.final_screenshot is not None:
                final_bytes = await anyio.to_thread.run_sync(
                    partial(
                        _decode_screenshot,
                        body.final_screenshot,
                        expected_width=registration.screen["width"],
                        expected_height=registration.screen["height"],
                    )
                )
            final_result = None if body.final_result is None else body.final_result.to_contract()
            if final_result is not None and (
                final_bytes is None
                or final_result.screenshot_sha256 != "sha256:" + sha256_bytes(final_bytes)
            ):
                raise _ApiError(
                    409,
                    "intent_reference_invalid",
                    "intent reference is invalid",
                )
            async with registration.lock:
                state = await anyio.to_thread.run_sync(registration.host.get, episode_id)
                if state.last_intent_status.value == "issued" and (
                    body.final_intent_id != state.last_intent_id or final_result is None
                ):
                    raise _ApiError(
                        409,
                        "intent_reference_invalid",
                        "intent reference is invalid",
                    )
                if (
                    state.resume_phase.value != "closed"
                    and state.last_intent_status.value != "issued"
                    and body.final_intent_id is not None
                ):
                    raise _ApiError(
                        409,
                        "intent_reference_invalid",
                        "intent reference is invalid",
                    )
                final_screenshot_reference = None
                if final_bytes is not None and body.final_screenshot is not None:
                    final_screenshot_reference = _screenshot_reference(
                        episode_id=episode_id,
                        data=final_bytes,
                        media_type=body.final_screenshot.media_type,
                    )
                    if state.resume_phase.value != "closed":
                        final_screenshot_reference = await anyio.to_thread.run_sync(
                            partial(
                                operational_log.store_final_screenshot,
                                episode_id=episode_id,
                                data=final_bytes,
                                media_type=body.final_screenshot.media_type,
                            )
                        )
                record = await anyio.to_thread.run_sync(
                    partial(
                        registration.host.close_episode,
                        episode_id=episode_id,
                        final_intent_id=body.final_intent_id,
                        final_result=final_result,
                        final_screenshot_sha256=(
                            None
                            if final_screenshot_reference is None
                            else final_screenshot_reference.sha256
                        ),
                        final_screenshot_object_key=(
                            None
                            if final_screenshot_reference is None
                            else final_screenshot_reference.object_key
                        ),
                    )
                )
                await anyio.to_thread.run_sync(operational_log.append, record)
            return _json_response(_close_response(record), identity=registration.identity)
        except _ApiError as exc:
            return _error_response(exc, None if registration is None else registration.identity)
        except Exception as exc:  # noqa: BLE001 - error mapping is intentionally closed.
            return _error_response(
                _map_host_error(exc),
                None if registration is None else registration.identity,
            )

    @router.get("/episodes/{episode_id}")
    async def episode_status(episode_id: str) -> JSONResponse:
        registration: RegisteredEpisode | None = None
        try:
            registration = _registration_or_error(session_registry, episode_id)
            async with registration.lock:
                state = await anyio.to_thread.run_sync(registration.host.get, episode_id)
            payload = {
                "schema_version": SESSION_SCHEMA_VERSION,
                "episode_id": state.episode_id,
                "identity": state.identity.to_dict(),
                "open": state.resume_phase.value not in {"sealed", "closed"},
                "step_index": state.step_index,
                "last_intent_id": state.last_intent_id,
                "last_intent_status": state.last_intent_status.value,
            }
            return _json_response(payload, identity=registration.identity)
        except _ApiError as exc:
            return _error_response(exc, None if registration is None else registration.identity)
        except Exception as exc:  # noqa: BLE001 - error mapping is intentionally closed.
            return _error_response(
                _map_host_error(exc),
                None if registration is None else registration.identity,
            )

    return router


def mount_episode_router(
    app: Any,
    *,
    host_factory: EpisodeHostFactory,
    session_registry: EpisodeSessionRegistry,
    operational_log: EpisodeOperationalLog,
) -> None:
    """Mount S4 routes on an existing service without coupling them to v1 runtime state."""

    app.include_router(
        create_episode_router(
            host_factory=host_factory,
            session_registry=session_registry,
            operational_log=operational_log,
        )
    )
