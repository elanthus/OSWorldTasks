from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import scripts.generate_d412_evidence_report as d412_report
from scripts.generate_d412_evidence_report import (
    CHECKLIST,
    DOCUMENTATION_PATHS,
    SUPPORTING_PATHS,
    _git_file_bytes,
    generate,
)

REVISION = "f92e307af7a3830347d50ca63f6a7d481489935c"
LIVE_DOCUMENTATION_REGRESSION_REVISION = "672a6556716e4779a7c494637d839d18bcdc9453"
KNOWN_LIMITATIONS_PATH = "artifacts/platform/known-limitations.md"
# See artifacts/platform/d4.12/f92e307af7a3830347d50ca63f6a7d481489935c/EVIDENCE-NOTE.md.
LEGACY_MANIFEST_KNOWN_LIMITATIONS = {
    "path": KNOWN_LIMITATIONS_PATH,
    "sha256": "1698fa65daf5e462486ab1467bb4ade2cbfe8600b66e7af78717dd39a074aa51",
    "size": 1440,
}
D412_ROOT = Path(__file__).parents[3] / "artifacts/platform/d4.12"
COMMITTED_REVISIONS = tuple(
    path.name for path in sorted(D412_ROOT.iterdir()) if path.is_dir()
)
MISSING_COMMITTED_REVISIONS = tuple(
    revision
    for revision in COMMITTED_REVISIONS
    if subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=D412_ROOT.parents[2],
        capture_output=True,
        check=False,
    ).returncode
    != 0
)
if MISSING_COMMITTED_REVISIONS:
    pytest.skip(
        "committed D4.12 revisions are unavailable in this shallow clone: "
        f"{', '.join(MISSING_COMMITTED_REVISIONS)}; run `git fetch --unshallow` "
        "locally or configure `fetch-depth: 0` in CI",
        allow_module_level=True,
    )


def _isolated_evidence(
    repository_root: Path, tmp_path: Path, revision: str = REVISION
) -> tuple[Path, Path]:
    isolated_root = tmp_path / "repository"
    source_evidence = repository_root / "artifacts/platform/d4.12" / revision
    evidence_dir = isolated_root / "artifacts/platform/d4.12" / revision
    shutil.copytree(
        source_evidence,
        evidence_dir,
        # This post-gate annotation is not part of the historical evidence snapshot.
        ignore=shutil.ignore_patterns("EVIDENCE-NOTE.md"),
    )
    for relative in SUPPORTING_PATHS:
        destination = isolated_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_git_file_bytes(repository_root, revision, relative))
    screenshot_manifest = json.loads(
        (isolated_root / "artifacts/platform/screenshots/manifest.json").read_text()
    )
    for item in screenshot_manifest["screenshots"]:
        relative = Path("artifacts/platform/screenshots") / item["path"]
        destination = isolated_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            _git_file_bytes(repository_root, revision, relative.as_posix())
        )
    (isolated_root / ".git").symlink_to(
        repository_root / ".git", target_is_directory=(repository_root / ".git").is_dir()
    )
    return isolated_root, evidence_dir


def _artifact_entries(manifest: dict[str, object], path: str) -> list[dict[str, object]]:
    supporting = manifest["supporting_artifacts"]
    checklist = manifest["checklist_items"]
    assert isinstance(supporting, list)
    assert isinstance(checklist, list)
    entries = [entry for entry in supporting if entry["path"] == path]
    entries.extend(
        entry
        for item in checklist
        for entry in item["evidence"]
        if entry["path"] == path
    )
    return entries


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
        data = _git_file_bytes(isolated_root, REVISION, entry["path"])
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

    generated_path = evidence_dir / "evidence-manifest.json"
    committed_path = (
        repository_root
        / "artifacts/platform/d4.12"
        / revision
        / "evidence-manifest.json"
    )
    if revision != REVISION:
        assert generated_path.read_bytes() == committed_path.read_bytes()
        return

    generated = json.loads(generated_path.read_text())
    committed = json.loads(committed_path.read_text())
    recorded_data = _git_file_bytes(repository_root, revision, KNOWN_LIMITATIONS_PATH)
    recorded_entry = {
        "path": KNOWN_LIMITATIONS_PATH,
        "sha256": hashlib.sha256(recorded_data).hexdigest(),
        "size": len(recorded_data),
    }
    assert _artifact_entries(generated, KNOWN_LIMITATIONS_PATH) == [recorded_entry] * 2
    assert _artifact_entries(committed, KNOWN_LIMITATIONS_PATH) == [
        LEGACY_MANIFEST_KNOWN_LIMITATIONS
    ] * 2
    for entry in _artifact_entries(committed, KNOWN_LIMITATIONS_PATH):
        entry.update(recorded_entry)
    generated_bytes = (json.dumps(generated, indent=2, sort_keys=True) + "\n").encode()
    committed_bytes = (json.dumps(committed, indent=2, sort_keys=True) + "\n").encode()
    assert generated_bytes == committed_bytes


@pytest.mark.parametrize(
    "relative",
    (
        "deploy/README.md",
        "artifacts/platform/architecture.md",
        "artifacts/platform/known-limitations.md",
    ),
)
def test_generator_reproduces_manifest_with_modified_live_supporting_artifact(
    repository_root: Path, tmp_path: Path, relative: str
) -> None:
    revision = LIVE_DOCUMENTATION_REGRESSION_REVISION
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path, revision)
    supporting_path = isolated_root / relative
    assert supporting_path.exists()
    supporting_path.write_text("Later supporting-artifact change.\n")

    generate(isolated_root, evidence_dir)

    assert (evidence_dir / "evidence-manifest.json").read_bytes() == (
        repository_root
        / "artifacts/platform/d4.12"
        / revision
        / "evidence-manifest.json"
    ).read_bytes()


def test_documentation_at_head_matches_recorded_git_bytes(repository_root: Path) -> None:
    checkout = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repository_root,
        capture_output=True,
        check=False,
        text=True,
    )
    if checkout.returncode != 0 or checkout.stdout.strip() != "true":
        pytest.skip("repository root is not a Git checkout")

    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *DOCUMENTATION_PATHS],
        cwd=repository_root,
        capture_output=True,
        check=True,
        text=True,
    )
    if status.stdout:
        pytest.skip("working-tree documentation differs from HEAD")

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
    with pytest.raises(ValueError, match="path is not a blob"):
        _git_file_bytes(repository_root, head, "deploy")


def test_generator_checks_recorded_revision_once(
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)
    revision_checks: list[str] = []
    original_run = d412_report.subprocess.run

    def counting_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if args[:3] == ["git", "cat-file", "-e"]:
            revision_checks.append(args[3])
        return original_run(args, **kwargs)

    monkeypatch.setattr(d412_report.subprocess, "run", counting_run)

    generate(isolated_root, evidence_dir)

    assert revision_checks == [f"{REVISION}^{{commit}}"]


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
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)
    lineage_path = "artifacts/platform/demo-mlflow-lineage.jsonl"
    rows = [
        json.loads(line)
        for line in _git_file_bytes(repository_root, REVISION, lineage_path)
        .decode()
        .splitlines()
    ]
    rows[1]["policy_id"] = rows[0]["policy_id"]
    tampered_lineage = "".join(
        json.dumps(row, sort_keys=True) + "\n" for row in rows
    ).encode()
    original_git_file_bytes = d412_report._git_file_bytes

    def git_file_bytes_with_conflicting_lineage(
        root: Path,
        revision: str,
        relative: str,
        *,
        revision_validated: bool = False,
    ) -> bytes:
        if relative == lineage_path:
            return tampered_lineage
        return original_git_file_bytes(
            root,
            revision,
            relative,
            revision_validated=revision_validated,
        )

    monkeypatch.setattr(
        d412_report, "_git_file_bytes", git_file_bytes_with_conflicting_lineage
    )

    with pytest.raises(ValueError, match="multiple candidates"):
        generate(isolated_root, evidence_dir)


def test_generator_rejects_host_identity_in_evidence_artifact(
    repository_root: Path, tmp_path: Path
) -> None:
    isolated_root, evidence_dir = _isolated_evidence(repository_root, tmp_path)
    command_path = evidence_dir / "commands/02-python-version.json"
    command = json.loads(command_path.read_text())
    command["operator_path"] = "/home/example-user/private.txt"
    command_path.write_text(json.dumps(command, indent=2, sort_keys=True) + "\n")

    with pytest.raises(ValueError, match="redaction scan found prohibited data"):
        generate(isolated_root, evidence_dir)
