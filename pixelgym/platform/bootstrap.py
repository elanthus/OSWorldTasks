"""Explicit application factory for the local Compose stack."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from pixelgym.platform.contracts import PolicyManifest
from pixelgym.platform.control_store import ControlStore, DeploymentRecord
from pixelgym.platform.deployment import DeploymentCoordinator
from pixelgym.platform.deployment_smoke import CandidateServiceSmoke, FrozenSmokeFixture
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.immutable_store import LocalImmutableStore, S3ImmutableStore
from pixelgym.platform.mlflow_tracking import MlflowTracking
from pixelgym.platform.operational_log import ImmutableOperationalLog
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
        self, *, image_bytes: bytes, media_type: str, target: str, policy: PolicyManifest
    ) -> tuple[str | None, str, float | None, dict[str, Any] | None]:
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
            # Serving audit writes must not outlive the bounded request lifecycle.
            retry_max_attempts=1,
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
    processes: dict[str, subprocess.Popen[str]] | None = None,
    process_lock: threading.Lock | None = None,
    cancelled_submissions: set[str] | None = None,
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
        "--provider-concurrency",
        "1",
        "--max-workers",
        "1",
    ]
    process: subprocess.Popen[str]
    if processes is not None and process_lock is not None:
        with process_lock:
            if (
                control.get_submission(submission_id)["status"] == "Cancelled"
                or cancelled_submissions is not None
                and submission_id in cancelled_submissions
            ):
                if cancelled_submissions is not None:
                    cancelled_submissions.discard(submission_id)
                return
            # Hold the same lock used by cancellation across process creation and
            # registration. A cancellation callback therefore cannot observe an
            # already-started but unregistered worker.
            process = subprocess.Popen(command, cwd=repository_root, text=True)
            processes[submission_id] = process
    else:
        if control.get_submission(submission_id)["status"] == "Cancelled":
            return
        process = subprocess.Popen(command, cwd=repository_root, text=True)
    returncode = process.wait()
    was_cancelled = False
    if processes is not None and process_lock is not None:
        with process_lock:
            processes.pop(submission_id, None)
            if cancelled_submissions is not None:
                was_cancelled = submission_id in cancelled_submissions
                cancelled_submissions.discard(submission_id)
    if (
        returncode != 0
        and not was_cancelled
        and control.get_submission(submission_id)["status"] != "Cancelled"
    ):
        control.mark_submission(submission_id, "Failed")


def _record_cancellation_intent(
    control: ControlStore,
    submission_id: str,
    *,
    process_lock: threading.Lock,
    cancelled_submissions: set[str],
) -> bool:
    """Coordinate a committed cancellation with worker startup/registration."""
    with process_lock:
        try:
            status = control.get_submission(submission_id)["status"]
        except KeyError:
            return False
        if status != "Cancelled":
            return False
        cancelled_submissions.add(submission_id)
        return True


def create_app(bind_address: str | None = None) -> FastAPI:
    """Construct dependencies, validate migrated state, and return the mounted application."""
    resolved_bind_address = (
        bind_address
        if bind_address is not None
        else os.environ.get("PIXELGYM_BIND_ADDRESS", "127.0.0.1")
    )
    proxy_allowlist = tuple(
        address.strip()
        for address in os.environ.get("PIXELGYM_TRUSTED_PROXY_ADDRESSES", "").split(",")
        if address.strip()
    )
    try:
        loopback_bind = resolved_bind_address.lower() == "localhost" or ipaddress.ip_address(
            resolved_bind_address
        ).is_loopback
    except ValueError as exc:
        raise ValueError("PIXELGYM_BIND_ADDRESS must be localhost or an IP address") from exc
    if not loopback_bind and not proxy_allowlist:
        raise RuntimeError(
            "a non-loopback PIXELGYM_BIND_ADDRESS requires PIXELGYM_TRUSTED_PROXY_ADDRESSES"
        )
    repository_root = _repository_root()
    csrf_secret = os.environ.get("PIXELGYM_CSRF_SECRET")
    if not csrf_secret:
        raise RuntimeError("PIXELGYM_CSRF_SECRET is required")

    control = _build_control(repository_root)
    immutable_store = _build_immutable_store(repository_root)
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    tracking = None
    if tracking_uri:
        try:
            tracking = MlflowTracking(tracking_uri)
        except Exception as exc:  # noqa: BLE001 - optional MLflow clients expose varied failures.
            # Tracking is a discoverability mirror. The transactional control plane
            # remains authoritative when MLflow is unavailable during startup.
            control.record_tracking_reconciliation(
                subject_id="mlflow-tracking",
                operation="initialize_tracking",
                error=f"{type(exc).__name__}: {exc}",
                resolved=False,
            )
    runtime = PolicyRuntime()
    serving_provider = DemoReplayServingProvider(repository_root)

    smoke_candidate = CandidateServiceSmoke(
        FrozenSmokeFixture.load(repository_root), serving_provider
    )

    def activate_runtime(deployment: DeploymentRecord, prepared: LoadedPolicy | bool) -> None:
        if not isinstance(prepared, LoadedPolicy):
            raise TypeError("deployment activation did not receive a loaded candidate runtime")
        # The only mutation of the traffic runtime happens after the database CAS succeeds.
        runtime.activate(replace(prepared, deployment_id=deployment.deployment_id))

    coordinator = DeploymentCoordinator(
        control=control,
        store=immutable_store,
        load_and_smoke=smoke_candidate,
        on_activated=activate_runtime,
        tracking=tracking,
    )
    coordinator.restore_active()

    scheduled: set[str] = set()
    schedule_lock = threading.Lock()
    processes: dict[str, subprocess.Popen[str]] = {}
    cancelled_submissions: set[str] = set()
    process_lock = threading.Lock()

    def schedule_submission(submission_id: str, payload: dict[str, str]) -> None:
        with schedule_lock:
            if submission_id in scheduled:
                return
            scheduled.add(submission_id)
        worker = threading.Thread(
            target=_run_flow,
            args=(
                repository_root,
                control,
                submission_id,
                dict(payload),
                processes,
                process_lock,
                cancelled_submissions,
            ),
            daemon=True,
            name=submission_id,
        )
        worker.start()

    def cancel_submission(submission_id: str) -> bool:
        """Record worker intent; the control-plane Cancelled state stops the flow safely."""
        return _record_cancellation_intent(
            control,
            submission_id,
            process_lock=process_lock,
            cancelled_submissions=cancelled_submissions,
        )

    app = create_control_app(
        control,
        coordinator=coordinator,
        csrf_secret=csrf_secret,
        bind_address=resolved_bind_address,
        principal_header=os.environ.get("PIXELGYM_PRINCIPAL_HEADER", "X-Forwarded-User"),
        trusted_proxy_addresses=proxy_allowlist,
        submit_callback=schedule_submission,
        cancel_callback=cancel_submission,
        tracking=tracking,
        mlflow_base_url=os.environ.get("PIXELGYM_MLFLOW_PUBLIC_URL", "http://localhost:5000"),
    )
    app.mount(
        "/", create_serving_app(runtime, operational_log=ImmutableOperationalLog(immutable_store))
    )
    app.state.deployment_coordinator = coordinator
    app.state.policy_runtime = runtime
    if tracking is not None:

        def run_reconciliation() -> None:
            try:
                coordinator.reconcile_tracking()
            except Exception as exc:  # noqa: BLE001 - optional clients expose varied failures.
                control.record_tracking_reconciliation(
                    subject_id="mlflow-tracking",
                    operation="startup_reconciliation",
                    error=f"{type(exc).__name__}: {exc}",
                    resolved=False,
                )

        reconciliation = threading.Thread(
            target=run_reconciliation,
            daemon=True,
            name="tracking-reconciliation",
        )
        reconciliation.start()
        app.state.tracking_reconciliation_thread = reconciliation
    return app
