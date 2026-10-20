"""Optional Git + Git LFS round trip using a filesystem remote, without credentials or sockets."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pixelgym.grounding.v5.retention import create_archive
from pixelgym.grounding.v5.retention_lfs import _GitStore, pack_archive, unpack_archive

pytestmark = pytest.mark.skipif(
    os.environ.get("PIXELGYM_RUN_LFS_INTEGRATION") != "1",
    reason="set PIXELGYM_RUN_LFS_INTEGRATION=1 with git and git-lfs installed",
)


def test_real_git_lfs_uses_new_cache_and_can_restore_pinned_previous_version(tmp_path: Path):
    subprocess.run(["git", "lfs", "version"], check=True, capture_output=True)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    run = tmp_path / "run"
    run.mkdir()
    (run / "evidence.txt").write_text("original evidence")
    archive = tmp_path / "archive"
    digest = create_archive(run, archive)["manifest_digest"]
    packed = tmp_path / "archive.tar.gz"
    entry = pack_archive(archive, packed, expected_digest=digest)
    writer = _GitStore(remote.as_uri(), tmp_path / "writer")
    commit = writer.publish(packed, digest, entry)
    shutil.rmtree(writer.root)
    shutil.rmtree(archive)
    packed.unlink()
    reader = _GitStore(remote.as_uri(), tmp_path / "reader")
    assert not (reader.root / ".git" / "lfs" / "objects").exists()
    fetched, actual = reader.download(commit, digest)
    assert actual == entry
    unpacked = tmp_path / "unpacked"
    unpack_archive(fetched, unpacked, expected_digest=digest)
    assert (unpacked / "files" / "evidence.txt").read_text() == "original evidence"
    # Repeated backup reuses the committed object, rather than appending a binary version.
    second = _GitStore(remote.as_uri(), tmp_path / "second-writer")
    assert second.publish(fetched, digest, entry) == commit
    (run / "evidence.txt").write_text("successor evidence")
    successor = tmp_path / "successor"
    second_digest = create_archive(run, successor)["manifest_digest"]
    second_pack = tmp_path / "successor.tar.gz"
    second_entry = pack_archive(successor, second_pack, expected_digest=second_digest)
    third = _GitStore(remote.as_uri(), tmp_path / "third-writer")
    next_commit = third.publish(second_pack, second_digest, second_entry)
    assert next_commit != commit
    pinned = _GitStore(remote.as_uri(), tmp_path / "pinned-reader")
    old_pack, _ = pinned.download(commit, digest)
    old_archive = tmp_path / "old-archive"
    unpack_archive(old_pack, old_archive, expected_digest=digest)
    assert (old_archive / "files" / "evidence.txt").read_text() == "original evidence"
