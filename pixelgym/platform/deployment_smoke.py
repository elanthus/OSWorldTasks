"""No-cost, isolated deployment smoke checks for the assembled serving app."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from pixelgym.platform.control_store import CandidateRecord
from pixelgym.platform.operational_log import MemoryOperationalLog
from pixelgym.platform.service import (
    LoadedPolicy,
    PolicyRuntime,
    ServingProvider,
    create_serving_app,
)
from pixelgym.serialization import load_jsonl


class DeploymentSmokeError(RuntimeError):
    """A candidate did not load, become ready, or produce the frozen smoke result."""


@dataclass(frozen=True)
class FrozenSmokeFixture:
    """One hash-pinned local request and its deterministic replay result."""

    image_bytes: bytes
    image_sha256: str
    target: str
    expected_by_prompt_version: dict[int, dict[str, int]]

    @classmethod
    def load(cls, repository_root: Path) -> FrozenSmokeFixture:
        example = load_jsonl(repository_root / "artifacts/grounding-dataset.jsonl")[0]
        image_bytes = (repository_root / example["image_path"]).read_bytes()
        image_sha256 = hashlib.sha256(image_bytes).hexdigest()
        if image_sha256 != example["image_sha256"]:
            raise DeploymentSmokeError("frozen smoke fixture image digest mismatch")
        predictions = load_jsonl(repository_root / "artifacts/grounding-predictions.jsonl")
        raw = next(
            row for row in predictions
            if row["example_id"] == example["example_id"] and row["condition"] == "raw"
        )
        marks = next(
            row for row in predictions
            if row["example_id"] == example["example_id"] and row["condition"] == "marks"
        )
        return cls(
            image_bytes=image_bytes,
            image_sha256=image_sha256,
            target=example["target"],
            expected_by_prompt_version={
                1: json.loads(raw["raw_response"]),
                2: {"x": int(marks["point"][0]), "y": int(marks["point"][1])},
            },
        )


class CandidateServiceSmoke:
    """Load a candidate off-line and exercise its real serving contract before activation."""

    def __init__(self, fixture: FrozenSmokeFixture, provider: ServingProvider) -> None:
        self.fixture = fixture
        self.provider = provider

    def __call__(self, candidate: CandidateRecord) -> LoadedPolicy:
        expected = self.fixture.expected_by_prompt_version.get(candidate.policy.prompt_version)
        if expected is None:
            raise DeploymentSmokeError("candidate has no frozen smoke expectation")
        loaded = LoadedPolicy(
            manifest=candidate.policy,
            deployment_id=f"smoke-{candidate.candidate_id}",
            exact_policy_version=candidate.candidate_id,
            provider=self.provider,
        )
        # This runtime and app are deliberately separate from the traffic-serving runtime.
        candidate_runtime = PolicyRuntime(loaded)
        client = TestClient(create_serving_app(candidate_runtime, operational_log=MemoryOperationalLog()))
        ready = client.get("/health/ready")
        if ready.status_code != 200 or ready.json() != {
            "status": "ready", "policy_id": candidate.policy.policy_id
        }:
            raise DeploymentSmokeError("candidate readiness check failed")
        policy = client.get("/api/v1/policy")
        if policy.status_code != 200 or policy.json() != {
            "schema_version": "pixelgym-grounding-api-v1",
            "policy_id": candidate.policy.policy_id,
            "deployment_id": loaded.deployment_id,
            "exact_policy_version": candidate.candidate_id,
            "provider": candidate.policy.provider,
            "model": candidate.policy.model,
            "prompt_version": candidate.policy.prompt_version,
        }:
            raise DeploymentSmokeError("candidate policy identity check failed")
        response = client.post(
            "/api/v1/ground",
            json={
                "image_base64": base64.b64encode(self.fixture.image_bytes).decode("ascii"),
                "media_type": "image/png",
                "target": self.fixture.target,
            },
        )
        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise DeploymentSmokeError("candidate smoke response was not JSON") from exc
        expected_keys = {
            "schema_version", "prediction", "parse_status", "parse_error", "policy_id",
            "deployment_id", "exact_policy_version", "provider_request_id",
        }
        if response.status_code != 200 or set(body) != expected_keys:
            raise DeploymentSmokeError("candidate smoke response contract failed")
        if (
            body["prediction"] != expected
            or body["parse_status"] != "parsed"
            or body["parse_error"] is not None
            or body["policy_id"] != candidate.policy.policy_id
            or body["deployment_id"] != loaded.deployment_id
            or body["exact_policy_version"] != candidate.candidate_id
            or not body["provider_request_id"].startswith("sha256:")
            or response.headers.get("X-PixelGym-Policy-ID") != candidate.policy.policy_id
            or response.headers.get("X-PixelGym-Deployment-ID") != loaded.deployment_id
        ):
            raise DeploymentSmokeError("candidate deterministic smoke result or identity check failed")
        return loaded
