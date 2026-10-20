"""Evidence loss, corruption, unsafe paths, WAL capture, and restore preconditions."""

from __future__ import annotations

import json
import os
import shutil
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
        file.write_bytes(b"x" * file.stat().st_size)
    elif damage == "missing":
        file.unlink()
    elif damage == "extra":
        (chosen / "unlisted").write_text("unlisted")
    else:
        manifest = json.loads((chosen / "manifest.json").read_bytes())
        manifest["source_fingerprint"] = "sha256:" + "0" * 64
        (chosen / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    message = {
        "corrupt": "archive file digest or size mismatch",
        "missing": "missing or unlisted files",
        "extra": "missing or unlisted files",
        "manifest": "archive manifest digest mismatch",
    }[damage]
    with pytest.raises(ValueError, match=message):
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
    with pytest.raises(ValueError, match="source changed since capture"):
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
    with pytest.raises(ValueError, match="invalid or duplicate archive path"):
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


def test_unreadable_directory_cannot_be_silently_omitted(tmp_path, monkeypatch) -> None:
    original = source(tmp_path)
    restricted = original / "restricted"
    restricted.mkdir()
    (restricted / "journal.json").write_text("must retain")
    scandir = os.scandir

    def deny_directory(path):
        if Path(path) == restricted:
            raise PermissionError("fixture: evidence directory is unreadable")
        return scandir(path)

    monkeypatch.setattr(os, "scandir", deny_directory)
    with pytest.raises(PermissionError, match="unreadable"):
        retention.create_archive(original, tmp_path / "archive")
    assert not (tmp_path / "archive").exists()
    assert (restricted / "journal.json").read_text() == "must retain"


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
    assert sorted(p.name for p in tmp_path.iterdir()) == ["attempts.sqlite"]
    with pytest.raises(sqlite3.OperationalError):
        V5AttemptJournal(tmp_path / "missing" / "attempts.sqlite", read_only=True)
    assert not (tmp_path / "missing").exists()


def test_read_only_journal_keeps_committed_wal_visible(tmp_path: Path) -> None:
    path = tmp_path / "attempts.sqlite"
    with closing(V5AttemptJournal(path)) as writer:
        writer._connection.execute("PRAGMA wal_autocheckpoint=0")
        writer.put_object("fixture", b"committed in WAL")
        assert path.with_name(path.name + "-wal").stat().st_size > 0
        with closing(V5AttemptJournal(path, read_only=True)) as reader:
            assert reader.integrity_report()["object_count"] == 1


def test_wrong_expected_manifest_digest_is_refused_directly(tmp_path: Path) -> None:
    _, archive, _, _ = archive_pair(tmp_path)
    with pytest.raises(ValueError, match="archive manifest digest mismatch"):
        retention.verify_archive(archive, expected_digest="sha256:" + "0" * 64)


def test_same_size_sqlite_damage_reaches_integrity_check(tmp_path: Path) -> None:
    original = source(tmp_path)
    with closing(sqlite3.connect(original / "events.sqlite")) as db:
        db.execute("CREATE TABLE evidence(value TEXT)")
        db.execute("INSERT INTO evidence VALUES ('stored')")
        db.commit()
    archive = tmp_path / "archive"
    retention.create_archive(original, archive)
    file = archive / "files" / "events.sqlite"
    data = bytearray(file.read_bytes())
    data[100] = 255  # Invalid first B-tree page type, with the SQLite header and size intact.
    file.write_bytes(data)
    manifest = json.loads((archive / "manifest.json").read_bytes())
    entry = next(e for e in manifest["files"] if e["path"] == "events.sqlite")
    entry["sha256"] = retention._hash(file)
    (archive / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        retention.verify_archive(archive, expected_digest=content_digest(manifest))


def test_cleanup_detects_source_change_during_restore_and_releases_each_copy(tmp_path, monkeypatch):
    original, archive, backup, digest = archive_pair(tmp_path)
    restore = retention.restore_archive
    destinations = []

    def mutate(copy, destination, *, expected_digest):
        if destinations:
            assert not destinations[-1].exists()
        result = restore(copy, destination, expected_digest=expected_digest)
        destinations.append(destination)
        (original / "summary.json").write_text("changed during cleanup")
        return result

    monkeypatch.setattr(retention, "restore_archive", mutate)
    with pytest.raises(ValueError, match="source changed during cleanup check"):
        retention.check_cleanup(original, archive, backup, expected_digest=digest)
    assert len(destinations) == 2 and all(not p.exists() for p in destinations)


def test_inspection_after_capture_does_not_invalidate_cleanup(tmp_path):
    original = source(tmp_path)
    path = original / "attempts.sqlite"
    with closing(V5AttemptJournal(path)) as journal:
        journal.put_object("fixture", b"stored")
    archive, backup = tmp_path / "archive", tmp_path / "backup"
    digest = retention.create_archive(original, archive)["manifest_digest"]
    retention.backup_archive(archive, backup, expected_digest=digest)
    with closing(V5AttemptJournal(path, read_only=True)) as journal:
        assert journal.integrity_report()["object_count"] == 1
    assert retention.check_cleanup(original, archive, backup, expected_digest=digest)[
        "cleanup_precondition_satisfied"
    ]


@pytest.mark.parametrize("operation", ["backup", "cleanup"])
def test_private_backup_and_cleanup_refuse_a_git_checkout(tmp_path, operation):
    original, archive, backup, digest = archive_pair(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    with pytest.raises(ValueError, match="outside Git checkouts"):
        if operation == "backup":
            retention.backup_archive(archive, checkout / "copy", expected_digest=digest)
        else:
            shutil.copytree(archive, checkout / "copy")
            retention.check_cleanup(original, checkout / "copy", backup, expected_digest=digest)


def test_parent_traversal_cannot_hide_a_symlink_prefix(tmp_path):
    (tmp_path / "target").mkdir()
    (tmp_path / "linked").symlink_to(tmp_path / "target", target_is_directory=True)
    with pytest.raises(ValueError, match="parent traversal"):
        retention._no_links(tmp_path / "linked" / ".." / "source")


def test_retention_cli_round_trip_and_existing_backup_preflight(tmp_path, monkeypatch, capsys):
    from scripts.retain_v5_evidence import main

    original = source(tmp_path)
    archive, backup = tmp_path / "archive", tmp_path / "backup"

    def command(*arguments):
        monkeypatch.setattr("sys.argv", ["retain_v5_evidence", *map(str, arguments)])
        main()
        return json.loads(capsys.readouterr().out)

    captured = command("capture", "--source", original, "--archive", archive, "--backup", backup)
    digest = captured["archive"]["manifest_digest"]
    assert command("verify", "--archive", archive, "--digest", digest)["file_count"] == 1
    assert (
        command(
            "restore",
            "--archive",
            archive,
            "--digest",
            digest,
            "--destination",
            tmp_path / "restored",
        )["restored_file_count"]
        == 1
    )
    receipt = tmp_path / "receipt.json"
    assert command(
        "check-cleanup",
        "--source",
        original,
        "--archive",
        archive,
        "--backup",
        backup,
        "--digest",
        digest,
        "--receipt",
        receipt,
    )["cleanup_precondition_satisfied"]
    with pytest.raises(FileExistsError, match="backup already exists"):
        command(
            "capture", "--source", original, "--archive", tmp_path / "unused", "--backup", backup
        )
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("location", ["source", "archive", "backup", "existing"])
def test_cleanup_cli_refuses_invalid_receipt_before_restoring(tmp_path, monkeypatch, location):
    from scripts import retain_v5_evidence as command

    original, archive, backup, digest = archive_pair(tmp_path)
    receipt = {"source": original, "archive": archive, "backup": backup, "existing": tmp_path}[
        location
    ] / "receipt.json"
    if location == "existing":
        receipt.write_text("preserve me")

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid receipt reached restore checks")

    monkeypatch.setattr(command, "check_cleanup", forbidden)
    monkeypatch.setattr(
        "sys.argv",
        [
            "retain_v5_evidence",
            "check-cleanup",
            "--source",
            str(original),
            "--archive",
            str(archive),
            "--backup",
            str(backup),
            "--digest",
            digest,
            "--receipt",
            str(receipt),
        ],
    )
    with pytest.raises((ValueError, FileExistsError), match="disjoint|receipt already exists"):
        command.main()
    if location != "existing":
        assert not receipt.exists()
