#!/usr/bin/env python3
"""Package release-hosted evidence PNGs into deterministic archives.

The PNG sets named in ``HOSTED_SETS`` are not kept in the Git tree. Each set
keeps a checked-in ``artifacts/<set>/images.manifest.json`` that lists the
relative path, byte size, and SHA-256 of every PNG. The PNGs themselves are
published as ``<set>-images.tar.gz`` assets on the ``evidence-images-v1``
GitHub release and restored with ``scripts/fetch_evidence_images.py``.

Commands:

``manifest``  write each set's manifest from the PNGs currently on disk.
``archive``   build ``dist/<set>-images.tar.gz`` from the manifest and verify
              every PNG against it first; writes ``dist/SHA256SUMS``.
``verify``    check every manifest entry whose PNG is present on disk; with
              ``--require-images`` a missing PNG is also an error.

Archives are deterministic: entries sorted by path, mtime 0, uid/gid 0, empty
owner names, mode 0644, and a gzip header with mtime 0 and no file name. The
standard library has no zstd codec and ``zstandard`` is not a dependency, so
the archives are gzip rather than zstd.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACTS_ROOT = REPO_ROOT / "artifacts"
DEFAULT_DIST = REPO_ROOT / "dist"
RELEASE_TAG = "evidence-images-v1"
MANIFEST_NAME = "images.manifest.json"
MANIFEST_SCHEMA = "pixelgym.evidence-images-manifest.v1"
ARCHIVE_SUFFIX = "-images.tar.gz"
CHECKSUMS_NAME = "SHA256SUMS"

HOSTED_SETS: tuple[str, ...] = (
    "day-2",
    "grounding-v3a",
    "grounding-v3b",
    "grounding-v3c",
    "grounding-v4-pilot",
    "grounding-v4b-pilot",
    "grounding-v4c-pilot",
    "grounding-v5-d56-twinb-audit",
    "grounding-v5-development-sample",
    "platform",
)


class EvidenceImageError(Exception):
    """A manifest, archive, or tree does not match the recorded evidence."""


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    size: int
    sha256: str


def archive_name(set_name: str) -> str:
    return f"{set_name}{ARCHIVE_SUFFIX}"


def manifest_path(artifacts_root: Path, set_name: str) -> Path:
    return artifacts_root / set_name / MANIFEST_NAME


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_relative_path(value: object) -> str:
    """Reject anything that is not a plain relative POSIX path to a PNG."""
    if not isinstance(value, str) or not value:
        raise EvidenceImageError(f"manifest path must be a non-empty string: {value!r}")
    parts = value.split("/")
    if (
        value.startswith("/")
        or "\\" in value
        or any(part in ("", ".", "..") for part in parts)
        or not value.endswith(".png")
    ):
        raise EvidenceImageError(f"unsafe or non-PNG manifest path: {value!r}")
    return value


def scan_pngs(set_dir: Path) -> list[ManifestEntry]:
    if not set_dir.is_dir():
        raise EvidenceImageError(f"set directory does not exist: {set_dir}")
    entries = []
    for path in sorted(set_dir.rglob("*.png")):
        if not path.is_file() or path.is_symlink():
            raise EvidenceImageError(f"not a regular file: {path}")
        relative = path.relative_to(set_dir).as_posix()
        entries.append(
            ManifestEntry(
                path=validate_relative_path(relative),
                size=path.stat().st_size,
                sha256=sha256_file(path),
            )
        )
    entries.sort(key=lambda entry: entry.path)
    if not entries:
        raise EvidenceImageError(f"no PNG files under {set_dir}")
    return entries


def manifest_document(set_name: str, entries: Sequence[ManifestEntry]) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA,
        "set": set_name,
        "release_tag": RELEASE_TAG,
        "archive": archive_name(set_name),
        "paths_relative_to": f"artifacts/{set_name}",
        "file_count": len(entries),
        "total_bytes": sum(entry.size for entry in entries),
        "files": [
            {"path": entry.path, "size": entry.size, "sha256": entry.sha256}
            for entry in entries
        ],
    }


def load_manifest(artifacts_root: Path, set_name: str) -> list[ManifestEntry]:
    path = manifest_path(artifacts_root, set_name)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise EvidenceImageError(f"missing manifest: {path}") from error
    except json.JSONDecodeError as error:
        raise EvidenceImageError(f"malformed manifest {path}: {error}") from error
    if not isinstance(document, dict):
        raise EvidenceImageError(f"manifest is not an object: {path}")
    if document.get("schema_version") != MANIFEST_SCHEMA or document.get("set") != set_name:
        raise EvidenceImageError(f"manifest schema or set name mismatch: {path}")
    files = document.get("files")
    if not isinstance(files, list) or not files:
        raise EvidenceImageError(f"manifest has no files: {path}")
    entries: list[ManifestEntry] = []
    for item in files:
        if not isinstance(item, dict):
            raise EvidenceImageError(f"manifest entry is not an object: {path}")
        size, digest = item.get("size"), item.get("sha256")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise EvidenceImageError(f"malformed manifest entry in {path}: {item!r}")
        entries.append(ManifestEntry(validate_relative_path(item.get("path")), size, digest))
    paths = [entry.path for entry in entries]
    if paths != sorted(set(paths)):
        raise EvidenceImageError(f"manifest paths are not sorted and unique: {path}")
    if document.get("file_count") != len(entries) or document.get("total_bytes") != sum(
        entry.size for entry in entries
    ):
        raise EvidenceImageError(f"manifest totals do not match its entries: {path}")
    return entries


def images_present(artifacts_root: Path, set_name: str) -> bool:
    """True when every PNG the manifest lists exists with the recorded size."""
    try:
        entries = load_manifest(artifacts_root, set_name)
    except EvidenceImageError:
        return False
    set_dir = artifacts_root / set_name
    for entry in entries:
        candidate = set_dir / entry.path
        if not candidate.is_file() or candidate.stat().st_size != entry.size:
            return False
    return True


def verify_tree(
    artifacts_root: Path, set_name: str, *, require_images: bool = False
) -> tuple[int, int]:
    """Verify present PNGs against the manifest. Returns (verified, missing)."""
    entries = load_manifest(artifacts_root, set_name)
    set_dir = artifacts_root / set_name
    verified = missing = 0
    problems: list[str] = []
    for entry in entries:
        candidate = set_dir / entry.path
        if not candidate.exists():
            missing += 1
            if require_images:
                problems.append(f"missing: {set_name}/{entry.path}")
            continue
        if candidate.stat().st_size != entry.size:
            problems.append(f"size mismatch: {set_name}/{entry.path}")
        elif sha256_file(candidate) != entry.sha256:
            problems.append(f"sha256 mismatch: {set_name}/{entry.path}")
        else:
            verified += 1
    listed = {entry.path for entry in entries}
    if set_dir.is_dir():
        for extra in sorted(set_dir.rglob("*.png")):
            relative = extra.relative_to(set_dir).as_posix()
            if relative not in listed:
                problems.append(f"PNG not in manifest: {set_name}/{relative}")
    if problems:
        raise EvidenceImageError("\n".join(problems))
    return verified, missing


def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mtime = 0
    info.mode = 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.type = tarfile.REGTYPE
    return info


def build_archive_bytes(set_dir: Path, entries: Iterable[ManifestEntry]) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for entry in sorted(entries, key=lambda item: item.path):
            data = (set_dir / entry.path).read_bytes()
            if len(data) != entry.size or sha256_bytes(data) != entry.sha256:
                raise EvidenceImageError(f"tree does not match manifest: {entry.path}")
            archive.addfile(_tar_info(entry.path, len(data)), io.BytesIO(data))
    gz_buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz_buffer, mtime=0, compresslevel=9) as gz:
        gz.write(tar_buffer.getvalue())
    return gz_buffer.getvalue()


def write_manifests(artifacts_root: Path, sets: Sequence[str]) -> None:
    for set_name in sets:
        entries = scan_pngs(artifacts_root / set_name)
        document = manifest_document(set_name, entries)
        manifest_path(artifacts_root, set_name).write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{set_name}: {len(entries)} PNGs, {document['total_bytes']} bytes")


def write_archives(artifacts_root: Path, sets: Sequence[str], dist: Path) -> None:
    dist.mkdir(parents=True, exist_ok=True)
    lines = []
    for set_name in sets:
        entries = load_manifest(artifacts_root, set_name)
        data = build_archive_bytes(artifacts_root / set_name, entries)
        (dist / archive_name(set_name)).write_bytes(data)
        lines.append(f"{sha256_bytes(data)}  {archive_name(set_name)}")
        print(lines[-1])
    (dist / CHECKSUMS_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("manifest", "archive", "verify"))
    parser.add_argument("--artifacts-root", type=Path, default=DEFAULT_ARTIFACTS_ROOT)
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--set", dest="sets", action="append", choices=HOSTED_SETS)
    parser.add_argument("--require-images", action="store_true")
    args = parser.parse_args(argv)
    sets: Sequence[str] = args.sets or HOSTED_SETS
    try:
        if args.command == "manifest":
            write_manifests(args.artifacts_root, sets)
        elif args.command == "archive":
            write_archives(args.artifacts_root, sets, args.dist)
        else:
            for set_name in sets:
                verified, missing = verify_tree(
                    args.artifacts_root, set_name, require_images=args.require_images
                )
                print(f"{set_name}: {verified} verified, {missing} not present")
    except EvidenceImageError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
