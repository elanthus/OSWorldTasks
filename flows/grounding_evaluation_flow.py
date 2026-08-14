"""Production-compatible local Metaflow for one frozen grounding evaluation.

The MVP deliberately uses one bounded deterministic shard. Increasing the shard count requires no
contract change, but paid-provider concurrency remains a separately approved human decision.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from metaflow import FlowSpec, Parameter, current, step

from pixelgym.platform.contracts import ArtifactRef, GatePolicy, GateReport, PolicyManifest
from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.evaluation import EvaluationRunner, ScriptedReplayProvider
from pixelgym.platform.fingerprints import build_dataset_manifest, canonical_json_bytes
from pixelgym.platform.immutable_store import LocalImmutableStore, S3ImmutableStore
from pixelgym.platform.mlflow_tracking import MlflowTracking
from pixelgym.platform.policy import PROMPT_NAME, build_policy_manifest, prompt_template


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


class GroundingEvaluationFlow(FlowSpec):
    submission_id = Parameter("submission-id", required=True)
    prompt_version = Parameter("prompt-version", type=int, required=True)
    model = Parameter("model", required=True)
    maximum_calls = Parameter("maximum-calls", type=int, default=100)

    @step
    def start(self) -> None:
        allowed = {
            1: "day3-replay-baseline-v1",
            2: "day3-replay-revised-v2",
        }
        if allowed.get(self.prompt_version) != self.model:
            raise ValueError("prompt/model pairing is outside the scripted allowlist")
        if self.maximum_calls != 100:
            raise ValueError("the frozen flow requires exactly 100 maximum calls")
        self.next(self.validate_and_freeze_inputs)

    @step
    def validate_and_freeze_inputs(self) -> None:
        root = _root()
        manifest, fingerprint = build_dataset_manifest(
            repository_root=root,
            dataset_path=root / "artifacts/grounding-dataset.jsonl",
            overlays_path=root / "artifacts/grounding-overlays.jsonl",
        )
        store = _store()
        self.dataset_manifest_ref = asdict(
            store.put_once(
                f"datasets/{fingerprint.removeprefix('sha256:')}.json",
                canonical_json_bytes(manifest) + b"\n",
                media_type="application/vnd.pixelgym.dataset-manifest+json",
            )
        )
        self.dataset_fingerprint = fingerprint
        gate_value = json.loads((root / "config/promotion-gates.demo-v1.json").read_text())
        self.gate_policy = gate_value
        code_revision = os.environ.get("PIXELGYM_CODE_REVISION", "unknown-dirty")
        lock_digest = __import__("hashlib").sha256((root / "pyproject.toml").read_bytes()).hexdigest()
        prompt = prompt_template(self.prompt_version)
        self.policy = asdict(
            build_policy_manifest(
                provider="scripted-demo",
                model=self.model,
                prompt_name=PROMPT_NAME,
                prompt_version=self.prompt_version,
                prompt=prompt,
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
    def create_or_recover_mlflow_run(self) -> None:
        # The EvaluationRunner performs the idempotent public-API create/recovery after Metaflow's
        # pathspec exists; keeping this as an explicit boundary makes interrupted dual writes clear.
        self.metaflow_pathspec = f"{current.flow_name}/{current.run_id}"
        self.next(self.build_shards)

    @step
    def build_shards(self) -> None:
        self.shards = [{"index": 0, "example_count": 100}]
        self.next(self.evaluate_shard, foreach="shards")

    @step
    def evaluate_shard(self) -> None:
        root = _root()
        provider = ScriptedReplayProvider(
            root / "artifacts/grounding-predictions.jsonl",
            variant="baseline" if self.prompt_version == 1 else "revised",
        )
        runner = EvaluationRunner(
            repository_root=root,
            store=_store(),
            tracking=MlflowTracking(os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")),
            provider=provider,
            policy=PolicyManifest(**self.policy),
            gate_policy=GatePolicy(**self.gate_policy),
            dataset_fingerprint=self.dataset_fingerprint,
            submission_id=self.submission_id,
            metaflow_pathspec=f"{current.flow_name}/{current.run_id}",
        )
        summary, report, refs = runner.run(max_calls=self.maximum_calls)
        self.summary = summary.to_dict()
        self.report = report.to_dict()
        self.references = [item.to_dict() for item in refs]
        self.next(self.join_responses)

    @step
    def join_responses(self, inputs: list[GroundingEvaluationFlow]) -> None:
        inputs = list(inputs)
        if len(inputs) != 1:
            raise ValueError("MVP flow expects one bounded deterministic shard")
        source = inputs[0]
        self.policy = source.policy
        self.gate_policy = source.gate_policy
        self.dataset_fingerprint = source.dataset_fingerprint
        self.dataset_manifest_ref = source.dataset_manifest_ref
        self.metaflow_pathspec = source.metaflow_pathspec
        self.summary = source.summary
        self.report = source.report
        self.references = source.references
        self.next(self.verify_raw_artifacts)

    @step
    def verify_raw_artifacts(self) -> None:
        store = _store()
        for value in self.references:
            store.get_verified(ArtifactRef(**value))
        self.next(self.parse_and_score)

    @step
    def parse_and_score(self) -> None:
        # Parsing and scoring already occurred strictly after each raw envelope write. This step is
        # an explicit resume boundary that carries only immutable parsed evidence onward.
        self.next(self.aggregate_metrics)

    @step
    def aggregate_metrics(self) -> None:
        if self.summary["scored_count"] != self.summary["expected_count"]:
            raise ValueError("aggregate is incomplete")
        self.next(self.evaluate_gates)

    @step
    def evaluate_gates(self) -> None:
        if self.report["policy_id"] != self.policy["policy_id"]:
            raise ValueError("gate report and policy identities differ")
        self.next(self.register_candidate)

    @step
    def register_candidate(self) -> None:
        control = ControlStore(
            os.environ.get("PIXELGYM_CONTROL_DB", str(_root() / ".cache/platform/control.db")),
            reviewer_identity=os.environ.get("PIXELGYM_REVIEWER_ID", "local-reviewer"),
        )
        record = control.register_candidate(
            source_run_id=self.summary["run_id"],
            policy=PolicyManifest(**self.policy),
            gate_report=GateReport.from_dict(self.report),
            artifacts=[ArtifactRef(**value) for value in self.references],
        )
        self.candidate_id = record.candidate_id
        control.link_run(
            self.submission_id,
            metaflow_pathspec=self.metaflow_pathspec,
            mlflow_run_id=self.summary["run_id"],
        )
        self.next(self.finalize_mlflow_run)

    @step
    def finalize_mlflow_run(self) -> None:
        # Tracking finalization is idempotently completed by EvaluationRunner. This boundary exists
        # so a production scheduler can reconcile a stopped control-plane write independently.
        self.next(self.end)

    @step
    def end(self) -> None:
        control = ControlStore(
            os.environ.get("PIXELGYM_CONTROL_DB", str(_root() / ".cache/platform/control.db")),
            reviewer_identity=os.environ.get("PIXELGYM_REVIEWER_ID", "local-reviewer"),
        )
        control.mark_submission(self.submission_id, "Complete")


if __name__ == "__main__":
    GroundingEvaluationFlow()
