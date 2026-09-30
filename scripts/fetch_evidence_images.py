#!/usr/bin/env python3
"""Restore release-hosted evidence PNGs and verify them against the manifests.

Usage::

    python scripts/fetch_evidence_images.py                 # all sets, from the release
    python scripts/fetch_evidence_images.py --set grounding-v4c-pilot
    python scripts/fetch_evidence_images.py --dir ~/Downloads/evidence-images-v1

Without ``--dir`` the archives are downloaded with ``gh release download`` from
``--repo`` at ``--tag`` into a temporary directory. With ``--dir`` no network is
used: the archives are read from that directory.

Every archive is checked completely before anything is written: each member
must be a regular file listed in the set's checked-in ``images.manifest.json``
with the recorded size and SHA-256, and every manifest entry must be present.
An existing file with different bytes is never overwritten. Any mismatch exits
non-zero and leaves ``artifacts/`` unchanged for that set.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.package_evidence_images import (  # noqa: E402
    DEFAULT_ARTIFACTS_ROOT,
    HOSTED_SETS,
    RELEASE_TAG,
    EvidenceImageError,
    ManifestEntry,
    archive_name,
    load_manifest,
    sha256_bytes,
    sha256_file,
)

DEFAULT_REPO = "elanthus/OSWorldTasks"


def read_verified_archive(archive: Path, entries: Sequence[ManifestEntry]) -> dict[str, bytes]:
    """Return {path: bytes} only if the archive matches the manifest exactly."""
    expected = {entry.path: entry for entry in entries}
    contents: dict[str, bytes] = {}
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            for member in tar:
                if member.name not in expected:
                    raise EvidenceImageError(f"{archive.name}: unexpected member {member.name!r}")
                if not member.isreg():
                    raise EvidenceImageError(f"{archive.name}: not a regular file: {member.name}")
                if member.name in contents:
                    raise EvidenceImageError(f"{archive.name}: duplicate member {member.name}")
                handle = tar.extractfile(member)
                if handle is None:
                    raise EvidenceImageError(f"{archive.name}: unreadable member {member.name}")
                data = handle.read()
                entry = expected[member.name]
                if len(data) != entry.size:
                    raise EvidenceImageError(
                        f"{archive.name}: size mismatch for {member.name}: "
                        f"{len(data)} != {entry.size}"
                    )
                if sha256_bytes(data) != entry.sha256:
                    raise EvidenceImageError(f"{archive.name}: sha256 mismatch for {member.name}")
                contents[member.name] = data
    except (tarfile.TarError, OSError, EOFError) as error:
        raise EvidenceImageError(f"{archive.name}: unreadable archive: {error}") from error
    missing = sorted(set(expected) - set(contents))
    if missing:
        raise EvidenceImageError(
            f"{archive.name}: {len(missing)} manifest entries missing, first {missing[0]}"
        )
    return contents


def install_set(
    artifacts_root: Path, set_name: str, archive: Path
) -> tuple[int, int]:
    """Verify then write one set. Returns (written, already_present)."""
    entries = load_manifest(artifacts_root, set_name)
    if not archive.is_file():
        raise EvidenceImageError(f"missing archive: {archive}")
    contents = read_verified_archive(archive, entries)
    set_dir = artifacts_root / set_name
    to_write: list[ManifestEntry] = []
    for entry in entries:
        target = set_dir / entry.path
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise EvidenceImageError(f"refusing to replace non-file {target}")
        if target.exists():
            if target.stat().st_size != entry.size or sha256_file(target) != entry.sha256:
                raise EvidenceImageError(
                    f"refusing to overwrite differing existing file {set_name}/{entry.path}"
                )
            continue
        to_write.append(entry)
    for entry in to_write:
        target = set_dir / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            handle.write(contents[entry.path])
            temporary = Path(handle.name)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    return len(to_write), len(entries) - len(to_write)


def download(tag: str, repo: str, sets: Sequence[str], destination: Path) -> None:
    command = ["gh", "release", "download", tag, "--repo", repo, "--dir", str(destination)]
    for set_name in sets:
        command += ["--pattern", archive_name(set_name)]
    subprocess.run(command, check=True)


def fetch(
    artifacts_root: Path, sets: Sequence[str], source_dir: Path
) -> int:
    failures = 0
    for set_name in sets:
        try:
            written, present = install_set(
                artifacts_root, set_name, source_dir / archive_name(set_name)
            )
        except EvidenceImageError as error:
            failures += 1
            print(f"FAIL {set_name}: {error}", file=sys.stderr)
            continue
        print(f"ok {set_name}: {written} written, {present} already present and verified")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tag", default=RELEASE_TAG)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument(
        "--dir",
        type=Path,
        help="read archives from this local directory instead of downloading",
    )
    parser.add_argument("--artifacts-root", type=Path, default=DEFAULT_ARTIFACTS_ROOT)
    parser.add_argument("--set", dest="sets", action="append", choices=HOSTED_SETS)
    args = parser.parse_args(argv)
    sets: Sequence[str] = args.sets or HOSTED_SETS
    if args.dir is not None:
        return fetch(args.artifacts_root, sets, args.dir)
    with tempfile.TemporaryDirectory() as temporary:
        try:
            download(args.tag, args.repo, sets, Path(temporary))
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"FAIL: download from {args.repo}@{args.tag}: {error}", file=sys.stderr)
            return 1
        return fetch(args.artifacts_root, sets, Path(temporary))


if __name__ == "__main__":
    raise SystemExit(main())
