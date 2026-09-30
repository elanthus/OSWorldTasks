"""Shared filesystem and Git helpers in pixelgym._io."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from pixelgym._io import atomic_write_json, require_clean_worktree, sha256_file


def test_sha256_file_known_value(tmp_path: Path) -> None:
    path = tmp_path / "abc.txt"
    path.write_bytes(b"abc")
    assert sha256_file(path) == ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


def test_sha256_file_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty"
    path.write_bytes(b"")
    assert sha256_file(path) == ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


def test_atomic_write_json_writes_sorted_json(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    atomic_write_json(path, {"b": 1, "a": [2]})
    assert path.read_text() == '{\n  "a": [\n    2\n  ],\n  "b": 1\n}\n'
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.json"]


def test_atomic_write_json_unserializable_leaves_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    path.write_text("original")
    with pytest.raises(TypeError):
        atomic_write_json(path, {"bad": object()})
    assert path.read_text() == "original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.json"]


def test_atomic_write_json_failed_rename_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "out.json"

    def failing_replace(source: object, destination: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_json(path, {"a": 1})
    assert list(tmp_path.iterdir()) == []


def test_atomic_write_json_failed_rename_preserves_previous_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "out.json"
    atomic_write_json(path, {"version": 1})
    before = path.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        atomic_write_json(path, {"version": 2})
    assert path.read_bytes() == before
    assert json.loads(before) == {"version": 1}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.json"]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    _git(tmp_path, "config", "user.name", "fixture")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "tracked.txt").write_text("one\n")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-q", "-m", "fixture")
    return tmp_path


def test_require_clean_worktree_accepts_clean_repository(repository: Path) -> None:
    require_clean_worktree(repository)


def test_require_clean_worktree_ignores_untracked_files(repository: Path) -> None:
    (repository / "scratch.txt").write_text("untracked\n")
    require_clean_worktree(repository)


def test_require_clean_worktree_rejects_modified_tracked_file(repository: Path) -> None:
    (repository / "tracked.txt").write_text("two\n")
    with pytest.raises(RuntimeError, match="commit first: .*tracked.txt"):
        require_clean_worktree(repository, message="commit first")


def test_require_clean_worktree_rejects_staged_change(repository: Path) -> None:
    (repository / "tracked.txt").write_text("two\n")
    _git(repository, "add", "tracked.txt")
    with pytest.raises(RuntimeError, match="tracked worktree must be clean"):
        require_clean_worktree(repository)


def test_require_clean_worktree_fails_outside_a_repository(tmp_path: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        require_clean_worktree(tmp_path / "missing")
