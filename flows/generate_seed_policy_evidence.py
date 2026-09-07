"""Run the local seed-policy fan-out and generate reviewer-safe structured evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pixelgym.platform.dependency_lock import dependency_lock_sha256
from pixelgym.platform.fingerprints import build_dataset_manifest, canonical_json_bytes
from pixelgym.platform.matrix_evaluation import (
    PLAN_SCHEMA_VERSION,
    assignment_id,
    canonical_seed_policy_plan,
    seed_policy_plan_digest,
    summarize_parallel_timing,
)
from pixelgym.platform.policy import PROMPT_NAME, build_policy_manifest, prompt_template
from pixelgym.platform.runtime_fixture import provider_ledger_snapshot
from pixelgym.platform.source_provenance import generate_source_provenance

POLICY_SPECS = (
    ("day3-replay-baseline-v1", 1, "baseline"),
    ("day3-replay-revised-v2", 2, "revised"),
    ("day3-replay-invalid-v1", 2, "invalid"),
    ("day3-replay-request-failure-v1", 2, "request_failure"),
)
WORKER_CAP = 4

# Deliberately explicit: this is the plan, not an implicit Cartesian-product enumeration.
EXPLICIT_ASSIGNMENT_KEYS = (
    (0, "day3-replay-baseline-v1"),
    (0, "day3-replay-revised-v2"),
    (0, "day3-replay-invalid-v1"),
    (0, "day3-replay-request-failure-v1"),
    (1, "day3-replay-baseline-v1"),
    (1, "day3-replay-revised-v2"),
    (1, "day3-replay-invalid-v1"),
    (1, "day3-replay-request-failure-v1"),
    (2, "day3-replay-baseline-v1"),
    (2, "day3-replay-revised-v2"),
    (2, "day3-replay-invalid-v1"),
    (2, "day3-replay-request-failure-v1"),
    (3, "day3-replay-baseline-v1"),
    (3, "day3-replay-revised-v2"),
    (3, "day3-replay-invalid-v1"),
    (3, "day3-replay-request-failure-v1"),
)


def _plan(repository_root: Path) -> tuple[dict[str, Any], str]:
    provenance = generate_source_provenance(repository_root)
    if provenance.state != "clean" or provenance.revision is None:
        raise RuntimeError("evidence generation requires a clean committed revision")
    _, dataset_fingerprint = build_dataset_manifest(
        repository_root=repository_root,
        dataset_path=repository_root / "artifacts/grounding-dataset.jsonl",
        overlays_path=repository_root / "artifacts/grounding-overlays.jsonl",
    )
    lock_digest = dependency_lock_sha256(repository_root)
    policies = []
    policy_by_model: dict[str, str] = {}
    for model, prompt_version, outcome in POLICY_SPECS:
        manifest = build_policy_manifest(
            provider="scripted-demo",
            model=model,
            prompt_name=PROMPT_NAME,
            prompt_version=prompt_version,
            prompt=prompt_template(prompt_version),
            condition="raw",
            parameters={
                "deterministic": True,
                "hidden_retries": 0,
                "scripted_outcome": outcome,
            },
            parser_version="pixelgym-grounding-parser-v1",
            scorer_version="pixelgym-point-inside-half-open-box-v1",
            overlay_version="none-raw-coordinate-policy",
            target_semantics="requested-control-center-point-v1",
            source_provenance=provenance,
            dependency_lock_sha256=lock_digest,
        )
        policies.append(asdict(manifest))
        policy_by_model[model] = manifest.policy_id
    assignments = [
        {
            "assignment_id": assignment_id(
                dataset_fingerprint=dataset_fingerprint,
                seed=seed,
                policy_id=policy_by_model[model],
            ),
            "seed": seed,
            "policy_id": policy_by_model[model],
        }
        for seed, model in EXPLICIT_ASSIGNMENT_KEYS
    ]
    return canonical_seed_policy_plan(
        {
            "schema_version": PLAN_SCHEMA_VERSION,
            "dataset_fingerprint": dataset_fingerprint,
            "policies": policies,
            "assignments": assignments,
        }
    ), provenance.revision


def _environment(repository_root: Path, run_root: Path, *, fail: bool) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "METAFLOW_USER": "pixelgym-seed-policy-evidence",
            "PIXELGYM_ENABLE_TEST_HOOKS": "1",
            "PIXELGYM_IMMUTABLE_ROOT": str(run_root / "immutable"),
            "PIXELGYM_REPOSITORY_ROOT": str(repository_root),
            "PIXELGYM_TEST_CONCURRENCY_BARRIER": "4",
            "PIXELGYM_TEST_CONCURRENCY_BARRIER_TIMEOUT_SECONDS": "30",
            "PIXELGYM_TEST_PROVIDER_LEDGER": str(run_root / "provider.db"),
            "PIXELGYM_TEST_STATE_ROOT": str(run_root / "events"),
            "PYTHONPATH": str(repository_root),
        }
    )
    if fail:
        environment["PIXELGYM_TEST_FAIL_ONCE"] = "matrix_branch_persisted"
    else:
        environment.pop("PIXELGYM_TEST_FAIL_ONCE", None)
    return environment


def _invoke(
    repository_root: Path,
    run_root: Path,
    arguments: list[str],
    *,
    fail: bool,
) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, str(repository_root / "flows/seed_policy_fanout_flow.py"), *arguments],
        cwd=run_root,
        env=_environment(repository_root, run_root, fail=fail),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
        check=False,
    )
    return completed, round((time.monotonic() - started) * 1000, 3)


def _write_note(path: Path, evidence: dict[str, Any]) -> None:
    measured = evidence["parallel_run"]["derived"]
    branch_count = measured["branch_count"]
    aggregate_size = evidence["parallel_run"]["aggregate_size_bytes"]
    bytes_per_assignment = aggregate_size / branch_count
    million_bytes = bytes_per_assignment * 1_000_000
    million_bytes_two_significant_figures = format(million_bytes, ".2g")
    partition_size = 1_000
    partition_count = 1_000_000 // partition_size
    path.write_text(
        f"""# What changes at 10^6 grounding episodes

> Generated by `flows/generate_seed_policy_evidence.py`; do not edit by hand.

This note deliberately separates a measured local demonstration from capacity estimates and a
production design. It makes no production-throughput claim from one laptop.

## Measured behavior

The checked-in structured evidence `artifacts/platform/seed-policy-fanout-evidence-v1.json` is the
source for this section. Revision `{evidence["revision"]}` ran an explicit {branch_count}-assignment
seed-by-policy plan with a worker cap of {evidence["worker_cap"]}. The branches contain
{evidence["record_count"]} deterministic scripted-provider records, including
{evidence["invalid_assignment_count"]} invalid-output assignments and
{evidence["request_failure_assignment_count"]} request-failure assignments.

The stored timestamps yield {measured["overlapping_branch_pairs"]} intersecting branch pairs and a
maximum of {measured["maximum_observed_parallel_branches"]} simultaneously active branch intervals.
The generator computed {measured["serial_equivalent_branch_runtime_ms"]:.3f} ms of serial-equivalent
branch work, {measured["observed_branch_window_ms"]:.3f} ms of observed parallel branch-window time,
{measured["observed_total_wall_ms"]:.3f} ms total flow wall time, and
{measured["join_duration_ms"]:.3f} ms in the join. These are local measurements, not capacity or
service-level objectives. CPU count, Python and Metaflow versions, raw branch timestamps, queue
durations, and resume events are retained in the same evidence file.

`serial_equivalent_branch_runtime_ms` sums in-branch work only; it excludes per-task orchestration
overhead and was measured with a fixture barrier that deliberately forces branch overlap. The
subprocess command wall measurement, {evidence["parallel_run"]["command_wall_ms"]:.3f} ms, includes
local Metaflow startup and shutdown around the recorded flow.

The injected-failure run resumed once. Its provider ledger records
{evidence["resume_run"]["provider_ledger"]["attempts"]} attempts,
{evidence["resume_run"]["provider_ledger"]["unique_request_ids"]} unique request IDs, and
{evidence["resume_run"]["provider_ledger"]["billable_calls"]} completed scripted operations. The
equality of these counts is the evidence that resume did not repeat completed provider work.

## Estimated capacity (not measured throughput)

The canonical aggregate is {aggregate_size} bytes for {branch_count} assignments, or
{bytes_per_assignment:.1f} bytes per assignment in this small fixture. A linear metadata-only
estimate for 1,000,000 assignments is {million_bytes_two_significant_figures} bytes (two significant
figures). This estimate excludes screenshots,
raw model payload growth, indexes, replication, object-version overhead, logs, and compression; its
basis is only the checked-in aggregate byte count.

An unvalidated planning default of {partition_size} assignments per partition would create
approximately {partition_count} partitions for 1,000,000 assignments. Both values are design
estimates chosen to bound retry and listing scope; they are not derived from local throughput and
must be load-tested against the selected orchestrator and stores.

## Required architectural changes

- **Orchestration and partitioning:** materialize the canonical plan in a durable scheduler, split
  it into estimated {partition_size}-assignment partitions, and use hierarchical joins rather than
  a million-way local foreach. Preserve assignment IDs as idempotency keys.
- **Object and metadata stores:** move raw envelopes and aggregates to versioned object storage and
  assignment state to a transactional metadata store. Never pass a million results through one task
  artifact or one filesystem directory.
- **Backpressure, rate limits, and spend governance:** admit work through bounded queues with
  per-provider and per-tenant limits. Reserve budget before dispatch, reconcile actual usage after
  responses, and stop dispatch when rate or spend ceilings are reached.
- **Retries:** distinguish transport attempts from billable operations, require provider-side
  idempotency where available, use capped exponential backoff with jitter, and send exhausted
  assignments to a reviewable dead-letter state. Completed assignment evidence remains immutable.
- **Observability:** emit queue age, active partitions, attempts, cache hits, provider latency,
  spend, failure class, and join lag keyed by plan, partition, assignment, seed, and policy
  identities.
- **Retention:** define separate lifecycle policies for prompts, compact aggregates, raw responses,
  screenshots, and operational logs; legal/privacy review sets the periods. The local put-once store
  does not establish production WORM retention.
- **Failure domains:** isolate provider, region, scheduler, object-store, metadata-store, and policy
  failures. Use checkpointed hierarchical joins, reconcile orphaned leases, and test regional and
  store outages before assigning an availability objective.

No paid calls, external infrastructure, production time estimate, or D4.12 verdict is represented
by this note.
"""
    )


def generate(repository_root: Path, output_dir: Path, *, overwrite: bool = False) -> None:
    targets = {
        "plan": output_dir / "seed-policy-plan-v1.json",
        "evidence": output_dir / "seed-policy-fanout-evidence-v1.json",
        "note": repository_root / "plans/million-episode-grounding-evaluation.md",
    }
    existing = [path for path in targets.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"refusing to overwrite evidence targets: {existing}")
    plan, revision = _plan(repository_root)
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pixelgym-seed-policy-") as temporary:
        temporary_root = Path(temporary)
        runtime_plan = temporary_root / "seed-policy-plan.json"
        runtime_plan.write_bytes(canonical_json_bytes(plan) + b"\n")
        clean_root = temporary_root / "parallel"
        clean_root.mkdir()
        clean_output = clean_root / "runtime.json"
        clean_run_id = clean_root / "run-id"
        run_arguments = [
            "run",
            "--plan-file",
            str(runtime_plan),
            "--output-file",
            str(clean_output),
            "--worker-cap",
            str(WORKER_CAP),
            "--max-workers",
            str(WORKER_CAP),
            "--run-id-file",
            str(clean_run_id),
        ]
        clean, clean_command_wall_ms = _invoke(
            repository_root, clean_root, run_arguments, fail=False
        )
        if clean.returncode != 0:
            raise RuntimeError(f"parallel evidence run failed:\n{clean.stdout[-4000:]}")
        clean_raw = json.loads(clean_output.read_text())

        resume_root = temporary_root / "resume"
        resume_root.mkdir()
        resume_output = resume_root / "runtime.json"
        origin_file = resume_root / "origin-run-id"
        failed_arguments = [
            "run",
            "--plan-file",
            str(runtime_plan),
            "--output-file",
            str(resume_output),
            "--worker-cap",
            str(WORKER_CAP),
            "--max-workers",
            str(WORKER_CAP),
            "--run-id-file",
            str(origin_file),
        ]
        failed, failed_wall_ms = _invoke(repository_root, resume_root, failed_arguments, fail=True)
        if failed.returncode == 0 or "injected one-shot failure" not in failed.stdout:
            raise RuntimeError(f"injected branch failure did not occur:\n{failed.stdout[-4000:]}")
        origin_run_id = origin_file.read_text().strip()
        resumed, resumed_wall_ms = _invoke(
            repository_root,
            resume_root,
            [
                "resume",
                "--origin-run-id",
                origin_run_id,
                "--max-workers",
                str(WORKER_CAP),
                "--run-id-file",
                str(resume_root / "resume-run-id"),
            ],
            fail=True,
        )
        if resumed.returncode != 0:
            raise RuntimeError(f"resume evidence run failed:\n{resumed.stdout[-4000:]}")
        resume_raw = json.loads(resume_output.read_text())
        failure_event = json.loads(
            (resume_root / "events/matrix_branch_persisted.triggered").read_text()
        )

        derived = summarize_parallel_timing(clean_raw)
        clean_ledger = provider_ledger_snapshot(clean_root / "provider.db")
        resume_ledger = provider_ledger_snapshot(resume_root / "provider.db")
        assignments = clean_raw["aggregate"]["assignments"]
        resume_assignments = resume_raw["aggregate"]["assignments"]
        if clean_raw["aggregate"] != resume_raw["aggregate"]:
            raise RuntimeError("resumed aggregate differs from uninterrupted aggregate")
        if not derived["overlap_demonstrated"]:
            raise RuntimeError("stored branch timestamps do not demonstrate overlap")
        runtime_context = clean_raw["runtime_context"]
        if runtime_context["revision"] != revision:
            raise RuntimeError("flow execution revision differs from generator provenance")
        if runtime_context["worker_cap"] != WORKER_CAP:
            raise RuntimeError("flow worker cap differs from the requested local runtime cap")
        if derived["maximum_observed_parallel_branches"] > WORKER_CAP:
            raise RuntimeError("observed branch parallelism exceeded the worker cap")
        if (
            runtime_context["maximum_observed_parallel_branches"]
            != derived["maximum_observed_parallel_branches"]
        ):
            raise RuntimeError("flow and generator disagree on observed branch parallelism")
        expected_calls = sum(item["expected_count"] for item in assignments)
        if resume_ledger["attempts"] != expected_calls:
            raise RuntimeError("resume repeated provider work")
        if clean_ledger["max_active"] != 4 or resume_ledger["max_active"] != 4:
            raise RuntimeError("provider ledger did not observe the configured concurrency of four")

        evidence = {
            "schema_version": "pixelgym-seed-policy-fanout-evidence-v1",
            "revision": revision,
            "plan_digest": seed_policy_plan_digest(plan),
            "max_workers": WORKER_CAP,
            "worker_cap": WORKER_CAP,
            "branch_count": len(assignments),
            "record_count": expected_calls,
            "invalid_assignment_count": sum(item["outcome"] == "invalid" for item in assignments),
            "request_failure_assignment_count": sum(
                item["outcome"] == "request_failure" for item in assignments
            ),
            "parallel_run": {
                "aggregate_size_bytes": len(canonical_json_bytes(clean_raw["aggregate"])) + 1,
                "command_wall_ms": clean_command_wall_ms,
                "derived": derived,
                "provider_ledger": clean_ledger,
                "raw": clean_raw,
            },
            "resume_run": {
                "aggregate_sha256": resume_raw["aggregate_sha256"],
                "assignment_count": len(resume_assignments),
                "failed_command_exit_code": failed.returncode,
                "failed_command_wall_ms": failed_wall_ms,
                "failure_event": failure_event,
                "provider_ledger": resume_ledger,
                "resume_command_exit_code": resumed.returncode,
                "resume_command_wall_ms": resumed_wall_ms,
                "retry_events": resume_raw["retry_events"],
                "resume_events": resume_raw["resume_events"],
                "total_observed_command_wall_ms": round(failed_wall_ms + resumed_wall_ms, 3),
            },
            "limitations": [
                "scripted no-cost provider; metrics are not model-quality evidence",
                "single local host; no production-throughput extrapolation",
                "UTC wall timestamps demonstrate overlap; monotonic clocks measure branch runtime",
            ],
        }
        targets["plan"].write_bytes(canonical_json_bytes(plan) + b"\n")
        targets["evidence"].write_bytes(canonical_json_bytes(evidence) + b"\n")
        _write_note(targets["note"], evidence)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace this generator's three versioned targets in place",
    )
    args = parser.parse_args()
    repository_root = Path(__file__).parents[1].resolve()
    generate(repository_root, args.output_dir.resolve(), overwrite=args.overwrite)


if __name__ == "__main__":
    main()
