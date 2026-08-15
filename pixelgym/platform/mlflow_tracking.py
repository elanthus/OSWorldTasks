"""Narrow MLflow adapter; core platform code never imports MLflow directly."""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pixelgym.platform.contracts import ArtifactRef, GateReport, PolicyManifest, RunSummary
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes

EXPERIMENT_NAME = "pixelgym-grounding"
REGISTERED_POLICY_NAME = "pixelgym-grounding-policy"
_SUBMISSION_ID_RE = re.compile(r"^submission-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

RUN_PARAM_KEYS = (
    "dataset_fingerprint",
    "dataset_protocol_version",
    "dataset_example_count",
    "prompt_name",
    "prompt_version",
    "prompt_sha256",
    "provider",
    "model",
    "condition",
    "parser_version",
    "scorer_version",
    "target_semantics",
    "price_catalog_version",
    "code_revision",
    "code_state",
    "source_tree_sha256",
    "source_provenance_verified",
    "dependency_lock_sha256",
    "python_version",
    "submission_id",
    "synthetic_provider",
)


class Tracking(Protocol):
    def ensure_prompt_version(
        self, name: str, version: int, templates: dict[int, str], expected_sha256: str
    ) -> None: ...
    def link_prompt_to_run(self, run_id: str, name: str, version: int) -> None: ...
    def create_or_recover_run(self, submission_id: str, params: dict[str, Any]) -> str: ...
    def reconcile_pathspec(self, run_id: str, pathspec: str) -> None: ...
    def log_summary(
        self,
        run_id: str,
        summary: RunSummary,
        gate_report: GateReport,
        artifacts: list[ArtifactRef],
    ) -> None: ...
    def register_policy(self, run_id: str, manifest: PolicyManifest) -> str: ...
    def finalize(self, run_id: str, status: str) -> None: ...


@dataclass
class MemoryRun:
    run_id: str
    params: dict[str, str]
    tags: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    artifacts: dict[str, bytes] = field(default_factory=dict)
    status: str = "RUNNING"


class InMemoryTracking:
    """Protocol-faithful test adapter with MLflow-like immutability semantics."""

    def __init__(self) -> None:
        self.runs: dict[str, MemoryRun] = {}
        self.by_submission: dict[str, str] = {}
        self.policies: dict[str, PolicyManifest] = {}
        self.prompts: dict[tuple[str, int], str] = {}

    def ensure_prompt_version(
        self, name: str, version: int, templates: dict[int, str], expected_sha256: str
    ) -> None:
        for number in range(1, version + 1):
            template = templates[number]
            existing = self.prompts.setdefault((name, number), template)
            if existing != template:
                raise ValueError("registered prompt version has different bytes")
        if sha256_bytes(self.prompts[(name, version)].encode()) != expected_sha256:
            raise ValueError("policy prompt digest does not match registered prompt")

    def link_prompt_to_run(self, run_id: str, name: str, version: int) -> None:
        if (name, version) not in self.prompts:
            raise KeyError("prompt version is not registered")
        self.runs[run_id].tags["prompt.uri"] = f"prompts:/{name}/{version}"

    def create_or_recover_run(self, submission_id: str, params: dict[str, Any]) -> str:
        _validate_submission_id(submission_id)
        missing = sorted(set(RUN_PARAM_KEYS) - set(params))
        if missing:
            raise ValueError(f"run contract is missing required params: {missing}")
        encoded = {key: _param(value) for key, value in params.items()}
        existing_id = self.by_submission.get(submission_id)
        if existing_id:
            if self.runs[existing_id].params != encoded:
                raise ValueError("immutable run params changed on resume")
            return existing_id
        run_id = f"mlflow-{len(self.runs) + 1:06d}"
        self.runs[run_id] = MemoryRun(run_id=run_id, params=encoded)
        self.by_submission[submission_id] = run_id
        return run_id

    def reconcile_pathspec(self, run_id: str, pathspec: str) -> None:
        run = self.runs[run_id]
        previous = run.tags.get("metaflow.pathspec")
        if previous and previous != pathspec:
            raise ValueError("Metaflow pathspec changed for an existing run")
        run.tags["metaflow.pathspec"] = pathspec
        run.tags["mlflow.run_id"] = run_id

    def log_summary(
        self,
        run_id: str,
        summary: RunSummary,
        gate_report: GateReport,
        artifacts: list[ArtifactRef],
    ) -> None:
        run = self.runs[run_id]
        run.metrics.update(_summary_metrics(summary, gate_report))
        run.tags.update(
            {
                "gate_status": "eligible" if gate_report.overall_passed else "failed",
                "synthetic_provider": str(summary.synthetic_provider).lower(),
                "dataset_fingerprint": summary.dataset_fingerprint,
                "scorer_version": summary.scorer_version,
            }
        )
        run.artifacts["summary.json"] = canonical_json_bytes(summary.to_dict()) + b"\n"
        run.artifacts["gate-report.json"] = canonical_json_bytes(gate_report.to_dict()) + b"\n"
        run.artifacts["immutable-artifact-index.json"] = canonical_json_bytes(
            [reference.to_dict() for reference in artifacts]
        ) + b"\n"

    def register_policy(self, run_id: str, manifest: PolicyManifest) -> str:
        if run_id not in self.runs:
            raise KeyError(run_id)
        self.policies.setdefault(manifest.policy_id, manifest)
        if self.policies[manifest.policy_id] != manifest:
            raise ValueError("policy ID collision")
        return f"memory://{REGISTERED_POLICY_NAME}/{manifest.policy_id}"

    def finalize(self, run_id: str, status: str) -> None:
        self.runs[run_id].status = status


def _param(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _validate_submission_id(submission_id: str) -> None:
    if not _SUBMISSION_ID_RE.fullmatch(submission_id):
        raise ValueError("submission ID contains characters unsafe for tracking queries")


def _summary_metrics(summary: RunSummary, report: GateReport) -> dict[str, float]:
    optional = {
        "accuracy": summary.accuracy,
        "cost_usd_per_100": summary.cost_usd_per_100,
        "provider_latency_p95_ms": summary.provider_latency_p95_ms,
    }
    metrics = {
        "correct_count": float(summary.correct_count),
        "denominator": float(summary.expected_count),
        "invalid_count": float(summary.invalid_count),
        "request_failure_count": float(summary.request_failure_count),
        "priced_call_count": float(summary.priced_call_count),
        "unpriced_call_count": float(summary.unpriced_call_count),
        "gate_overall_passed": float(report.overall_passed),
        "gate_accuracy_threshold": report.accuracy.threshold,
        "gate_cost_usd_per_100_threshold": report.cost_usd_per_100.threshold,
        "gate_provider_latency_p95_ms_threshold": report.provider_latency_p95_ms.threshold,
    }
    metrics.update({key: float(value) for key, value in optional.items() if value is not None})
    return metrics


class MlflowTracking:
    """Public-API-only MLflow implementation, loaded only with the platform extra."""

    def __init__(self, tracking_uri: str, experiment_name: str = EXPERIMENT_NAME) -> None:
        try:
            import mlflow
            from mlflow import MlflowClient
        except ImportError as exc:  # pragma: no cover - exercised in optional integration suite
            raise RuntimeError('install pixelgym with the "platform" extra') from exc
        mlflow.set_tracking_uri(tracking_uri)
        self._mlflow = mlflow
        self.client = MlflowClient(tracking_uri=tracking_uri)
        experiment = self.client.get_experiment_by_name(experiment_name)
        self.experiment_id = (
            experiment.experiment_id
            if experiment is not None
            else self.client.create_experiment(experiment_name)
        )

    def ensure_prompt_version(
        self, name: str, version: int, templates: dict[int, str], expected_sha256: str
    ) -> None:
        from pixelgym.platform.fingerprints import sha256_bytes

        existing = (
            {int(item.version): item for item in self.client.search_prompt_versions(name)}
            if self.client.get_prompt(name) is not None
            else {}
        )
        for number in range(1, version + 1):
            template = templates[number]
            item = existing.get(number)
            if item is None:
                item = self.client.register_prompt(
                    name=name,
                    template=template,
                    commit_message=f"PixelGym frozen prompt v{number}",
                    tags={"prompt_sha256": sha256_bytes(template.encode())},
                )
                if int(item.version) != number:
                    raise ValueError("MLflow assigned an unexpected prompt version")
            if item.template != template:
                raise ValueError("registered prompt version has different bytes")
        if sha256_bytes(templates[version].encode()) != expected_sha256:
            raise ValueError("policy prompt digest does not match registered prompt")

    def link_prompt_to_run(self, run_id: str, name: str, version: int) -> None:
        prompt = self.client.get_prompt_version(name, version)
        if prompt is None:
            raise KeyError("prompt version is not registered")
        self.client.link_prompt_version_to_run(run_id, f"prompts:/{name}/{version}")
        # MLflow 3.14's OSS prompt/run listing endpoint does not surface the registry link on all
        # SQL backends, so mirror bidirectional lineage with public, namespaced tags as well.
        self.client.set_tag(run_id, "prompt.uri", f"prompts:/{name}/{version}")
        self.client.set_prompt_version_tag(name, version, f"pixelgym.run.{run_id}", "linked")

    def create_or_recover_run(self, submission_id: str, params: dict[str, Any]) -> str:
        _validate_submission_id(submission_id)
        missing = sorted(set(RUN_PARAM_KEYS) - set(params))
        if missing:
            raise ValueError(f"run contract is missing required params: {missing}")
        matches = self.client.search_runs(
            [self.experiment_id],
            filter_string=f"tags.`pixelgym.submission_id` = '{submission_id}'",
            max_results=2,
        )
        encoded = {key: _param(value) for key, value in params.items()}
        if matches:
            if len(matches) != 1:
                raise ValueError("submission ID has duplicate MLflow runs")
            run = matches[0]
            if run.data.params != encoded:
                raise ValueError("immutable run params changed on resume")
            return run.info.run_id
        run = self.client.create_run(
            self.experiment_id,
            tags={
                "pixelgym.submission_id": submission_id,
                "pixelgym.synthetic": encoded["synthetic_provider"].lower(),
            },
        )
        for key, value in encoded.items():
            self.client.log_param(run.info.run_id, key, value)
        return run.info.run_id

    def reconcile_pathspec(self, run_id: str, pathspec: str) -> None:
        run = self.client.get_run(run_id)
        previous = run.data.tags.get("metaflow.pathspec")
        if previous and previous != pathspec:
            raise ValueError("Metaflow pathspec changed for an existing run")
        self.client.set_tag(run_id, "metaflow.pathspec", pathspec)
        self.client.set_tag(run_id, "mlflow.run_id", run_id)

    def log_summary(
        self,
        run_id: str,
        summary: RunSummary,
        gate_report: GateReport,
        artifacts: list[ArtifactRef],
    ) -> None:
        for key, value in _summary_metrics(summary, gate_report).items():
            self.client.log_metric(run_id, key, value)
        self.client.set_tag(
            run_id, "gate_status", "eligible" if gate_report.overall_passed else "failed"
        )
        self.client.set_tag(run_id, "dataset_fingerprint", summary.dataset_fingerprint)
        self.client.set_tag(run_id, "scorer_version", summary.scorer_version)
        payloads = {
            "summary.json": summary.to_dict(),
            "gate-report.json": gate_report.to_dict(),
            "immutable-artifact-index.json": [item.to_dict() for item in artifacts],
        }
        with tempfile.TemporaryDirectory(prefix="pixelgym-mlflow-") as directory:
            for name, value in payloads.items():
                path = Path(directory) / name
                path.write_bytes(canonical_json_bytes(value) + b"\n")
                self.client.log_artifact(run_id, str(path))

    def register_policy(self, run_id: str, manifest: PolicyManifest) -> str:
        with tempfile.TemporaryDirectory(prefix="pixelgym-policy-") as directory:
            path = Path(directory) / "policy-manifest.json"
            path.write_bytes(canonical_json_bytes(manifest.to_dict()) + b"\n")
            self.client.log_artifact(run_id, str(path), artifact_path="policy")
        try:
            self.client.create_registered_model(REGISTERED_POLICY_NAME)
        except Exception as exc:  # MLflow raises a typed RESOURCE_ALREADY_EXISTS exception.
            if "already exists" not in str(exc).lower():
                raise
        for existing in self.client.search_model_versions(
            f"name = '{REGISTERED_POLICY_NAME}'"
        ):
            if existing.run_id == run_id and existing.tags.get("policy_id") == manifest.policy_id:
                return str(existing.version)
        version = self.client.create_model_version(
            name=REGISTERED_POLICY_NAME,
            source=f"runs:/{run_id}/policy",
            run_id=run_id,
            tags={"policy_id": manifest.policy_id},
        )
        return str(version.version)

    def finalize(self, run_id: str, status: str) -> None:
        self.client.set_terminated(run_id, status=status)
