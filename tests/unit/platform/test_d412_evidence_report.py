from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.generate_d412_evidence_report import CHECKLIST, SUPPORTING_PATHS, generate

REVISION = "f92e307af7a3830347d50ca63f6a7d481489935c"


def _isolated_evidence(repository_root: Path, tmp_path: Path) -> tuple[Path, Path]:
    isolated_root = tmp_path / "repository"
    source_evidence = repository_root / "artifacts/platform/d4.12" / REVISION
    evidence_dir = isolated_root / "artifacts/platform/d4.12" / REVISION
    shutil.copytree(source_evidence, evidence_dir)
    for relative in SUPPORTING_PATHS:
        source = repository_root / relative
        destination = isolated_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    screenshot_manifest = json.loads(
        (repository_root / "artifacts/platform/screenshots/manifest.json").read_text()
    )
    for item in screenshot_manifest["screenshots"]:
        relative = Path("artifacts/platform/screenshots") / item["path"]
        destination = isolated_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repository_root / relative, destination)
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
        data = (isolated_root / entry["path"]).read_bytes()
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


def test_generator_reproduces_committed_manifest(repository_root: Path, tmp_path: Path) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)

    generate(isolated_root, evidence_dir)
    generated = json.loads((evidence_dir / "evidence-manifest.json").read_text())
    committed = json.loads(
        (repository_root / "artifacts/platform/d4.12" / REVISION / "evidence-manifest.json").read_text()
    )

    assert generated == committed


def test_generator_rejects_dirty_frozen_worktree(repository_root: Path, tmp_path: Path) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)
    status_path = evidence_dir / "commands/01-worktree-status.json"
    record = json.loads(status_path.read_text())
    record["output"] = " M pixelgym/platform/control_store.py\n"
    status_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

    with pytest.raises(ValueError, match="frozen checkout status"):
        generate(isolated_root, evidence_dir)


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
