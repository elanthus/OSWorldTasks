"""Narrow MLflow adapter; core platform code never imports MLflow directly."""

from __future__ import annotations

import json
import logging
import queue
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pixelgym.platform.contracts import ArtifactRef, GateReport, PolicyManifest, RunSummary
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes

EXPERIMENT_NAME = "pixelgym-grounding"
REGISTERED_POLICY_NAME = "pixelgym-grounding-policy"
COMPATIBLE_SEARCH_CAPACITY = 4
"""Maximum number of MLflow compatible-run calls that may remain outstanding."""

COMPATIBLE_SEARCH_CAPACITY_WAIT_SECONDS = 0.05
"""Maximum time a compatible-run request waits for bounded worker capacity."""

_SUBMISSION_ID_RE = re.compile(r"^submission-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LOGGER = logging.getLogger(__name__)
_COMPATIBLE_SEARCH_SLOTS = threading.BoundedSemaphore(COMPATIBLE_SEARCH_CAPACITY)
_COMPATIBLE_SEARCH_OCCUPANCY_LOCK = threading.Lock()
_compatible_search_occupancy = 0

RUN_PARAM_KEYS = (
    "dataset_name",
    "dataset_fingerprint",
    "mlflow_dataset_digest",
    "dataset_manifest_uri",
    "dataset_manifest_version",
    "dataset_schema",
    "dataset_protocol_version",
    "dataset_example_count",
    "prompt_name",
    "prompt_version",
    "prompt_sha256",
    "rendered_template_schema",
    "provider",
    "model",
    "model_alias_disclosure",
    "endpoint_class",
    "structured_output_mode",
    "inference_temperature",
    "inference_reasoning",
    "inference_seed",
    "inference_max_output",
    "condition",
    "retry_policy",
    "concurrency",
    "parser_version",
    "scorer_version",
    "target_semantics",
    "price_catalog_version",
    "code_revision",
    "code_state",
    "source_tree_sha256",
    "source_provenance_verified",
    "source_provenance_failure_reason",
    "dependency_lock_sha256",
    "python_version",
    "metaflow_flow_name",
    "metaflow_attempt",
    "metaflow_resume_origin",
    "submission_id",
    "synthetic_provider",
)

PRIMARY_METRIC = "accuracy"


class TrackingMirrorError(RuntimeError):
    """A recoverable failure while mirroring authoritative lifecycle state."""


class CompatibleSearchCapacityError(RuntimeError):
    """The bounded pool of compatible-run workers has no available slot."""

    def __init__(self, *, capacity: int, occupancy: int) -> None:
        super().__init__(f"compatible-run search capacity exhausted ({occupancy}/{capacity})")
        self.capacity = capacity
        self.occupancy = occupancy


@dataclass(frozen=True)
class DatasetInputContract:
    name: str
    fingerprint: str
    mlflow_digest: str
    manifest_uri: str
    manifest_version: str
    schema: str
    protocol_version: str
    example_count: int


@dataclass(frozen=True)
class TrackingRunView:
    run_id: str
    status: str
    params: dict[str, str]
    tags: dict[str, str]
    metrics: dict[str, float]
    artifact_paths: tuple[str, ...]
    dataset_inputs: tuple[DatasetInputContract, ...]

    @property
    def comparison_key(self) -> tuple[str, str, str, str]:
        return (
            self.params["dataset_fingerprint"],
            self.params["scorer_version"],
            self.params["target_semantics"],
            self.tags.get("primary_metric", PRIMARY_METRIC),
        )


class Tracking(Protocol):
    def ensure_prompt_version(
        self, name: str, version: int, templates: dict[int, str], expected_sha256: str
    ) -> None: ...
    def link_prompt_to_run(self, run_id: str, name: str, version: int) -> None: ...
    def create_or_recover_run(self, submission_id: str, params: dict[str, Any]) -> str: ...
    def log_dataset_input(self, run_id: str, dataset: DatasetInputContract) -> None: ...
    def reconcile_pathspec(self, run_id: str, pathspec: str) -> None: ...
    def log_summary(
        self,
        run_id: str,
        summary: RunSummary,
        gate_report: GateReport,
        artifacts: list[ArtifactRef],
    ) -> None: ...
    def register_policy(self, run_id: str, manifest: PolicyManifest) -> str: ...
    def mirror_candidate_status(
        self, policy_id: str, *, gate_status: str, approval_status: str
    ) -> None: ...
    def set_champion(self, policy_id: str) -> None: ...
    def search_compatible_runs(
        self,
        *,
        dataset_fingerprint: str,
        scorer_version: str,
        target_semantics: str,
        primary_metric: str = PRIMARY_METRIC,
        max_results: int = 20,
        timeout_seconds: float = 5.0,
    ) -> list[TrackingRunView]: ...
    def finalize(self, run_id: str, status: str) -> None: ...


@dataclass
class MemoryRun:
    run_id: str
    params: dict[str, str]
    tags: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    artifacts: dict[str, bytes] = field(default_factory=dict)
    dataset_inputs: list[DatasetInputContract] = field(default_factory=list)
    status: str = "RUNNING"


class InMemoryTracking:
    """Protocol-faithful test adapter with MLflow-like immutability semantics."""

    def __init__(self) -> None:
        self.runs: dict[str, MemoryRun] = {}
        self.by_submission: dict[str, str] = {}
        self.policies: dict[str, PolicyManifest] = {}
        self.policy_versions: dict[str, str] = {}
        self.policy_tags: dict[str, dict[str, str]] = {}
        self.aliases: dict[str, str] = {}
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

    def log_dataset_input(self, run_id: str, dataset: DatasetInputContract) -> None:
        run = self.runs[run_id]
        if run.dataset_inputs and run.dataset_inputs != [dataset]:
            raise ValueError("immutable dataset input changed on resume")
        run.dataset_inputs[:] = [dataset]

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
                "primary_metric": summary.primary_metric,
                "proposal_coverage_status": (
                    "measured" if summary.proposal_coverage is not None else "not_applicable"
                ),
                "conditional_mark_selection_accuracy_status": (
                    "measured"
                    if summary.conditional_mark_selection_accuracy is not None
                    else "not_applicable"
                ),
                "provider_latency_boundary": "request_dispatch_to_full_response_receipt",
                "end_to_end_boundary": "first_dispatch_to_last_receipt",
            }
        )
        run.artifacts["summary.json"] = canonical_json_bytes(summary.to_dict()) + b"\n"
        run.artifacts["gate-report.json"] = canonical_json_bytes(gate_report.to_dict()) + b"\n"
        run.artifacts["immutable-artifact-index.json"] = canonical_json_bytes(
            [reference.to_dict() for reference in artifacts]
        ) + b"\n"
        for name, payload in _artifact_category_payloads(artifacts).items():
            run.artifacts[name] = canonical_json_bytes(payload) + b"\n"

    def register_policy(self, run_id: str, manifest: PolicyManifest) -> str:
        if run_id not in self.runs:
            raise KeyError(run_id)
        self.policies.setdefault(manifest.policy_id, manifest)
        if self.policies[manifest.policy_id] != manifest:
            raise ValueError("policy ID collision")
        version = self.policy_versions.setdefault(
            manifest.policy_id, str(len(self.policy_versions) + 1)
        )
        self.policy_tags.setdefault(
            manifest.policy_id,
            {
                "policy_id": manifest.policy_id,
                "gate_status": self.runs[run_id].tags.get("gate_status", "unknown"),
                "approval_status": "pending",
            },
        )
        self.policy_tags[manifest.policy_id]["gate_status"] = self.runs[run_id].tags.get(
            "gate_status", "unknown"
        )
        self.policy_tags[manifest.policy_id].setdefault("approval_status", "pending")
        return version

    def mirror_candidate_status(
        self, policy_id: str, *, gate_status: str, approval_status: str
    ) -> None:
        if policy_id not in self.policy_versions:
            raise TrackingMirrorError(f"policy is not registered: {policy_id}")
        self.policy_tags[policy_id].update(
            {"gate_status": gate_status, "approval_status": approval_status}
        )

    def set_champion(self, policy_id: str) -> None:
        if policy_id not in self.policy_versions:
            raise TrackingMirrorError(f"policy is not registered: {policy_id}")
        self.aliases["champion"] = self.policy_versions[policy_id]

    def search_compatible_runs(
        self,
        *,
        dataset_fingerprint: str,
        scorer_version: str,
        target_semantics: str,
        primary_metric: str = PRIMARY_METRIC,
        max_results: int = 20,
        timeout_seconds: float = 5.0,
    ) -> list[TrackingRunView]:
        if not 1 <= max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        views = [self._view(run) for run in self.runs.values()]
        key = (dataset_fingerprint, scorer_version, target_semantics, primary_metric)
        return [view for view in views if view.comparison_key == key][:max_results]

    def _view(self, run: MemoryRun) -> TrackingRunView:
        return TrackingRunView(
            run_id=run.run_id,
            status=run.status,
            params=dict(run.params),
            tags=dict(run.tags),
            metrics=dict(run.metrics),
            artifact_paths=tuple(sorted(run.artifacts)),
            dataset_inputs=tuple(run.dataset_inputs),
        )

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
        "provider_latency_p50_ms": summary.provider_latency_p50_ms,
        "provider_latency_max_ms": summary.provider_latency_max_ms,
        "evaluation_end_to_end_duration_ms": summary.evaluation_end_to_end_duration_ms,
        "total_cost_usd": summary.total_cost_usd,
        "cost_usd_per_example": summary.cost_usd_per_example,
        "proposal_coverage": summary.proposal_coverage,
        "conditional_mark_selection_accuracy": summary.conditional_mark_selection_accuracy,
    }
    metrics = {
        "correct_count": float(summary.correct_count),
        "denominator": float(summary.expected_count),
        "invalid_count": float(summary.invalid_count),
        "request_failure_count": float(summary.request_failure_count),
        "invalid_rate": (
            float(summary.invalid_count) / summary.expected_count
            if summary.expected_count
            else 0.0
        ),
        "request_failure_rate": (
            float(summary.request_failure_count) / summary.expected_count
            if summary.expected_count
            else 0.0
        ),
        "latency_measured_count": float(summary.latency_measured_count),
        "priced_call_count": float(summary.priced_call_count),
        "unpriced_call_count": float(summary.unpriced_call_count),
        "gate_overall_passed": float(report.overall_passed),
        "gate_accuracy_passed": float(report.accuracy.passed),
        "gate_cost_usd_per_100_passed": float(report.cost_usd_per_100.passed),
        "gate_provider_latency_p95_ms_passed": float(
            report.provider_latency_p95_ms.passed
        ),
        "gate_completeness_passed": float(report.completeness.passed),
        "gate_accuracy_threshold": report.accuracy.threshold,
        "gate_cost_usd_per_100_threshold": report.cost_usd_per_100.threshold,
        "gate_provider_latency_p95_ms_threshold": report.provider_latency_p95_ms.threshold,
    }
    metrics.update({key: float(value) for key, value in optional.items() if value is not None})
    if report.accuracy.observed is not None:
        metrics["gate_accuracy_observed"] = float(report.accuracy.observed)
    if report.cost_usd_per_100.observed is not None:
        metrics["gate_cost_usd_per_100_observed"] = float(
            report.cost_usd_per_100.observed
        )
    if report.provider_latency_p95_ms.observed is not None:
        metrics["gate_provider_latency_p95_ms_observed"] = float(
            report.provider_latency_p95_ms.observed
        )
    if report.confidence_bound is not None and report.confidence_bound.observed is not None:
        metrics["accuracy_confidence_lower_bound"] = report.confidence_bound.observed
    return metrics


def _artifact_category_payloads(artifacts: list[ArtifactRef]) -> dict[str, Any]:
    categories: dict[str, tuple[ArtifactRef, ...]] = {
        "run-manifest-reference.json": tuple(
            item for item in artifacts if item.logical_key.endswith("/run-manifest.json")
        ),
        "raw-response-index-reference.json": tuple(
            item
            for item in artifacts
            if item.logical_key.endswith("/raw-response-index.json")
        ),
        "parsed-predictions-reference.json": tuple(
            item for item in artifacts if item.logical_key.endswith("/predictions.jsonl")
        ),
        "per-example-scores-reference.json": tuple(
            item
            for item in artifacts
            if item.logical_key.endswith("/per-example-scores.jsonl")
        ),
        "summary-reference.json": tuple(
            item for item in artifacts if item.logical_key.endswith("/summary.json")
        ),
        "gate-report-reference.json": tuple(
            item for item in artifacts if item.logical_key.endswith("/gate-report.json")
        ),
        "environment-manifest-reference.json": tuple(
            item
            for item in artifacts
            if item.logical_key.endswith("/environment-manifest.json")
        ),
        "policy-package-reference.json": tuple(
            item for item in artifacts if item.logical_key.startswith("policies/")
        ),
        "representative-images-reference.json": tuple(
            item for item in artifacts if "/representative-images/" in item.logical_key
        ),
    }
    missing = [name for name, references in categories.items() if not references]
    if missing:
        raise ValueError(f"complete MLflow artifact contract is missing categories: {missing}")
    return {
        name: [reference.to_dict() for reference in references]
        for name, references in categories.items()
    }


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
            return str(run.info.run_id)
        run = self.client.create_run(
            self.experiment_id,
            tags={
                "pixelgym.submission_id": submission_id,
                "pixelgym.synthetic": encoded["synthetic_provider"].lower(),
            },
        )
        for key, value in encoded.items():
            self.client.log_param(run.info.run_id, key, value)
        return str(run.info.run_id)

    def log_dataset_input(self, run_id: str, dataset: DatasetInputContract) -> None:
        from mlflow.entities import Dataset, DatasetInput, InputTag

        existing = self.client.get_run(run_id).inputs.dataset_inputs
        if existing:
            if len(existing) != 1 or _dataset_contract(existing[0]) != dataset:
                raise ValueError("immutable dataset input changed on resume")
            return
        entity = Dataset(
            name=dataset.name,
            digest=dataset.mlflow_digest,
            source_type="pixelgym_immutable_manifest",
            source=json.dumps(
                {"uri": dataset.manifest_uri, "version_id": dataset.manifest_version},
                sort_keys=True,
                separators=(",", ":"),
            ),
            schema=dataset.schema,
            profile=json.dumps(
                {
                    "example_count": dataset.example_count,
                    "fingerprint": dataset.fingerprint,
                    "protocol_version": dataset.protocol_version,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        self.client.log_inputs(
            run_id,
            datasets=[
                DatasetInput(
                    entity,
                    tags=[
                        InputTag("pixelgym.fingerprint", dataset.fingerprint),
                        InputTag("pixelgym.manifest_uri", dataset.manifest_uri),
                        InputTag("pixelgym.manifest_version", dataset.manifest_version),
                        InputTag("pixelgym.protocol_version", dataset.protocol_version),
                    ],
                )
            ],
        )

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
        self.client.set_tag(run_id, "primary_metric", summary.primary_metric)
        self.client.set_tag(
            run_id,
            "proposal_coverage_status",
            "measured" if summary.proposal_coverage is not None else "not_applicable",
        )
        self.client.set_tag(
            run_id,
            "conditional_mark_selection_accuracy_status",
            (
                "measured"
                if summary.conditional_mark_selection_accuracy is not None
                else "not_applicable"
            ),
        )
        self.client.set_tag(
            run_id,
            "provider_latency_boundary",
            "request_dispatch_to_full_response_receipt",
        )
        self.client.set_tag(
            run_id, "end_to_end_boundary", "first_dispatch_to_last_receipt"
        )
        payloads = {
            "summary.json": summary.to_dict(),
            "gate-report.json": gate_report.to_dict(),
            "immutable-artifact-index.json": [item.to_dict() for item in artifacts],
            **_artifact_category_payloads(artifacts),
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
        for existing in self._all_policy_versions():
            if existing.run_id == run_id and existing.tags.get("policy_id") == manifest.policy_id:
                self.client.set_model_version_tag(
                    REGISTERED_POLICY_NAME,
                    existing.version,
                    "gate_status",
                    self.client.get_run(run_id).data.tags.get("gate_status", "unknown"),
                )
                if "approval_status" not in existing.tags:
                    self.client.set_model_version_tag(
                        REGISTERED_POLICY_NAME,
                        existing.version,
                        "approval_status",
                        "pending",
                    )
                return str(existing.version)
        version = self.client.create_model_version(
            name=REGISTERED_POLICY_NAME,
            source=f"runs:/{run_id}/policy",
            run_id=run_id,
            tags={
                "policy_id": manifest.policy_id,
                "gate_status": self.client.get_run(run_id).data.tags.get(
                    "gate_status", "unknown"
                ),
                "approval_status": "pending",
            },
        )
        return str(version.version)

    def _policy_version(self, policy_id: str) -> Any:
        matches = [
            item
            for item in self._all_policy_versions()
            if item.tags.get("policy_id") == policy_id
        ]
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one MLflow policy version for {policy_id}, found {len(matches)}"
            )
        return matches[0]

    def _all_policy_versions(self) -> list[Any]:
        versions: list[Any] = []
        page_token: str | None = None
        while True:
            page = self.client.search_model_versions(
                f"name = '{REGISTERED_POLICY_NAME}'",
                max_results=1000,
                page_token=page_token,
            )
            versions.extend(page)
            page_token = getattr(page, "token", None)
            if not page_token:
                return versions

    def mirror_candidate_status(
        self, policy_id: str, *, gate_status: str, approval_status: str
    ) -> None:
        try:
            version = self._policy_version(policy_id)
            self.client.set_model_version_tag(
                REGISTERED_POLICY_NAME, version.version, "gate_status", gate_status
            )
            self.client.set_model_version_tag(
                REGISTERED_POLICY_NAME,
                version.version,
                "approval_status",
                approval_status,
            )
        except Exception as exc:
            raise TrackingMirrorError("failed to mirror MLflow policy status tags") from exc

    def set_champion(self, policy_id: str) -> None:
        try:
            version = self._policy_version(policy_id)
            self.client.set_registered_model_alias(
                REGISTERED_POLICY_NAME, "champion", version.version
            )
        except Exception as exc:
            raise TrackingMirrorError("failed to mirror MLflow champion alias") from exc

    def search_compatible_runs(
        self,
        *,
        dataset_fingerprint: str,
        scorer_version: str,
        target_semantics: str,
        primary_metric: str = PRIMARY_METRIC,
        max_results: int = 20,
        timeout_seconds: float = 5.0,
    ) -> list[TrackingRunView]:
        if not 1 <= max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        filter_string = " and ".join(
            (
                f"params.dataset_fingerprint = '{_filter_literal(dataset_fingerprint)}'",
                f"params.scorer_version = '{_filter_literal(scorer_version)}'",
                f"params.target_semantics = '{_filter_literal(target_semantics)}'",
                f"tags.primary_metric = '{_filter_literal(primary_metric)}'",
            )
        )
        runs = _bounded_call(
            lambda: self.client.search_runs(
                [self.experiment_id],
                filter_string=filter_string,
                max_results=max_results,
                order_by=["start_time DESC"],
            ),
            timeout_seconds=timeout_seconds,
        )
        # Compatibility search is a lightweight request path. Artifact paths are
        # loaded only by the explicit detail view below.
        return [self._view(run) for run in runs]

    def get_run_view(self, run_id: str) -> TrackingRunView:
        return self._view(
            self.client.get_run(run_id), artifact_paths=self._artifact_paths(run_id)
        )

    def _artifact_paths(self, run_id: str, path: str | None = None) -> tuple[str, ...]:
        paths: list[str] = []
        for item in self.client.list_artifacts(run_id, path):
            if item.is_dir:
                paths.extend(self._artifact_paths(run_id, item.path))
            else:
                paths.append(item.path)
        return tuple(sorted(paths))

    def _view(
        self, run: Any, *, artifact_paths: tuple[str, ...] = ()
    ) -> TrackingRunView:
        return TrackingRunView(
            run_id=run.info.run_id,
            status=run.info.status,
            params=dict(run.data.params),
            tags=dict(run.data.tags),
            metrics=dict(run.data.metrics),
            artifact_paths=artifact_paths,
            dataset_inputs=tuple(
                _dataset_contract(item) for item in run.inputs.dataset_inputs
            ),
        )

    def finalize(self, run_id: str, status: str) -> None:
        self.client.set_terminated(run_id, status=status)


def _filter_literal(value: str) -> str:
    if "'" in value or "\\" in value:
        raise ValueError("tracking filter values contain unsafe characters")
    return value


def _bounded_call(call: Callable[[], Any], *, timeout_seconds: float) -> Any:
    """Return promptly while capping MLflow calls that outlive their request."""
    global _compatible_search_occupancy

    with _COMPATIBLE_SEARCH_OCCUPANCY_LOCK:
        acquired = _COMPATIBLE_SEARCH_SLOTS.acquire(
            timeout=COMPATIBLE_SEARCH_CAPACITY_WAIT_SECONDS
        )
        if acquired:
            _compatible_search_occupancy += 1
        occupancy = _compatible_search_occupancy
    if not acquired:
        _LOGGER.warning(
            "compatible-run search capacity rejected",
            extra={
                "compatible_search_capacity": COMPATIBLE_SEARCH_CAPACITY,
                "compatible_search_occupancy": occupancy,
            },
        )
        raise CompatibleSearchCapacityError(
            capacity=COMPATIBLE_SEARCH_CAPACITY,
            occupancy=occupancy,
        )
    result: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
    release_lock = threading.Lock()
    released = False
    start_failed = threading.Event()

    def release_capacity() -> None:
        global _compatible_search_occupancy
        nonlocal released

        with release_lock:
            if released:
                return
            released = True
            _COMPATIBLE_SEARCH_SLOTS.release()
            with _COMPATIBLE_SEARCH_OCCUPANCY_LOCK:
                _compatible_search_occupancy -= 1

    def invoke() -> None:
        try:
            result.put((True, call()))
        except Exception as exc:  # noqa: BLE001 - preserve the adapter's original exception.
            result.put((False, exc))
        finally:
            if not start_failed.is_set():
                release_capacity()

    worker = threading.Thread(
        target=invoke,
        daemon=True,
        name="mlflow-compatible-run-search",
    )
    try:
        worker.start()
    except BaseException:
        start_failed.set()
        release_capacity()
        raise
    try:
        succeeded, value = result.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise TimeoutError("MLflow request exceeded its deadline") from exc
    if not succeeded:
        if not isinstance(value, BaseException):
            raise TypeError("tracking request returned an invalid failure value")
        raise value
    return value


def _dataset_contract(value: Any) -> DatasetInputContract:
    source = json.loads(value.dataset.source)
    profile = json.loads(value.dataset.profile)
    tags = {item.key: item.value for item in value.tags}
    return DatasetInputContract(
        name=value.dataset.name,
        fingerprint=tags["pixelgym.fingerprint"],
        mlflow_digest=value.dataset.digest,
        manifest_uri=source["uri"],
        manifest_version=source["version_id"],
        schema=value.dataset.schema,
        protocol_version=tags["pixelgym.protocol_version"],
        example_count=int(profile["example_count"]),
    )
