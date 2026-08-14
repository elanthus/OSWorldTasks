"""Environment-only application wiring for the local Compose stack."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.deployment import DeploymentCoordinator
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.immutable_store import LocalImmutableStore, S3ImmutableStore
from pixelgym.platform.service import LoadedPolicy, PolicyRuntime, create_serving_app
from pixelgym.platform.web import create_control_app

ROOT = Path(os.environ.get("PIXELGYM_REPOSITORY_ROOT", Path.cwd())).resolve()
CONTROL_DB = os.environ.get("PIXELGYM_CONTROL_DB", str(ROOT / ".cache/platform/control.db"))
REVIEWER = os.environ.get("PIXELGYM_REVIEWER_ID", "local-reviewer")
control = ControlStore(CONTROL_DB, reviewer_identity=REVIEWER)
immutable_store = (
    S3ImmutableStore(
        bucket=os.environ["PIXELGYM_IMMUTABLE_BUCKET"],
        prefix=os.environ.get("PIXELGYM_IMMUTABLE_PREFIX", "platform"),
        object_lock=os.environ.get("PIXELGYM_OBJECT_LOCK", "true").lower() == "true",
        retention_days=int(os.environ.get("PIXELGYM_RETENTION_DAYS", "30")),
    )
    if os.environ.get("PIXELGYM_IMMUTABLE_BUCKET")
    else LocalImmutableStore(
        Path(os.environ.get("PIXELGYM_IMMUTABLE_ROOT", ROOT / ".cache/platform/immutable"))
    )
)


class DemoReplayServingProvider:
    """Scripted demo provider; mappings are provider fixtures, never policy package data."""

    def __init__(self) -> None:
        examples = [
            json.loads(line)
            for line in (ROOT / "artifacts/grounding-dataset.jsonl").read_text().splitlines()
            if line.strip()
        ]
        predictions = [
            json.loads(line)
            for line in (ROOT / "artifacts/grounding-predictions.jsonl").read_text().splitlines()
            if line.strip()
        ]
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
                {"x": int(row["point"][0]), "y": int(row["point"][1])}, separators=(",", ":")
            )
            for row in predictions
            if row["condition"] == "marks"
        }

    def ground(self, *, image_bytes: bytes, media_type: str, target: str, policy: object) -> tuple:
        del media_type
        example_id = self.examples.get((hashlib.sha256(image_bytes).hexdigest(), target))
        responses = self.baseline if policy.prompt_version == 1 else self.revised
        raw = responses.get(example_id) if example_id else None
        request_id = "sha256:" + sha256_bytes(
            canonical_json_bytes(
                {"image_sha256": hashlib.sha256(image_bytes).hexdigest(), "target": target, "policy_id": policy.policy_id}
            )
        )
        return raw, request_id, 25.0, {"input_tokens": 0, "output_tokens": 0}


runtime = PolicyRuntime()
serving_provider = DemoReplayServingProvider()


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
_scheduled: set[str] = set()
_schedule_lock = threading.Lock()


def _run_flow(submission_id: str, payload: dict[str, str]) -> None:
    command = [
        sys.executable,
        str(ROOT / "flows/grounding_evaluation_flow.py"),
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
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        control.mark_submission(submission_id, "Failed")


def schedule_submission(submission_id: str, payload: dict[str, str]) -> None:
    with _schedule_lock:
        if submission_id in _scheduled:
            return
        _scheduled.add(submission_id)
    worker = threading.Thread(
        target=_run_flow, args=(submission_id, dict(payload)), daemon=True, name=submission_id
    )
    worker.start()


control_app = create_control_app(
    control,
    coordinator=coordinator,
    csrf_secret=os.environ.get("PIXELGYM_CSRF_SECRET", "local-demo-csrf-secret-change-me"),
    submit_callback=schedule_submission,
    mlflow_base_url=os.environ.get("PIXELGYM_MLFLOW_PUBLIC_URL", "http://localhost:5000"),
)
control_app.mount("/", create_serving_app(runtime))
