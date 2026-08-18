from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

mlflow = pytest.importorskip("mlflow")

from pixelgym.platform.contracts import ArtifactRef, GatePolicy, RunSummary
from pixelgym.platform.dependency_lock import dependency_lock_sha256
from pixelgym.platform.fingerprints import sha256_bytes
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.mlflow_tracking import (
    RUN_PARAM_KEYS,
    DatasetInputContract,
    MlflowTracking,
)
from pixelgym.platform.policy import (
    PROMPT_NAME,
    PROMPT_TEMPLATES,
    build_policy_manifest,
    prompt_template,
)
from pixelgym.platform.source_provenance import SOURCE_PROVENANCE_SCHEMA_VERSION, SourceProvenance


@pytest.mark.platform_integration
def test_real_mlflow_adapter_logs_complete_linked_contract(tmp_path) -> None:
    repository_root = Path.cwd()
    tracking = MlflowTracking(f"sqlite:///{tmp_path / 'mlflow.db'}")
    gate = GatePolicy(
        **json.loads((repository_root / "config/promotion-gates.demo-v1.json").read_text())
    )
    policy = build_policy_manifest(
        provider="scripted-demo",
        model="day3-replay-revised-v2",
        prompt_name=PROMPT_NAME,
        prompt_version=2,
        prompt=prompt_template(2),
        condition="raw",
        parameters={"deterministic": True},
        parser_version="pixelgym-grounding-parser-v1",
        scorer_version=gate.required_scorer_version,
        overlay_version="none-raw-coordinate-policy",
        target_semantics=gate.required_target_semantics,
        source_provenance=SourceProvenance(
            SOURCE_PROVENANCE_SCHEMA_VERSION, "b" * 40, "c" * 64, "clean", "git-build-inputs-v1"
        ),
        dependency_lock_sha256=dependency_lock_sha256(repository_root),
    )
    tracking.ensure_prompt_version(
        policy.prompt_name,
        policy.prompt_version,
        PROMPT_TEMPLATES,
        sha256_bytes(prompt_template(policy.prompt_version).encode()),
    )
    params = {
        "dataset_name": "pixelgym-grounding-day3-frozen",
        "dataset_fingerprint": gate.required_dataset_fingerprint,
        "mlflow_dataset_digest": gate.required_dataset_fingerprint.removeprefix("sha256:")[:32],
        "dataset_manifest_uri": "s3://immutable/datasets/manifest.json",
        "dataset_manifest_version": "version-1",
        "dataset_schema": "pixelgym-grounding-dataset-manifest-v1",
        "dataset_protocol_version": "pixelgym-grounding-v1",
        "dataset_example_count": 100,
        "prompt_name": policy.prompt_name,
        "prompt_version": policy.prompt_version,
        "prompt_sha256": policy.prompt_sha256,
        "rendered_template_schema": "pixelgym-grounding-rendered-prompt-v1",
        "provider": policy.provider,
        "model": policy.model,
        "model_alias_disclosure": policy.model_alias_disclosure,
        "endpoint_class": "local-scripted",
        "structured_output_mode": "json-schema",
        "inference_temperature": None,
        "inference_reasoning": "unsupported",
        "inference_seed": None,
        "inference_max_output": "provider-default",
        "condition": policy.condition,
        "retry_policy": {"hidden_retries": 0, "request_retry": "none"},
        "concurrency": 1,
        "parser_version": policy.parser_version,
        "scorer_version": policy.scorer_version,
        "target_semantics": policy.target_semantics,
        "price_catalog_version": "pixelgym-demo-prices-v1",
        "code_revision": policy.code_revision,
        "code_state": policy.code_state,
        "source_tree_sha256": policy.source_tree_sha256,
        "source_provenance_verified": policy.source_provenance_verified,
        "source_provenance_failure_reason": policy.source_provenance_failure_reason,
        "dependency_lock_sha256": policy.dependency_lock_sha256,
        "python_version": "3.12.0",
        "metaflow_flow_name": "GroundingEvaluationFlow",
        "metaflow_attempt": 0,
        "metaflow_resume_origin": "GroundingEvaluationFlow/integration",
        "submission_id": "submission-integration",
        "synthetic_provider": True,
    }
    run_id = tracking.create_or_recover_run("submission-integration", params)
    dataset_input = DatasetInputContract(
            name=params["dataset_name"],
            fingerprint=params["dataset_fingerprint"],
            mlflow_digest=params["mlflow_dataset_digest"],
            manifest_uri=params["dataset_manifest_uri"],
            manifest_version=params["dataset_manifest_version"],
            schema=params["dataset_schema"],
            protocol_version=params["dataset_protocol_version"],
            example_count=100,
        )
    tracking.log_dataset_input(run_id, dataset_input)
    tracking.log_dataset_input(run_id, dataset_input)
    tracking.link_prompt_to_run(run_id, policy.prompt_name, policy.prompt_version)
    tracking.reconcile_pathspec(run_id, "GroundingEvaluationFlow/integration")
    summary = RunSummary(
        run_id,
        gate.required_dataset_fingerprint,
        policy.policy_id,
        gate.required_scorer_version,
        gate.required_target_semantics,
        100,
        100,
        100,
        100,
        1.0,
        0.0,
        100,
        0,
        25.0,
        100,
    )
    summary = replace(
        summary,
        provider_latency_p50_ms=20.0,
        provider_latency_max_ms=30.0,
        evaluation_end_to_end_duration_ms=2500.0,
        total_cost_usd=0.0,
        cost_usd_per_example=0.0,
    )
    report = evaluate_gates(gate, summary)
    suffixes = (
        "run-manifest.json",
        "raw-response-index.json",
        "predictions.jsonl",
        "per-example-scores.jsonl",
        "summary.json",
        "gate-report.json",
        "environment-manifest.json",
    )
    artifacts = [
        ArtifactRef(
            logical_key=f"runs/submission-integration/{suffix}",
            uri=f"s3://immutable/{suffix}",
            version_id="version-1",
            sha256=f"{index:064x}",
            size=1,
            media_type="application/json",
            retention_status="locked",
        )
        for index, suffix in enumerate(suffixes, 1)
    ]
    artifacts.extend(
        [
            ArtifactRef(
                logical_key="policies/policy.json",
                uri="s3://immutable/policy.json",
                version_id="version-1",
                sha256="a" * 64,
                size=1,
                media_type="application/json",
                retention_status="locked",
            ),
            ArtifactRef(
                logical_key="runs/submission-integration/representative-images/example.png",
                uri="s3://immutable/example.png",
                version_id="version-1",
                sha256="b" * 64,
                size=1,
                media_type="image/png",
                retention_status="locked",
            ),
        ]
    )
    tracking.log_summary(run_id, summary, report, artifacts)
    first_version = tracking.register_policy(run_id, policy)
    assert tracking.register_policy(run_id, policy) == first_version
    tracking.finalize(run_id, "FINISHED")

    run = tracking.client.get_run(run_id)
    assert run.info.status == "FINISHED"
    assert run.data.tags["metaflow.pathspec"] == "GroundingEvaluationFlow/integration"
    assert run.data.tags["pixelgym.synthetic"] == "true"
    assert run.data.metrics["accuracy"] == 1.0
    assert set(run.data.params) == set(RUN_PARAM_KEYS)
    assert len(run.inputs.dataset_inputs) == 1
    assert run.inputs.dataset_inputs[0].dataset.digest == params["mlflow_dataset_digest"]
    assert {
        "provider_latency_p50_ms",
        "provider_latency_p95_ms",
        "provider_latency_max_ms",
        "evaluation_end_to_end_duration_ms",
        "total_cost_usd",
        "cost_usd_per_example",
        "invalid_rate",
        "request_failure_rate",
        "gate_accuracy_passed",
        "gate_cost_usd_per_100_passed",
        "gate_provider_latency_p95_ms_passed",
    } <= set(run.data.metrics)
    assert {item.path for item in tracking.client.list_artifacts(run_id)} >= {
        "summary.json",
        "gate-report.json",
        "immutable-artifact-index.json",
        "run-manifest-reference.json",
        "raw-response-index-reference.json",
        "parsed-predictions-reference.json",
        "per-example-scores-reference.json",
        "environment-manifest-reference.json",
        "representative-images-reference.json",
        "policy",
    }
    assert run.data.tags["prompt.uri"] == f"prompts:/{policy.prompt_name}/{policy.prompt_version}"
    prompt = tracking.client.get_prompt_version(policy.prompt_name, policy.prompt_version)
    assert prompt.tags[f"pixelgym.run.{run_id}"] == "linked"
    versions = tracking.client.search_model_versions("name = 'pixelgym-grounding-policy'")
    assert len(versions) == 1
    gate_status = "eligible" if report.overall_passed else "failed"
    assert versions[0].tags["gate_status"] == gate_status
    assert versions[0].tags["approval_status"] == "pending"
    tracking.mirror_candidate_status(
        policy.policy_id, gate_status=gate_status, approval_status="approved"
    )
    tracking.set_champion(policy.policy_id)
    version = tracking.client.get_model_version("pixelgym-grounding-policy", first_version)
    assert version.tags["approval_status"] == "approved"
    assert (
        str(
            tracking.client.get_model_version_by_alias(
                "pixelgym-grounding-policy", "champion"
            ).version
        )
        == first_version
    )
    compatible = tracking.search_compatible_runs(
        dataset_fingerprint=gate.required_dataset_fingerprint,
        scorer_version=gate.required_scorer_version,
        target_semantics=gate.required_target_semantics,
    )
    assert [item.run_id for item in compatible] == [run_id]
    assert compatible[0].artifact_paths == ()
    detail = tracking.get_run_view(run_id)
    assert "policy/policy-manifest.json" in detail.artifact_paths
