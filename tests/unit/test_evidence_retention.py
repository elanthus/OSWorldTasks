"""Evidence loss, corruption, unsafe paths, WAL capture, and restore preconditions."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import retention
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.serialization import canonical_json_bytes


def source(tmp_path: Path) -> Path:
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "summary.json").write_text('{"completed": 1}')
    return directory


def archive_pair(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    original = source(tmp_path)
    archive, backup = tmp_path / "archive", tmp_path / "backup"
    digest = retention.create_archive(original, archive)["manifest_digest"]
    retention.backup_archive(archive, backup, expected_digest=digest)
    return original, archive, backup, digest


def test_cleanup_restores_both_copies_without_deleting_source(tmp_path: Path) -> None:
    original, archive, backup, digest = archive_pair(tmp_path)
    result = retention.check_cleanup(original, archive, backup, expected_digest=digest)
    assert result["cleanup_precondition_satisfied"] is True
    assert result["deleted_source"] is False
    assert len(result["restored_copies"]) == 2
    assert all(r["restored_file_count"] == 1 for r in result["restored_copies"])
    assert (original / "summary.json").read_text() == '{"completed": 1}'
    assert not archive.stat().st_mode & 0o077
    assert not (archive / "files" / "summary.json").stat().st_mode & 0o077


def test_sqlite_capture_includes_committed_wal_and_excludes_sidecars(tmp_path: Path) -> None:
    original = source(tmp_path)
    path = original / "attempts.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('committed-in-wal')")
        connection.commit()
        assert path.with_name("attempts.sqlite-wal").stat().st_size > 0
        archive = tmp_path / "archive"
        result = retention.create_archive(original, archive)
    restored = tmp_path / "restored"
    retention.restore_archive(archive, restored, expected_digest=result["manifest_digest"])
    assert not list(restored.glob("*-wal"))
    with closing(sqlite3.connect(restored / "attempts.sqlite")) as connection:
        assert connection.execute("SELECT value FROM evidence").fetchall() == [
            ("committed-in-wal",)
        ]


@pytest.mark.parametrize("copy", ["archive", "backup"])
@pytest.mark.parametrize("damage", ["corrupt", "missing", "extra", "manifest"])
def test_cleanup_refuses_damaged_copies(tmp_path: Path, copy: str, damage: str) -> None:
    original, archive, backup, digest = archive_pair(tmp_path)
    chosen = {"archive": archive, "backup": backup}[copy]
    file = chosen / "files" / "summary.json"
    if damage == "corrupt":
        file.write_text("corrupt")
    elif damage == "missing":
        file.unlink()
    elif damage == "extra":
        (chosen / "unlisted").write_text("unlisted")
    else:
        manifest = json.loads((chosen / "manifest.json").read_bytes())
        manifest["source_fingerprint"] = "sha256:" + "0" * 64
        (chosen / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    with pytest.raises(ValueError):
        retention.check_cleanup(original, archive, backup, expected_digest=digest)
    assert original.is_dir()


@pytest.mark.parametrize("change", ["edit", "add", "remove", "non_sqlite_shm"])
def test_cleanup_refuses_changed_source(tmp_path: Path, change: str) -> None:
    original, archive, backup, digest = archive_pair(tmp_path)
    if change == "edit":
        (original / "summary.json").write_text("changed")
    elif change == "add":
        (original / "new.json").write_text("new")
    elif change == "non_sqlite_shm":
        (original / "new-shm").write_text("unarchived evidence")
    else:
        (original / "summary.json").unlink()
    with pytest.raises(ValueError, match="source changed"):
        retention.check_cleanup(original, archive, backup, expected_digest=digest)


def test_source_changes_during_capture_leave_no_complete_archive(tmp_path, monkeypatch) -> None:
    original = source(tmp_path)
    snapshot = retention._snapshot

    def mutate(source, target):
        kind = snapshot(source, target)
        source.write_text("changed during capture")
        return kind

    monkeypatch.setattr(retention, "_snapshot", mutate)
    archive = tmp_path / "archive"
    with pytest.raises(ValueError, match="source changed during capture"):
        retention.create_archive(original, archive)
    assert not archive.exists()


@pytest.mark.parametrize("unsafe", ["../escape", "/absolute", "a/../escape", "a//b", "a\\b", "."])
def test_restore_refuses_unsafe_manifest_paths(tmp_path: Path, unsafe: str) -> None:
    _original, archive, _backup, _digest = archive_pair(tmp_path)
    manifest = json.loads((archive / "manifest.json").read_bytes())
    manifest["files"][0]["path"] = unsafe
    (archive / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    with pytest.raises(ValueError):
        retention.restore_archive(
            archive, tmp_path / "restored", expected_digest=content_digest(manifest)
        )
    assert not (tmp_path / "restored").exists()


def test_restore_refuses_duplicate_entries(tmp_path: Path) -> None:
    _original, archive, _backup, _digest = archive_pair(tmp_path)
    manifest = json.loads((archive / "manifest.json").read_bytes())
    manifest["files"] *= 2
    (archive / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    with pytest.raises(ValueError, match="duplicate"):
        retention.verify_archive(archive)


@pytest.mark.parametrize("location", ["source", "archive", "parent"])
def test_symlinks_are_refused(tmp_path: Path, location: str) -> None:
    original = source(tmp_path)
    if location == "source":
        (original / "link").symlink_to(original / "summary.json")
    elif location == "parent":
        (tmp_path / "linked").symlink_to(original, target_is_directory=True)
        original = tmp_path / "linked"
    else:
        (tmp_path / "archive").symlink_to(original, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink|regular"):
        retention.create_archive(original, tmp_path / "archive")


def test_private_archives_refuse_checkouts_and_nested_paths(tmp_path: Path) -> None:
    original = source(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: example")
    with pytest.raises(ValueError, match="outside Git"):
        retention.create_archive(original, checkout / "archive")
    with pytest.raises(ValueError, match="disjoint"):
        retention.create_archive(original, original / "archive")


@pytest.mark.parametrize("operation", ["capture", "restore", "backup"])
def test_existing_destinations_are_never_overwritten(tmp_path: Path, operation: str) -> None:
    original, archive, backup, digest = archive_pair(tmp_path)
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep").write_text("user evidence")
    with pytest.raises(FileExistsError):
        if operation == "capture":
            retention.create_archive(original, target)
        elif operation == "restore":
            retention.restore_archive(archive, target, expected_digest=digest)
        else:
            retention.backup_archive(backup, target, expected_digest=digest)
    assert (target / "keep").read_text() == "user evidence"


def test_corrupt_sqlite_capture_fails_without_publishing_archive(tmp_path: Path) -> None:
    original = source(tmp_path)
    (original / "attempts.sqlite").write_text("not a database")
    with pytest.raises(sqlite3.DatabaseError):
        retention.create_archive(original, tmp_path / "archive")
    assert not (tmp_path / "archive").exists()


def test_sqlite_rollback_journal_requires_recovery_before_capture(tmp_path: Path) -> None:
    original = source(tmp_path)
    with closing(sqlite3.connect(original / "attempts.sqlite")) as db:
        db.execute("CREATE TABLE evidence(value TEXT)")
    (original / "attempts.sqlite-journal").write_bytes(b"unresolved rollback")
    with pytest.raises(ValueError, match="rollback journal"):
        retention.create_archive(original, tmp_path / "archive")


def test_special_files_and_orphan_sidecars_are_refused(tmp_path: Path) -> None:
    original = source(tmp_path)
    os.mkfifo(original / "pipe")
    with pytest.raises(ValueError, match="regular"):
        retention.create_archive(original, tmp_path / "archive")
    (original / "pipe").unlink()
    (original / "missing.sqlite-wal").write_bytes(b"orphan")
    with pytest.raises(ValueError, match="orphan"):
        retention.create_archive(original, tmp_path / "archive")


def test_non_sqlite_sidecars_cannot_be_silently_discarded(tmp_path: Path) -> None:
    original = source(tmp_path)
    (original / "summary.json-shm").write_bytes(b"evidence, not a SQLite index")
    with pytest.raises(ValueError, match="orphan"):
        retention.create_archive(original, tmp_path / "archive")


def test_read_only_journal_never_creates_or_migrates_evidence(tmp_path: Path) -> None:
    path = tmp_path / "attempts.sqlite"
    with closing(V5AttemptJournal(path)) as journal:
        journal.put_object("fixture", b"stored")
    before = path.read_bytes()
    with closing(V5AttemptJournal(path, read_only=True)) as journal:
        assert journal.integrity_report()["object_count"] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            journal.put_object("fixture", b"refused")
    assert path.read_bytes() == before
    with pytest.raises(sqlite3.OperationalError):
        V5AttemptJournal(tmp_path / "missing" / "attempts.sqlite", read_only=True)
    assert not (tmp_path / "missing").exists()
