"""Verify closed D5.8 evidence using its executed source revision in a temporary tree."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = (
    "grounding-v5-d58-focus-calibration",
    "grounding-v5-d58-focus-continuation",
    "grounding-v5-d58-reliable-diagnostic",
    "grounding-v5-d58-reliable-continuation",
    "grounding-v5-d58-reliable-extension",
    "grounding-v5-d58-owner-budget-continuation",
    "grounding-v5-d58-owner-budget",
)


def verify(name: str, journal: Path | None) -> dict[str, object]:
    if name not in ARTIFACTS:
        raise ValueError("unsupported closed evidence package")
    accounting_only = name == "grounding-v5-d58-owner-budget"
    filename = "reconciliation.json" if accounting_only else "execution-plan.json"
    plan = json.loads((ROOT / "artifacts" / name / filename).read_text())
    revision = plan["driver_code_revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("executed revision must be a complete commit ID")
    archive = subprocess.run(
        ["git", "archive", revision, "pixelgym", "scripts", "pyproject.toml", "requirements"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="d58-verification-") as directory:
        tree = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(tree, filter="data")
        # Historical drivers can name predecessors different from today's source.
        # Copy the complete D5.8 evidence family, including admission dependencies.
        for source in sorted((ROOT / "artifacts").glob("grounding-v5-d58-*")):
            if source.is_dir():
                shutil.copytree(source, tree / "artifacts" / source.name)
        analyzer_package = "grounding-v5-d58-owner-budget-continuation" if accounting_only else name
        successor = (
            ROOT
            / "scripts"
            / (
                "verify_d58_"
                + analyzer_package.removeprefix("grounding-v5-d58-").replace("-", "_")
                + ".py"
            )
        )
        analyzer = tree / "artifacts" / analyzer_package / "analyze.py"
        if successor.is_file():
            analyzer = tree / "scripts" / successor.name
            shutil.copy2(successor, analyzer)
        command = [sys.executable, str(analyzer)]
        if accounting_only:
            command.append("--verify-reconciliation")
        if journal:
            command += ["--journal", str(journal.resolve())]
        environment = {
            k: v for k, v in os.environ.items() if k not in {"OPENROUTER_API_KEY", "PYTHONOPTIMIZE"}
        }
        environment.update(PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            command, cwd=tree, env=environment, capture_output=True, text=True, check=False
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
        return {"executed_revision": revision, "verification": json.loads(result.stdout)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", choices=ARTIFACTS)
    parser.add_argument("--journal", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.artifact, args.journal), sort_keys=True))


if __name__ == "__main__":
    main()
