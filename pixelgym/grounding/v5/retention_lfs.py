"""Private GitHub LFS transport for sealed evidence archives.

GitHub enforces the spending limit. Personal-account budgets have no public REST
endpoint, so an operator must record a recent UI observation before each transfer.
Neither a local size limit nor that observation changes the remote billing setting.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from pixelgym.grounding.v5.retention import (
    _checkout,
    _files,
    _hash,
    _no_links,
    _separate,
    restore_archive,
    source_fingerprint,
    verify_archive,
    write_private,
)
from pixelgym.serialization import canonical_json_bytes

MAX_ARCHIVE_BYTES = 512 * 1024**2
MAX_STORAGE_BYTES = 5 * 1024**3
TRANSFER_TIMEOUT_SECONDS = 900
CATALOG_SCHEMA = "pixelgym-lfs-catalog-v1"
BUDGET_SCHEMA = "pixelgym-lfs-zero-budget-observation-v1"
RECEIPT_SCHEMA = "pixelgym-lfs-restore-receipt-v1"
ATTRIBUTES = b"archives/*.tar.gz filter=lfs diff=lfs merge=lfs -text\n"
README = b"# Private PixelGym evidence\n\nSealed archives. No model execution.\n"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+\Z")


def check_budget(record: Path, repository: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Require a recent, repository-bound operator observation; never infer free capacity."""
    record = _no_links(record)
    if _checkout(record) is not None:
        raise ValueError("budget observation must stay outside Git checkouts")
    if record.stat().st_size > 16_384:
        raise ValueError("budget observation is too large")
    value = json.loads(record.read_bytes())
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != BUDGET_SCHEMA
        or value.get("repository") != repository
        or value.get("product") != "git_lfs"
        or type(value.get("budget_usd")) is not int
        or value["budget_usd"] != 0
        or value.get("stop_usage") is not True
        or value.get("scope") not in {"account", "repository"}
        or value.get("observed_via") != "github-billing-ui"
    ):
        raise ValueError("a matching Git LFS $0 budget with stop usage is required")
    try:
        observed = datetime.fromisoformat(value["observed_at_utc"])
        current = now or datetime.now(UTC)
        age = current - observed
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid budget observation time") from None
    if observed.utcoffset() != timedelta(0) or not timedelta(0) <= age <= timedelta(hours=24):
        raise ValueError("budget observation must be UTC and no more than 24 hours old")
    return value


def _environment() -> dict[str, str]:
    # Retain only GitHub authentication inputs and ordinary CLI runtime locations.
    allowed = ("PATH", "HOME", "USER", "SSH_AUTH_SOCK", "GH_CONFIG_DIR", "GH_TOKEN", "GITHUB_TOKEN")
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result.update(
        {
            "GH_HOST": "github.com",
            "GH_PROMPT_DISABLED": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "LC_ALL": "C",
        }
    )
    return result


def _stop_command(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        # Bound cleanup even if an unrelated detached process holds a pipe open.
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        process.wait(timeout=5)


def _run(arguments: list[str], *, cwd: Path | None = None, timeout_seconds: float = 180) -> str:
    process = None
    try:
        process = subprocess.Popen(
            arguments,
            cwd=cwd,
            env=_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, _stderr = process.communicate(timeout=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired):
        if process is not None:
            _stop_command(process)
        raise ValueError("GitHub/LFS command unavailable or timed out; no receipt issued") from None
    except BaseException:
        if process is not None:
            _stop_command(process)
        raise
    if process.returncode:
        # Credential-helper and remote output must never enter a public receipt or traceback.
        raise ValueError("GitHub/LFS command failed; source evidence is preserved")
    return stdout.decode("utf-8")


def _private_repository(repository: str) -> None:
    if not _REPOSITORY.fullmatch(repository) or repository.split("/")[1] in {".", ".."}:
        raise ValueError("repository must be an owner/name on github.com")
    metadata = json.loads(_run(["gh", "api", "repos/" + repository]))
    if (
        metadata.get("private") is not True
        or metadata.get("visibility") != "private"
        or metadata.get("full_name", "").lower() != repository.lower()
        or metadata.get("archived") is not False
        or metadata.get("disabled") is not False
        or metadata.get("fork") is not False
    ):
        raise ValueError("backup requires the exact active, private, non-fork repository")


def pack_archive(archive: Path, target: Path, *, expected_digest: str) -> dict[str, Any]:
    archive, target = map(_no_links, (archive, target))
    _separate(archive, target)
    before = verify_archive(archive, expected_digest=expected_digest)
    if before["bytes"] > MAX_ARCHIVE_BYTES:
        raise ValueError("archive exceeds the 512 MiB transfer limit")
    write_private(target, b"")
    # Fixed metadata makes repeated packaging independent of file timestamps and owners.
    with (
        target.open("wb") as stream,
        gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as zipped,
    ):
        with tarfile.open(fileobj=zipped, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for name, path in _files(archive).items():
                entry = tarfile.TarInfo(name)
                entry.size = path.stat().st_size
                entry.mode = 0o600
                with path.open("rb") as data:
                    tar.addfile(entry, data)
        stream.flush()
    if verify_archive(archive, expected_digest=expected_digest) != before:
        raise ValueError("archive changed during packaging")
    size = target.stat().st_size
    if size > MAX_ARCHIVE_BYTES:
        raise ValueError("packed archive exceeds the 512 MiB transfer limit")
    return {
        "path": "archives/" + expected_digest[7:] + ".tar.gz",
        "oid": _hash(target),
        "size": size,
    }


def unpack_archive(packed: Path, destination: Path, *, expected_digest: str) -> dict[str, Any]:
    packed, destination = map(_no_links, (packed, destination))
    _separate(packed, destination)
    if _checkout(destination) is not None:
        raise ValueError("private archives must be outside Git checkouts")
    destination.mkdir(mode=0o700, parents=True)
    total = 0
    names: set[str] = set()
    try:
        with tarfile.open(packed, mode="r|gz") as tar:
            for member in tar:
                name = member.name
                if (
                    not member.isreg()
                    or not name
                    or "\\" in name
                    or "\x00" in name
                    or PurePosixPath(name).is_absolute()
                    or ".." in PurePosixPath(name).parts
                    or PurePosixPath(name).as_posix() != name
                    or name in names
                    or member.size < 0
                    or len(names) >= 10_000
                ):
                    raise ValueError("unsafe or duplicate packed archive member")
                names.add(name)
                total += member.size
                if total > MAX_ARCHIVE_BYTES + 1024**2:
                    raise ValueError("unpacked archive exceeds the size limit")
                source = tar.extractfile(member)
                if source is None:
                    raise ValueError("packed archive member has no data")
                target = destination / name
                write_private(target, b"")
                with source, target.open("wb") as writer:
                    shutil.copyfileobj(source, writer)
        return verify_archive(destination, expected_digest=expected_digest)
    except BaseException:
        shutil.rmtree(destination)
        raise


def _catalog(root: Path) -> dict[str, Any]:
    def read_small(path: Path, limit: int) -> bytes:
        _no_links(path)
        if not path.is_file() or path.stat().st_size > limit:
            raise ValueError("missing or oversized private repository metadata")
        return path.read_bytes()

    if (
        read_small(root / ".gitattributes", 4096) != ATTRIBUTES
        or read_small(root / "README.md", 4096) != README
    ):
        raise ValueError("unexpected private evidence repository configuration")
    value = json.loads(read_small(root / "catalog.json", 1024**2))
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "archives"}
        or value["schema_version"] != CATALOG_SCHEMA
        or not isinstance(value["archives"], dict)
    ):
        raise ValueError("invalid LFS catalog")
    total = 0
    for digest, entry in value["archives"].items():
        if (
            not _DIGEST.fullmatch(digest)
            or not isinstance(entry, dict)
            or set(entry) != {"path", "oid", "size"}
            or entry["path"] != "archives/" + digest[7:] + ".tar.gz"
            or not isinstance(entry["oid"], str)
            or not _DIGEST.fullmatch(entry["oid"])
            or type(entry["size"]) is not int
            or not 0 < entry["size"] <= MAX_ARCHIVE_BYTES
        ):
            raise ValueError("invalid LFS catalog entry")
        pointer = read_small(root / entry["path"], 1024)
        expected = (
            "version https://git-lfs.github.com/spec/v1\n"
            f"oid {entry['oid']}\nsize {entry['size']}\n"
        ).encode()
        if pointer != expected:
            raise ValueError("catalog does not match its Git LFS pointer")
        total += entry["size"]
    if total > MAX_STORAGE_BYTES:
        raise ValueError("catalog exceeds the 5 GiB storage guard")
    # A dedicated repository has no executable hooks, workflows, or alternate LFS endpoint.
    expected_files = {"README.md", ".gitattributes", "catalog.json"} | {
        entry["path"] for entry in value["archives"].values()
    }
    files = _run(["git", "ls-files", "-z"], cwd=root).split("\0")
    if set(files) - {""} != expected_files:
        raise ValueError("unexpected files in the private evidence repository")
    return value


class _GitStore:
    """Small Git/LFS adapter. Each instance has a new repository and private LFS cache."""

    def __init__(self, url: str, root: Path) -> None:
        self.root = root
        self.url = url
        _run(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "credential.helper=!gh auth git-credential",
                "clone",
                "--no-checkout",
                "--no-local",
                url,
                str(root),
            ]
        )
        self.git("config", "credential.helper", "!gh auth git-credential")
        self.git("config", "core.hooksPath", str(root / ".git" / "hooks"))
        self.git("lfs", "install", "--local")
        self.git("config", "lfs.storage", str(root / ".git" / "lfs"))
        self.git("config", "lfs.url", url + "/info/lfs" if url.startswith("https:") else url)

    def git(self, *arguments: str, timeout_seconds: float = 180) -> str:
        return _run(["git", *arguments], cwd=self.root, timeout_seconds=timeout_seconds).strip()

    def checkout(self, commit: str) -> dict[str, Any]:
        if not _COMMIT.fullmatch(commit):
            raise ValueError("restore requires a full Git commit")
        self.git("checkout", "--detach", commit)
        return _catalog(self.root)

    def publish(self, packed: Path, digest: str, entry: dict[str, Any]) -> str:
        branches = self.git("for-each-ref", "--format=%(refname)", "refs/remotes/origin/")
        if not branches:
            self.git("checkout", "--orphan", "main")
            catalog: dict[str, Any] = {"schema_version": CATALOG_SCHEMA, "archives": {}}
            write_private(self.root / "README.md", README)
            write_private(self.root / ".gitattributes", ATTRIBUTES)
        else:
            self.git("checkout", "--detach", "origin/main")
            catalog = _catalog(self.root)
        if digest in catalog["archives"]:
            return self.git("rev-parse", "HEAD")
        if (
            sum(item["size"] for item in catalog["archives"].values()) + entry["size"]
            > MAX_STORAGE_BYTES
        ):
            raise ValueError("upload would exceed the 5 GiB storage guard")
        target = self.root / entry["path"]
        target.parent.mkdir(mode=0o700, exist_ok=True)
        shutil.copyfile(packed, target)
        os.chmod(target, 0o600)
        catalog["archives"][digest] = entry
        (self.root / "catalog.json").write_bytes(canonical_json_bytes(catalog))
        self.git("add", "--", "README.md", ".gitattributes", "catalog.json", entry["path"])
        self.git(
            "-c",
            "user.name=PixelGym evidence backup",
            "-c",
            "user.email=backup@localhost",
            "commit",
            "-m",
            "Retain sealed evidence " + digest,
        )
        # The installed LFS pre-push hook uploads objects. No force push or hook bypass.
        self.git("push", "origin", "HEAD:refs/heads/main", timeout_seconds=TRANSFER_TIMEOUT_SECONDS)
        return self.git("rev-parse", "HEAD")

    def download(self, commit: str, digest: str) -> tuple[Path, dict[str, Any]]:
        catalog = self.checkout(commit)
        if digest not in catalog["archives"]:
            raise ValueError("archive is absent from the pinned remote commit")
        entry = catalog["archives"][digest]
        self.git(
            "lfs",
            "fetch",
            "--include=" + entry["path"],
            "--exclude=",
            "origin",
            commit,
            timeout_seconds=TRANSFER_TIMEOUT_SECONDS,
        )
        self.git("lfs", "checkout", entry["path"])
        packed = self.root / entry["path"]
        if packed.stat().st_size != entry["size"] or _hash(packed) != entry["oid"]:
            raise ValueError("downloaded LFS object digest or size mismatch")
        return packed, entry


def restore_remote(
    repository: str,
    commit: str,
    digest: str,
    destination: Path,
    budget_record: Path,
    *,
    verify_restored: Callable[[Path, Path], dict[str, Any]] | None = None,
    logs: Path | None = None,
) -> dict[str, Any]:
    """Fetch from a fresh cache, verify, and restore. No local archive can satisfy the fetch."""
    if not _DIGEST.fullmatch(digest) or not _COMMIT.fullmatch(commit):
        raise ValueError("a full commit and archive manifest digest are required")
    destination = _no_links(destination)
    if destination.exists() or _checkout(destination) is not None:
        raise ValueError("restore destination must be new and outside Git")
    if verify_restored is not None and logs is None:
        raise ValueError("scripted verification requires a private log directory")
    check_budget(budget_record, repository)
    _private_repository(repository)
    with tempfile.TemporaryDirectory(prefix="pixelgym-lfs-download-") as temporary:
        root = Path(temporary).resolve()
        store = _GitStore("https://github.com/" + repository + ".git", root / "clone")
        # Recheck immediately before a transfer that consumes download allowance.
        budget = check_budget(budget_record, repository)
        _private_repository(repository)
        packed, entry = store.download(commit, digest)
        archive = root / "archive"
        unpack_archive(packed, archive, expected_digest=digest)
        restored = restore_archive(archive, destination, expected_digest=digest)
    before = source_fingerprint(destination)
    check = verify_restored(destination, logs) if verify_restored is not None and logs else None
    if source_fingerprint(destination) != before:
        raise ValueError("verification mutated restored evidence")
    return {
        "schema_version": RECEIPT_SCHEMA,
        "repository": repository,
        "commit": commit,
        "manifest_digest": digest,
        "lfs_object": entry,
        "restored_archive": restored,
        "fresh_git_and_lfs_cache": True,
        "verification_scope": "scripted-campaign" if check else "archive-bytes",
        "restored_source_check": check,
        "budget_observation": budget,
        "billing_setting_verified_by_api": False,
        "transport_source_digest": _hash(Path(__file__)),
        "restored_at_utc": datetime.now(UTC).isoformat(),
        "real_provider_requests": 0,
    }


def backup_remote(
    archive: Path,
    repository: str,
    digest: str,
    budget_record: Path,
    destination: Path,
    *,
    verify_restored: Callable[[Path, Path], dict[str, Any]] | None = None,
    logs: Path | None = None,
) -> dict[str, Any]:
    """Publish once per manifest, then independently download and restore the remote copy."""
    check_budget(budget_record, repository)
    _private_repository(repository)
    archive, destination = map(_no_links, (archive, destination))
    _separate(archive, destination)
    if destination.exists() or _checkout(destination) is not None:
        raise ValueError("restore destination must be new and outside Git")
    if verify_restored is not None and logs is None:
        raise ValueError("scripted verification requires a private log directory")
    with tempfile.TemporaryDirectory(prefix="pixelgym-lfs-upload-") as temporary:
        root = Path(temporary).resolve()
        packed = root / "archive.tar.gz"
        entry = pack_archive(archive, packed, expected_digest=digest)
        store = _GitStore("https://github.com/" + repository + ".git", root / "clone")
        check_budget(budget_record, repository)
        _private_repository(repository)
        commit = store.publish(packed, digest, entry)
    return restore_remote(
        repository,
        commit,
        digest,
        destination,
        budget_record,
        verify_restored=verify_restored,
        logs=logs,
    )
