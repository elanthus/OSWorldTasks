"""D5.8 evidence verifiers fail closed, including under python -O."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import verify_d58_gemini38_calibration as gemini
from scripts import verify_d58_reliable_continuation as reliable

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("existing", ["analysis.json", "verification.json", "files.json"])
def test_partial_recording_is_never_overwritten(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(gemini, "DIRECTORY", tmp_path)
    monkeypatch.setattr(gemini, "read", lambda name: {})
    monkeypatch.setattr(gemini, "render_report", lambda summary: "report")
    monkeypatch.setattr(gemini, "analyze", lambda *args: {})
    monkeypatch.setattr(
        gemini, "verify_journal", lambda *args: pytest.fail("must reject before journal audit")
    )
    monkeypatch.setattr(sys, "argv", ["verify", "--record"])
    (tmp_path / "report.md").write_text("report")
    (tmp_path / existing).write_bytes(b"original evidence")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(FileExistsError):
        gemini.main()
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("damage", ["none", "extra", "missing", "hash"])
def test_readonly_manifest_requires_exact_file_set(tmp_path, monkeypatch, damage):
    monkeypatch.setattr(reliable, "PUBLIC", tmp_path)
    monkeypatch.setattr(reliable, "verify", lambda journal: ({}, {}))
    monkeypatch.setattr(sys, "argv", ["verify"])
    (tmp_path / "analysis.json").write_text("{}")
    digest = "sha256:" + reliable.sha256_bytes(b"{}")
    (tmp_path / "files.json").write_text(
        json.dumps({"analysis.json": digest, "fixture.txt": digest})
    )
    (tmp_path / "fixture.txt").write_text("{}")
    if damage == "extra":
        (tmp_path / "unlisted.txt").write_text("extra")
    elif damage == "missing":
        (tmp_path / "fixture.txt").unlink()
    elif damage == "hash":
        (tmp_path / "fixture.txt").write_text("changed")
    if damage == "none":
        reliable.main()
    else:
        with pytest.raises(ValueError):
            reliable.main()


@pytest.mark.parametrize("function", ["verify", "verify_reconciliation"])
def test_owner_verification_rejects_corruption_under_optimization(tmp_path, function):
    code = """
from scripts import verify_d58_owner_budget_continuation as v
original = v.read
def corrupt(path):
    value = original(path)
    if path == v.RECONCILIATION:
        value['approval_digest'] = 'corrupt'
    if path == v.PUBLIC / 'execution-plan.json':
        value['execution_plan_digest'] = 'corrupt'
    return value
v.read = corrupt
"""
    code += f"v.{function}()\n"
    result = subprocess.run(
        [sys.executable, "-O", "-c", code], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "ValueError: evidence check failed" in result.stderr


def test_successor_checks_have_no_optimization_removable_assertions():
    for name in ("owner_budget_continuation", "reliable_continuation", "gemini38_calibration"):
        tree = ast.parse((ROOT / f"scripts/verify_d58_{name}.py").read_text())
        assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))


@pytest.mark.parametrize("accounting", [False, True])
def test_revision_wrapper_copies_predecessors_and_clears_optimization(
    tmp_path, monkeypatch, accounting
):
    import io
    import tarfile
    from types import SimpleNamespace

    from scripts import verify_d58_at_revision as wrapper

    name = "grounding-v5-d58-owner-budget" if accounting else "grounding-v5-d58-reliable-diagnostic"
    directory = tmp_path / "artifacts" / name
    directory.mkdir(parents=True)
    filename = "reconciliation.json" if accounting else "execution-plan.json"
    (directory / filename).write_text(json.dumps({"driver_code_revision": "a" * 40}))
    predecessor = tmp_path / "artifacts/grounding-v5-d58-gemini38-calibration"
    predecessor.mkdir()
    (predecessor / "summary.json").write_text("{}")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w"):
        pass
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["git", "archive"]:
            return SimpleNamespace(stdout=archive.getvalue())
        environment = kwargs["env"]
        assert "PYTHONOPTIMIZE" not in environment
        assert "OPENROUTER_API_KEY" not in environment
        assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
        tree = Path(environment["PYTHONPATH"])
        assert (tree / "artifacts" / predecessor.name / "summary.json").read_text() == "{}"
        assert ("--verify-reconciliation" in command) is accounting
        return SimpleNamespace(returncode=0, stdout='{"verified": true}', stderr="")

    monkeypatch.setattr(wrapper, "ROOT", tmp_path)
    monkeypatch.setattr(wrapper.subprocess, "run", run)
    monkeypatch.setenv("PYTHONOPTIMIZE", "2")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-never-sent")
    assert wrapper.verify(name, None)["verification"] == {"verified": True}
    assert len(calls) == 2
