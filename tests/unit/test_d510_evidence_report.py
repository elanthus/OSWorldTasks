"""No-process checks for the D5.10 raw-evidence index generator, on a tiny fixture."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import scripts.generate_d510_evidence_report as d510_report
from scripts.generate_d510_evidence_report import (
    COMMAND_SCHEMA,
    MAP_SCHEMA,
    EvidenceError,
    generate,
    main,
    parse_pytest_summary,
)

REVISION = "a" * 40
SOURCE_REVISION = "b" * 40
CHECKLIST_SOURCE = (
    "# Plan\n"
    "| D5.10 | **YOU** | Review raw commands and wording | Agent does not declare it |\n"
    "- [ ] First checklist line\n"
    "  continues on a second line.\n"
    "- [ ] Second checklist line.\n"
)


def _record(command: str, output: str, exit_status: int = 0) -> dict[str, Any]:
    return {
        "schema_version": COMMAND_SCHEMA,
        "command": command,
        "argv": command.split(),
        "cwd": "<path-0>",
        "environment": {},
        "started_at_utc": "2026-10-06T00:00:00+00:00",
        "ended_at_utc": "2026-10-06T00:00:01+00:00",
        "duration_seconds": 1.0,
        "exit_status": exit_status,
        "output": output,
    }


def _checklist_map() -> dict[str, Any]:
    return {
        "schema_version": MAP_SCHEMA,
        "gate": "D5.10",
        "verdict": None,
        "verdict_owner": "project owner",
        "checklist_source": {
            "revision": SOURCE_REVISION,
            "path": "plans/plan.md",
            "record": "commands/03-checklist-source.json",
        },
        "identity_records": {
            "revision": "commands/00-git-revision.json",
            "branch": "commands/02-evidence-branch.json",
            "worktree_status": ["commands/01-worktree-status.json"],
        },
        "public_wording_inventory_record": "commands/06-public-wording.json",
        "placeholders": {"<path-0>": "evidence worktree"},
        "recording_notes": ["Fixture note."],
        "items": [
            {
                "id": "delivery-row",
                "kind": "delivery_row",
                "source": "bbbbbbb:plans/plan.md:2",
                "checklist_text": "Review raw commands and wording",
                "records": [],
                "artifacts": [],
            },
            {
                "id": "done-when-01",
                "kind": "done_when",
                "source": "bbbbbbb:plans/plan.md:3",
                "source_end_line": 4,
                "checklist_text": "First checklist line continues on a second line.",
                "records": ["commands/04-unit-group.json", "commands/05-failing-group.json"],
                "artifacts": ["artifacts/example.json"],
                "linked_records": [
                    {"path": "artifacts/example.json", "verbatim": "fixture waiver"}
                ],
            },
            {
                "id": "done-when-02",
                "kind": "done_when",
                "source": "bbbbbbb:plans/plan.md:5",
                "checklist_text": "Second checklist line.",
                "records": [],
                "artifacts": [],
            },
        ],
        "not_run": [{"item": "scripts/example.py --verify", "reason": "input not retained"}],
        "withdrawn_records": [
            {
                "record": "commands/08-withdrawn.json",
                "sha256": "0" * 64,
                "exit_status": 0,
                "reason": "fixture withdrawal",
                "replacement_records": ["commands/04-unit-group.json"],
            }
        ],
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    evidence = repository / "artifacts/grounding-v5-d510" / REVISION
    (evidence / "commands").mkdir(parents=True)
    (repository / "artifacts/example.json").write_text('{"note": "fixture waiver"}\n')
    records = {
        "00-git-revision": _record("git rev-parse HEAD", REVISION + "\n"),
        "01-worktree-status": _record("git status --porcelain", ""),
        "02-evidence-branch": _record("git branch --show-current", "evidence/d510-aaaaaaaa\n"),
        "03-checklist-source": _record("git show plan", CHECKLIST_SOURCE),
        "04-unit-group": _record("pytest -q a.py", "..s\n2 passed, 1 skipped in 0.12s\n"),
        "05-failing-group": _record("pytest -q b.py", "F\n1 failed, 3 passed in 0.05s\n", 1),
        "06-public-wording": _record("grep -n v5 README.md", "README.md:3:v5 status line\n"),
    }
    for name, record in records.items():
        (evidence / "commands" / f"{name}.json").write_text(json.dumps(record, indent=2))
    _write_map(evidence, _checklist_map())
    return repository, evidence


def _write_map(evidence: Path, value: dict[str, Any]) -> None:
    (evidence / "checklist-map.json").write_text(json.dumps(value, indent=2) + "\n")


def _generated(evidence: Path) -> list[str]:
    return sorted(
        name
        for name in ("REPORT.md", "evidence-manifest.json", "redaction-scan.json")
        if (evidence / name).exists()
    )


def test_report_renders_null_verdict_and_copies_raw_counts(tmp_path: Path) -> None:
    repository, evidence = _fixture(tmp_path)

    manifest = generate(repository, evidence)

    report = (evidence / "REPORT.md").read_text()
    assert manifest["verdict"] is None
    assert json.loads((evidence / "evidence-manifest.json").read_text())["verdict"] is None
    assert "Verdict: `null`" in report
    assert "Verdict owner: project owner" in report
    assert "no stored observation" in report
    assert '`04-unit-group`: exit 0; pytest {"passed": 2, "runtime": 0.12, "skipped": 1}' in report
    assert (
        '`05-failing-group`: exit 1; pytest {"failed": 1, "passed": 3, "runtime": 0.05}' in report
    )
    assert "> fixture waiver" in report
    assert "README.md:3:v5 status line" in report
    assert "`commands/08-withdrawn.json`" in report and "fixture withdrawal" in report
    assert "- [ ]" not in report and "- [x]" not in report.lower()
    scan = json.loads((evidence / "redaction-scan.json").read_text())
    assert not any(scan["finding_counts"].values())
    listed = {entry["path"]: entry for entry in manifest["package_files"]}
    on_disk = {
        path.relative_to(evidence).as_posix()
        for path in evidence.rglob("*")
        if path.is_file() and path.name != "evidence-manifest.json"
    }
    assert set(listed) == on_disk
    for name, entry in listed.items():
        assert entry["sha256"] == hashlib.sha256((evidence / name).read_bytes()).hexdigest()


def test_missing_referenced_record_fails_closed(tmp_path: Path) -> None:
    repository, evidence = _fixture(tmp_path)
    value = _checklist_map()
    value["items"][2]["records"] = ["commands/99-never-recorded.json"]
    _write_map(evidence, value)

    with pytest.raises(EvidenceError, match="referenced record is missing"):
        generate(repository, evidence)
    assert _generated(evidence) == []


def test_missing_artifact_path_fails_closed(tmp_path: Path) -> None:
    repository, evidence = _fixture(tmp_path)
    (repository / "artifacts/example.json").unlink()

    with pytest.raises(EvidenceError, match="path is missing"):
        generate(repository, evidence)
    assert _generated(evidence) == []


def test_paraphrased_checklist_text_fails_closed(tmp_path: Path) -> None:
    repository, evidence = _fixture(tmp_path)
    value = _checklist_map()
    value["items"][2]["checklist_text"] = "Second checklist line, paraphrased."
    _write_map(evidence, value)

    with pytest.raises(EvidenceError, match="not verbatim"):
        generate(repository, evidence)
    assert _generated(evidence) == []


def test_redaction_finding_fails_closed_before_writing(tmp_path: Path) -> None:
    repository, evidence = _fixture(tmp_path)
    leaked = _record("pytest -q c.py", "/Users/someone/private\n1 passed in 0.01s\n")
    (evidence / "commands/07-leaked.json").write_text(json.dumps(leaked))

    with pytest.raises(EvidenceError, match="redaction scan found prohibited data"):
        generate(repository, evidence)
    assert _generated(evidence) == []
    assert main(["scan", "--path", str(evidence / "commands")]) == 1


def test_generator_starts_no_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the D5.10 generator must not start a process")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, refuse)
    for name in ("system", "popen", "posix_spawn", "posix_spawnp", "execv", "execvp"):
        monkeypatch.setattr(os, name, refuse)
    repository, evidence = _fixture(tmp_path)

    arguments = ["render", "--repository-root", str(repository), "--evidence-dir", str(evidence)]
    assert main(arguments) == 0
    assert main(["scan", "--path", str(evidence / "commands")]) == 0
    assert _generated(evidence) == ["REPORT.md", "evidence-manifest.json", "redaction-scan.json"]

    imported: set[str] = set()
    for node in ast.walk(ast.parse(Path(d510_report.__file__).read_text())):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"subprocess", "multiprocessing", "pty", "shlex"}


def test_pytest_summary_parser_copies_printed_counts() -> None:
    assert parse_pytest_summary(
        "x\n2568 passed, 6 skipped, 12 warnings in 191.32s (0:03:11)\n"
    ) == {
        "passed": 2568,
        "runtime": 191.32,
        "skipped": 6,
        "warnings": 12,
    }
    assert parse_pytest_summary("== 1 failed, 1 error, 1 warning in 2.00s ==") == {
        "errors": 1,
        "failed": 1,
        "runtime": 2.0,
        "warnings": 1,
    }
    assert parse_pytest_summary("13 tests collected in 18.54s") == {
        "collected": 13,
        "runtime": 18.54,
    }
    assert parse_pytest_summary("no tests ran in 0.10s") == {"no_tests_ran": True, "runtime": 0.1}
    assert parse_pytest_summary("Python 3.12.15\n") is None
