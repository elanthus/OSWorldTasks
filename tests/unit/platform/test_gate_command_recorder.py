from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

from pixelgym.evidence_redaction import redact_evidence_text, standard_path_replacements


def _record(
    repository_root: Path,
    tmp_path: Path,
    command: list[str],
    *,
    environment: list[str] | None = None,
    relative_cwd: bool = False,
) -> subprocess.CompletedProcess[str]:
    # ``relative_cwd`` runs the recorder from ``tmp_path`` and spells it ``--cwd .``.
    return subprocess.run(
        [
            sys.executable,
            str(repository_root / "scripts/record_gate_command.py"),
            "--output",
            str(tmp_path / "record.json"),
            "--cwd",
            "." if relative_cwd else str(tmp_path),
            "--redact-path",
            str(repository_root),
            *(argument for value in environment or [] for argument in ("--env", value)),
            "--",
            *command,
        ],
        cwd=tmp_path if relative_cwd else repository_root,
        text=True,
        capture_output=True,
        check=False,
    )


def test_recorder_redacts_paths_and_local_demo_secrets(
    repository_root: Path, tmp_path: Path
) -> None:
    completed = _record(
        repository_root,
        tmp_path,
        [
            sys.executable,
            "-c",
            ("import pathlib; print(pathlib.Path.cwd()); print('local_demo_postgres_only')"),
        ],
    )

    assert completed.returncode == 0
    record = json.loads((tmp_path / "record.json").read_text())
    assert record["exit_status"] == 0
    assert str(tmp_path) not in record["output"]
    assert "local_demo_postgres_only" not in record["output"]
    assert "<path-0>" in record["output"]
    assert "<redacted-local-demo-secret>" in record["output"]


def test_recorder_redacts_system_temporary_paths(repository_root: Path, tmp_path: Path) -> None:
    completed = _record(
        repository_root,
        tmp_path,
        [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"],
    )

    assert completed.returncode == 0
    record = json.loads((tmp_path / "record.json").read_text())
    assert record["output"] == "<system-temp>\n"


def test_redact_handles_literal_and_resolved_path_spellings(tmp_path: Path) -> None:
    target = tmp_path / "real-root"
    target.mkdir()
    literal = tmp_path / "alias-root"
    literal.symlink_to(target, target_is_directory=True)
    resolved = literal.resolve()
    assert resolved != literal

    replacements = standard_path_replacements([literal])
    assert redact_evidence_text(str(literal / "result.json"), replacements) == (
        "<path-0>/result.json"
    )
    assert redact_evidence_text(str(resolved / "result.json"), replacements) == (
        "<path-0>/result.json"
    )


def test_recorder_with_relative_cwd_keeps_standalone_dots(
    repository_root: Path, tmp_path: Path
) -> None:
    # Regression: ``--cwd .`` made the literal ``.`` a replacement source, so records
    # stored ``(?:\\<path-0>[0-9]+)?`` and ``at + '<path-0>' + key`` as the program text.
    program = (
        "import os, pathlib, re; "
        "print(re.sub(r'(?:\\.[0-9]+)?$', '', '1.25') + '.' + 'key'); "
        "print('(done). next'); print('.'); print(os.environ['DOTTED']); "
        "print(pathlib.Path.cwd())"
    )
    command = [sys.executable, "-c", program, "."]
    completed = _record(
        repository_root,
        tmp_path,
        command,
        environment=["DOTTED=a . b"],
        relative_cwd=True,
    )

    assert completed.returncode == 0
    stored = (tmp_path / "record.json").read_text()
    record = json.loads(stored)
    assert record["argv"][1:] == command[1:]
    assert record["command"].endswith(shlex.join(command[1:]))
    assert record["environment"] == {"DOTTED": "a . b"}
    assert record["output"] == "1.key\n(done). next\n.\na . b\n<path-0>\n"
    assert record["cwd"] == "<path-0>"
    assert str(tmp_path) not in stored
    assert str(tmp_path.resolve()) not in stored


def test_redact_ignores_relative_spelling_but_redacts_its_resolved_path(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    here = Path.cwd()

    for relative in (Path("."), Path("results")):
        replacements = standard_path_replacements([relative])
        text = "at + '.' + key; (see results). ./results/run.json"
        assert redact_evidence_text(text, replacements) == text
        assert redact_evidence_text(str(here / relative / "run.json"), replacements) == (
            "<path-0>/run.json"
        )


def test_recorder_redacts_public_environment_values(repository_root: Path, tmp_path: Path) -> None:
    completed = _record(
        repository_root,
        tmp_path,
        [sys.executable, "-c", "print('ok')"],
        environment=["DEMO_SECRET=local_demo_postgres_only"],
    )

    assert completed.returncode == 0
    record = json.loads((tmp_path / "record.json").read_text())
    assert record["environment"] == {"DEMO_SECRET": "<redacted-local-demo-secret>"}


def test_recorder_does_not_publish_inherited_environment(
    repository_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIXELGYM_INHERITED_TEST_SECRET", "not-for-the-record")
    completed = _record(
        repository_root,
        tmp_path,
        [
            sys.executable,
            "-c",
            "import os; assert os.environ['PIXELGYM_INHERITED_TEST_SECRET']",
        ],
    )

    assert completed.returncode == 0
    record = json.loads((tmp_path / "record.json").read_text())
    assert record["environment"] == {}
    assert "not-for-the-record" not in completed.stdout


def test_recorder_preserves_failure_output_and_exit_status(
    repository_root: Path, tmp_path: Path
) -> None:
    completed = _record(
        repository_root,
        tmp_path,
        [sys.executable, "-c", "import sys; print('raw failure'); raise SystemExit(7)"],
    )

    assert completed.returncode == 7
    record = json.loads((tmp_path / "record.json").read_text())
    assert record["exit_status"] == 7
    assert record["output"] == "raw failure\n"
