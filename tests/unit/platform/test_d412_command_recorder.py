from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.record_d412_command import _redact


def _record(
    repository_root: Path,
    tmp_path: Path,
    command: list[str],
    *,
    environment: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(repository_root / "scripts/record_d412_command.py"),
            "--output",
            str(tmp_path / "record.json"),
            "--cwd",
            str(tmp_path),
            "--redact-path",
            str(repository_root),
            *(argument for value in environment or [] for argument in ("--env", value)),
            "--",
            *command,
        ],
        cwd=repository_root,
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

    assert _redact(str(literal / "result.json"), [literal]) == "<path-0>/result.json"
    assert _redact(str(resolved / "result.json"), [literal]) == "<path-0>/result.json"


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
