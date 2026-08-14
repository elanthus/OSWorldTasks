from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

mlflow = pytest.importorskip("mlflow")

from pixelgym.platform.contracts import GatePolicy, RunSummary
from pixelgym.platform.fingerprints import sha256_bytes
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.mlflow_tracking import MlflowTracking
from pixelgym.platform.policy import (
    PROMPT_NAME,
    PROMPT_TEMPLATES,
    build_policy_manifest,
    prompt_template,
)


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
        code_revision="b" * 40,
        dependency_lock_sha256=hashlib.sha256(
            (repository_root / "pyproject.toml").read_bytes()
        ).hexdigest(),
    )
    tracking.ensure_prompt_version(
        policy.prompt_name,
        policy.prompt_version,
        PROMPT_TEMPLATES,
        sha256_bytes(prompt_template(policy.prompt_version).encode()),
    )
    params = {
        "dataset_fingerprint": gate.required_dataset_fingerprint,
        "dataset_protocol_version": "pixelgym-grounding-v1",
        "dataset_example_count": 100,
        "prompt_name": policy.prompt_name,
        "prompt_version": policy.prompt_version,
        "prompt_sha256": policy.prompt_sha256,
        "provider": policy.provider,
        "model": policy.model,
        "condition": policy.condition,
        "parser_version": policy.parser_version,
        "scorer_version": policy.scorer_version,
        "target_semantics": policy.target_semantics,
        "price_catalog_version": "pixelgym-demo-prices-v1",
        "code_revision": policy.code_revision,
        "dependency_lock_sha256": policy.dependency_lock_sha256,
        "python_version": "3.12.0",
        "submission_id": "submission-integration",
        "synthetic_provider": True,
    }
    run_id = tracking.create_or_recover_run("submission-integration", params)
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
    report = evaluate_gates(gate, summary)
    tracking.log_summary(run_id, summary, report, [])
    first_version = tracking.register_policy(run_id, policy)
    assert tracking.register_policy(run_id, policy) == first_version
    tracking.finalize(run_id, "FINISHED")

    run = tracking.client.get_run(run_id)
    assert run.info.status == "FINISHED"
    assert run.data.tags["metaflow.pathspec"] == "GroundingEvaluationFlow/integration"
    assert run.data.tags["pixelgym.synthetic"] == "true"
    assert run.data.metrics["accuracy"] == 1.0
    assert len(run.data.params) == len(params)
    assert {item.path for item in tracking.client.list_artifacts(run_id)} >= {
        "summary.json",
        "gate-report.json",
        "immutable-artifact-index.json",
        "policy",
    }
    assert run.data.tags["prompt.uri"] == f"prompts:/{policy.prompt_name}/{policy.prompt_version}"
    prompt = tracking.client.get_prompt_version(policy.prompt_name, policy.prompt_version)
    assert prompt.tags[f"pixelgym.run.{run_id}"] == "linked"
    versions = tracking.client.search_model_versions("name = 'pixelgym-grounding-policy'")
    assert len(versions) == 1
