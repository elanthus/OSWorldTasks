"""Export reviewer-safe lifecycle evidence from stored control-plane records only."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pixelgym.platform.control_store import ControlStore


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _jsonl(path: Path, values: list[object]) -> None:
    path.write_text("".join(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n" for value in values))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", default="artifacts/platform")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    control = ControlStore(
        args.database,
        reviewer_identity=os.environ.get("PIXELGYM_REVIEWER_ID", "local-reviewer"),
    )
    submissions = control.list_submissions()
    candidates = control.list_candidates()
    candidates_by_run = {candidate.source_run_id: candidate for candidate in candidates}
    manifests = [
        {
            "schema_version": "pixelgym-platform-run-manifest-v1",
            "submission_id": row["submission_id"],
            "mlflow_run_id": row["mlflow_run_id"],
            "metaflow_pathspec": row["metaflow_pathspec"],
            "dataset_fingerprint": (
                candidates_by_run[row["mlflow_run_id"]].gate_report["dataset_fingerprint"]
                if row["mlflow_run_id"] in candidates_by_run
                else None
            ),
            "policy_id": (
                candidates_by_run[row["mlflow_run_id"]].policy.policy_id
                if row["mlflow_run_id"] in candidates_by_run
                else None
            ),
            "status": row["status"],
            "request": row["request"],
            "artifact_index": (
                [
                    reference.to_dict()
                    for reference in candidates_by_run[row["mlflow_run_id"]].artifacts
                ]
                if row["mlflow_run_id"] in candidates_by_run
                else []
            ),
        }
        for row in submissions
    ]
    gate_reports = [candidate.gate_report for candidate in candidates]
    comparison = {
        "schema_version": "pixelgym-platform-comparison-export-v1",
        "note": "Stored observed values only; gate semantics were not recomputed.",
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "policy_id": item.policy.policy_id,
                "state": item.state.value,
                "dataset_fingerprint": item.gate_report["dataset_fingerprint"],
                "accuracy": item.gate_report["accuracy"],
                "cost_usd_per_100": item.gate_report["cost_usd_per_100"],
                "provider_latency_p95_ms": item.gate_report["provider_latency_p95_ms"],
            }
            for item in candidates
        ],
    }
    _jsonl(output / "demo-run-manifests.jsonl", manifests)
    _json(output / "demo-comparison.json", comparison)
    _jsonl(output / "demo-gate-reports.jsonl", gate_reports)
    _jsonl(output / "demo-approval-events.jsonl", control.approval_events())
    _jsonl(output / "demo-deployment-events.jsonl", control.deployment_history())
    _jsonl(output / "demo-audit-events.jsonl", control.audit_events())
    print(f"exported stored platform evidence to {output}")


if __name__ == "__main__":
    main()
