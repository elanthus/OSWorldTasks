"""Local Metaflow evidence run over an explicit seed-by-policy assignment plan."""

from __future__ import annotations

import json
import os
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import metaflow
from metaflow import FlowSpec, Parameter, current, step

from pixelgym.platform.contracts import GatePolicy, PolicyManifest
from pixelgym.platform.evaluation import (
    EvaluationRunner,
    PlatformProviderResponse,
    ScriptedReplayProvider,
)
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.immutable_store import LocalImmutableStore
from pixelgym.platform.matrix_evaluation import (
    canonical_seed_policy_aggregate,
    example_ids_for_seed,
    load_seed_policy_plan,
    seed_policy_plan_digest,
)
from pixelgym.platform.schema_validation import load_gate_policy, load_price_catalog

_TEST_HOOKS_ENV = "PIXELGYM_ENABLE_TEST_HOOKS"
_MODEL_BEHAVIOR = {
    "day3-replay-baseline-v1": "baseline",
    "day3-replay-revised-v2": "revised",
    "day3-replay-invalid-v1": "invalid",
    "day3-replay-request-failure-v1": "request_failure",
}


def _root() -> Path:
    return Path(os.environ.get("PIXELGYM_REPOSITORY_ROOT", Path.cwd())).resolve()


def _store() -> LocalImmutableStore:
    return LocalImmutableStore(
        Path(os.environ.get("PIXELGYM_IMMUTABLE_ROOT", _root() / ".cache/platform/matrix"))
    )


class _OutcomeProvider:
    name = "scripted-demo"
    synthetic = True

    def __init__(self, *, model: str, outcome: str) -> None:
        self.model = model
        self.outcome = outcome

    def invoke(self, **request: Any) -> PlatformProviderResponse:
        del request
        if self.outcome == "invalid":
            return PlatformProviderResponse("not-json", 25.0, {}, 0.0)
        return PlatformProviderResponse(
            None,
            None,
            None,
            None,
            "deterministic scripted request failure",
        )


def _provider(policy: PolicyManifest) -> object:
    behavior = _MODEL_BEHAVIOR.get(policy.model)
    if behavior is None:
        raise ValueError("matrix policy model is outside the scripted allowlist")
    ledger = os.environ.get("PIXELGYM_TEST_PROVIDER_LEDGER")
    if ledger:
        if os.environ.get(_TEST_HOOKS_ENV) != "1":
            raise RuntimeError("the ledgered provider is available only with explicit test hooks")
        from pixelgym.platform.runtime_fixture import LedgeredScriptedReplayProvider

        return LedgeredScriptedReplayProvider(
            _root() / "artifacts/grounding-predictions.jsonl",
            variant=behavior,
            model=policy.model,
            ledger_path=Path(ledger),
            concurrency_barrier=int(os.environ.get("PIXELGYM_TEST_CONCURRENCY_BARRIER", "1")),
        )
    if behavior in {"baseline", "revised"}:
        return ScriptedReplayProvider(
            _root() / "artifacts/grounding-predictions.jsonl",
            variant=behavior,
            model=policy.model,
        )
    return _OutcomeProvider(model=policy.model, outcome=behavior)


def _runner(flow: object, policy: PolicyManifest) -> EvaluationRunner:
    return EvaluationRunner(
        repository_root=_root(),
        store=_store(),
        tracking=None,
        provider=_provider(policy),
        policy=policy,
        gate_policy=GatePolicy(**flow.gate_policy),
        dataset_fingerprint=flow.dataset_fingerprint,
        submission_id=f"matrix-{flow.plan_digest.removeprefix('sha256:')[:16]}",
        metaflow_pathspec=flow.metaflow_pathspec,
        price_catalog_version=flow.price_catalog_version,
        provider_concurrency=1,
    )


def _fail_branch_once(assignment_id: str) -> None:
    if os.environ.get(_TEST_HOOKS_ENV) != "1":
        return
    if os.environ.get("PIXELGYM_TEST_FAIL_ONCE") != "matrix_branch_persisted":
        return
    state_root = os.environ.get("PIXELGYM_TEST_STATE_ROOT")
    if not state_root:
        raise RuntimeError("matrix failure injection requires PIXELGYM_TEST_STATE_ROOT")
    root = Path(state_root)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / "matrix_branch_persisted.triggered"
    try:
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    with os.fdopen(descriptor, "w") as handle:
        json.dump({"assignment_id": assignment_id, "event": "injected_branch_failure"}, handle)
    raise RuntimeError(f"injected one-shot failure after matrix branch {assignment_id}")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SeedPolicyFanoutFlow(FlowSpec):
    plan_file = Parameter("plan-file", required=True)
    output_file = Parameter("output-file", required=True)
    worker_cap = Parameter("worker-cap", type=int, default=4)

    @step
    def start(self) -> None:
        if self.worker_cap != 4:
            raise ValueError("the local evidence flow requires the owner-approved worker cap of 4")
        self.flow_started_at_utc = _now()
        self.plan = load_seed_policy_plan(Path(self.plan_file), repository_root=_root())
        self.plan_digest = seed_policy_plan_digest(self.plan)
        self.dataset_fingerprint = self.plan["dataset_fingerprint"]
        self.gate_policy = load_gate_policy(_root()).to_dict()
        price_catalog = load_price_catalog(_root())
        self.price_catalog_version = price_catalog["catalog_version"]
        lineage_run_id = getattr(current, "origin_run_id", None) or current.run_id
        self.metaflow_pathspec = f"{current.flow_name}/{lineage_run_id}"
        self.next(self.build_shards)

    @step
    def build_shards(self) -> None:
        policies = {item["policy_id"]: item for item in self.plan["policies"]}
        self.shards = [
            {
                **assignment,
                "policy": policies[assignment["policy_id"]],
                "example_ids": example_ids_for_seed(_root(), assignment["seed"]),
            }
            for assignment in self.plan["assignments"]
        ]
        self.fanout_dispatched_at_utc = _now()
        self.next(self.evaluate_shard, foreach="shards")

    @step
    def evaluate_shard(self) -> None:
        started_at = _now()
        started_monotonic = time.monotonic()
        policy = PolicyManifest(**self.input["policy"])
        runner = _runner(self, policy)
        raw = runner.evaluate_shard(
            {"index": 0, "example_ids": self.input["example_ids"]},
            max_calls=len(self.input["example_ids"]),
        )
        verified = runner.verify_raw_artifacts(raw, require_complete=False)
        records = runner.parse_and_score(verified, require_complete=False)
        canonical_records = [
            {
                "correct": row["correct"],
                "example_id": row["example_id"],
                "parse_error": row["parse_error"],
                "parse_status": row["parse_status"],
                "request_failure": (
                    row["parse_error"] if row["parse_status"] == "request_failure" else None
                ),
            }
            for row in records
        ]
        invalid_count = sum(row["parse_status"] == "invalid" for row in records)
        failure_count = sum(row["parse_status"] == "request_failure" for row in records)
        outcome = "request_failure" if failure_count else "invalid" if invalid_count else "completed"
        ended_at = _now()
        self.branch_result = {
            "content": {
                "assignment_id": self.input["assignment_id"],
                "correct_count": sum(row["correct"] is True for row in records),
                "expected_count": len(self.input["example_ids"]),
                "invalid_count": invalid_count,
                "outcome": outcome,
                "policy_id": self.input["policy_id"],
                "records": canonical_records,
                "request_failure_count": failure_count,
                "seed": self.input["seed"],
            },
            "timing": {
                "assignment_id": self.input["assignment_id"],
                "attempt": int(getattr(current, "retry_count", 0)),
                "ended_at_utc": ended_at,
                "queue_duration_ms": round(
                    (datetime.fromisoformat(started_at) - datetime.fromisoformat(self.fanout_dispatched_at_utc)).total_seconds()
                    * 1000,
                    3,
                ),
                "resume_origin_run_id": getattr(current, "origin_run_id", None),
                "run_id": str(current.run_id),
                "runtime_duration_ms": round((time.monotonic() - started_monotonic) * 1000, 3),
                "started_at_utc": started_at,
                "task_id": str(current.task_id),
            },
        }
        _fail_branch_once(self.input["assignment_id"])
        self.next(self.join_shards)

    @step
    def join_shards(self, inputs: list[SeedPolicyFanoutFlow]) -> None:
        join_started = _now()
        if not inputs:
            raise ValueError("seed-policy join requires branch inputs")
        source = inputs[0]
        for name in (
            "dataset_fingerprint",
            "fanout_dispatched_at_utc",
            "flow_started_at_utc",
            "gate_policy",
            "metaflow_pathspec",
            "plan",
            "plan_digest",
            "price_catalog_version",
        ):
            setattr(self, name, getattr(source, name))
        results = [branch.branch_result for branch in inputs]
        aggregate_bytes = canonical_seed_policy_aggregate(self.plan, results)
        join_ended = _now()
        policy_revisions = sorted({item["code_revision"] for item in self.plan["policies"]})
        if len(policy_revisions) != 1:
            raise ValueError("all policies in one matrix plan must share one exact revision")
        branch_timings = sorted(
            (result["timing"] for result in results), key=lambda item: item["assignment_id"]
        )
        retry_events = [
            {
                "assignment_id": timing["assignment_id"],
                "attempt": timing["attempt"],
                "event": "metaflow_retry",
                "run_id": timing["run_id"],
                "task_id": timing["task_id"],
            }
            for timing in branch_timings
            if timing["attempt"] > 0
        ]
        resume_events = (
            [
                {
                    "event": "metaflow_resume",
                    "origin_run_id": str(current.origin_run_id),
                    "resumed_run_id": str(current.run_id),
                }
            ]
            if getattr(current, "origin_run_id", None)
            else []
        )
        evidence = {
            "schema_version": "pixelgym-seed-policy-runtime-evidence-v1",
            "plan_digest": self.plan_digest,
            "aggregate_sha256": "sha256:" + sha256_bytes(aggregate_bytes),
            "aggregate": json.loads(aggregate_bytes),
            "branches": branch_timings,
            "flow_started_at_utc": self.flow_started_at_utc,
            "flow_ended_at_utc": join_ended,
            "join_started_at_utc": join_started,
            "join_ended_at_utc": join_ended,
            "retry_events": retry_events,
            "resume_events": resume_events,
            "runtime_context": {
                "cpu_count": os.cpu_count(),
                "metaflow_version": metaflow.__version__,
                "platform": platform.platform(),
                "policy_revisions": policy_revisions,
                "python_version": platform.python_version(),
                "revision": policy_revisions[0],
                "worker_cap": self.worker_cap,
            },
        }
        output = Path(self.output_file)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(canonical_json_bytes(evidence) + b"\n")
        self.evidence = evidence
        self.next(self.end)

    @step
    def end(self) -> None:
        pass


if __name__ == "__main__":
    SeedPolicyFanoutFlow()
