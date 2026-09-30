"""Tests for release-hosted evidence images: manifests, archives, and fetch.

The fetch tests use a local directory of archives as the fake release source;
nothing here touches the network or ``gh``.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts import fetch_evidence_images as fetch
from scripts import package_evidence_images as package
from tests.support.evidence_images import manifest_images_present

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SET = "grounding-v4-pilot"
FILES = {
    "a.png": b"\x89PNG first image",
    "nested/b.png": b"\x89PNG second image, longer",
}


def _make_tree(root: Path, files: dict[str, bytes] = FILES) -> Path:
    set_dir = root / SET
    for relative, data in files.items():
        (set_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        (set_dir / relative).write_bytes(data)
    package.write_manifests(root, [SET])
    return set_dir


def _release(tmp_path: Path) -> tuple[Path, Path]:
    """Build a source tree, archive it, and return (fresh artifacts root, release dir)."""
    source = tmp_path / "source"
    _make_tree(source)
    release = tmp_path / "release"
    package.write_archives(source, [SET], release)
    fresh = tmp_path / "fresh"
    (fresh / SET).mkdir(parents=True)
    (fresh / SET / package.MANIFEST_NAME).write_bytes(
        (source / SET / package.MANIFEST_NAME).read_bytes()
    )
    return fresh, release


def _write_archive(path: Path, members: dict[str, bytes]) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    path.write_bytes(gzip.compress(buffer.getvalue(), mtime=0))


# --- manifests and archives -------------------------------------------------


def test_manifest_lists_sorted_paths_sizes_and_digests(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    document = json.loads((tmp_path / SET / package.MANIFEST_NAME).read_text())
    assert document["file_count"] == 2
    assert document["archive"] == f"{SET}-images.tar.gz"
    assert [entry["path"] for entry in document["files"]] == ["a.png", "nested/b.png"]
    for entry in document["files"]:
        data = FILES[entry["path"]]
        assert entry["size"] == len(data)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()


def test_archives_are_byte_identical_across_builds_and_mtimes(tmp_path: Path) -> None:
    set_dir = _make_tree(tmp_path)
    entries = package.load_manifest(tmp_path, SET)
    first = package.build_archive_bytes(set_dir, entries)
    for path in set_dir.rglob("*.png"):
        path.touch()
    assert package.build_archive_bytes(set_dir, entries) == first
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as tar:
        members = tar.getmembers()
    assert [member.name for member in members] == ["a.png", "nested/b.png"]
    assert {(m.mtime, m.uid, m.gid, m.mode, m.uname) for m in members} == {(0, 0, 0, 0o644, "")}


def test_archive_refuses_tree_that_differs_from_manifest(tmp_path: Path) -> None:
    set_dir = _make_tree(tmp_path)
    (set_dir / "a.png").write_bytes(b"changed")
    with pytest.raises(package.EvidenceImageError, match="a.png"):
        package.build_archive_bytes(set_dir, package.load_manifest(tmp_path, SET))


@pytest.mark.parametrize("bad", ["../x.png", "/abs.png", "a/./b.png", "a\\b.png", "a.txt"])
def test_manifest_rejects_unsafe_paths(bad: str) -> None:
    with pytest.raises(package.EvidenceImageError):
        package.validate_relative_path(bad)


def test_verify_tree_reports_tamper_extra_and_required_missing(tmp_path: Path) -> None:
    set_dir = _make_tree(tmp_path)
    assert package.verify_tree(tmp_path, SET) == (2, 0)
    (set_dir / "a.png").unlink()
    assert package.verify_tree(tmp_path, SET) == (1, 1)
    with pytest.raises(package.EvidenceImageError, match="missing"):
        package.verify_tree(tmp_path, SET, require_images=True)
    (set_dir / "a.png").write_bytes(b"\x89PNG first imagX")
    with pytest.raises(package.EvidenceImageError, match="sha256 mismatch"):
        package.verify_tree(tmp_path, SET)
    (set_dir / "a.png").write_bytes(FILES["a.png"])
    (set_dir / "stray.png").write_bytes(b"stray")
    with pytest.raises(package.EvidenceImageError, match="not in manifest"):
        package.verify_tree(tmp_path, SET)


def test_images_present_needs_every_entry(tmp_path: Path) -> None:
    set_dir = _make_tree(tmp_path)
    assert package.images_present(tmp_path, SET)
    (set_dir / "nested/b.png").unlink()
    assert not package.images_present(tmp_path, SET)
    assert not package.images_present(tmp_path, "grounding-v3a")


# --- fetch against a local fake release -------------------------------------


def test_good_fetch_writes_verified_files_and_is_idempotent(tmp_path: Path) -> None:
    fresh, release = _release(tmp_path)
    args = ["--dir", str(release), "--artifacts-root", str(fresh), "--set", SET]
    assert fetch.main(args) == 0
    for relative, data in FILES.items():
        assert (fresh / SET / relative).read_bytes() == data
    assert package.verify_tree(fresh, SET, require_images=True) == (2, 0)
    assert fetch.main(args) == 0


def test_tampered_archive_member_fails_and_writes_nothing(tmp_path: Path) -> None:
    fresh, release = _release(tmp_path)
    tampered = dict(FILES)
    tampered["nested/b.png"] = b"\x89PNG second image, longeR"  # same size, other bytes
    _write_archive(release / f"{SET}-images.tar.gz", tampered)
    assert fetch.main(["--dir", str(release), "--artifacts-root", str(fresh), "--set", SET]) == 1
    assert list((fresh / SET).rglob("*.png")) == []


def test_archive_missing_a_manifest_entry_fails(tmp_path: Path) -> None:
    fresh, release = _release(tmp_path)
    _write_archive(release / f"{SET}-images.tar.gz", {"a.png": FILES["a.png"]})
    with pytest.raises(package.EvidenceImageError, match="missing"):
        fetch.install_set(fresh, SET, release / f"{SET}-images.tar.gz")
    assert list((fresh / SET).rglob("*.png")) == []


def test_missing_archive_fails_non_zero(tmp_path: Path) -> None:
    fresh, release = _release(tmp_path)
    (release / f"{SET}-images.tar.gz").unlink()
    assert fetch.main(["--dir", str(release), "--artifacts-root", str(fresh), "--set", SET]) == 1


def test_size_mismatch_fails(tmp_path: Path) -> None:
    fresh, release = _release(tmp_path)
    manifest = fresh / SET / package.MANIFEST_NAME
    document = json.loads(manifest.read_text())
    document["files"][0]["size"] += 1
    document["total_bytes"] += 1
    manifest.write_text(json.dumps(document))
    with pytest.raises(package.EvidenceImageError, match="size mismatch"):
        fetch.install_set(fresh, SET, release / f"{SET}-images.tar.gz")


@pytest.mark.parametrize("name", ["../escape.png", "extra.png"])
def test_unlisted_or_traversal_member_fails(tmp_path: Path, name: str) -> None:
    fresh, release = _release(tmp_path)
    _write_archive(release / f"{SET}-images.tar.gz", {**FILES, name: b"x"})
    with pytest.raises(package.EvidenceImageError, match="unexpected member"):
        fetch.install_set(fresh, SET, release / f"{SET}-images.tar.gz")
    assert not (tmp_path / "escape.png").exists()


def test_refuses_to_overwrite_differing_existing_file(tmp_path: Path) -> None:
    fresh, release = _release(tmp_path)
    (fresh / SET / "a.png").write_bytes(b"local edit")
    assert fetch.main(["--dir", str(release), "--artifacts-root", str(fresh), "--set", SET]) == 1
    assert (fresh / SET / "a.png").read_bytes() == b"local edit"
    assert not (fresh / SET / "nested/b.png").exists()


def test_download_failure_exits_non_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fail(command: list[str], check: bool) -> None:
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(fetch.subprocess, "run", fail)
    assert fetch.main(["--artifacts-root", str(tmp_path), "--set", SET, "--tag", "t"]) == 1
    assert calls[0][:4] == ["gh", "release", "download", "t"]
    assert "--pattern" in calls[0] and f"{SET}-images.tar.gz" in calls[0]


# --- the checked-in manifests -----------------------------------------------


@pytest.mark.parametrize("set_name", package.HOSTED_SETS)
def test_checked_in_manifest_matches_tree_when_present(set_name: str) -> None:
    """Every present PNG verifies; no PNG in a hosted set is unlisted."""
    artifacts = REPOSITORY_ROOT / "artifacts"
    entries = package.load_manifest(artifacts, set_name)
    assert entries
    verified, missing = package.verify_tree(artifacts, set_name)
    assert verified + missing == len(entries)
    assert (missing == 0) == manifest_images_present(set_name)
