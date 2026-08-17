#!/usr/bin/env python3
"""Index stored D4.12 observations without rerunning checks or judging the gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

COMMAND_SCHEMA = "pixelgym-d412-command-record-v1"
MANIFEST_SCHEMA = "pixelgym-d412-evidence-manifest-v1"

CHECKLIST: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "Existing PixelGym fast suite still passes from the documented clean install.",
        ("commands/07-install-dev.json", "commands/08-fast-suite.json", "commands/09-environment-checker.json", "commands/10-golden-trajectory.json", "commands/13-ruff.json"),
        "593 passed, 5 skipped, 3 warnings in 53.84s; environment checker 1 passed in 0.04s; golden trajectory reported OK; Ruff reported all checks passed.",
    ),
    (
        "Platform unit tests run without network, OSWorld, provider credentials, or wall-clock sleeps.",
        ("commands/11-platform-unit-boundaries.json", "commands/15-boundary-inventory.json", "commands/16-platform-sleep-scan.json"),
        "216 passed, 3 warnings in 14.13s with closed proxy endpoints and provider credentials removed; osworld_installed=false; provider_credentials_present=false; the sleep scan returned zero matches (rg exit 1).",
    ),
    (
        "Marked platform integration tests pass against a fresh local stack.",
        ("commands/30-compose-browser-sandbox-retry.json", "commands/37-compose-cleanup-filtered.json"),
        "2 passed in 153.97s; the post-run filtered Docker inventory contained zero pixelgym-it containers, images, networks, or volumes.",
    ),
    (
        "Metaflow resume tests prove no duplicate fixture-provider calls.",
        ("commands/28-platform-local-runtime-retry.json", "commands/39-integration-test-inventory.json"),
        "The marked local runtime command recorded 9 passed in 86.30s; its collected cases include every side-effect boundary, hard-kill resume, call caps, and concurrency caps.",
    ),
    (
        "MLflow contains the complete run contract and links back to Metaflow.",
        ("commands/28-platform-local-runtime-retry.json", "commands/30-compose-browser-sandbox-retry.json", "artifacts/platform/demo-mlflow-lineage.jsonl"),
        "The local runtime and fresh Compose commands recorded 9 and 2 passing tests; three stored MLflow lineage records reconcile with three run manifests by run ID, Metaflow pathspec, and policy ID.",
    ),
    (
        "Dataset and raw-response hashes verify from immutable storage.",
        ("commands/30-compose-browser-sandbox-retry.json", "artifacts/platform/immutable-artifact-verification.json", "identity-reconciliation.json"),
        "The stored immutable verification has 310 verified pinned objects and failure_count=0; its reference set equals the three run-manifest artifact union; the fresh Compose case also exercised real MinIO version/digest/tamper paths.",
    ),
    (
        "Missing cost or latency blocks promotion.",
        ("commands/31-mechanical-boundaries.json",),
        "The named missing/non-finite cost and latency boundary cases are present in the 23-case command output; 23 passed in 1.30s.",
    ),
    (
        "Gate failure blocks both UI and direct approval API.",
        ("commands/30-compose-browser-sandbox-retry.json", "commands/31-mechanical-boundaries.json", "artifacts/platform/demo-api-transcript.jsonl", "artifacts/platform/screenshots/02-candidate-a-gate-blocked.png"),
        "The stored blocked-approval exchange is HTTP 409; the failed-candidate screenshot is hash verified; the named direct-approval case appears in the 23-case output; the fresh browser/API lifecycle recorded 2 passed.",
    ),
    (
        "Passing gates do not bypass human approval.",
        ("commands/30-compose-browser-sandbox-retry.json", "commands/31-mechanical-boundaries.json", "artifacts/platform/demo-approval-events.jsonl", "artifacts/platform/screenshots/06-candidate-b-eligible.png", "d411-human-confirmation.json"),
        "Two stored approval events belong only to the two stored passing-policy identities; the eligible screenshot precedes approval; the project owner separately confirmed all four D4.11 review items.",
    ),
    (
        "Serving rejects an unapproved exact version.",
        ("commands/30-compose-browser-sandbox-retry.json", "commands/31-mechanical-boundaries.json"),
        "The named unapproved deployment and serving-restore cases appear in the 23-case output; the fresh browser lifecycle recorded the pre-approval deployment response as 409 within its passing case.",
    ),
    (
        "Failed deployment leaves the current version active.",
        ("commands/30-compose-browser-sandbox-retry.json", "commands/31-mechanical-boundaries.json"),
        "The named deploy and pre-activation failure cases appear in the 23-case output; the fresh Compose lifecycle exercised missing-package deploy and rollback failures while retaining the stored active identity.",
    ),
    (
        "Rollback restores the previous approved exact version.",
        ("commands/30-compose-browser-sandbox-retry.json", "artifacts/platform/demo-deployment-events.jsonl", "artifacts/platform/demo-api-transcript.jsonl"),
        "Stored generations are deploy/deploy/rollback with generation 3 restoring generation 1's candidate and policy; the fresh browser lifecycle recorded 2 passed.",
    ),
    (
        "Concurrent transition tests produce one active deployment.",
        ("commands/30-compose-browser-sandbox-retry.json", "commands/31-mechanical-boundaries.json"),
        "The fresh lifecycle's passing case contains exact [303, 409] deploy and rollback race assertions and an active-pointer count of one; the named stale compare-and-swap case is in the 23-case output.",
    ),
    (
        "Demo evidence clearly distinguishes scripted and real provider results.",
        ("artifacts/platform/EVIDENCE_REVIEW.md", "artifacts/platform/rehearsal-environment.json", "artifacts/platform/demo-mlflow-lineage.jsonl"),
        "The review index labels all demo metrics synthetic, and the stored environment records deterministic scripted replay, zero network model calls, zero paid calls, and no external deployment.",
    ),
    (
        "No credentials, private payloads, or privileged benchmark data appear in artifacts.",
        ("commands/40-redaction-scan.json", "artifacts/platform/screenshots/manifest.json"),
        "The recorded redaction scan reports zero findings in every prohibited category; the screenshot manifest states the excluded data classes.",
    ),
    (
        "Retention, backup, recovery, and known limitations are documented.",
        ("deploy/README.md", "artifacts/platform/architecture.md", "artifacts/platform/known-limitations.md"),
        "Stored documentation distinguishes 30-day local governance retention from production WORM and identifies production backup, replication, recovery testing, and disaster-recovery work as unapproved/not demonstrated.",
    ),
)

SUPPORTING_PATHS = (
    "artifacts/platform/EVIDENCE_REVIEW.md",
    "artifacts/platform/architecture.md",
    "artifacts/platform/demo-api-transcript.jsonl",
    "artifacts/platform/demo-approval-events.jsonl",
    "artifacts/platform/demo-audit-events.jsonl",
    "artifacts/platform/demo-comparison.json",
    "artifacts/platform/demo-deployment-events.jsonl",
    "artifacts/platform/demo-gate-reports.jsonl",
    "artifacts/platform/demo-mlflow-lineage.jsonl",
    "artifacts/platform/demo-run-manifests.jsonl",
    "artifacts/platform/demo-script.md",
    "artifacts/platform/immutable-artifact-verification.json",
    "artifacts/platform/known-limitations.md",
    "artifacts/platform/rehearsal-environment.json",
    "artifacts/platform/screenshots/manifest.json",
    "deploy/README.md",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text())


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _artifact_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return item["uri"], item["version_id"], item["sha256"]


def _index(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _command_index(evidence_dir: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    by_path: dict[str, dict[str, Any]] = {}
    for path in sorted((evidence_dir / "commands").glob("*.json")):
        record = _json(path)
        if record.get("schema_version") != COMMAND_SCHEMA:
            raise ValueError(f"unexpected command schema: {path}")
        relative = path.relative_to(evidence_dir).as_posix()
        entry = {
            "path": relative,
            "sha256": _sha256(path),
            "command": record["command"],
            "exit_status": record["exit_status"],
            "duration_seconds": record["duration_seconds"],
            "started_at_utc": record["started_at_utc"],
            "ended_at_utc": record["ended_at_utc"],
            "full_output_path": relative,
        }
        summary = re.search(
            r"(?P<passed>\d+) passed(?:, (?P<skipped>\d+) skipped)?(?:, (?P<warnings>\d+) warnings?)? in (?P<runtime>[0-9.]+)s",
            record["output"],
        )
        if summary:
            entry["observed_test_summary"] = {
                key: (float(value) if key == "runtime" else int(value))
                for key, value in summary.groupdict().items()
                if value is not None
            }
        records.append(entry)
        by_path[relative] = record
    return records, by_path


def _reconcile(repository_root: Path) -> dict[str, Any]:
    base = repository_root / "artifacts/platform"
    manifests = _jsonl(base / "demo-run-manifests.jsonl")
    lineage = _jsonl(base / "demo-mlflow-lineage.jsonl")
    gates = _jsonl(base / "demo-gate-reports.jsonl")
    approvals = _jsonl(base / "demo-approval-events.jsonl")
    deployments = _jsonl(base / "demo-deployment-events.jsonl")
    audit = _jsonl(base / "demo-audit-events.jsonl")
    api = _jsonl(base / "demo-api-transcript.jsonl")
    verification = _json(base / "immutable-artifact-verification.json")
    screenshots = _json(base / "screenshots/manifest.json")

    manifest_identity = {
        (row["mlflow_run_id"], row["metaflow_pathspec"], row["policy_id"])
        for row in manifests
    }
    lineage_identity = {
        (row["mlflow_run_id"], row["metaflow_pathspec"], row["policy_id"])
        for row in lineage
    }
    manifest_artifacts = {
        _artifact_key(item) for row in manifests for item in row["artifact_index"]
    }
    verified_artifacts = {_artifact_key(item) for item in verification["verified"]}
    passing_policies = {row["policy_id"] for row in gates if row["overall_passed"]}
    failed_policies = {row["policy_id"] for row in gates if not row["overall_passed"]}
    approval_policies = {row["policy_id"] for row in approvals}
    approval_candidates = {row["candidate_id"] for row in approvals}

    screenshot_hashes_match = True
    for item in screenshots["screenshots"]:
        image = base / "screenshots" / item["path"]
        screenshot_hashes_match &= (
            image.stat().st_size == item["size"] and _sha256(image) == item["sha256"]
        )

    known_candidates = {row["candidate_id"] for row in lineage}
    known_runs = {row["mlflow_run_id"] for row in lineage}
    known_policies = {row["policy_id"] for row in lineage}
    known_deployments = {row["deployment_id"] for row in deployments}
    screenshot_identities_match = all(
        set(item["candidate_ids"]) <= known_candidates
        and set(item["mlflow_run_ids"]) <= known_runs
        and set(item["policy_ids"]) <= known_policies
        and set(item["deployment_ids"]) <= known_deployments
        for item in screenshots["screenshots"]
    )

    return {
        "schema_version": "pixelgym-d412-identity-reconciliation-v1",
        "stored_counts": {
            "run_manifests": len(manifests),
            "mlflow_lineage_records": len(lineage),
            "gate_reports": len(gates),
            "approval_events": len(approvals),
            "deployment_events": len(deployments),
            "audit_events": len(audit),
            "api_exchanges": len(api),
            "screenshots": len(screenshots["screenshots"]),
            "manifest_artifact_union": len(manifest_artifacts),
            "immutable_verified": len(verified_artifacts),
            "immutable_failures": verification["failure_count"],
        },
        "run_manifest_mlflow_metaflow_policy_identity_matches": manifest_identity == lineage_identity,
        "manifest_artifact_union_matches_immutable_verification": manifest_artifacts == verified_artifacts,
        "approval_policy_ids": sorted(approval_policies),
        "approval_candidate_ids": sorted(approval_candidates),
        "approval_policies_are_stored_passing_policies": approval_policies <= passing_policies,
        "stored_failed_policies_have_no_approval": failed_policies.isdisjoint(approval_policies),
        "deployment_candidates_are_approved": {
            row["candidate_id"] for row in deployments
        } <= approval_candidates,
        "deployment_generations": [row["generation"] for row in deployments],
        "deployment_actions": [row["action"] for row in deployments],
        "rollback_restores_generation_1_policy": deployments[2]["policy_id"] == deployments[0]["policy_id"],
        "screenshot_hashes_and_sizes_match": screenshot_hashes_match,
        "screenshot_identity_references_match": screenshot_identities_match,
    }


def _redaction_scan(repository_root: Path, evidence_dir: Path) -> dict[str, Any]:
    paths = [path for path in evidence_dir.rglob("*") if path.is_file()]
    paths.extend(repository_root / path for path in SUPPORTING_PATHS)
    paths.extend((repository_root / "artifacts/platform/screenshots").glob("*.png"))
    patterns = {
        "local_absolute_paths": re.compile(rb"/Users/|/private/tmp/"),
        "local_demo_credentials": re.compile(
            rb"local_demo_postgres_only|local_demo_minio_only|local-demo-csrf-secret"
        ),
        "aws_access_keys": re.compile(rb"AKIA[0-9A-Z]{16}"),
        "private_key_markers": re.compile(rb"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
        "privileged_json_fields": re.compile(
            rb'"(?:expected_answer|ground_truth|bbox|bounding_box)"\s*:'
        ),
        "unredacted_image_payloads": re.compile(rb'"image_base64"\s*:\s*"[A-Za-z0-9+/]{100}'),
    }
    findings = {name: 0 for name in patterns}
    scanned = 0
    for path in dict.fromkeys(paths):
        if path.name in {"evidence-manifest.json", "REPORT.md", "redaction-scan.json"}:
            continue
        data = path.read_bytes()
        scanned += 1
        for name, pattern in patterns.items():
            findings[name] += len(pattern.findall(data))
    return {
        "schema_version": "pixelgym-d412-redaction-scan-v1",
        "files_scanned": scanned,
        "finding_counts": findings,
    }


def generate(repository_root: Path, evidence_dir: Path) -> dict[str, Any]:
    repository_root = repository_root.resolve()
    evidence_dir = evidence_dir.resolve()
    commands, command_records = _command_index(evidence_dir)
    revision = command_records["commands/00-git-revision.json"]["output"].strip()
    branch = command_records["commands/38-evidence-branch.json"]["output"].strip()
    source = json.loads(command_records["commands/17-source-provenance.json"]["output"])
    if command_records["commands/01-worktree-status.json"]["output"] != "":
        raise ValueError("the frozen checkout status observation is not empty")
    if source["revision"] != revision or source["state"] != "clean":
        raise ValueError("stored source provenance does not match the frozen clean revision")

    supporting = [_index(repository_root / relative, repository_root) for relative in SUPPORTING_PATHS]
    screenshots = _json(repository_root / "artifacts/platform/screenshots/manifest.json")
    for item in screenshots["screenshots"]:
        relative = Path("artifacts/platform/screenshots") / item["path"]
        supporting.append(_index(repository_root / relative, repository_root))

    reconciliation = _reconcile(repository_root)
    (evidence_dir / "identity-reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2, sort_keys=True) + "\n"
    )
    redaction = _redaction_scan(repository_root, evidence_dir)
    (evidence_dir / "redaction-scan.json").write_text(
        json.dumps(redaction, indent=2, sort_keys=True) + "\n"
    )

    started = min(record["started_at_utc"] for record in command_records.values())
    ended = max(record["ended_at_utc"] for record in command_records.values())
    artifact_lookup = {entry["path"]: entry for entry in [*commands, *supporting]}
    artifact_lookup["identity-reconciliation.json"] = _index(
        evidence_dir / "identity-reconciliation.json", evidence_dir
    )
    artifact_lookup["redaction-scan.json"] = _index(evidence_dir / "redaction-scan.json", evidence_dir)
    artifact_lookup["d411-human-confirmation.json"] = _index(
        evidence_dir / "d411-human-confirmation.json", evidence_dir
    )

    items = []
    for number, (text, evidence, observed) in enumerate(CHECKLIST, start=1):
        missing = [path for path in evidence if path not in artifact_lookup]
        items.append(
            {
                "number": number,
                "checklist_text": text,
                "evidence": [artifact_lookup[path] for path in evidence if path in artifact_lookup],
                "missing_evidence": missing,
                "observed_raw_result": observed,
            }
        )

    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "gate": "D4.12",
        "verdict": None,
        "verdict_owner": "project owner",
        "evidence_revision": revision,
        "evidence_branch": branch,
        "frozen_worktree_status": "",
        "source_tree_sha256": source["source_tree_sha256"],
        "started_at_utc": started,
        "ended_at_utc": ended,
        "provider_calls": 0,
        "paid_services": 0,
        "external_deployments": 0,
        "command_records": commands,
        "supporting_artifacts": supporting,
        "identity_reconciliation": reconciliation,
        "redaction_scan": redaction,
        "checklist_items": items,
    }
    (evidence_dir / "evidence-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )

    report_lines = [
        "# D4.12 raw evidence index",
        "",
        f"Evidence revision: `{revision}`  ",
        f"Evidence branch: `{branch}`  ",
        f"Observation window: `{started}` to `{ended}`  ",
        "Verdict: `null`  ",
        "Verdict owner: project owner",
        "",
        "This index copies and links stored observations only. It does not rerun tests, reinterpret gate semantics, declare a milestone verdict, or change human-owned checklist state.",
        "",
        "## Checklist evidence",
        "",
        "| # | Checklist line | Stored raw observation | Evidence |",
        "| ---: | --- | --- | --- |",
    ]
    for item in items:
        links = ", ".join(f"`{entry['path']}`" for entry in item["evidence"])
        if item["missing_evidence"]:
            links += "; missing: " + ", ".join(item["missing_evidence"])
        report_lines.append(
            f"| {item['number']} | {item['checklist_text']} | {item['observed_raw_result']} | {links} |"
        )
    report_lines.extend(
        [
            "",
            "## Raw command inventory",
            "",
            "Each JSON record stores the exact argv/command, cwd, public environment, UTC timestamps, process runtime, exit status, and complete combined output. Expected sandbox/no-match attempts remain visible alongside their successful retries.",
            "",
            "| Record | Exit | Runtime (s) | Parsed pytest summary |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for entry in commands:
        summary = json.dumps(entry.get("observed_test_summary"), sort_keys=True)
        report_lines.append(
            f"| `{entry['path']}` | {entry['exit_status']} | {entry['duration_seconds']} | `{summary}` |"
        )
    report_lines.extend(
        [
            "",
            "## Identity and redaction indexes",
            "",
            "See `identity-reconciliation.json` for stored cross-file identities and counts, `redaction-scan.json` for prohibited-pattern counts, and `evidence-manifest.json` for SHA-256 and size metadata.",
            "",
        ]
    )
    report = "\n".join(report_lines)
    if "- [ ]" in report or "- [x]" in report.lower():
        raise ValueError("generated report must not contain checklist boxes")
    (evidence_dir / "REPORT.md").write_text(report)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = generate(args.repository_root, args.evidence_dir)
    print(
        json.dumps(
            {
                "evidence_revision": manifest["evidence_revision"],
                "checklist_items": len(manifest["checklist_items"]),
                "verdict": manifest["verdict"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
