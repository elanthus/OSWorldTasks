"""Bounded versioned grounding inference API pinned to one approved policy."""

from __future__ import annotations

import base64
import binascii
import io
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from pixelgym.grounding.evaluation import parse_prediction
from pixelgym.platform.contracts import PolicyManifest

API_SCHEMA_VERSION = "pixelgym-grounding-api-v1"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_DIMENSION = 4096
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


def create_serving_app(runtime: PolicyRuntime) -> FastAPI:
    app = FastAPI(title="PixelGym Grounding API", docs_url=None, redoc_url=None)

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
            raise HTTPException(503, "no approved policy is loaded")
        loaded = runtime.loaded
        try:
            raw, request_id, _latency_ms, _usage = loaded.provider.ground(
                image_bytes=image,
                media_type=request.media_type,
                target=target,
                policy=loaded.manifest,
            )
        except ProviderFailure as exc:
            status = 504 if exc.code == "timeout" else 429 if exc.code == "rate_limit" else 502
            raise HTTPException(status, f"provider request failed: {exc.code}") from exc
        parsed = parse_prediction(raw, condition="raw", width=width, height=height, marks=[])
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
            provider_request_id=request_id,
        )

    return app
