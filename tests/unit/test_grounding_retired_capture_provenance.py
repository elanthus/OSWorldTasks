"""Pinned Git provenance for captures whose live source moved out of the wheel."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
SCHEMA = "pixelgym-grounding-capture-provenance-v1"
REVISION = re.compile(r"[0-9a-f]{40}")
SIDECAR_SHA256 = {
    "grounding-v3a-capture.json": "4281a48a6c0d1909cfa3b6fa6ad9d828b8bcadaeba5183b3327cf623c8834371",
    "grounding-v3b-capture.json": "ba7c1605757d51a2b732168401b3735e47663ac7bb7190837917047d99e6ca87",
    "grounding-v3c-capture.json": "b4d73e595b7ed631c82c03c3d19f00f88fe6234b9c92daaf80d7bc04f705e76b",
    "grounding-v4b-pilot-capture.json": "c3011bc0938463c7903470b2532da47d0c94bb890076edbd6a2aabe253d1d045",
    "grounding-v4c-pilot-capture.json": "d9d7a8eadd2e60e53626ade3c39acb18f3cc4eebd6025adb6fd2130e7bc72509",
}


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AssertionError(f"{label} is missing or unreadable") from exc
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError(f"{label} contains malformed JSON") from exc
    assert isinstance(value, dict), f"{label} must contain a JSON object"
    return value


def _sidecar_path(capture_name: str) -> Path:
    return ROOT / "artifacts" / capture_name.replace(".json", ".provenance.json")


def _load_sidecar(path: Path, capture_name: str) -> dict[str, Any]:
    value = _load(path, f"{capture_name} provenance sidecar")
    assert set(value) == {
        "capture_artifact",
        "derivation",
        "earliest_matching_revision",
        "matching_range_end",
        "schema_version",
        "verified_paths",
    }
    assert value["schema_version"] == SCHEMA
    assert value["capture_artifact"] == f"artifacts/{capture_name}"
    for field in ("earliest_matching_revision", "matching_range_end"):
        assert isinstance(value[field], str) and REVISION.fullmatch(value[field])
    paths = value["verified_paths"]
    assert isinstance(paths, list) and paths == sorted(set(paths)) and paths
    canonical = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert path.read_bytes() == canonical
    return value


def _cat_blob(revision: str, relative: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "blob", f"{revision}:{relative}"],
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0, f"{relative}: source or revision is absent at {revision}"
    return result.stdout


def _assert_hashes(capture: dict[str, Any], sidecar: dict[str, Any], revision: str) -> None:
    source_hashes = capture.get("source_sha256")
    assert isinstance(source_hashes, dict) and source_hashes
    assert sidecar["verified_paths"] == sorted(source_hashes)
    for relative in sidecar["verified_paths"]:
        actual = hashlib.sha256(_cat_blob(revision, relative)).hexdigest()
        assert actual == source_hashes[relative], f"{relative}: source hash mismatch"


@pytest.mark.parametrize("capture_name", sorted(SIDECAR_SHA256))
def test_retired_capture_hashes_match_pinned_git_revisions(capture_name: str) -> None:
    sidecar_path = _sidecar_path(capture_name)
    sidecar = _load_sidecar(sidecar_path, capture_name)
    capture = _load(ROOT / "artifacts" / capture_name, capture_name)
    for field in ("earliest_matching_revision", "matching_range_end"):
        _assert_hashes(capture, sidecar, sidecar[field])
    assert hashlib.sha256(sidecar_path.read_bytes()).hexdigest() == SIDECAR_SHA256[capture_name]


def test_retired_capture_provenance_fails_closed_without_sidecar(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="missing or unreadable"):
        _load_sidecar(tmp_path / "missing.provenance.json", "missing.json")


def test_retired_capture_provenance_fails_closed_without_revision() -> None:
    with pytest.raises(AssertionError, match="source or revision is absent"):
        _cat_blob("0" * 40, "pixelgym/grounding/capture.py")


def test_retired_capture_hash_mismatch_names_path() -> None:
    capture_name = "grounding-v3a-capture.json"
    sidecar = _load_sidecar(_sidecar_path(capture_name), capture_name)
    capture = _load(ROOT / "artifacts" / capture_name, capture_name)
    tampered_path = sidecar["verified_paths"][0]
    capture["source_sha256"][tampered_path] = "0" * 64
    with pytest.raises(AssertionError, match=re.escape(tampered_path)):
        _assert_hashes(capture, sidecar, sidecar["earliest_matching_revision"])
