"""Materialise frozen evidence inputs from a recorded Git revision.

D5.9 plan tests reproduce checked-in artifacts from the bytes at their recorded
source revision rather than from the live checkout, so later edits to
``pixelgym/`` do not break byte verification of frozen evidence.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import d59_haiku_retry_successor as successor

ROOT = Path(__file__).resolve().parents[2]


def archive_recorded_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: str, paths: tuple[str, ...]
) -> Path:
    """Extract ``paths`` at ``revision`` into ``tmp_path``.

    Git queries (``cat-file``, ``show``, ``ls-tree``) are redirected to the
    repository object store so the recorded revision still resolves, while file
    bytes are read from the extracted archive instead of the working tree.
    """
    archive = successor._git_output(ROOT, "archive", revision, "--", *paths)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(tmp_path, filter="data")
    original = successor._git_output
    monkeypatch.setattr(successor, "_git_output", lambda _root, *args: original(ROOT, *args))
    return tmp_path


def artifact_revision(path: str | Path) -> str:
    """Return the commit that last wrote the checked-in artifact at ``path``.

    For frozen artifacts that do not store their own source revision, the commit
    that wrote them is the revision whose sources they were generated from.
    """
    output = successor._git_output(ROOT, "log", "-1", "--format=%H", "--", str(path))
    revision = output.decode("ascii").strip()
    if len(revision) != 40:
        raise ValueError(f"{path} has no recorded revision in the local history")
    return revision
