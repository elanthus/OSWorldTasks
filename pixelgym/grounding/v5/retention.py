"""Private, exclusive evidence archives and a restore-tested cleanup precondition.

Archive operations never execute payloads. SQLite's backup API includes committed
WAL data; copying only the main database file would silently lose recent events. Callers must
quiesce writers across the entire source directory before capture and cleanup verification.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.serialization import canonical_json_bytes

SCHEMA = "pixelgym-private-evidence-archive-v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _no_links(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ValueError("evidence paths must not contain symlinks")
    return absolute


def _checkout(path: Path) -> Path | None:
    return next((p for p in (path, *path.parents) if (p / ".git").exists()), None)


def _separate(*paths: Path) -> None:
    for index, path in enumerate(paths):
        for other in paths[index + 1 :]:
            if path == other or path in other.parents or other in path.parents:
                raise ValueError("source, archive, backup, and restore paths must be disjoint")


def _files(root: Path) -> dict[str, Path]:
    _no_links(root)
    if not root.is_dir():
        raise ValueError("evidence directory is missing")
    result = {}
    for directory, directories, names in os.walk(root, followlinks=False):
        for name in directories + names:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError("evidence must contain only regular files and directories")
            if stat.S_ISREG(mode):
                result[path.relative_to(root).as_posix()] = path
    return dict(sorted(result.items()))


def _hash(path: Path) -> str:
    # Stream large screenshot journals rather than loading them into memory.
    import hashlib

    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def source_fingerprint(source: Path) -> str:
    """Bind file names and bytes, including WALs, excluding SQLite's volatile SHM index."""
    files = _files(source)
    hashes = {}
    for name, path in files.items():
        if name.endswith("-shm") and name[:-4] in files and _is_sqlite(files[name[:-4]]):
            continue
        hashes[name] = _hash(path)
    return content_digest(hashes)


def write_private(path: Path, data: bytes) -> None:
    _no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with source.open("rb") as reader, target.open("xb") as writer:
        os.chmod(target, 0o600)
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())


def _sqlite_check(path: Path) -> None:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")


def _snapshot(source: Path, target: Path) -> str:
    with source.open("rb") as stream:
        sqlite_header = stream.read(16) == b"SQLite format 3\x00"
    if source.suffix not in {".sqlite", ".db"} and not sqlite_header:
        _copy(source, target)
        return "file"
    if source.with_name(source.name + "-journal").exists():
        raise ValueError("SQLite rollback journal present; recover and stop the database first")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private(target, b"")
    # A stopped WAL-mode database may have no sidecars. Immutable mode avoids creating
    # them merely by opening it; use the WAL-aware reader when a WAL is present.
    query = (
        "?mode=ro" if source.with_name(source.name + "-wal").exists() else "?mode=ro&immutable=1"
    )
    with (
        closing(sqlite3.connect(source.as_uri() + query, uri=True)) as reader,
        closing(sqlite3.connect(target)) as writer,
    ):
        reader.backup(writer)
        writer.execute("PRAGMA journal_mode=DELETE")
    _sqlite_check(target)
    with target.open("rb") as stream:
        os.fsync(stream.fileno())
    return "sqlite"


def _is_sqlite(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(16) == b"SQLite format 3\x00"


def create_archive(source: Path, archive: Path) -> dict[str, Any]:
    """Capture a stopped run outside its checkout; never overwrite an existing archive."""
    source, archive = _no_links(source), _no_links(archive)
    _separate(source, archive)
    if _checkout(archive) is not None:
        raise ValueError("private archives must be outside Git checkouts")
    before = source_fingerprint(source)
    files = _files(source)
    if not files:
        raise ValueError("cannot archive empty evidence")
    archive.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive.mkdir(mode=0o700)  # Exclusive reservation; a partial archive cannot verify.
    try:
        entries = []
        for name, path in files.items():
            if name.endswith(("-wal", "-shm")):
                base = name.rsplit("-", 1)[0]
                if base not in files or not _is_sqlite(files[base]):
                    raise ValueError("orphan SQLite sidecar")
                continue
            target = archive / "files" / name
            kind = _snapshot(path, target)
            entries.append(
                {"path": name, "sha256": _hash(target), "size": target.stat().st_size, "kind": kind}
            )
        if source_fingerprint(source) != before:
            raise ValueError("source changed during capture; quiesce writers and retry")
        manifest = {"schema_version": SCHEMA, "source_fingerprint": before, "files": entries}
        write_private(archive / "manifest.json", canonical_json_bytes(manifest))
        for directory, _, _ in os.walk(archive, topdown=False):
            _sync_directory(Path(directory))
        _sync_directory(archive.parent)
        return verify_archive(archive, expected_digest=content_digest(manifest))
    except BaseException:
        shutil.rmtree(archive)
        raise


def _manifest(archive: Path) -> dict[str, Any]:
    files = _files(archive)
    if "manifest.json" not in files:
        raise ValueError("archive manifest is missing")
    value = json.loads(files["manifest.json"].read_bytes())
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "source_fingerprint", "files"}
        or value["schema_version"] != SCHEMA
        or not isinstance(value["source_fingerprint"], str)
        or not _DIGEST.fullmatch(value["source_fingerprint"])
        or not isinstance(value["files"], list)
        or not value["files"]
    ):
        raise ValueError("invalid archive manifest")
    names = set()
    for entry in value["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size", "kind"}:
            raise ValueError("invalid archive file entry")
        name = entry["path"]
        if (
            not isinstance(name, str)
            or not name
            or "\\" in name
            or "\x00" in name
            or PurePosixPath(name).is_absolute()
            or ".." in PurePosixPath(name).parts
            or PurePosixPath(name).as_posix() != name
            or name in names
            or type(entry["size"]) is not int
            or entry["size"] < 0
            or not isinstance(entry["sha256"], str)
            or not _DIGEST.fullmatch(entry["sha256"])
            or entry["kind"] not in {"file", "sqlite"}
        ):
            raise ValueError("invalid or duplicate archive path or metadata")
        names.add(name)
    if set(files) != {"manifest.json", *("files/" + name for name in names)}:
        raise ValueError("archive contains missing or unlisted files")
    return value


def verify_archive(archive: Path, *, expected_digest: str | None = None) -> dict[str, Any]:
    archive = _no_links(archive)
    manifest = _manifest(archive)
    digest = content_digest(manifest)
    if expected_digest is not None and expected_digest != digest:
        raise ValueError("archive manifest digest mismatch")
    for entry in manifest["files"]:
        path = archive / "files" / entry["path"]
        if path.stat().st_size != entry["size"] or _hash(path) != entry["sha256"]:
            raise ValueError("archive file digest or size mismatch")
        if entry["kind"] == "sqlite":
            _sqlite_check(path)
    return {
        "manifest_digest": digest,
        "source_fingerprint": manifest["source_fingerprint"],
        "file_count": len(manifest["files"]),
        "bytes": sum(entry["size"] for entry in manifest["files"]),
    }


def restore_archive(archive: Path, destination: Path, *, expected_digest: str) -> dict[str, Any]:
    archive, destination = _no_links(archive), _no_links(destination)
    _separate(archive, destination)
    verified = verify_archive(archive, expected_digest=expected_digest)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.mkdir(mode=0o700)
    try:
        for entry in _manifest(archive)["files"]:
            _copy(archive / "files" / entry["path"], destination / entry["path"])
        expected = {e["path"]: e["sha256"] for e in _manifest(archive)["files"]}
        actual = {name: _hash(path) for name, path in _files(destination).items()}
        if expected != actual:
            raise ValueError("restored files do not match the archive")
        return {**verified, "restored_file_count": len(actual)}
    except BaseException:
        shutil.rmtree(destination)
        raise


def backup_archive(archive: Path, backup: Path, *, expected_digest: str) -> dict[str, Any]:
    archive, backup = _no_links(archive), _no_links(backup)
    _separate(archive, backup)
    if _checkout(backup) is not None:
        raise ValueError("private backups must be outside Git checkouts")
    verify_archive(archive, expected_digest=expected_digest)
    backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup.mkdir(mode=0o700)
    try:
        for name, path in _files(archive).items():
            _copy(path, backup / name)
        result = verify_archive(backup, expected_digest=expected_digest)
        for directory, _, _ in os.walk(backup, topdown=False):
            _sync_directory(Path(directory))
        _sync_directory(backup.parent)
        return result
    except BaseException:
        shutil.rmtree(backup)
        raise


def check_cleanup(
    source: Path, archive: Path, backup: Path, *, expected_digest: str
) -> dict[str, Any]:
    """Restore BOTH copies now and bind the current source. Never delete source evidence."""
    source, archive, backup = map(_no_links, (source, archive, backup))
    _separate(source, archive, backup)
    if _checkout(archive) is not None or _checkout(backup) is not None:
        raise ValueError("retained copies must be outside Git checkouts")
    before = source_fingerprint(source)
    restored = []
    with tempfile.TemporaryDirectory(prefix="pixelgym-restore-check-") as temporary:
        for index, copy in enumerate((archive, backup)):
            result = restore_archive(
                copy, Path(temporary).resolve() / str(index), expected_digest=expected_digest
            )
            if result["source_fingerprint"] != before:
                raise ValueError("source changed since capture; cleanup is refused")
            restored.append(result)
    if source_fingerprint(source) != before:
        raise ValueError("source changed during cleanup check")
    return {
        "schema_version": "pixelgym-evidence-cleanup-receipt-v1",
        "source_fingerprint": before,
        "manifest_digest": expected_digest,
        "restored_copies": restored,
        "cleanup_precondition_satisfied": True,
        "deleted_source": False,
    }
