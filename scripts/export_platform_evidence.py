"""Export reviewer-safe lifecycle evidence from stored control-plane records only."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.schema_validation import ContractValidationError, PlatformSchemas


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _jsonl(path: Path, values: list[object]) -> None:
    path.write_text(
        "".join(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n" for value in values)
    )


def export_evidence(control: ControlStore, output: Path) -> None:
    schemas = PlatformSchemas(Path(__file__).parents[1])
    submissions = control.list_submissions()
    candidates = control.list_candidates()
    gate_reports = [candidate.gate_report for candidate in candidates]
    for report in gate_reports:
        schemas.validate("gate_report", report)
    for candidate in candidates:
        schemas.validate("policy_package", candidate.policy.to_dict())

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
    approvals = control.approval_events()
    deployments = control.deployment_history()
    try:
        audit_events = control.audit_events()
    except (json.JSONDecodeError, TypeError) as exc:
        raise ContractValidationError(
            "audit_event details_json is not strict JSON"
        ) from exc
    for manifest in manifests:
        schemas.validate("run_manifest", manifest)
    for approval in approvals:
        schemas.validate("approval", approval)
    for deployment in deployments:
        schemas.validate("deployment", deployment)
    for event in audit_events:
        schemas.validate("audit_event", event)

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
    output.mkdir(parents=True, exist_ok=True)
    _jsonl(output / "demo-run-manifests.jsonl", manifests)
    _json(output / "demo-comparison.json", comparison)
    _jsonl(output / "demo-gate-reports.jsonl", gate_reports)
    _jsonl(output / "demo-approval-events.jsonl", approvals)
    _jsonl(output / "demo-deployment-events.jsonl", deployments)
    _jsonl(output / "demo-audit-events.jsonl", audit_events)
    _jsonl(
        output / "demo-mlflow-lineage.jsonl",
        [
            {
                "schema_version": "pixelgym-platform-mlflow-lineage-v1",
                "candidate_id": item.candidate_id,
                "mlflow_run_id": item.source_run_id,
                "metaflow_pathspec": next(
                    (
                        row["metaflow_pathspec"]
                        for row in submissions
                        if row["mlflow_run_id"] == item.source_run_id
                    ),
                    None,
                ),
                "policy_id": item.policy.policy_id,
                "prompt": {
                    "name": item.policy.prompt_name,
                    "version": item.policy.prompt_version,
                    "sha256": item.policy.prompt_sha256,
                },
                "provider": item.policy.provider,
                "model": item.policy.model,
                "synthetic": True,
                "immutable_artifacts": [reference.to_dict() for reference in item.artifacts],
            }
            for item in candidates
        ],
    )
    print(f"exported stored platform evidence to {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", default="artifacts/platform")
    args = parser.parse_args()
    control = ControlStore(
        args.database,
        reviewer_identity=os.environ.get("PIXELGYM_REVIEWER_ID", "local-reviewer"),
    )
    export_evidence(control, Path(args.output))


if __name__ == "__main__":
    main()
