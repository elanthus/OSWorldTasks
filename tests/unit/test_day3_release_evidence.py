from __future__ import annotations

import re
from pathlib import Path

from scripts.run_day3_clean_install_check import _redact

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_PATH_PATTERN = re.compile(r"/(?:Users|home)/|/private/(?:tmp|var)/|/var/folders/")


def test_clean_install_redaction_uses_stable_path_placeholders(tmp_path: Path) -> None:
    repository = tmp_path / "account" / "project"
    home = tmp_path / "account"
    system_temporary = tmp_path / "system-temporary"
    value = (
        f"repo={repository}; home={home / 'Library/Caches/pip'}; "
        f"venv={system_temporary / 'run/.venv'}"
    )

    assert _redact(value, [repository, home, system_temporary]) == (
        "repo=<path-0>; home=<path-1>/Library/Caches/pip; "
        "venv=<path-2>/run/.venv"
    )
    assert _redact(
        str(home / ".pyenv/shims/python3.12"), [repository, home, system_temporary]
    ) == "<path-1>/.pyenv/shims/python3.12"


def test_checked_in_day3_release_evidence_has_no_local_absolute_paths() -> None:
    release_directory = REPOSITORY_ROOT / "artifacts/day-3/release"
    evidence_paths = sorted(release_directory.glob("*.json"))

    assert evidence_paths
    for path in evidence_paths:
        assert LOCAL_PATH_PATTERN.search(path.read_text()) is None, path
