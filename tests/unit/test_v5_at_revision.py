"""Local Git fixtures exercise historical verification without models or browsers."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts import verify_v5_at_revision as wrapper

PROGRAM = """import json, os, pathlib, subprocess, sys
from pixelgym.marker import MARKER
root = pathlib.Path.cwd()
print(json.dumps({"marker": MARKER, "arguments": sys.argv[1:],
                  "environment": sorted(os.environ)}), flush=True)
assert MARKER == "historical"
assert json.loads((root / "artifacts/data.json").read_text()) == {"value": 7}
assert not any(k in os.environ for k in ["OPENROUTER_API_KEY", "PYTHONOPTIMIZE", "AWS_PROFILE"])
assert not list(pathlib.Path.home().iterdir())
assert subprocess.check_output(["git", "cat-file", "-t", "HEAD"], text=True).strip() == "commit"
{tail}
"""


def _git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.check_output(
        ["git", "-c", "commit.gpgsign=false", *args], cwd=root, env=env, text=True
    ).strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    def create(*, tail="", bad_binding=False, bad_data=False):
        root = tmp_path / "repository"
        root.mkdir()
        _git(root, "init", "-q")
        _git(root, "config", "user.name", "Fixture")
        _git(root, "config", "user.email", "fixture@example.invalid")
        for name in ("pixelgym", "scripts", "requirements", "artifacts"):
            (root / name).mkdir()
        (root / "pixelgym/__init__.py").touch()
        (root / "scripts/__init__.py").touch()
        (root / "pixelgym/marker.py").write_text('MARKER = "historical"\n')
        (root / "scripts/check.py").write_text(PROGRAM.replace("{tail}", tail))
        (root / "pyproject.toml").write_text("# fixture\n")
        (root / "requirements/fixture.txt").touch()
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "Historical source")
        source = _git(root, "rev-parse", "HEAD")
        (root / "artifacts/data.json").write_text(json.dumps({"value": 8 if bad_data else 7}))
        bindings = {
            path: wrapper._hash(root / path) for path in ("scripts/check.py", "pixelgym/marker.py")
        }
        if bad_binding:
            bindings["scripts/check.py"] = "sha256:" + "0" * 64
        (root / "artifacts/binding.json").write_text(json.dumps({"files": bindings}))
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "Frozen evidence")
        evidence = _git(root, "rev-parse", "HEAD")
        (root / "pixelgym/marker.py").write_text('MARKER = "later"\n')
        monkeypatch.setattr(wrapper, "EVIDENCE_REVISION", evidence)
        monkeypatch.setattr(
            wrapper,
            "CASES",
            {"46": wrapper.Case(source, "scripts.check", "binding.json", ("files",), True)},
        )
        return root, source, evidence

    return create


def test_historical_code_and_frozen_evidence_ignore_later_checkout(repository, monkeypatch):
    root, source, evidence = repository()
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-never-sent")
    monkeypatch.setenv("PYTHONOPTIMIZE", "2")
    monkeypatch.setenv("AWS_PROFILE", "fixture-never-used")
    monkeypatch.setenv("GIT_DIR", "/unrelated/repository")
    before = (root / "pixelgym/marker.py").read_bytes()

    result = wrapper.verify("46", root=root)

    assert result["exit_status"] == 0, result
    assert result["verification_errors"] == []
    assert result["source_revision"] == source
    assert result["artifact_revision"] == evidence
    assert result["artifact_file_count"] == 2
    assert result["artifact_changes"] == []
    assert len(result["artifact_inventory_sha256"]) == 64
    output = json.loads(result["output"])
    assert output["marker"] == "historical"
    assert output["arguments"] == ["--verify", "--source-revision", source]
    assert (root / "pixelgym/marker.py").read_bytes() == before
    assert str(root) not in json.dumps(result)
    assert "fixture-never" not in json.dumps(result)


def test_mismatched_source_binding_stops_before_verifier(repository):
    root, _, _ = repository(bad_binding=True)
    result = wrapper.verify("46", root=root)
    assert result["exit_status"] is None
    assert result["output"] == ""
    assert result["verification_errors"] == ["historical source binding mismatch: scripts/check.py"]


def test_verifier_failure_retains_full_output(repository):
    root, _, _ = repository(bad_data=True)
    result = wrapper.verify("46", root=root)
    assert result["exit_status"] == 1
    assert "historical" in result["output"]
    assert "AssertionError" in result["output"]
    assert result["artifact_changes"] == []


@pytest.mark.parametrize(
    "tail, changed",
    [
        ('(root / "artifacts/data.json").write_text("changed")', "artifacts/data.json"),
        ('(root / "artifacts/new.json").write_text("new")', "artifacts/new.json"),
        ('(root / "artifacts/data.json").unlink()', "artifacts/data.json"),
    ],
)
def test_exit_zero_cannot_hide_artifact_mutation(repository, tail, changed):
    root, _, _ = repository(tail=tail)
    result = wrapper.verify("46", root=root)
    assert result["exit_status"] == 0
    assert result["artifact_changes"] == [changed]
    assert result["verification_errors"] == ["historical verifier mutated artifact files"]
    assert json.loads((root / "artifacts/data.json").read_text()) == {"value": 7}


def test_missing_revision_is_recorded_without_fallback(repository, monkeypatch):
    root, _, _ = repository()
    monkeypatch.setattr(wrapper, "EVIDENCE_REVISION", "a" * 40)
    result = wrapper.verify("46", root=root)
    assert result["exit_status"] is None
    assert "git archive failed" in result["verification_errors"][0]


def test_timeout_keeps_partial_output_and_no_fabricated_exit_code(repository, monkeypatch):
    root, _, _ = repository()
    original = wrapper.subprocess.run

    def run(command, **kwargs):
        if "-m" in command:
            raise subprocess.TimeoutExpired(command, 10, output=b"partial output\n")
        return original(command, **kwargs)

    monkeypatch.setattr(wrapper.subprocess, "run", run)
    result = wrapper.verify("46", root=root, timeout=10)
    assert result["timed_out"] is True
    assert result["exit_status"] is None
    assert result["output"] == "partial output\n"
    assert result["verification_errors"] == ["historical verifier exceeded timeout"]


@pytest.mark.parametrize("name, kind", [("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)])
def test_archive_cannot_escape_scratch_tree(tmp_path, monkeypatch, name, kind):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = "/outside"
        archive.addfile(info)
    monkeypatch.setattr(wrapper, "_git", lambda *args: buffer.getvalue())
    with pytest.raises((ValueError, tarfile.TarError)):
        wrapper._extract(tmp_path, "a" * 40, ("scripts",), tmp_path / "tree")
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_rejected_without_process(timeout):
    with pytest.raises(ValueError, match="timeout"):
        wrapper.verify("27", timeout=timeout)


def test_unknown_case_cannot_execute_arbitrary_module():
    with pytest.raises(ValueError, match="unsupported"):
        wrapper.verify("scripts.run_grounding_v5_d59_haiku")


def test_cli_refuses_overwrite_before_running(tmp_path, monkeypatch):
    output = tmp_path / "record.json"
    output.write_text("original\n")
    monkeypatch.setattr("sys.argv", ["verify", "27", "--output", str(output)])
    monkeypatch.setattr(wrapper, "verify", lambda *a, **kw: pytest.fail("must not run"))
    with pytest.raises(SystemExit) as error:
        wrapper.main()
    assert error.value.code == 2
    assert output.read_text() == "original\n"


def test_cli_keeps_failures_and_continues_selected_records(tmp_path, monkeypatch, capsys):
    rows = iter(
        [
            {"exit_status": 2, "output": "failure\n", "verification_errors": []},
            {"exit_status": 0, "output": "observation\n", "verification_errors": []},
        ]
    )
    monkeypatch.setattr(wrapper, "verify", lambda *a, **kw: next(rows))
    output = tmp_path / "record.json"
    monkeypatch.setattr("sys.argv", ["verify", "27", "42", "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        wrapper.main()
    assert error.value.code == 1
    result = json.loads(output.read_text())
    assert result == json.loads(capsys.readouterr().out)
    assert result["verdict"] is None
    assert [r["exit_status"] for r in result["observations"]] == [2, 0]
