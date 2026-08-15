from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelgym.platform.contracts import GatePolicy, RunSummary
from pixelgym.platform.dependency_lock import dependency_lock_sha256
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.policy import PROMPT_NAME, build_policy_manifest, prompt_template
from pixelgym.platform.source_provenance import SOURCE_PROVENANCE_SCHEMA_VERSION, SourceProvenance


@pytest.fixture
def repository_root() -> Path:
    return Path(__file__).parents[3]


@pytest.fixture
def gate_policy(repository_root: Path) -> GatePolicy:
    return GatePolicy(
        **json.loads((repository_root / "config/promotion-gates.demo-v1.json").read_text())
    )


@pytest.fixture
def policy_factory(repository_root: Path, gate_policy: GatePolicy):
    def build(version: int = 2, *, revision: str = "a" * 40, model: str | None = None):
        return build_policy_manifest(
            provider="scripted-demo",
            model=model or ("day3-replay-baseline-v1" if version == 1 else "day3-replay-revised-v2"),
            prompt_name=PROMPT_NAME,
            prompt_version=version,
            prompt=prompt_template(version),
            condition="raw",
            parameters={"deterministic": True, "hidden_retries": 0},
            parser_version="pixelgym-grounding-parser-v1",
            scorer_version=gate_policy.required_scorer_version,
            overlay_version="none-raw-coordinate-policy",
            target_semantics=gate_policy.required_target_semantics,
            source_provenance=SourceProvenance(
                SOURCE_PROVENANCE_SCHEMA_VERSION, revision, "b" * 64, "clean", "git-build-inputs-v1"
            ),
            dependency_lock_sha256=dependency_lock_sha256(repository_root),
        )

    return build


@pytest.fixture
def passing_evidence(gate_policy: GatePolicy, policy_factory):
    policy = policy_factory()
    summary = RunSummary(
        run_id="run-1",
        dataset_fingerprint=gate_policy.required_dataset_fingerprint,
        policy_id=policy.policy_id,
        scorer_version=gate_policy.required_scorer_version,
        target_semantics=gate_policy.required_target_semantics,
        expected_count=100,
        scored_count=100,
        unique_record_count=100,
        correct_count=80,
        accuracy=0.8,
        cost_usd_per_100=0.0,
        priced_call_count=100,
        unpriced_call_count=0,
        provider_latency_p95_ms=100.0,
        latency_measured_count=100,
        code_state="clean",
        code_provenance_verified=True,
    )
    return policy, summary, evaluate_gates(gate_policy, summary)
