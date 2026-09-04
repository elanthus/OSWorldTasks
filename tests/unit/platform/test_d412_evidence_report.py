from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.generate_d412_evidence_report import (
    CHECKLIST,
    DOCUMENTATION_PATHS,
    SUPPORTING_PATHS,
    _git_file_bytes,
    generate,
)

REVISION = "f92e307af7a3830347d50ca63f6a7d481489935c"
D412_ROOT = Path(__file__).parents[3] / "artifacts/platform/d4.12"
COMMITTED_REVISIONS = tuple(
    path.name for path in sorted(D412_ROOT.iterdir()) if path.is_dir()
)


def _isolated_evidence(
    repository_root: Path, tmp_path: Path, revision: str = REVISION
) -> tuple[Path, Path]:
    isolated_root = tmp_path / "repository"
    source_evidence = repository_root / "artifacts/platform/d4.12" / revision
    evidence_dir = isolated_root / "artifacts/platform/d4.12" / revision
    shutil.copytree(source_evidence, evidence_dir)
    manifest_path = source_evidence / "evidence-manifest.json"
    manifest_snapshot = subprocess.run(
        [
            "git",
            "log",
            "-1",
            "--format=%H",
            "--",
            manifest_path.relative_to(repository_root).as_posix(),
        ],
        cwd=repository_root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    for relative in SUPPORTING_PATHS:
        if relative in DOCUMENTATION_PATHS:
            continue
        destination = isolated_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_git_file_bytes(repository_root, manifest_snapshot, relative))
    screenshot_manifest = json.loads(
        (repository_root / "artifacts/platform/screenshots/manifest.json").read_text()
    )
    for item in screenshot_manifest["screenshots"]:
        relative = Path("artifacts/platform/screenshots") / item["path"]
        destination = isolated_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repository_root / relative, destination)
    (isolated_root / ".git").symlink_to(
        repository_root / ".git", target_is_directory=(repository_root / ".git").is_dir()
    )
    return isolated_root, evidence_dir


def test_generator_indexes_stored_observations_without_a_gate_verdict(
    repository_root: Path, tmp_path: Path
) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)

    manifest = generate(isolated_root, evidence_dir)
    report = (evidence_dir / "REPORT.md").read_text()

    assert manifest["verdict"] is None
    assert manifest["verdict_owner"] == "project owner"
    assert manifest["evidence_revision"] == REVISION
    assert len(manifest["checklist_items"]) == len(CHECKLIST) == 16
    assert all(not item["missing_evidence"] for item in manifest["checklist_items"])
    assert all(
        isinstance(json.loads(item["observed_raw_result"]), dict)
        for item in manifest["checklist_items"]
    )
    assert "- [ ]" not in report
    assert "- [x]" not in report.lower()
    assert "declare a milestone verdict" in report
    redaction = json.loads((evidence_dir / "redaction-scan.json").read_text())
    assert redaction["final_deliverables_scanned"] == [
        "REPORT.md",
        "evidence-manifest.json",
    ]
    assert all(count == 0 for count in redaction["finding_counts"].values())


def test_generated_artifact_digests_verify_independently(
    repository_root: Path, tmp_path: Path
) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)
    manifest = generate(isolated_root, evidence_dir)

    for entry in manifest["command_records"]:
        data = (evidence_dir / entry["path"]).read_bytes()
        assert len(data) == entry["size"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    for entry in manifest["supporting_artifacts"]:
        data = (
            _git_file_bytes(isolated_root, REVISION, entry["path"])
            if entry["path"] in DOCUMENTATION_PATHS
            else (isolated_root / entry["path"]).read_bytes()
        )
        assert len(data) == entry["size"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]

    on_disk = json.loads((evidence_dir / "evidence-manifest.json").read_text())
    assert on_disk["identity_reconciliation"]["stored_counts"] == {
        "api_exchanges": 5,
        "approval_events": 2,
        "audit_events": 11,
        "deployment_events": 3,
        "gate_reports": 3,
        "immutable_failures": 0,
        "immutable_verified": 310,
        "manifest_artifact_union": 310,
        "mlflow_lineage_records": 3,
        "run_manifests": 3,
        "screenshots": 10,
    }
    reconciliation = on_disk["identity_reconciliation"]
    assert reconciliation["approval_candidate_policy_gate_digest_tuples_match"] is True
    assert reconciliation["deployment_candidate_policy_tuples_are_approved"] is True


def test_generator_rejects_tampered_success_claim(repository_root: Path, tmp_path: Path) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)
    command_path = evidence_dir / "commands/08-fast-suite.json"
    command = json.loads(command_path.read_text())
    command["exit_status"] = 9
    command["output"] = "BROKEN\n"
    command_path.write_text(json.dumps(command, indent=2, sort_keys=True) + "\n")

    with pytest.raises(ValueError, match="unexpected exit status"):
        generate(isolated_root, evidence_dir)


@pytest.mark.parametrize("revision", COMMITTED_REVISIONS)
def test_generator_reproduces_committed_manifest(
    repository_root: Path, tmp_path: Path, revision: str
) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path, revision)

    generate(isolated_root, evidence_dir)

    assert (evidence_dir / "evidence-manifest.json").read_bytes() == (
        repository_root
        / "artifacts/platform/d4.12"
        / revision
        / "evidence-manifest.json"
    ).read_bytes()


def test_generator_reproduces_manifest_with_modified_live_documentation(
    repository_root: Path, tmp_path: Path
) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)
    deployment_readme = isolated_root / "deploy/README.md"
    assert not deployment_readme.exists()
    deployment_readme.parent.mkdir(parents=True)
    deployment_readme.write_text("Later documentation change.\n")

    generate(isolated_root, evidence_dir)

    assert (evidence_dir / "evidence-manifest.json").read_bytes() == (
        repository_root
        / "artifacts/platform/d4.12"
        / REVISION
        / "evidence-manifest.json"
    ).read_bytes()


def test_documentation_at_head_matches_recorded_git_bytes(repository_root: Path) -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()

    for relative in DOCUMENTATION_PATHS:
        assert (repository_root / relative).read_bytes() == _git_file_bytes(
            repository_root, head, relative
        )


def test_git_file_read_rejects_missing_revision_and_path(repository_root: Path) -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()

    with pytest.raises(ValueError, match="revision is not present"):
        _git_file_bytes(repository_root, "0" * 40, "deploy/README.md")
    with pytest.raises(ValueError, match="path is not present"):
        _git_file_bytes(repository_root, head, "deploy/missing-documentation.md")


def test_generator_rejects_dirty_frozen_worktree(repository_root: Path, tmp_path: Path) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)
    status_path = evidence_dir / "commands/01-worktree-status.json"
    record = json.loads(status_path.read_text())
    record["output"] = " M pixelgym/platform/control_store.py\n"
    status_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

    with pytest.raises(ValueError, match="frozen checkout status"):
        generate(isolated_root, evidence_dir)


def test_generator_rejects_revision_directory_mismatch(
    repository_root: Path, tmp_path: Path
) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)
    mismatched = evidence_dir.with_name("0" * 40)
    evidence_dir.rename(mismatched)

    with pytest.raises(ValueError, match="recorded revision does not match evidence directory"):
        generate(isolated_root, mismatched)


def test_generator_rejects_tampered_resume_ledger(repository_root: Path, tmp_path: Path) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)
    command_path = evidence_dir / "commands/41-metaflow-resume-ledgers.json"
    command = json.loads(command_path.read_text())
    ledger = json.loads(command["output"])
    ledger["boundaries"]["run_linked"]["provider_ledger"]["billable_calls"] = 101
    command["output"] = json.dumps(ledger, sort_keys=True) + "\n"
    command_path.write_text(json.dumps(command, indent=2, sort_keys=True) + "\n")

    with pytest.raises(ValueError, match="unexpected provider ledger"):
        generate(isolated_root, evidence_dir)


def test_generator_rejects_policy_mapped_to_multiple_candidates(
    repository_root: Path, tmp_path: Path
) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)
    lineage_path = isolated_root / "artifacts/platform/demo-mlflow-lineage.jsonl"
    rows = [json.loads(line) for line in lineage_path.read_text().splitlines()]
    rows[1]["policy_id"] = rows[0]["policy_id"]
    lineage_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))

    with pytest.raises(ValueError, match="multiple candidates"):
        generate(isolated_root, evidence_dir)


def test_generator_rejects_host_identity_in_supporting_artifact(
    repository_root: Path, tmp_path: Path
) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)
    limitations = isolated_root / "artifacts/platform/known-limitations.md"
    limitations.write_text(limitations.read_text() + "\n/home/example-user/private.txt\n")

    with pytest.raises(ValueError, match="redaction scan found prohibited data"):
        generate(isolated_root, evidence_dir)
