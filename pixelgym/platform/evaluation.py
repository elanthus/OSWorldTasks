"""Idempotent platform evaluation wrapped around the frozen Day 3 parser and scorer."""

from __future__ import annotations

import json
import math
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pixelgym.grounding.evaluation import parse_prediction, prompt_for, schema_for, score_point
from pixelgym.platform.contracts import (
    ArtifactRef,
    GatePolicy,
    GateReport,
    PolicyManifest,
    RunSummary,
)
from pixelgym.platform.fingerprints import (
    build_dataset_manifest,
    canonical_json_bytes,
    sha256_bytes,
)
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.immutable_store import ImmutableStore
from pixelgym.platform.mlflow_tracking import DatasetInputContract, Tracking
from pixelgym.platform.policy import PROMPT_TEMPLATES, prompt_template
from pixelgym.platform.schema_validation import PlatformSchemas
from pixelgym.serialization import load_jsonl

RAW_RESPONSE_SCHEMA_VERSION = "pixelgym-raw-response-v1"
RUN_MANIFEST_SCHEMA_VERSION = "pixelgym-platform-run-manifest-v1"


@dataclass(frozen=True)
class PlatformProviderResponse:
    raw_response: str | None
    latency_ms: float | None
    usage: dict[str, int | float] | None
    cost_usd: float | None
    request_failure: str | None = None


class PlatformProvider(Protocol):
    name: str
    model: str
    synthetic: bool

    def invoke(
        self,
        *,
        request_id: str,
        example_id: str,
        condition: str,
        image_path: Path,
        prompt: str,
        schema: dict[str, Any],
    ) -> PlatformProviderResponse: ...


class ScriptedReplayProvider:
    """No-cost provider replaying frozen Day 3 response text with synthetic timing."""

    name = "scripted-demo"
    synthetic = True

    def __init__(
        self,
        prediction_path: Path,
        *,
        variant: str,
        latency_ms: float = 25.0,
        model: str | None = None,
    ) -> None:
        if variant not in {"baseline", "revised"}:
            raise ValueError("scripted variant must be baseline or revised")
        self.condition = "raw"
        default_model = f"day3-replay-{variant}-{'v1' if variant == 'baseline' else 'v2'}"
        self.model = model or default_model
        self.latency_ms = latency_ms
        rows = load_jsonl(prediction_path)
        if variant == "baseline":
            self.responses = {
                row["example_id"]: row["raw_response"]
                for row in rows
                if row.get("condition") == "raw"
            }
        else:
            self.responses = {
                row["example_id"]: json.dumps(
                    {"x": int(row["point"][0]), "y": int(row["point"][1])},
                    separators=(",", ":"),
                )
                for row in rows
                if row.get("condition") == "marks" and row.get("point") is not None
            }
        self.call_ids: list[str] = []

    def invoke(
        self,
        *,
        request_id: str,
        example_id: str,
        condition: str,
        image_path: Path,
        prompt: str,
        schema: dict[str, Any],
    ) -> PlatformProviderResponse:
        del image_path, prompt, schema
        if condition != self.condition or example_id not in self.responses:
            return PlatformProviderResponse(None, self.latency_ms, {}, 0.0, "fixture missing")
        self.call_ids.append(request_id)
        return PlatformProviderResponse(
            self.responses[example_id], self.latency_ms, {"input_tokens": 0, "output_tokens": 0}, 0.0
        )


def percentile_r7(values: list[float], quantile: float) -> float:
    """Hyndman-Fan type 7 linear percentile, matching the frozen Day 3 method."""
    if not values or not 0 <= quantile <= 1:
        raise ValueError("percentile requires values and a quantile in [0, 1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


class EvaluationRunner:
    def __init__(
        self,
        *,
        repository_root: Path,
        store: ImmutableStore,
        tracking: Tracking | None,
        provider: PlatformProvider,
        policy: PolicyManifest,
        gate_policy: GatePolicy,
        dataset_fingerprint: str,
        submission_id: str,
        metaflow_pathspec: str,
        price_catalog_version: str = "pixelgym-demo-prices-v1",
        provider_concurrency: int = 1,
        provider_response_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.root = repository_root
        self.store = store
        self.tracking = tracking
        self.provider = provider
        self.policy = policy
        self.gate_policy = gate_policy
        self.dataset_fingerprint = dataset_fingerprint
        self.submission_id = submission_id
        self.metaflow_pathspec = metaflow_pathspec
        self.price_catalog_version = price_catalog_version
        if provider_concurrency <= 0:
            raise ValueError("provider concurrency must be positive")
        self.provider_concurrency = provider_concurrency
        self.provider_response_hook = provider_response_hook
        self._tracking_run_id: str | None = None
        self.schemas = PlatformSchemas()
        self.schemas.validate("gate_policy", self.gate_policy.to_dict())
        self.schemas.validate("policy_package", self.policy.to_dict())

    def _tracking_params(self, example_count: int) -> dict[str, Any]:
        dataset = self._dataset_input()
        parameters = self.policy.parameters
        return {
            "dataset_name": "pixelgym-grounding-day3-frozen",
            "dataset_fingerprint": self.dataset_fingerprint,
            "mlflow_dataset_digest": dataset.mlflow_digest,
            "dataset_manifest_uri": dataset.manifest_uri,
            "dataset_manifest_version": dataset.manifest_version,
            "dataset_schema": dataset.schema,
            "dataset_protocol_version": "pixelgym-grounding-v1",
            "dataset_example_count": example_count,
            "prompt_name": self.policy.prompt_name,
            "prompt_version": self.policy.prompt_version,
            "prompt_sha256": self.policy.prompt_sha256,
            "rendered_template_schema": "pixelgym-grounding-rendered-prompt-v1",
            "provider": self.policy.provider,
            "model": self.policy.model,
            "model_alias_disclosure": self.policy.model_alias_disclosure,
            "endpoint_class": "local-scripted" if self.provider.synthetic else "remote-provider",
            "structured_output_mode": "json-schema",
            "inference_temperature": parameters.get("temperature"),
            "inference_reasoning": parameters.get("reasoning", "unsupported"),
            "inference_seed": parameters.get("seed"),
            "inference_max_output": parameters.get("max_output_tokens", "provider-default"),
            "condition": self.policy.condition,
            "retry_policy": {
                "hidden_retries": parameters.get("hidden_retries", 0),
                "request_retry": "none",
            },
            "concurrency": self.provider_concurrency,
            "parser_version": self.policy.parser_version,
            "scorer_version": self.policy.scorer_version,
            "target_semantics": self.policy.target_semantics,
            "price_catalog_version": self.price_catalog_version,
            "code_revision": self.policy.code_revision,
            "code_state": self.policy.code_state,
            "source_tree_sha256": self.policy.source_tree_sha256,
            "source_provenance_verified": self.policy.source_provenance_verified,
            "source_provenance_failure_reason": self.policy.source_provenance_failure_reason,
            "dependency_lock_sha256": self.policy.dependency_lock_sha256,
            "python_version": platform.python_version(),
            "metaflow_flow_name": self.metaflow_pathspec.split("/", 1)[0],
            "metaflow_attempt": 0,
            "metaflow_resume_origin": self.metaflow_pathspec,
            "submission_id": self.submission_id,
            "synthetic_provider": self.provider.synthetic,
        }

    def _dataset_input(self) -> DatasetInputContract:
        manifest, fingerprint = build_dataset_manifest(
            repository_root=self.root,
            dataset_path=self.root / "artifacts/grounding-dataset.jsonl",
            overlays_path=self.root / "artifacts/grounding-overlays.jsonl",
        )
        if fingerprint != self.dataset_fingerprint:
            raise ValueError("frozen dataset fingerprint differs from the evaluation contract")
        reference = self.store.put_once(
            f"datasets/{fingerprint.removeprefix('sha256:')}.json",
            canonical_json_bytes(manifest) + b"\n",
            media_type="application/vnd.pixelgym.dataset-manifest+json",
        )
        return DatasetInputContract(
            name="pixelgym-grounding-day3-frozen",
            fingerprint=fingerprint,
            mlflow_digest=fingerprint.removeprefix("sha256:")[:32],
            manifest_uri=reference.uri,
            manifest_version=reference.version_id,
            schema=str(manifest.get("schema_version", "pixelgym-grounding-dataset-manifest-v1")),
            protocol_version="pixelgym-grounding-v1",
            example_count=int(manifest["example_count"]),
        )

    def _inputs(self) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        examples = sorted(
            load_jsonl(self.root / "artifacts/grounding-dataset.jsonl"),
            key=lambda row: row["example_id"],
        )
        overlay_rows = load_jsonl(self.root / "artifacts/grounding-overlays.jsonl")
        overlay_ids = [row["example_id"] for row in overlay_rows]
        if len(overlay_ids) != len(set(overlay_ids)):
            raise ValueError("overlay example IDs must be unique")
        overlays = {row["example_id"]: row for row in overlay_rows}
        example_ids = [row["example_id"] for row in examples]
        if len(example_ids) != len(set(example_ids)):
            raise ValueError("dataset example IDs must be unique")
        if set(example_ids) != set(overlays):
            raise ValueError("dataset and overlay example IDs differ")
        return examples, overlays

    def build_shards(self, *, shard_size: int, max_calls: int) -> list[dict[str, Any]]:
        """Return deterministic, JSON-safe shard membership before any provider call."""
        if shard_size <= 0:
            raise ValueError("shard size must be positive")
        examples, _ = self._inputs()
        if max_calls < len(examples):
            raise RuntimeError("evaluation call cap is below the frozen example count")
        identifiers = [row["example_id"] for row in examples]
        return [
            {
                "index": index // shard_size,
                "example_ids": identifiers[index : index + shard_size],
            }
            for index in range(0, len(identifiers), shard_size)
        ]

    def create_or_recover_run(self, *, max_calls: int) -> str:
        if self.tracking is None:
            raise RuntimeError("tracking is required to create or recover a run")
        examples, _ = self._inputs()
        if max_calls < len(examples):
            raise RuntimeError("evaluation call cap is below the frozen example count")
        self.tracking.ensure_prompt_version(
            self.policy.prompt_name,
            self.policy.prompt_version,
            PROMPT_TEMPLATES,
            self.policy.prompt_sha256,
        )
        run_id = self.tracking.create_or_recover_run(
            self.submission_id, self._tracking_params(len(examples))
        )
        self.tracking.log_dataset_input(run_id, self._dataset_input())
        self._tracking_run_id = run_id
        self.tracking.link_prompt_to_run(
            run_id, self.policy.prompt_name, self.policy.prompt_version
        )
        self.tracking.reconcile_pathspec(run_id, self.metaflow_pathspec)
        return run_id

    def _request_material(
        self, example: dict[str, Any], overlay: dict[str, Any]
    ) -> tuple[str, str, str, dict[str, Any], Path]:
        condition = self.policy.condition
        prompt = prompt_for(example, condition)
        if self.policy.prompt_version > 1:
            prompt += " " + prompt_template(self.policy.prompt_version).replace(
                "{{target}}", example["target"]
            )
        schema = schema_for(condition)
        image_relative = example["image_path"] if condition == "raw" else overlay["marked_image_path"]
        request_material = {
            "dataset_fingerprint": self.dataset_fingerprint,
            "policy_id": self.policy.policy_id,
            "example_id": example["example_id"],
            "condition": condition,
            "image_sha256": (
                example["image_sha256"] if condition == "raw" else overlay["marked_image_sha256"]
            ),
            "prompt_sha256": sha256_bytes(prompt.encode()),
            "schema": schema,
        }
        request_sha256 = sha256_bytes(canonical_json_bytes(request_material))
        return request_sha256, prompt, condition, schema, self.root / image_relative

    def evaluate_shard(
        self, shard: dict[str, Any], *, max_calls: int
    ) -> list[dict[str, Any]]:
        """Invoke the provider and durably store raw envelopes; never parse or score here."""
        examples, overlays = self._inputs()
        by_id = {row["example_id"]: row for row in examples}
        identifiers = shard.get("example_ids")
        if not isinstance(shard.get("index"), int) or not isinstance(identifiers, list):
            raise TypeError("shard must contain an integer index and example ID list")
        if not identifiers or len(identifiers) != len(set(identifiers)):
            raise ValueError("shard example IDs must be nonempty and unique")
        if len(identifiers) > max_calls:
            raise RuntimeError("shard exceeds evaluation call cap")
        if any(identifier not in by_id for identifier in identifiers):
            raise ValueError("shard contains an unknown example ID")

        raw: list[dict[str, Any]] = []
        for identifier in sorted(identifiers):
            example = by_id[identifier]
            overlay = overlays[identifier]
            request_sha256, prompt, condition, schema, image_path = self._request_material(
                example, overlay
            )
            request_id = "sha256:" + request_sha256
            logical_key = f"raw-responses/{request_sha256}.json"
            reference = self.store.get_reference(logical_key)
            if reference is None:
                response = self.provider.invoke(
                    request_id=request_id,
                    example_id=identifier,
                    condition=condition,
                    image_path=image_path,
                    prompt=prompt,
                    schema=schema,
                )
                if self.provider_response_hook is not None:
                    self.provider_response_hook(request_id)
                response_bytes = (
                    response.raw_response.encode("utf-8")
                    if response.raw_response is not None
                    else b""
                )
                envelope = {
                    "schema_version": RAW_RESPONSE_SCHEMA_VERSION,
                    "request_id": request_id,
                    "example_id": identifier,
                    "dataset_fingerprint": self.dataset_fingerprint,
                    "policy_id": self.policy.policy_id,
                    "request_sha256": request_sha256,
                    "started_at_utc": None,
                    "started_at_missing_reason": (
                        "provider adapter does not expose an attributable request-start timestamp"
                    ),
                    "latency_ms": response.latency_ms,
                    "usage": response.usage,
                    "usage_missing_reason": (
                        None if response.usage is not None else "provider omitted usage"
                    ),
                    "price_catalog_version": self.price_catalog_version,
                    "cost_usd": response.cost_usd,
                    "cost_missing_reason": (
                        None if response.cost_usd is not None else "price or usage unavailable"
                    ),
                    "response_media_type": "application/json",
                    "response_sha256": sha256_bytes(response_bytes),
                    "raw_response": response.raw_response,
                    "request_status": "request_failure" if response.request_failure else "responded",
                    "request_failure": response.request_failure,
                }
                self.schemas.validate("raw_response", envelope)
                # This durable write is the provider/parse side-effect boundary.
                reference = self.store.put_once(
                    logical_key,
                    canonical_json_bytes(envelope) + b"\n",
                    media_type="application/vnd.pixelgym.raw-response+json",
                )
            raw.append({"example_id": identifier, "reference": reference.to_dict()})
        return raw

    def canonical_join(self, shards: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """Join branch outputs independently of branch completion or input order."""
        examples, _ = self._inputs()
        expected = [row["example_id"] for row in examples]
        joined = sorted(
            (item for shard in shards for item in shard),
            key=lambda item: item["example_id"],
        )
        identifiers = [item.get("example_id") for item in joined]
        if identifiers != expected:
            raise ValueError("joined shards do not contain each expected example exactly once")
        for item in joined:
            ArtifactRef(**item["reference"])
        return joined

    def verify_raw_artifacts(
        self, raw: list[dict[str, Any]], *, require_complete: bool = True
    ) -> list[dict[str, Any]]:
        """Return verified envelopes after checking pinned bytes and request identity."""
        examples, overlays = self._inputs()
        by_id = {row["example_id"]: row for row in examples}
        identifiers = [item.get("example_id") for item in raw]
        if require_complete and identifiers != list(by_id):
            raise ValueError("raw artifact set is not the canonical complete dataset")
        if identifiers != sorted(set(identifiers)) or any(item not in by_id for item in identifiers):
            raise ValueError("raw artifact set must be canonical, unique, and known")
        verified: list[dict[str, Any]] = []
        for item in raw:
            identifier = item["example_id"]
            reference = ArtifactRef(**item["reference"])
            request_sha256, _, _, _, _ = self._request_material(
                by_id[identifier], overlays[identifier]
            )
            envelope = json.loads(self.store.get_verified(reference))
            self.schemas.validate("raw_response", envelope)
            if (
                reference.logical_key != f"raw-responses/{request_sha256}.json"
                or envelope.get("schema_version") != RAW_RESPONSE_SCHEMA_VERSION
                or envelope.get("example_id") != identifier
                or envelope.get("request_id") != "sha256:" + request_sha256
                or envelope.get("request_sha256") != request_sha256
                or envelope.get("policy_id") != self.policy.policy_id
                or envelope.get("dataset_fingerprint") != self.dataset_fingerprint
            ):
                raise ValueError("stored raw response identity does not match request")
            response_text = envelope.get("raw_response")
            response_digest = sha256_bytes(
                response_text.encode("utf-8") if response_text is not None else b""
            )
            if response_digest != envelope.get("response_sha256"):
                raise ValueError("stored raw response digest mismatch")
            verified.append(
                {
                    "example_id": identifier,
                    "reference": reference.to_dict(),
                    "envelope": envelope,
                }
            )
        return verified

    def parse_and_score(
        self, verified: list[dict[str, Any]], *, require_complete: bool = True
    ) -> list[dict[str, Any]]:
        """Parse verified raw envelopes and score them without provider or tracking side effects."""
        examples, overlays = self._inputs()
        by_id = {row["example_id"]: row for row in examples}
        identifiers = [item.get("example_id") for item in verified]
        if require_complete and identifiers != list(by_id):
            raise ValueError("verified response set is not the canonical complete dataset")
        if identifiers != sorted(set(identifiers)) or any(item not in by_id for item in identifiers):
            raise ValueError("verified response set must be canonical, unique, and known")
        records: list[dict[str, Any]] = []
        for item in verified:
            example = by_id[item["example_id"]]
            overlay = overlays[example["example_id"]]
            reference = ArtifactRef(**item["reference"])
            envelope = item["envelope"]
            self.schemas.validate("raw_response", envelope)
            if not isinstance(envelope, dict) or envelope.get("example_id") != example["example_id"]:
                raise ValueError("verified response envelope does not match its example")
            if envelope["request_failure"]:
                parse_status, parsed, point, mark_id, parse_error = (
                    "request_failure",
                    None,
                    None,
                    None,
                    envelope["request_failure"],
                )
            else:
                outcome = parse_prediction(
                    envelope["raw_response"],
                    condition=self.policy.condition,
                    width=example["screen_width"],
                    height=example["screen_height"],
                    marks=overlay["marks"],
                )
                parse_status = outcome.status
                parsed = outcome.parsed_prediction
                point = outcome.point
                mark_id = outcome.mark_id
                parse_error = outcome.error
            correct, distance = score_point(
                point,
                example["bbox"],
                width=example["screen_width"],
                height=example["screen_height"],
            )
            records.append(
                {
                    "example_id": example["example_id"],
                    "condition": self.policy.condition,
                    "raw_artifact": reference.to_dict(),
                    "parse_status": parse_status,
                    "parse_error": parse_error,
                    "parsed_prediction": parsed,
                    "point": point,
                    "mark_id": mark_id,
                    "correct": correct,
                    "normalized_center_distance": distance,
                    "latency_ms": envelope["latency_ms"],
                    "cost_usd": envelope["cost_usd"],
                    "usage": envelope["usage"],
                }
            )
        return sorted(records, key=lambda row: (row["example_id"], row["condition"]))

    def aggregate_metrics(
        self,
        records: list[dict[str, Any]],
        *,
        run_id: str,
        evaluation_end_to_end_duration_ms: float | None = None,
    ) -> RunSummary:
        examples, _ = self._inputs()
        existing_summary = self.store.get_reference(
            f"runs/{self.submission_id}/summary.json"
        )
        if existing_summary is not None:
            stored_summary = json.loads(self.store.get_verified(existing_summary))
            evaluation_end_to_end_duration_ms = stored_summary.get(
                "evaluation_end_to_end_duration_ms"
            )
        unique = {(row["example_id"], row["condition"]) for row in records}
        latency = [float(row["latency_ms"]) for row in records if row["latency_ms"] is not None]
        costs = [float(row["cost_usd"]) for row in records if row["cost_usd"] is not None]
        correct_count = sum(row["correct"] is True for row in records)
        return RunSummary(
            run_id=run_id,
            dataset_fingerprint=self.dataset_fingerprint,
            policy_id=self.policy.policy_id,
            scorer_version=self.policy.scorer_version,
            target_semantics=self.policy.target_semantics,
            expected_count=len(examples),
            scored_count=len(records),
            unique_record_count=len(unique),
            correct_count=correct_count,
            accuracy=correct_count / len(examples) if len(records) == len(examples) else None,
            cost_usd_per_100=(
                sum(costs) * 100 / len(records)
                if records and len(costs) == len(records)
                else None
            ),
            priced_call_count=len(costs),
            unpriced_call_count=len(records) - len(costs),
            provider_latency_p95_ms=percentile_r7(latency, 0.95) if latency else None,
            latency_measured_count=len(latency),
            provider_latency_p50_ms=percentile_r7(latency, 0.50) if latency else None,
            provider_latency_max_ms=max(latency) if latency else None,
            evaluation_end_to_end_duration_ms=evaluation_end_to_end_duration_ms,
            total_cost_usd=sum(costs) if len(costs) == len(records) else None,
            cost_usd_per_example=(
                sum(costs) / len(records)
                if records and len(costs) == len(records)
                else None
            ),
            proposal_coverage=None,
            conditional_mark_selection_accuracy=None,
            invalid_count=sum(row["parse_status"] == "invalid" for row in records),
            request_failure_count=sum(
                row["parse_status"] == "request_failure" for row in records
            ),
            dirty_code=self.policy.code_state != "clean",
            code_state=self.policy.code_state,
            code_provenance_verified=self.policy.source_provenance_verified,
            synthetic_provider=self.provider.synthetic,
        )

    def evaluate_gates(self, summary: RunSummary) -> GateReport:
        return evaluate_gates(self.gate_policy, summary)

    def persist_evidence(
        self,
        records: list[dict[str, Any]],
        summary: RunSummary,
        report: GateReport,
        raw: list[dict[str, Any]],
    ) -> list[ArtifactRef]:
        self.schemas.validate("gate_report", report.to_dict())
        self.schemas.validate("policy_package", self.policy.to_dict())
        prediction_ref = self.store.put_once(
            f"runs/{self.submission_id}/predictions.jsonl",
            b"".join(canonical_json_bytes(row) + b"\n" for row in records),
            media_type="application/x-ndjson",
        )
        score_ref = self.store.put_once(
            f"runs/{self.submission_id}/per-example-scores.jsonl",
            b"".join(canonical_json_bytes(row) + b"\n" for row in records),
            media_type="application/x-ndjson",
        )
        summary_ref = self.store.put_once(
            f"runs/{self.submission_id}/summary.json",
            canonical_json_bytes(summary.to_dict()) + b"\n",
            media_type="application/vnd.pixelgym.run-summary+json",
        )
        gate_ref = self.store.put_once(
            f"runs/{self.submission_id}/gate-report.json",
            canonical_json_bytes(report.to_dict()) + b"\n",
            media_type="application/vnd.pixelgym.gate-report+json",
        )
        policy_ref = self.store.put_once(
            f"policies/{self.policy.policy_id.removeprefix('sha256:')}.json",
            canonical_json_bytes(self.policy.to_dict()) + b"\n",
            media_type="application/vnd.pixelgym.policy+json",
        )
        dataset_ref = self.store.get_reference(
            f"datasets/{self.dataset_fingerprint.removeprefix('sha256:')}.json"
        )
        raw_refs = [ArtifactRef(**item["reference"]) for item in raw]
        raw_index_ref = self.store.put_once(
            f"runs/{self.submission_id}/raw-response-index.json",
            canonical_json_bytes([item.to_dict() for item in raw_refs]) + b"\n",
            media_type="application/vnd.pixelgym.artifact-index+json",
        )
        environment_ref = self.store.put_once(
            f"runs/{self.submission_id}/environment-manifest.json",
            canonical_json_bytes(
                {
                    "python_version": platform.python_version(),
                    "dependency_lock_sha256": self.policy.dependency_lock_sha256,
                    "code_revision": self.policy.code_revision,
                    "code_state": self.policy.code_state,
                    "provider_concurrency": self.provider_concurrency,
                }
            )
            + b"\n",
            media_type="application/vnd.pixelgym.environment-manifest+json",
        )
        examples, _ = self._inputs()
        representative_refs = [
            self.store.put_once(
                f"runs/{self.submission_id}/representative-images/{example['example_id']}.png",
                (self.root / example["image_path"]).read_bytes(),
                media_type="image/png",
            )
            for example in examples[:3]
        ]
        references = [
            *([dataset_ref] if dataset_ref else []),
            *raw_refs,
            raw_index_ref,
            prediction_ref,
            score_ref,
            summary_ref,
            gate_ref,
            environment_ref,
            policy_ref,
            *representative_refs,
        ]
        run_manifest = {
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
            "submission_id": self.submission_id,
            "mlflow_run_id": summary.run_id,
            "metaflow_pathspec": self.metaflow_pathspec,
            "dataset_fingerprint": self.dataset_fingerprint,
            "policy_id": self.policy.policy_id,
            "status": "Complete",
            "request": {
                "dataset": "day3-frozen-v1",
                "prompt_version": str(self.policy.prompt_version),
                "model": self.policy.model,
                "condition": self.policy.condition,
                "maximum_calls": str(summary.expected_count),
                "price_catalog": self.price_catalog_version,
            },
            "artifact_index": [item.to_dict() for item in references],
        }
        self.schemas.validate("run_manifest", run_manifest)
        manifest_ref = self.store.put_once(
            f"runs/{self.submission_id}/run-manifest.json",
            canonical_json_bytes(run_manifest) + b"\n",
            media_type="application/vnd.pixelgym.run-manifest+json",
        )
        return [*references, manifest_ref]

    def finalize_success(
        self,
        summary: RunSummary,
        report: GateReport,
        references: list[ArtifactRef],
    ) -> None:
        if self.tracking is None:
            raise RuntimeError("tracking is required to finalize a successful run")
        self.tracking.log_summary(summary.run_id, summary, report, references)
        self.tracking.register_policy(summary.run_id, self.policy)
        self.tracking.finalize(summary.run_id, "FINISHED")

    def finalize_failure(self, *, run_id: str | None = None) -> str:
        """Recover the parent run and retain an explicit failed terminal status."""
        if self.tracking is None:
            raise RuntimeError("tracking is required to finalize a failed run")
        target_run_id = run_id or self._tracking_run_id
        if target_run_id is None:
            examples, _ = self._inputs()
            target_run_id = self.tracking.create_or_recover_run(
                self.submission_id, self._tracking_params(len(examples))
            )
        self._tracking_run_id = target_run_id
        self.tracking.finalize(target_run_id, "FAILED")
        return target_run_id

    def run(self, *, max_calls: int) -> tuple[RunSummary, Any, list[ArtifactRef]]:
        try:
            return self._run_once(max_calls=max_calls)
        except BaseException:
            # Recover the same parent run and retain an explicit terminal status. Immutable raw
            # evidence already written before the failure remains available for resume.
            self.finalize_failure()
            raise

    def _run_once(self, *, max_calls: int) -> tuple[RunSummary, Any, list[ArtifactRef]]:
        run_id = self.create_or_recover_run(max_calls=max_calls)
        evaluation_started = time.perf_counter()
        # The compatibility runner keeps a one-example parse boundary so its interruption tests
        # remain maximally strict. The Metaflow graph uses larger deterministic fetch shards and
        # joins them before the explicit verification and offline parse steps.
        shards = self.build_shards(shard_size=1, max_calls=max_calls)
        raw_parts: list[list[dict[str, Any]]] = []
        records: list[dict[str, Any]] = []
        for shard in shards:
            part = self.evaluate_shard(shard, max_calls=max_calls)
            verified = self.verify_raw_artifacts(part, require_complete=False)
            records.extend(self.parse_and_score(verified, require_complete=False))
            raw_parts.append(part)
        raw = self.canonical_join(raw_parts)
        summary = self.aggregate_metrics(
            records,
            run_id=run_id,
            evaluation_end_to_end_duration_ms=(time.perf_counter() - evaluation_started) * 1000,
        )
        report = self.evaluate_gates(summary)
        references = self.persist_evidence(records, summary, report, raw)
        self.finalize_success(summary, report, references)
        return summary, report, references
