"""The full-calibration verifier fails closed under python -O."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("corruption", ["none", "hash", "report"])
def test_public_verifier_under_optimization(tmp_path: Path, corruption: str) -> None:
    directory = tmp_path / "bundle"
    original = ROOT / "artifacts/grounding-v5-d58-full-calibration"
    shutil.copytree(original, directory, ignore=shutil.ignore_patterns("__pycache__"))
    if corruption != "none":
        with (directory / "report.md").open("a") as stream:
            stream.write("\ncorrupted report\n")
    code = "from pathlib import Path\nfrom scripts import verify_grounding_v5_full_calibration as v\nimport json\n"
    code += f"v.DIRECTORY = Path({str(directory)!r})\n"
    if corruption == "report":
        code += "(v.DIRECTORY / 'files.json').write_text(json.dumps(v.hashes()))\n"
    code += "v.verify_public()\n"
    result = subprocess.run(
        [sys.executable, "-O", "-c", code], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if corruption == "none":
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "ValueError" in result.stderr
        assert (
            "hashes differ" if corruption == "hash" else "report does not match"
        ) in result.stderr


def test_journal_verifier_rejects_wrong_amendment_under_optimization(tmp_path: Path) -> None:
    journal = tmp_path / "present.sqlite"
    journal.touch()
    code = (
        "from pathlib import Path\nfrom scripts import verify_grounding_v5_full_calibration as v\n"
    )
    code += f"v.JOURNAL = Path({str(journal)!r})\n"
    code += "v.canonical_plan = lambda: {}\nv.execution_amendment = lambda *args: {'wrong': True}\nv.audit_journal()\n"
    result = subprocess.run(
        [sys.executable, "-O", "-c", code], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "ValueError: execution amendment differs from the approved record" in result.stderr
