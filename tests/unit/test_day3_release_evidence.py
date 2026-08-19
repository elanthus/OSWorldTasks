from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from pixelgym.evidence_redaction import indexed_path_replacements, redact_evidence_text
from scripts.run_day3_clean_install_check import REDACTION_LEGEND

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_PATH_PATTERN = re.compile(
    r"/(?:Users|home|tmp)/|/private/(?:tmp|var)/|/var/(?:tmp|folders)/"
)


def test_clean_install_redaction_uses_stable_path_placeholders(tmp_path: Path) -> None:
    repository = tmp_path / "account" / "project"
    home = tmp_path / "account"
    system_temporary = tmp_path / "system-temporary"
    value = (
        f"repo={repository}; home={home / 'Library/Caches/pip'}; "
        f"venv={system_temporary / 'run/.venv'}"
    )

    replacements = indexed_path_replacements([repository, home, system_temporary])
    assert redact_evidence_text(value, replacements) == (
        "repo=<path-0>; home=<path-1>/Library/Caches/pip; "
        "venv=<path-2>/run/.venv"
    )
    assert redact_evidence_text(str(home / ".pyenv/shims/python3.12"), replacements) == (
        "<path-1>/.pyenv/shims/python3.12"
    )


def test_checked_in_text_artifacts_have_no_local_absolute_paths() -> None:
    tracked = subprocess.run(
        ["git", "grep", "-Il", "-e", "", "--", "artifacts"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    evidence_paths = [REPOSITORY_ROOT / relative for relative in tracked.stdout.splitlines()]

    assert evidence_paths
    for path in evidence_paths:
        assert LOCAL_PATH_PATTERN.search(path.read_text()) is None, path


def test_day3_clean_install_evidence_explains_path_placeholders() -> None:
    release_directory = REPOSITORY_ROOT / "artifacts/day-3/release"
    evidence_paths = sorted(release_directory.glob("clean-install*.json"))

    expected_names = {
        "clean-install.json",
        "clean-install-sandbox-attempt.json",
        "clean-install-final-sandbox-attempt.json",
    }
    assert expected_names <= {path.name for path in evidence_paths}
    for path in evidence_paths:
        assert json.loads(path.read_text())["redaction"] == REDACTION_LEGEND
    release_observations = json.loads(
        (release_directory / "release-observations.json").read_text()
    )
    assert release_observations["clean_install_command_evidence"]["redaction"] == (
        REDACTION_LEGEND
    )
