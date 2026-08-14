"""Idempotent platform evaluation wrapped around the frozen Day 3 parser and scorer."""

from __future__ import annotations

import json
import math
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pixelgym.grounding.evaluation import parse_prediction, prompt_for, schema_for, score_point
from pixelgym.platform.contracts import ArtifactRef, GatePolicy, PolicyManifest, RunSummary
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.immutable_store import ImmutableStore
from pixelgym.platform.mlflow_tracking import Tracking
from pixelgym.platform.policy import PROMPT_TEMPLATES, is_verified_clean_revision, prompt_template

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

    def __init__(self, prediction_path: Path, *, variant: str, latency_ms: float = 25.0) -> None:
        if variant not in {"baseline", "revised"}:
            raise ValueError("scripted variant must be baseline or revised")
        self.condition = "raw"
        self.model = f"day3-replay-{variant}-{'v1' if variant == 'baseline' else 'v2'}"
        self.latency_ms = latency_ms
        rows = [json.loads(line) for line in prediction_path.read_text().splitlines() if line.strip()]
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


def _load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class EvaluationRunner:
    def __init__(
        self,
        *,
        repository_root: Path,
        store: ImmutableStore,
        tracking: Tracking,
        provider: PlatformProvider,
        policy: PolicyManifest,
        gate_policy: GatePolicy,
        dataset_fingerprint: str,
        submission_id: str,
        metaflow_pathspec: str,
        price_catalog_version: str = "pixelgym-demo-prices-v1",
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

    def _tracking_params(self, example_count: int) -> dict[str, Any]:
        return {
            "dataset_fingerprint": self.dataset_fingerprint,
            "dataset_protocol_version": "pixelgym-grounding-v1",
            "dataset_example_count": example_count,
            "prompt_name": self.policy.prompt_name,
            "prompt_version": self.policy.prompt_version,
            "prompt_sha256": self.policy.prompt_sha256,
            "provider": self.policy.provider,
            "model": self.policy.model,
            "condition": self.policy.condition,
            "parser_version": self.policy.parser_version,
            "scorer_version": self.policy.scorer_version,
            "target_semantics": self.policy.target_semantics,
            "price_catalog_version": self.price_catalog_version,
            "code_revision": self.policy.code_revision,
            "dependency_lock_sha256": self.policy.dependency_lock_sha256,
            "python_version": platform.python_version(),
            "submission_id": self.submission_id,
            "synthetic_provider": self.provider.synthetic,
        }

    def run(self, *, max_calls: int) -> tuple[RunSummary, Any, list[ArtifactRef]]:
        try:
            return self._run_once(max_calls=max_calls)
        except BaseException:
            # Recover the same parent run and retain an explicit terminal status. Immutable raw
            # evidence already written before the failure remains available for resume.
            examples = _load_rows(self.root / "artifacts/grounding-dataset.jsonl")
            run_id = self.tracking.create_or_recover_run(
                self.submission_id, self._tracking_params(len(examples))
            )
            self.tracking.finalize(run_id, "FAILED")
            raise

    def _run_once(self, *, max_calls: int) -> tuple[RunSummary, Any, list[ArtifactRef]]:
        examples = _load_rows(self.root / "artifacts/grounding-dataset.jsonl")
        overlays = {
            row["example_id"]: row
            for row in _load_rows(self.root / "artifacts/grounding-overlays.jsonl")
        }
        if max_calls < len(examples):
            raise RuntimeError("evaluation call cap is below the frozen example count")
        self.tracking.ensure_prompt_version(
            self.policy.prompt_name,
            self.policy.prompt_version,
            PROMPT_TEMPLATES,
            self.policy.prompt_sha256,
        )
        mlflow_run_id = self.tracking.create_or_recover_run(
            self.submission_id, self._tracking_params(len(examples))
        )
        self.tracking.link_prompt_to_run(
            mlflow_run_id, self.policy.prompt_name, self.policy.prompt_version
        )
        self.tracking.reconcile_pathspec(mlflow_run_id, self.metaflow_pathspec)
        records: list[dict[str, Any]] = []
        raw_refs: list[ArtifactRef] = []
        for example in sorted(examples, key=lambda row: row["example_id"]):
            overlay = overlays[example["example_id"]]
            condition = self.policy.condition
            # Version 1 is the frozen Day 3 renderer. Version 2 adds a target-neutral semantic
            # disambiguation sentence without introducing boxes or expected answers.
            prompt = prompt_for(example, condition)
            if self.policy.prompt_version > 1:
                prompt += " " + prompt_template(self.policy.prompt_version).replace(
                    "{{target}}", example["target"]
                )
            schema = schema_for(condition)
            image_relative = (
                example["image_path"] if condition == "raw" else overlay["marked_image_path"]
            )
            request_material = {
                "dataset_fingerprint": self.dataset_fingerprint,
                "policy_id": self.policy.policy_id,
                "example_id": example["example_id"],
                "condition": condition,
                "image_sha256": (
                    example["image_sha256"]
                    if condition == "raw"
                    else overlay["marked_image_sha256"]
                ),
                "prompt_sha256": sha256_bytes(prompt.encode()),
                "schema": schema,
            }
            request_sha256 = sha256_bytes(canonical_json_bytes(request_material))
            request_id = "sha256:" + request_sha256
            logical_key = f"raw-responses/{request_sha256}.json"
            reference = self.store.get_reference(logical_key)
            if reference is None:
                response = self.provider.invoke(
                    request_id=request_id,
                    example_id=example["example_id"],
                    condition=condition,
                    image_path=self.root / image_relative,
                    prompt=prompt,
                    schema=schema,
                )
                response_bytes = (
                    response.raw_response.encode("utf-8")
                    if response.raw_response is not None
                    else b""
                )
                envelope = {
                    "schema_version": RAW_RESPONSE_SCHEMA_VERSION,
                    "request_id": request_id,
                    "example_id": example["example_id"],
                    "dataset_fingerprint": self.dataset_fingerprint,
                    "policy_id": self.policy.policy_id,
                    "request_sha256": request_sha256,
                    "started_at_utc": None,
                    "started_at_missing_reason": (
                        "provider adapter does not expose an attributable request-start timestamp"
                    ),
                    "latency_ms": response.latency_ms,
                    "usage": response.usage,
                    "usage_missing_reason": None if response.usage is not None else "provider omitted usage",
                    "price_catalog_version": self.price_catalog_version,
                    "cost_usd": response.cost_usd,
                    "cost_missing_reason": None if response.cost_usd is not None else "price or usage unavailable",
                    "response_media_type": "application/json",
                    "response_sha256": sha256_bytes(response_bytes),
                    "raw_response": response.raw_response,
                    "request_status": "request_failure" if response.request_failure else "responded",
                    "request_failure": response.request_failure,
                }
                # This durable write is deliberately before parser execution.
                reference = self.store.put_once(
                    logical_key,
                    canonical_json_bytes(envelope) + b"\n",
                    media_type="application/vnd.pixelgym.raw-response+json",
                )
            envelope = json.loads(self.store.get_verified(reference))
            if (
                envelope["request_sha256"] != request_sha256
                or envelope["policy_id"] != self.policy.policy_id
                or envelope["dataset_fingerprint"] != self.dataset_fingerprint
            ):
                raise ValueError("stored raw response identity does not match request")
            response_text = envelope["raw_response"]
            response_digest = sha256_bytes(
                response_text.encode("utf-8") if response_text is not None else b""
            )
            if response_digest != envelope["response_sha256"]:
                raise ValueError("stored raw response digest mismatch")
            if envelope["request_failure"]:
                parse_status, parsed, point, mark_id, parse_error = (
                    "request_failure", None, None, None, envelope["request_failure"]
                )
            else:
                outcome = parse_prediction(
                    response_text,
                    condition=condition,
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
                    "condition": condition,
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
            raw_refs.append(reference)
        unique = {(row["example_id"], row["condition"]) for row in records}
        latency = [float(row["latency_ms"]) for row in records if row["latency_ms"] is not None]
        costs = [float(row["cost_usd"]) for row in records if row["cost_usd"] is not None]
        correct_count = sum(row["correct"] is True for row in records)
        summary = RunSummary(
            run_id=mlflow_run_id,
            dataset_fingerprint=self.dataset_fingerprint,
            policy_id=self.policy.policy_id,
            scorer_version=self.policy.scorer_version,
            target_semantics=self.policy.target_semantics,
            expected_count=len(examples),
            scored_count=len(records),
            unique_record_count=len(unique),
            correct_count=correct_count,
            accuracy=correct_count / len(examples),
            cost_usd_per_100=(sum(costs) * 100 / len(records)) if len(costs) == len(records) else None,
            priced_call_count=len(costs),
            unpriced_call_count=len(records) - len(costs),
            provider_latency_p95_ms=percentile_r7(latency, 0.95) if latency else None,
            latency_measured_count=len(latency),
            invalid_count=sum(row["parse_status"] == "invalid" for row in records),
            request_failure_count=sum(row["parse_status"] == "request_failure" for row in records),
            dirty_code=not is_verified_clean_revision(self.policy.code_revision),
            synthetic_provider=self.provider.synthetic,
        )
        report = evaluate_gates(self.gate_policy, summary)
        prediction_ref = self.store.put_once(
            f"runs/{self.submission_id}/predictions.jsonl",
            b"".join(canonical_json_bytes(row) + b"\n" for row in records),
            media_type="application/x-ndjson",
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
        refs = [*([dataset_ref] if dataset_ref else []), *raw_refs, prediction_ref, gate_ref, policy_ref]
        self.tracking.log_summary(mlflow_run_id, summary, report, refs)
        self.tracking.register_policy(mlflow_run_id, self.policy)
        self.tracking.finalize(mlflow_run_id, "FINISHED")
        return summary, report, refs
