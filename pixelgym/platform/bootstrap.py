"""Explicit application factory for the local Compose stack."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import FastAPI

from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.deployment import DeploymentCoordinator
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.immutable_store import LocalImmutableStore, S3ImmutableStore
from pixelgym.platform.service import LoadedPolicy, PolicyRuntime, create_serving_app
from pixelgym.platform.web import create_control_app
from pixelgym.serialization import load_jsonl


class DemoReplayServingProvider:
    """Scripted demo provider; mappings are provider fixtures, never policy package data."""

    def __init__(self, repository_root: Path) -> None:
        examples = load_jsonl(repository_root / "artifacts/grounding-dataset.jsonl")
        predictions = load_jsonl(repository_root / "artifacts/grounding-predictions.jsonl")
        self.examples = {
            (row["image_sha256"], row["target"]): row["example_id"] for row in examples
        }
        self.baseline = {
            row["example_id"]: row["raw_response"]
            for row in predictions
            if row["condition"] == "raw"
        }
        self.revised = {
            row["example_id"]: json.dumps(
                {"x": int(row["point"][0]), "y": int(row["point"][1])},
                separators=(",", ":"),
            )
            for row in predictions
            if row["condition"] == "marks"
        }

    def ground(
        self, *, image_bytes: bytes, media_type: str, target: str, policy: object
    ) -> tuple:
        del media_type
        image_sha256 = hashlib.sha256(image_bytes).hexdigest()
        example_id = self.examples.get((image_sha256, target))
        responses = self.baseline if policy.prompt_version == 1 else self.revised
        raw = responses.get(example_id) if example_id else None
        request_id = "sha256:" + sha256_bytes(
            canonical_json_bytes(
                {
                    "image_sha256": image_sha256,
                    "target": target,
                    "policy_id": policy.policy_id,
                }
            )
        )
        return raw, request_id, 25.0, {"input_tokens": 0, "output_tokens": 0}


def _repository_root() -> Path:
    return Path(os.environ.get("PIXELGYM_REPOSITORY_ROOT", Path.cwd())).resolve()


def _build_control(repository_root: Path) -> ControlStore:
    database = os.environ.get(
        "PIXELGYM_CONTROL_DB", str(repository_root / ".cache/platform/control.db")
    )
    if database != ":memory:" and not database.startswith("file:"):
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    control = ControlStore(
        database,
        reviewer_identity=os.environ.get("PIXELGYM_REVIEWER_ID", "local-reviewer"),
    )
    control.require_migrated()
    return control


def _build_immutable_store(repository_root: Path) -> LocalImmutableStore | S3ImmutableStore:
    bucket = os.environ.get("PIXELGYM_IMMUTABLE_BUCKET")
    if bucket:
        return S3ImmutableStore(
            bucket=bucket,
            prefix=os.environ.get("PIXELGYM_IMMUTABLE_PREFIX", "platform"),
            object_lock=os.environ.get("PIXELGYM_OBJECT_LOCK", "true").lower() == "true",
            retention_days=int(os.environ.get("PIXELGYM_RETENTION_DAYS", "30")),
        )
    return LocalImmutableStore(
        Path(
            os.environ.get(
                "PIXELGYM_IMMUTABLE_ROOT",
                repository_root / ".cache/platform/immutable",
            )
        )
    )


def _run_flow(
    repository_root: Path,
    control: ControlStore,
    submission_id: str,
    payload: dict[str, str],
) -> None:
    command = [
        sys.executable,
        str(repository_root / "flows/grounding_evaluation_flow.py"),
        "run",
        "--submission-id",
        submission_id,
        "--prompt-version",
        payload["prompt_version"],
        "--model",
        payload["model"],
        "--maximum-calls",
        payload["maximum_calls"],
        "--max-workers",
        "1",
    ]
    completed = subprocess.run(command, cwd=repository_root, check=False)
    if completed.returncode != 0:
        control.mark_submission(submission_id, "Failed")


def create_app() -> FastAPI:
    """Construct dependencies, validate migrated state, and return the mounted application."""
    repository_root = _repository_root()
    csrf_secret = os.environ.get("PIXELGYM_CSRF_SECRET")
    if not csrf_secret:
        raise RuntimeError("PIXELGYM_CSRF_SECRET is required")

    control = _build_control(repository_root)
    immutable_store = _build_immutable_store(repository_root)
    runtime = PolicyRuntime()
    serving_provider = DemoReplayServingProvider(repository_root)

    def activate_runtime(deployment: object) -> None:
        candidate = control.get_candidate(deployment.candidate_id)
        runtime.activate(
            LoadedPolicy(
                manifest=candidate.policy,
                deployment_id=deployment.deployment_id,
                exact_policy_version=candidate.candidate_id,
                provider=serving_provider,
                approved=candidate.state.value == "Approved",
                gate_passed=bool(candidate.gate_report["overall_passed"]),
            )
        )

    coordinator = DeploymentCoordinator(
        control=control,
        store=immutable_store,
        load_and_smoke=lambda policy: policy.condition == "raw",
        on_activated=activate_runtime,
    )
    active, _generation = control.active()
    if active is not None:
        activate_runtime(active)

    scheduled: set[str] = set()
    schedule_lock = threading.Lock()

    def schedule_submission(submission_id: str, payload: dict[str, str]) -> None:
        with schedule_lock:
            if submission_id in scheduled:
                return
            scheduled.add(submission_id)
        worker = threading.Thread(
            target=_run_flow,
            args=(repository_root, control, submission_id, dict(payload)),
            daemon=True,
            name=submission_id,
        )
        worker.start()

    app = create_control_app(
        control,
        coordinator=coordinator,
        csrf_secret=csrf_secret,
        submit_callback=schedule_submission,
        mlflow_base_url=os.environ.get(
            "PIXELGYM_MLFLOW_PUBLIC_URL", "http://localhost:5000"
        ),
    )
    app.mount("/", create_serving_app(runtime))
    return app
