#!/usr/bin/env python3
"""Index stored platform-gate observations without rerunning or judging them."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

COMMAND_SCHEMA = "pixelgym-d412-command-record-v1"
MANIFEST_SCHEMA = "pixelgym-d412-evidence-manifest-v1"
LEGACY_EVIDENCE_REVISION = "f92e307af7a3830347d50ca63f6a7d481489935c"
CREDENTIALS_ABSENT_REVISIONS = {
    LEGACY_EVIDENCE_REVISION,
    "0d161893f9e0cd500bd57cecde2ce5d80e991730",
    "421570dfbe78fca0d65f97968211c4e2d3f299d7",
}
PRE_FINAL_REDACTION_REVISIONS = {
    LEGACY_EVIDENCE_REVISION,
    "0d161893f9e0cd500bd57cecde2ce5d80e991730",
    "421570dfbe78fca0d65f97968211c4e2d3f299d7",
}
PYTEST_SUMMARY = re.compile(
    r"(?P<passed>\d+) passed(?:, (?P<skipped>\d+) skipped)?"
    r"(?:, (?P<warnings>\d+) warnings?)? in (?P<runtime>[0-9.]+)s"
)

EXPECTED_COMMAND_STATUSES = {
    **{
        f"commands/{number:02d}-{name}.json": 0
        for number, name in (
            (0, "git-revision"),
            (1, "worktree-status"),
            (2, "python-version"),
            (3, "os-architecture"),
            (4, "docker-version"),
            (5, "compose-version"),
            (6, "create-dev-venv"),
            (7, "install-dev"),
            (8, "fast-suite"),
            (9, "environment-checker"),
            (10, "golden-trajectory"),
            (11, "platform-unit-boundaries"),
            (12, "dependency-lock-check"),
            (13, "ruff"),
            (14, "pip-check"),
            (15, "boundary-inventory"),
            (17, "source-provenance"),
            (18, "lock-sha256"),
            (19, "dev-package-versions"),
            (20, "create-integration-venv"),
            (21, "install-platform-lock"),
            (22, "install-repository-no-deps"),
            (23, "playwright-chromium"),
            (24, "integration-package-versions"),
            (25, "integration-pip-check"),
            (26, "integration-test-inventory"),
            (27, "platform-local-runtime"),
            (30, "compose-browser-sandbox-retry"),
            (31, "mechanical-boundaries"),
            (37, "compose-cleanup-filtered"),
            (38, "evidence-branch"),
            (40, "redaction-scan"),
            (41, "metaflow-resume-ledgers"),
        )
    },
    "commands/16-platform-sleep-scan.json": 1,
    "commands/29-compose-browser.json": 1,
}

CHECKLIST: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "Existing PixelGym fast suite still passes from the documented clean install.",
        (
            "commands/07-install-dev.json",
            "commands/08-fast-suite.json",
            "commands/09-environment-checker.json",
            "commands/10-golden-trajectory.json",
            "commands/13-ruff.json",
        ),
        "fast_suite",
    ),
    (
        "Platform unit tests run without network, OSWorld, provider credentials, or wall-clock sleeps.",
        (
            "commands/11-platform-unit-boundaries.json",
            "commands/15-boundary-inventory.json",
            "commands/16-platform-sleep-scan.json",
        ),
        "unit_boundaries",
    ),
    (
        "Marked platform integration tests pass against a fresh local stack.",
        (
            "commands/30-compose-browser-sandbox-retry.json",
            "commands/37-compose-cleanup-filtered.json",
        ),
        "fresh_stack",
    ),
    (
        "Metaflow resume tests prove no duplicate fixture-provider calls.",
        (
            "commands/27-platform-local-runtime.json",
            "commands/26-integration-test-inventory.json",
            "commands/41-metaflow-resume-ledgers.json",
        ),
        "metaflow_resume",
    ),
    (
        "MLflow contains the complete run contract and links back to Metaflow.",
        (
            "commands/27-platform-local-runtime.json",
            "commands/30-compose-browser-sandbox-retry.json",
            "artifacts/platform/demo-mlflow-lineage.jsonl",
        ),
        "mlflow_lineage",
    ),
    (
        "Dataset and raw-response hashes verify from immutable storage.",
        (
            "commands/30-compose-browser-sandbox-retry.json",
            "artifacts/platform/immutable-artifact-verification.json",
            "identity-reconciliation.json",
        ),
        "immutable_storage",
    ),
    (
        "Missing cost or latency blocks promotion.",
        ("commands/31-mechanical-boundaries.json",),
        "mechanical_boundaries",
    ),
    (
        "Gate failure blocks both UI and direct approval API.",
        (
            "commands/30-compose-browser-sandbox-retry.json",
            "commands/31-mechanical-boundaries.json",
            "artifacts/platform/demo-api-transcript.jsonl",
            "artifacts/platform/screenshots/02-candidate-a-gate-blocked.png",
        ),
        "gate_blocks_approval",
    ),
    (
        "Passing gates do not bypass human approval.",
        (
            "commands/30-compose-browser-sandbox-retry.json",
            "commands/31-mechanical-boundaries.json",
            "artifacts/platform/demo-approval-events.jsonl",
            "artifacts/platform/screenshots/06-candidate-b-eligible.png",
            "d411-human-confirmation.json",
        ),
        "human_approval",
    ),
    (
        "Serving rejects an unapproved exact version.",
        (
            "commands/30-compose-browser-sandbox-retry.json",
            "commands/31-mechanical-boundaries.json",
        ),
        "unapproved_rejected",
    ),
    (
        "Failed deployment leaves the current version active.",
        (
            "commands/30-compose-browser-sandbox-retry.json",
            "commands/31-mechanical-boundaries.json",
        ),
        "failed_deployment",
    ),
    (
        "Rollback restores the previous approved exact version.",
        (
            "commands/30-compose-browser-sandbox-retry.json",
            "artifacts/platform/demo-deployment-events.jsonl",
            "artifacts/platform/demo-api-transcript.jsonl",
        ),
        "rollback",
    ),
    (
        "Concurrent transition tests produce one active deployment.",
        (
            "commands/30-compose-browser-sandbox-retry.json",
            "commands/31-mechanical-boundaries.json",
        ),
        "concurrent_transitions",
    ),
    (
        "Demo evidence clearly distinguishes scripted and real provider results.",
        (
            "artifacts/platform/EVIDENCE_REVIEW.md",
            "artifacts/platform/rehearsal-environment.json",
            "artifacts/platform/demo-mlflow-lineage.jsonl",
        ),
        "scripted_provider",
    ),
    (
        "No credentials, private payloads, or privileged benchmark data appear in artifacts.",
        (
            "commands/40-redaction-scan.json",
            "redaction-scan.json",
            "artifacts/platform/screenshots/manifest.json",
        ),
        "redaction",
    ),
    (
        "Retention, backup, recovery, and known limitations are documented.",
        (
            "deploy/README.md",
            "artifacts/platform/architecture.md",
            "artifacts/platform/known-limitations.md",
        ),
        "retention",
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

DOCUMENTATION_PATHS = (
    "README.md",
    "deploy/README.md",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_file_bytes(repository_root: Path, revision: str, relative: str) -> bytes:
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError(f"recorded evidence revision is not a full lowercase SHA: {revision}")
    try:
        revision_check = subprocess.run(
            ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
            cwd=repository_root,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise ValueError("could not inspect the repository with git") from error
    if revision_check.returncode != 0:
        raise ValueError(
            f"recorded evidence revision is not present in the repository: {revision}"
        )
    result = subprocess.run(
        ["git", "show", f"{revision}:{relative}"],
        cwd=repository_root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(
            f"path is not present at recorded evidence revision: {revision}:{relative}"
        )
    return result.stdout


def _json(path: Path) -> Any:
    return json.loads(path.read_text())


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _artifact_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return item["uri"], item["version_id"], item["sha256"]


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _index(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _index_git_file(repository_root: Path, revision: str, relative: str) -> dict[str, Any]:
    data = _git_file_bytes(repository_root, revision, relative)
    return {
        "path": relative,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
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
            "size": path.stat().st_size,
            "command": record["command"],
            "exit_status": record["exit_status"],
            "duration_seconds": record["duration_seconds"],
            "started_at_utc": record["started_at_utc"],
            "ended_at_utc": record["ended_at_utc"],
            "full_output_path": relative,
        }
        summary = PYTEST_SUMMARY.search(record["output"])
        if summary:
            entry["observed_test_summary"] = {
                key: (float(value) if key == "runtime" else int(value))
                for key, value in summary.groupdict().items()
                if value is not None
            }
        records.append(entry)
        by_path[relative] = record
    return records, by_path


def _summary(
    records: dict[str, dict[str, Any]],
    path: str,
    *,
    passed: int,
    skipped: int = 0,
    warnings: int = 0,
) -> dict[str, Any]:
    match = PYTEST_SUMMARY.search(records[path]["output"])
    if match is None:
        raise ValueError(f"missing pytest summary in {path}")
    observed = {
        "passed": int(match["passed"]),
        "skipped": int(match["skipped"] or 0),
        "warnings": int(match["warnings"] or 0),
        "runtime": float(match["runtime"]),
    }
    expected = {"skipped": skipped, "warnings": warnings}
    if observed["passed"] < passed or {key: observed[key] for key in expected} != expected:
        raise ValueError(f"unexpected pytest summary in {path}: {observed}")
    return observed


def _require_text(records: dict[str, dict[str, Any]], path: str, text: str) -> None:
    if text not in records[path]["output"]:
        raise ValueError(f"missing expected output {text!r} in {path}")


def _require_argv_tokens(
    records: dict[str, dict[str, Any]], path: str, tokens: tuple[str, ...]
) -> None:
    argv = records[path]["argv"]
    missing = [token for token in tokens if token not in argv]
    if missing:
        raise ValueError(f"missing required argv tokens in {path}: {missing}")


def _validate_commands(
    records: dict[str, dict[str, Any]], *, evidence_directory_name: str
) -> dict[str, Any]:
    if set(records) != set(EXPECTED_COMMAND_STATUSES):
        missing = sorted(set(EXPECTED_COMMAND_STATUSES) - set(records))
        extra = sorted(set(records) - set(EXPECTED_COMMAND_STATUSES))
        raise ValueError(f"unexpected command record set; missing={missing}, extra={extra}")
    for path, expected in EXPECTED_COMMAND_STATUSES.items():
        if records[path]["exit_status"] != expected:
            raise ValueError(
                f"unexpected exit status in {path}: {records[path]['exit_status']} != {expected}"
            )

    revision = records["commands/00-git-revision.json"]["output"].strip()
    if revision != evidence_directory_name:
        raise ValueError(
            "recorded revision does not match evidence directory: "
            f"{revision} != {evidence_directory_name}"
        )
    pass_floors = {
        "fast": 679,
        "platform_units": 300,
        "local_runtime": 10,
        "mechanical": 30,
    }
    if revision == LEGACY_EVIDENCE_REVISION:
        # This immutable revision records the smaller counts below. Every other
        # revision must meet the expanded suite floors declared above.
        pass_floors = {
            "fast": 593,
            "platform_units": 216,
            "local_runtime": 9,
            "mechanical": 23,
        }
    summaries = {
        "fast": _summary(
            records,
            "commands/08-fast-suite.json",
            passed=pass_floors["fast"],
            skipped=5,
            warnings=3,
        ),
        "environment": _summary(records, "commands/09-environment-checker.json", passed=1),
        "platform_units": _summary(
            records,
            "commands/11-platform-unit-boundaries.json",
            passed=pass_floors["platform_units"],
            warnings=3,
        ),
        "local_runtime": _summary(
            records,
            "commands/27-platform-local-runtime.json",
            passed=pass_floors["local_runtime"],
        ),
        "compose": _summary(records, "commands/30-compose-browser-sandbox-retry.json", passed=2),
        "mechanical": _summary(
            records,
            "commands/31-mechanical-boundaries.json",
            passed=pass_floors["mechanical"],
            warnings=3,
        ),
    }
    _require_text(records, "commands/10-golden-trajectory.json", "OK")
    _require_text(records, "commands/13-ruff.json", "All checks passed!")
    _require_argv_tokens(
        records,
        "commands/11-platform-unit-boundaries.json",
        (
            "HTTP_PROXY=http://127.0.0.1:9",
            "http_proxy=http://127.0.0.1:9",
            "HTTPS_PROXY=http://127.0.0.1:9",
            "https_proxy=http://127.0.0.1:9",
            "ALL_PROXY=http://127.0.0.1:9",
            "all_proxy=http://127.0.0.1:9",
            "NO_PROXY=localhost,127.0.0.1,::1",
            "no_proxy=localhost,127.0.0.1,::1",
        ),
    )
    _require_argv_tokens(
        records,
        "commands/21-install-platform-lock.json",
        ("--require-hashes", "requirements/platform-py312.lock"),
    )
    _require_argv_tokens(
        records,
        "commands/22-install-repository-no-deps.json",
        ("--no-deps", "-e", "."),
    )
    if records["commands/40-redaction-scan.json"]["argv"][0] != ("<path-2>/dev-venv/bin/python"):
        raise ValueError("redaction scan was not run with the recorded clean dev environment")
    inventory = records["commands/15-boundary-inventory.json"]["output"].splitlines()
    expected_credentials_present = revision not in CREDENTIALS_ABSENT_REVISIONS
    if inventory != [
        "osworld_installed=false",
        "provider_credentials_present_before_sanitization="
        + str(expected_credentials_present).lower(),
        "provider_credentials_removed=true",
    ]:
        raise ValueError(f"unexpected boundary inventory: {inventory}")
    if records["commands/16-platform-sleep-scan.json"]["output"]:
        raise ValueError("platform sleep scan must have zero matches")
    dev_versions = json.loads(records["commands/19-dev-package-versions.json"]["output"])
    if set(dev_versions) != {"pixelgym", "pytest", "ruff", "gymnasium", "metaflow", "mlflow"}:
        raise ValueError(f"unexpected dev package inventory: {dev_versions}")
    cleanup = json.loads(records["commands/37-compose-cleanup-filtered.json"]["output"])
    if cleanup != {"containers": [], "images": [], "networks": [], "volumes": []}:
        raise ValueError(f"fresh Compose cleanup is incomplete: {cleanup}")
    for name in (
        "test_metaflow_resume_at_each_side_effect_boundary[run_linked]",
        "test_metaflow_resume_at_each_side_effect_boundary[provider_response_received]",
        "test_metaflow_hard_kill_after_durable_evidence_resumes_without_duplicate_calls",
    ):
        _require_text(records, "commands/26-integration-test-inventory.json", name)
    for name in (
        "test_missing_and_nonfinite_gate_evidence_fails_closed",
        "test_gate_failure_blocks_direct_approval_and_passing_gates_do_not_autoapprove",
        "test_deployment_coordinator_rejects_unapproved_candidate_before_smoke",
        "test_serving_restore_rejects_unapproved_or_gate_failed_active_policy",
        "test_deploy_failure_preserves_active_and_repeated_rollbacks_follow_event_order",
        "test_stale_compare_and_swap_loses_cleanly",
        "test_assembled_app_pre_activation_failures_preserve_active_pointer_and_runtime",
    ):
        _require_text(records, "commands/31-mechanical-boundaries.json", name)
    return summaries


def _validate_resume_ledgers(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    evidence = json.loads(records["commands/41-metaflow-resume-ledgers.json"]["output"])
    if evidence.get("schema_version") != "pixelgym-d412-metaflow-resume-ledgers-v1":
        raise ValueError("unexpected Metaflow resume-ledger schema")
    expected_boundaries = {
        "run_linked",
        "provider_response_received",
        "raw_responses_persisted",
        "evidence_persisted",
        "mlflow_finalized",
        "candidate_registered",
    }
    if set(evidence.get("boundaries", {})) != expected_boundaries:
        raise ValueError("resume-ledger evidence does not cover every supported boundary")
    snapshots = {
        "uninterrupted": evidence["uninterrupted"],
        **evidence["boundaries"],
        "hard_kill": evidence["hard_kill"],
    }
    for name, snapshot in snapshots.items():
        ledger = snapshot["provider_ledger"]
        expected_attempts = 101 if name == "provider_response_received" else 100
        expected_hits = 1 if name == "provider_response_received" else 0
        expected_ledger = {
            "attempts": expected_attempts,
            "unique_request_ids": 100,
            "cache_hits": expected_hits,
            "active": 0,
            "max_active": 2,
            "billable_calls": 100,
        }
        if ledger != expected_ledger:
            raise ValueError(f"unexpected provider ledger for {name}: {ledger}")
        expected_snapshot = {
            "normalized_evidence_equal": True,
            "prediction_count": 100,
            "unique_example_conditions": 100,
            "submission_status": "Complete",
            "candidate_count": 1,
            "mlflow_status": "FINISHED",
        }
        if any(snapshot.get(key) != value for key, value in expected_snapshot.items()):
            raise ValueError(f"unexpected normalized result for {name}: {snapshot}")
    if evidence["hard_kill"].get("killed_with_sigkill") is not True:
        raise ValueError("hard-kill evidence did not record SIGKILL")
    return evidence


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
        (row["mlflow_run_id"], row["metaflow_pathspec"], row["policy_id"]) for row in manifests
    }
    lineage_identity = {
        (row["mlflow_run_id"], row["metaflow_pathspec"], row["policy_id"]) for row in lineage
    }
    manifest_artifacts = {
        _artifact_key(item) for row in manifests for item in row["artifact_index"]
    }
    verified_artifacts = {_artifact_key(item) for item in verification["verified"]}
    candidate_ids_by_policy: dict[str, set[str]] = {}
    for row in lineage:
        candidate_ids_by_policy.setdefault(row["policy_id"], set()).add(row["candidate_id"])
    conflicting_policies = {
        policy_id: sorted(candidate_ids)
        for policy_id, candidate_ids in candidate_ids_by_policy.items()
        if len(candidate_ids) != 1
    }
    if conflicting_policies:
        raise ValueError(f"lineage maps a policy to multiple candidates: {conflicting_policies}")
    candidate_by_policy = {
        policy_id: next(iter(candidate_ids))
        for policy_id, candidate_ids in candidate_ids_by_policy.items()
    }
    passing_approval_tuples = {
        (candidate_by_policy[row["policy_id"]], row["policy_id"], _canonical_sha256(row))
        for row in gates
        if row["overall_passed"]
    }
    failed_approval_tuples = {
        (candidate_by_policy[row["policy_id"]], row["policy_id"], _canonical_sha256(row))
        for row in gates
        if not row["overall_passed"]
    }
    approval_tuples = {
        (row["candidate_id"], row["policy_id"], row["gate_report_sha256"]) for row in approvals
    }
    approval_candidate_policy = {
        (candidate_id, policy_id) for candidate_id, policy_id, _ in approval_tuples
    }
    deployment_candidate_policy = {(row["candidate_id"], row["policy_id"]) for row in deployments}

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
        "run_manifest_mlflow_metaflow_policy_identity_matches": manifest_identity
        == lineage_identity,
        "manifest_artifact_union_matches_immutable_verification": manifest_artifacts
        == verified_artifacts,
        "expected_passing_approval_tuples": sorted(passing_approval_tuples),
        "stored_approval_tuples": sorted(approval_tuples),
        "approval_candidate_policy_gate_digest_tuples_match": (
            approval_tuples == passing_approval_tuples
        ),
        "stored_failed_candidate_policy_gate_digest_tuples_have_no_approval": (
            failed_approval_tuples.isdisjoint(approval_tuples)
        ),
        "deployment_candidate_policy_tuples_are_approved": (
            deployment_candidate_policy <= approval_candidate_policy
        ),
        "deployment_generations": [row["generation"] for row in deployments],
        "deployment_actions": [row["action"] for row in deployments],
        "rollback_restores_generation_1_policy": deployments[2]["policy_id"]
        == deployments[0]["policy_id"],
        "screenshot_hashes_and_sizes_match": screenshot_hashes_match,
        "screenshot_identity_references_match": screenshot_identities_match,
    }


def _redaction_scan(
    repository_root: Path, evidence_dir: Path, revision: str
) -> dict[str, Any]:
    paths = [path for path in evidence_dir.rglob("*") if path.is_file()]
    paths.extend(repository_root / path for path in SUPPORTING_PATHS)
    paths.extend((repository_root / "artifacts/platform/screenshots").glob("*.png"))
    patterns = {
        "local_absolute_paths": re.compile(
            rb"/(?:Users|home)/|/private/(?:tmp|var)/|/var/folders/"
        ),
        "host_identity_paths_or_emails": re.compile(
            rb"/(?:Users|home)/[^/\s]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
        ),
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
    unique_paths = list(dict.fromkeys(paths))
    for path in unique_paths:
        if path.name == "redaction-scan.json":
            continue
        try:
            relative = path.relative_to(repository_root).as_posix()
        except ValueError:
            relative = ""
        data = (
            _git_file_bytes(repository_root, revision, relative)
            if relative in DOCUMENTATION_PATHS
            else path.read_bytes()
        )
        scanned += 1
        for name, pattern in patterns.items():
            findings[name] += len(pattern.findall(data))
    return {
        "schema_version": "pixelgym-d412-redaction-scan-v1",
        "files_scanned": scanned,
        "final_deliverables_scanned": [
            name
            for name in ("REPORT.md", "evidence-manifest.json")
            if evidence_dir / name in unique_paths
        ],
        "finding_counts": findings,
    }


def _observations(
    repository_root: Path,
    evidence_dir: Path,
    records: dict[str, dict[str, Any]],
    summaries: dict[str, Any],
    resume: dict[str, Any],
    reconciliation: dict[str, Any],
    generated_redaction: dict[str, Any],
) -> dict[str, str]:
    counts = reconciliation["stored_counts"]
    required_reconciliation = (
        "run_manifest_mlflow_metaflow_policy_identity_matches",
        "manifest_artifact_union_matches_immutable_verification",
        "approval_candidate_policy_gate_digest_tuples_match",
        "stored_failed_candidate_policy_gate_digest_tuples_have_no_approval",
        "deployment_candidate_policy_tuples_are_approved",
        "rollback_restores_generation_1_policy",
        "screenshot_hashes_and_sizes_match",
        "screenshot_identity_references_match",
    )
    if not all(reconciliation[key] is True for key in required_reconciliation):
        raise ValueError("stored identity reconciliation contains a mismatch")
    confirmation = _json(evidence_dir / "d411-human-confirmation.json")
    if confirmation["confirmation_count"] != 4 or confirmation["d412_verdict"] is not None:
        raise ValueError("D4.11 human confirmation is incomplete or contains a D4.12 verdict")
    environment = _json(repository_root / "artifacts/platform/rehearsal-environment.json")
    provider = environment["provider"]
    if provider != {
        "type": "deterministic scripted replay",
        "network_model_calls": False,
        "paid_calls": 0,
        "external_deployment": False,
    }:
        raise ValueError(f"unexpected stored provider environment: {provider}")
    api = _jsonl(repository_root / "artifacts/platform/demo-api-transcript.jsonl")
    blocked = [row for row in api if row["event"] == "blocked-approval"]
    if len(blocked) != 1 or blocked[0]["response"]["status"] != 409:
        raise ValueError("stored blocked-approval exchange is not exactly one HTTP 409")
    prior_redaction = json.loads(records["commands/40-redaction-scan.json"]["output"])
    if any(prior_redaction["finding_counts"].values()):
        raise ValueError("recorded pre-generation redaction scan contains findings")

    fast = summaries["fast"]
    env_check = summaries["environment"]
    units = summaries["platform_units"]
    local = summaries["local_runtime"]
    compose = summaries["compose"]
    mechanical = summaries["mechanical"]
    cleanup = json.loads(records["commands/37-compose-cleanup-filtered.json"]["output"])
    resume_ledgers = {
        "uninterrupted": resume["uninterrupted"]["provider_ledger"],
        **{
            name: snapshot["provider_ledger"]
            for name, snapshot in sorted(resume["boundaries"].items())
        },
        "hard_kill": resume["hard_kill"]["provider_ledger"],
    }

    def observed(**fields: Any) -> str:
        return json.dumps(fields, separators=(",", ":"), sort_keys=True)

    redaction_observation = {"pre_generation_scan": prior_redaction}
    revision = records["commands/00-git-revision.json"]["output"].strip()
    if revision not in PRE_FINAL_REDACTION_REVISIONS:
        redaction_observation["generated_final_deliverable_scan"] = generated_redaction

    return {
        "fast_suite": observed(
            fast_pytest=fast,
            environment_pytest=env_check,
            golden_trajectory_output_contains_OK=True,
            ruff_output_contains_all_checks_passed=True,
        ),
        "unit_boundaries": observed(
            platform_unit_pytest=units,
            boundary_inventory=records["commands/15-boundary-inventory.json"][
                "output"
            ].splitlines(),
            sleep_scan_exit_status=records["commands/16-platform-sleep-scan.json"]["exit_status"],
            sleep_scan_output=records["commands/16-platform-sleep-scan.json"]["output"],
        ),
        "fresh_stack": observed(
            compose_pytest=compose,
            post_run_docker_inventory=cleanup,
        ),
        "metaflow_resume": observed(
            local_runtime_pytest=local,
            boundary_names=sorted(resume["boundaries"]),
            provider_ledgers=resume_ledgers,
            hard_kill_signal_recorded=resume["hard_kill"]["killed_with_sigkill"],
            normalized_evidence_equal={
                name: snapshot["normalized_evidence_equal"]
                for name, snapshot in {
                    "uninterrupted": resume["uninterrupted"],
                    **resume["boundaries"],
                    "hard_kill": resume["hard_kill"],
                }.items()
            },
        ),
        "mlflow_lineage": observed(
            local_runtime_pytest=local,
            compose_pytest=compose,
            mlflow_lineage_records=counts["mlflow_lineage_records"],
            run_manifests=counts["run_manifests"],
            identity_matches=reconciliation["run_manifest_mlflow_metaflow_policy_identity_matches"],
        ),
        "immutable_storage": observed(
            immutable_verified=counts["immutable_verified"],
            immutable_failures=counts["immutable_failures"],
            manifest_artifact_union=counts["manifest_artifact_union"],
            manifest_union_matches_verification=reconciliation[
                "manifest_artifact_union_matches_immutable_verification"
            ],
        ),
        "mechanical_boundaries": observed(
            mechanical_pytest=mechanical,
        ),
        "gate_blocks_approval": observed(
            blocked_approval_statuses=[row["response"]["status"] for row in blocked],
            screenshot_hashes_and_sizes_match=reconciliation["screenshot_hashes_and_sizes_match"],
            screenshot_identity_references_match=reconciliation[
                "screenshot_identity_references_match"
            ],
            mechanical_pytest=mechanical,
        ),
        "human_approval": observed(
            approval_events=counts["approval_events"],
            approval_tuples_match=reconciliation[
                "approval_candidate_policy_gate_digest_tuples_match"
            ],
            failed_tuples_have_no_approval=reconciliation[
                "stored_failed_candidate_policy_gate_digest_tuples_have_no_approval"
            ],
            d411_confirmation_count=confirmation["confirmation_count"],
            d412_verdict=confirmation["d412_verdict"],
        ),
        "unapproved_rejected": observed(
            mechanical_pytest=mechanical,
        ),
        "failed_deployment": observed(
            mechanical_pytest=mechanical,
        ),
        "rollback": observed(
            deployment_actions=reconciliation["deployment_actions"],
            deployment_generations=reconciliation["deployment_generations"],
            rollback_restores_generation_1_policy=reconciliation[
                "rollback_restores_generation_1_policy"
            ],
        ),
        "concurrent_transitions": observed(
            compose_pytest=compose,
            mechanical_pytest=mechanical,
        ),
        "scripted_provider": observed(
            provider=provider,
        ),
        "redaction": observed(**redaction_observation),
        "retention": observed(
            indexed_documents=[
                "deploy/README.md",
                "artifacts/platform/architecture.md",
                "artifacts/platform/known-limitations.md",
            ],
        ),
    }


def generate(repository_root: Path, evidence_dir: Path) -> dict[str, Any]:
    repository_root = repository_root.resolve()
    evidence_dir = evidence_dir.resolve()
    commands, command_records = _command_index(evidence_dir)
    summaries = _validate_commands(command_records, evidence_directory_name=evidence_dir.name)
    resume = _validate_resume_ledgers(command_records)
    revision = command_records["commands/00-git-revision.json"]["output"].strip()
    branch = command_records["commands/38-evidence-branch.json"]["output"].strip()
    source = json.loads(command_records["commands/17-source-provenance.json"]["output"])
    if command_records["commands/01-worktree-status.json"]["output"] != "":
        raise ValueError("the frozen checkout status observation is not empty")
    if source["revision"] != revision or source["state"] != "clean":
        raise ValueError("stored source provenance does not match the frozen clean revision")

    supporting = [
        (
            _index_git_file(repository_root, revision, relative)
            if relative in DOCUMENTATION_PATHS
            else _index(repository_root / relative, repository_root)
        )
        for relative in SUPPORTING_PATHS
    ]
    screenshots = _json(repository_root / "artifacts/platform/screenshots/manifest.json")
    for item in screenshots["screenshots"]:
        relative = Path("artifacts/platform/screenshots") / item["path"]
        supporting.append(_index(repository_root / relative, repository_root))

    reconciliation = _reconcile(repository_root)
    (evidence_dir / "identity-reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2, sort_keys=True) + "\n"
    )
    # Keep these files present during both scans so the count and result are stable.
    (evidence_dir / "evidence-manifest.json").write_text("{}\n")
    (evidence_dir / "REPORT.md").write_text("")
    preliminary_redaction = _redaction_scan(repository_root, evidence_dir, revision)
    if any(preliminary_redaction["finding_counts"].values()):
        raise ValueError(f"redaction scan found prohibited data: {preliminary_redaction}")
    (evidence_dir / "redaction-scan.json").write_text(
        json.dumps(preliminary_redaction, indent=2, sort_keys=True) + "\n"
    )
    observations = _observations(
        repository_root,
        evidence_dir,
        command_records,
        summaries,
        resume,
        reconciliation,
        preliminary_redaction,
    )

    started = min(record["started_at_utc"] for record in command_records.values())
    ended = max(record["ended_at_utc"] for record in command_records.values())
    artifact_lookup = {entry["path"]: entry for entry in [*commands, *supporting]}
    artifact_lookup["identity-reconciliation.json"] = _index(
        evidence_dir / "identity-reconciliation.json", evidence_dir
    )
    artifact_lookup["redaction-scan.json"] = _index(
        evidence_dir / "redaction-scan.json", evidence_dir
    )
    artifact_lookup["d411-human-confirmation.json"] = _index(
        evidence_dir / "d411-human-confirmation.json", evidence_dir
    )

    items = []
    for number, (text, evidence, observation_key) in enumerate(CHECKLIST, start=1):
        missing = [path for path in evidence if path not in artifact_lookup]
        if missing:
            raise ValueError(f"checklist item {number} has missing evidence: {missing}")
        items.append(
            {
                "number": number,
                "checklist_text": text,
                "evidence": [artifact_lookup[path] for path in evidence],
                "missing_evidence": [],
                "observed_raw_result": observations[observation_key],
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
        "provider_environment": _json(
            repository_root / "artifacts/platform/rehearsal-environment.json"
        )["provider"],
        "command_records": commands,
        "supporting_artifacts": supporting,
        "identity_reconciliation": reconciliation,
        "redaction_scan": preliminary_redaction,
        "checklist_items": items,
    }
    (evidence_dir / "evidence-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )

    report_lines = [
        "# D4.12 raw evidence index",
        "",
        f"Evidence revision: `{revision}`",
        "",
        f"Evidence branch: `{branch}`",
        "",
        f"Observation window: `{started}` to `{ended}`",
        "",
        "Verdict: `null`",
        "",
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
            "Each JSON record stores the exact argv/command, cwd, public environment, UTC timestamps, process runtime, exit status, and complete combined output. Expected sandbox and no-match observations remain visible.",
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
    final_redaction = _redaction_scan(repository_root, evidence_dir, revision)
    if final_redaction != preliminary_redaction:
        raise ValueError(
            "redaction result changed after writing the final report and manifest: "
            f"{preliminary_redaction} != {final_redaction}"
        )
    if any(final_redaction["finding_counts"].values()):
        raise ValueError(f"redaction scan found prohibited data: {final_redaction}")
    (evidence_dir / "redaction-scan.json").write_text(
        json.dumps(final_redaction, indent=2, sort_keys=True) + "\n"
    )
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
