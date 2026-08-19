#!/usr/bin/env python3
"""Capture raw clean-install command evidence without issuing a verdict."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from pixelgym.evidence_redaction import (
    PathReplacement,
    indexed_path_replacements,
    redact_evidence_text,
)

SCHEMA_VERSION = "pixelgym-day3-clean-install-evidence-v1"
REDACTION_LEGEND = {
    "path-0": "repository root",
    "path-1": "home directory",
    "path-2": "system temporary directory",
}


def _run(
    command: list[str], *, cwd: Path, path_replacements: list[PathReplacement]
) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    return {
        "command": [redact_evidence_text(argument, path_replacements) for argument in command],
        "exit_code": completed.returncode,
        "duration_seconds": time.monotonic() - started,
        "output": redact_evidence_text(completed.stdout, path_replacements),
    }


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    output_path = repository_root / "artifacts/day-3/release/clean-install.json"
    python = shutil.which("python3.12")
    if python is None:
        raise SystemExit("python3.12 was not found on PATH")
    git_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    worktree_status_before = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    records = []
    with tempfile.TemporaryDirectory(prefix="pixelgym-day3-release-") as temporary:
        path_replacements = indexed_path_replacements(
            [repository_root, Path.home(), Path(tempfile.gettempdir())]
        )
        venv_path = Path(temporary) / ".venv"
        records.append(
            _run(
                [python, "-m", "venv", str(venv_path)],
                cwd=repository_root,
                path_replacements=path_replacements,
            )
        )
        venv_python = venv_path / "bin" / "python"
        if records[-1]["exit_code"] == 0:
            records.append(
                _run(
                    [str(venv_python), "-m", "pip", "install", "-e", ".[dev]"],
                    cwd=repository_root,
                    path_replacements=path_replacements,
                )
            )
        commands = [
            [str(venv_python), "-m", "pytest", "tests/", "-q"],
            [str(venv_python), "scripts/golden_trajectory.py", "check"],
            [str(venv_python), "scripts/demo_fake_backend.py", "--seed", "7"],
        ]
        if records and records[-1]["exit_code"] == 0:
            for command in commands:
                records.append(
                    _run(command, cwd=repository_root, path_replacements=path_replacements)
                )
                if records[-1]["exit_code"] != 0:
                    break
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "gate": "D3.11",
        "verdict": None,
        "verdict_owner": "project owner",
        "redaction": REDACTION_LEGEND,
        "repository_commit": git_commit,
        "worktree_status_before": worktree_status_before,
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python3_12_executable": redact_evidence_text(python, path_replacements),
            "python3_12_version": subprocess.run(
                [python, "--version"], capture_output=True, text=True, check=True
            ).stdout.strip(),
        },
        "commands": records,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
