"""The supplement renderer copies stored failures and never runs verification."""

from __future__ import annotations

import json
import subprocess

import pytest

from scripts import report_v5_historical_verification as report


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    original = tmp_path / "artifacts/grounding-v5-d510" / report.EVIDENCE_REVISION
    (original / "commands").mkdir(parents=True)
    package_files = []
    for number in report.CASES:
        path = original / f"commands/{number}-original.json"
        path.write_text(json.dumps({"exit_status": 1, "output": "original failure\n"}))
        package_files.append({"path": str(path.relative_to(original)), "sha256": report._sha(path)})
    (original / "evidence-manifest.json").write_text(json.dumps({"package_files": package_files}))
    directory = tmp_path / "supplement"
    directory.mkdir()
    rows = [
        {
            "original_record": number,
            "source_revision": case.revision,
            "exit_status": 7 if number == "27" else 0,
            "duration_seconds": 2.0,
            "command_duration_seconds": 1.0,
            "command": "python -m fixture --verify",
            "output": "retained failure\n" if number == "27" else "stored observation\n",
            "artifact_file_count": 4,
            "artifact_changes": [],
            "verification_errors": [],
        }
        for number, case in report.CASES.items()
    ]
    (directory / "investigation.json").write_text(
        json.dumps(
            {
                "verdict": None,
                "selected_verifications": {row["original_record"]: row for row in rows},
            }
        )
    )
    (directory / "entry-point-run.json").write_text(
        json.dumps(
            {
                "schema_version": report.SCHEMA,
                "verdict": None,
                "observations": rows,
            }
        )
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: pytest.fail("renderer cannot run commands")
    )
    return tmp_path, directory, original


def test_renderer_preserves_failure_and_original_hashes(evidence):
    root, directory, original = evidence
    before = {str(p): p.read_bytes() for p in original.rglob("*") if p.is_file()}
    report.render(directory, root=root)
    report.render(directory, root=root, check=True)
    body = (directory / "REPORT.md").read_text()
    assert "Exit: `7`" in body
    assert "retained failure" in body
    assert "Verdict: `null`" in body
    assert before == {str(p): p.read_bytes() for p in original.rglob("*") if p.is_file()}
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["original_manifest_sha256"] == report._sha(original / "evidence-manifest.json")
    for name, sha in manifest["files"].items():
        assert report._sha(directory / name) == sha


def test_renderer_rejects_changed_original_without_outputs(evidence):
    root, directory, original = evidence
    (original / "commands/27-original.json").write_text("changed")
    with pytest.raises(ValueError, match="original evidence changed"):
        report.render(directory, root=root)
    assert not (directory / "REPORT.md").exists()


def test_renderer_does_not_overwrite_different_supplement(evidence):
    root, directory, _ = evidence
    (directory / "manifest.json").write_text("old evidence")
    with pytest.raises(ValueError, match="refusing to replace"):
        report.render(directory, root=root)
    assert not (directory / "REPORT.md").exists()
    assert (directory / "manifest.json").read_text() == "old evidence"


def test_check_detects_modified_generated_report(evidence):
    root, directory, _ = evidence
    report.render(directory, root=root)
    (directory / "REPORT.md").write_text("changed")
    with pytest.raises(ValueError, match="stored supplement differs"):
        report.render(directory, root=root, check=True)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "verdict"])
def test_renderer_rejects_incomplete_or_reclassified_observations(evidence, mutation):
    root, directory, _ = evidence
    path = directory / "entry-point-run.json"
    value = json.loads(path.read_text())
    if mutation == "missing":
        value["observations"].pop()
    elif mutation == "duplicate":
        value["observations"].append(value["observations"][0])
    else:
        value["verdict"] = "PASS"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        report.render(directory, root=root)
