"""Metaflow graph for a frozen, deterministic grounding evaluation.

Metaflow supplies inspectable boundaries, deterministic shard fan-out, and task resumption.
Content-addressed raw-response storage prevents calls from being repeated after durable persistence.
A stronger receipt-before-persistence billing guarantee additionally requires provider-side
idempotency for the deterministic request ID; the runtime fixture exercises that distinction.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable
from dataclasses import asdict
from functools import wraps
from pathlib import Path
from typing import Any

from metaflow import FlowSpec, Parameter, current, step

from pixelgym.platform.contracts import (
    ArtifactRef,
    GatePolicy,
    GateReport,
    PolicyManifest,
    RunSummary,
)
from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.evaluation import EvaluationRunner, ScriptedReplayProvider
from pixelgym.platform.fingerprints import build_dataset_manifest, canonical_json_bytes
from pixelgym.platform.immutable_store import LocalImmutableStore, S3ImmutableStore
from pixelgym.platform.mlflow_tracking import MlflowTracking
from pixelgym.platform.policy import PROMPT_NAME, build_policy_manifest, prompt_template

_TEST_HOOKS_ENV = "PIXELGYM_ENABLE_TEST_HOOKS"
SCRIPTED_MODEL_VARIANTS = {
    (1, "day3-replay-baseline-v1"): "baseline",
    (2, "day3-replay-revised-v2"): "revised",
    (2, "day3-replay-revised-rollback-seed-v1"): "revised",
}


def _root() -> Path:
    return Path(os.environ.get("PIXELGYM_REPOSITORY_ROOT", Path.cwd())).resolve()


def _store() -> object:
    bucket = os.environ.get("PIXELGYM_IMMUTABLE_BUCKET")
    if bucket:
        return S3ImmutableStore(
            bucket=bucket,
            prefix=os.environ.get("PIXELGYM_IMMUTABLE_PREFIX", "platform"),
            object_lock=os.environ.get("PIXELGYM_OBJECT_LOCK", "true").lower() == "true",
            retention_days=int(os.environ.get("PIXELGYM_RETENTION_DAYS", "30")),
        )
    return LocalImmutableStore(
        Path(os.environ.get("PIXELGYM_IMMUTABLE_ROOT", _root() / ".cache/platform/immutable"))
    )


def _tracking() -> object:
    return MlflowTracking(os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"))


def _control() -> ControlStore:
    return ControlStore(
        os.environ.get("PIXELGYM_CONTROL_DB", str(_root() / ".cache/platform/control.db")),
        reviewer_identity=os.environ.get("PIXELGYM_REVIEWER_ID", "local-reviewer"),
    )


def _test_fail_once(name: str) -> None:
    """Raise once at a named boundary when explicitly enabled by a runtime test."""
    if os.environ.get(_TEST_HOOKS_ENV) != "1":
        return
    if os.environ.get("PIXELGYM_TEST_FAIL_ONCE") != name:
        return
    state_root = os.environ.get("PIXELGYM_TEST_STATE_ROOT")
    if not state_root:
        raise RuntimeError("test failure injection requires PIXELGYM_TEST_STATE_ROOT")
    marker_root = Path(state_root)
    marker_root.mkdir(parents=True, exist_ok=True)
    marker = marker_root / f"{name}.triggered"
    try:
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    os.close(descriptor)
    raise RuntimeError(f"injected one-shot failure after {name}")


def _provider(flow: object) -> object:
    variant = SCRIPTED_MODEL_VARIANTS.get((flow.prompt_version, flow.model))
    if variant is None:
        raise ValueError("prompt/model pairing is outside the scripted allowlist")
    ledger = os.environ.get("PIXELGYM_TEST_PROVIDER_LEDGER")
    if ledger:
        if os.environ.get(_TEST_HOOKS_ENV) != "1":
            raise RuntimeError("the ledgered provider is available only with explicit test hooks")
        from pixelgym.platform.runtime_fixture import LedgeredScriptedReplayProvider

        return LedgeredScriptedReplayProvider(
            _root() / "artifacts/grounding-predictions.jsonl",
            variant=variant,
            ledger_path=Path(ledger),
            concurrency_barrier=int(
                os.environ.get("PIXELGYM_TEST_CONCURRENCY_BARRIER", "1")
            ),
        )
    return ScriptedReplayProvider(
        _root() / "artifacts/grounding-predictions.jsonl",
        variant=variant,
        model=flow.model,
    )


def _runner(flow: object, *, with_tracking: bool = False) -> EvaluationRunner:
    return EvaluationRunner(
        repository_root=_root(),
        store=_store(),
        tracking=_tracking() if with_tracking else None,
        provider=_provider(flow),
        policy=PolicyManifest(**flow.policy),
        gate_policy=GatePolicy(**flow.gate_policy),
        dataset_fingerprint=flow.dataset_fingerprint,
        submission_id=flow.submission_id,
        metaflow_pathspec=flow.metaflow_pathspec,
        provider_response_hook=(
            (lambda request_id: _test_fail_once("provider_response_received"))
            if os.environ.get(_TEST_HOOKS_ENV) == "1"
            else None
        ),
    )


def _record_failure(flow: object) -> None:
    """Best-effort terminal evidence without hiding the step's original exception."""
    with contextlib.suppress(Exception):
        flow.mlflow_run_id = _runner(flow, with_tracking=True).finalize_failure(
            run_id=getattr(flow, "mlflow_run_id", None)
        )
    with contextlib.suppress(Exception):
        _control().mark_submission(flow.submission_id, "Failed")


def _finalize_on_error(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapped(flow: object, *args: object, **kwargs: object) -> Any:
        try:
            return method(flow, *args, **kwargs)
        except BaseException:
            _record_failure(flow)
            raise

    return wrapped


class GroundingEvaluationFlow(FlowSpec):
    submission_id = Parameter("submission-id", required=True)
    prompt_version = Parameter("prompt-version", type=int, required=True)
    model = Parameter("model", required=True)
    maximum_calls = Parameter("maximum-calls", type=int, default=100)
    shard_size = Parameter("shard-size", type=int, default=25)

    @step
    def start(self) -> None:
        if (self.prompt_version, self.model) not in SCRIPTED_MODEL_VARIANTS:
            raise ValueError("prompt/model pairing is outside the scripted allowlist")
        if self.maximum_calls != 100:
            raise ValueError("the frozen flow requires exactly 100 maximum calls")
        if not 1 <= self.shard_size <= self.maximum_calls:
            raise ValueError("shard size must be between one and maximum calls")
        self.next(self.validate_and_freeze_inputs)

    @step
    def validate_and_freeze_inputs(self) -> None:
        root = _root()
        manifest, fingerprint = build_dataset_manifest(
            repository_root=root,
            dataset_path=root / "artifacts/grounding-dataset.jsonl",
            overlays_path=root / "artifacts/grounding-overlays.jsonl",
        )
        self.dataset_manifest_ref = asdict(
            _store().put_once(
                f"datasets/{fingerprint.removeprefix('sha256:')}.json",
                canonical_json_bytes(manifest) + b"\n",
                media_type="application/vnd.pixelgym.dataset-manifest+json",
            )
        )
        self.dataset_fingerprint = fingerprint
        self.gate_policy = json.loads((root / "config/promotion-gates.demo-v1.json").read_text())
        code_revision = os.environ.get("PIXELGYM_CODE_REVISION", "unknown-dirty")
        lock_digest = __import__("hashlib").sha256((root / "pyproject.toml").read_bytes()).hexdigest()
        self.policy = asdict(
            build_policy_manifest(
                provider="scripted-demo",
                model=self.model,
                prompt_name=PROMPT_NAME,
                prompt_version=self.prompt_version,
                prompt=prompt_template(self.prompt_version),
                condition="raw",
                parameters={"deterministic": True, "hidden_retries": 0},
                parser_version="pixelgym-grounding-parser-v1",
                scorer_version="pixelgym-point-inside-half-open-box-v1",
                overlay_version="none-raw-coordinate-policy",
                target_semantics="requested-control-center-point-v1",
                code_revision=code_revision,
                dependency_lock_sha256=lock_digest,
            )
        )
        self.next(self.create_or_recover_mlflow_run)

    @step
    @_finalize_on_error
    def create_or_recover_mlflow_run(self) -> None:
        lineage_run_id = getattr(current, "origin_run_id", None) or current.run_id
        self.metaflow_pathspec = f"{current.flow_name}/{lineage_run_id}"
        self.mlflow_run_id = _runner(self, with_tracking=True).create_or_recover_run(
            max_calls=self.maximum_calls
        )
        _control().link_run(
            self.submission_id,
            metaflow_pathspec=self.metaflow_pathspec,
            mlflow_run_id=self.mlflow_run_id,
        )
        _test_fail_once("run_linked")
        self.next(self.build_shards)

    @step
    @_finalize_on_error
    def build_shards(self) -> None:
        self.shards = _runner(self).build_shards(
            shard_size=self.shard_size,
            max_calls=self.maximum_calls,
        )
        self.next(self.evaluate_shard, foreach="shards")

    @step
    @_finalize_on_error
    def evaluate_shard(self) -> None:
        self.raw_responses = _runner(self).evaluate_shard(
            self.input,
            max_calls=self.maximum_calls,
        )
        _test_fail_once("raw_responses_persisted")
        self.next(self.join_responses)

    @step
    @_finalize_on_error
    def join_responses(self, inputs: list[GroundingEvaluationFlow]) -> None:
        sources = list(inputs)
        if not sources:
            raise ValueError("shard join requires at least one branch")
        source = sources[0]
        for name in (
            "policy",
            "gate_policy",
            "dataset_fingerprint",
            "dataset_manifest_ref",
            "metaflow_pathspec",
            "mlflow_run_id",
        ):
            setattr(self, name, getattr(source, name))
        self.raw_responses = _runner(self).canonical_join(
            [branch.raw_responses for branch in sources]
        )
        self.next(self.verify_raw_artifacts)

    @step
    @_finalize_on_error
    def verify_raw_artifacts(self) -> None:
        self.verified_responses = _runner(self).verify_raw_artifacts(self.raw_responses)
        self.raw_artifacts_verified = True
        self.next(self.parse_and_score)

    @step
    @_finalize_on_error
    def parse_and_score(self) -> None:
        if self.raw_artifacts_verified is not True:
            raise ValueError("raw artifacts must verify before parsing")
        self.records = _runner(self).parse_and_score(self.verified_responses)
        self.next(self.aggregate_metrics)

    @step
    @_finalize_on_error
    def aggregate_metrics(self) -> None:
        summary = _runner(self).aggregate_metrics(self.records, run_id=self.mlflow_run_id)
        self.summary = summary.to_dict()
        self.next(self.evaluate_gates)

    @step
    @_finalize_on_error
    def evaluate_gates(self) -> None:
        runner = _runner(self)
        report = runner.evaluate_gates(RunSummary(**self.summary))
        self.report = report.to_dict()
        self.references = [
            item.to_dict()
            for item in runner.persist_evidence(
                self.records,
                report,
                self.raw_responses,
            )
        ]
        _test_fail_once("evidence_persisted")
        self.next(self.finalize_mlflow_run)

    @step
    @_finalize_on_error
    def finalize_mlflow_run(self) -> None:
        # Candidate registration is deliberately downstream: a run that cannot finalize must
        # never leave an Eligible control-plane record behind.
        _runner(self, with_tracking=True).finalize_success(
            RunSummary(**self.summary),
            GateReport.from_dict(self.report),
            [ArtifactRef(**value) for value in self.references],
        )
        _test_fail_once("mlflow_finalized")
        self.next(self.register_candidate)

    @step
    def register_candidate(self) -> None:
        try:
            record = _control().register_candidate(
                source_run_id=self.summary["run_id"],
                policy=PolicyManifest(**self.policy),
                gate_report=GateReport.from_dict(self.report),
                artifacts=[ArtifactRef(**value) for value in self.references],
                submission_id=self.submission_id,
            )
        except BaseException:
            _record_failure(self)
            raise
        self.candidate_id = record.candidate_id
        # Registration and submission completion are one committed control-plane transaction.
        # A failure after that commit must not rewrite the already-complete run as Failed; resume
        # simply replays this idempotent terminal step.
        _test_fail_once("candidate_registered")
        self.next(self.end)

    @step
    @_finalize_on_error
    def end(self) -> None:
        pass


if __name__ == "__main__":
    GroundingEvaluationFlow()
