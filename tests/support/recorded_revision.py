"""Materialise frozen evidence inputs from a recorded Git revision.

D5.9 plan tests reproduce checked-in artifacts from the bytes at their recorded
source revision rather than from the live checkout, so later edits to
``pixelgym/`` do not break byte verification of frozen evidence.
"""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# The frozen D5.9 successor resolves recorded revisions through this helper; the
# redirect below is applied by dotted path so this module never imports it.
_SUCCESSOR_GIT_OUTPUT = "pixelgym.grounding.v5.d59_haiku_retry_successor._git_output"


def git_output(root: Path, *arguments: str) -> bytes:
    """Run ``git`` in ``root`` with inherited ``GIT_*`` variables removed."""
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            env=environment,
        ).stdout
    except subprocess.CalledProcessError as error:
        raise ValueError("source revision or path is unavailable") from error


def archive_recorded_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: str, paths: tuple[str, ...]
) -> Path:
    """Extract ``paths`` at ``revision`` into ``tmp_path``.

    Git queries (``cat-file``, ``show``, ``ls-tree``) are redirected to the
    repository object store so the recorded revision still resolves, while file
    bytes are read from the extracted archive instead of the working tree.
    """
    archive = git_output(ROOT, "archive", revision, "--", *paths)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(tmp_path, filter="data")
    monkeypatch.setattr(_SUCCESSOR_GIT_OUTPUT, lambda _root, *args: git_output(ROOT, *args))
    return tmp_path


def artifact_revision(path: str | Path) -> str:
    """Return the commit that last wrote the checked-in artifact at ``path``.

    For frozen artifacts that do not store their own source revision, the commit
    that wrote them is the revision whose sources they were generated from.
    """
    output = git_output(ROOT, "log", "-1", "--format=%H", "--", str(path))
    revision = output.decode("ascii").strip()
    if len(revision) != 40:
        raise ValueError(f"{path} has no recorded revision in the local history")
    return revision
