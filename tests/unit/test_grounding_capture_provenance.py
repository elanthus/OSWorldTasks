"""Historical source provenance for the frozen 100-example grounding capture."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CAPTURE_RELATIVE_PATH = Path("artifacts/grounding-capture.json")
PROVENANCE_RELATIVE_PATH = Path("artifacts/grounding-capture.provenance.json")
PROVENANCE_SCHEMA_VERSION = "pixelgym-grounding-capture-provenance-v1"
PROVENANCE_SHA256 = "eceb0f65caf26177b595c4c70c9f48062157f3ff2ad2c1f7ab8073d2845643da"
GIT_REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _load_json_object(path: Path, name: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AssertionError(f"{name} is missing or unreadable") from exc
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError(f"{name} contains malformed JSON") from exc
    assert isinstance(value, dict), f"{name} must contain a JSON object"
    return value


def _load_provenance(path: Path) -> dict[str, Any]:
    provenance = _load_json_object(path, "grounding capture provenance sidecar")
    assert set(provenance) == {
        "schema_version",
        "capture_artifact",
        "earliest_matching_revision",
        "matching_range_end",
        "derivation",
        "verified_paths",
    }, "grounding capture provenance sidecar has malformed fields"
    assert provenance["schema_version"] == PROVENANCE_SCHEMA_VERSION, (
        "grounding capture provenance sidecar has an unsupported schema_version"
    )
    assert provenance["capture_artifact"] == CAPTURE_RELATIVE_PATH.as_posix(), (
        "grounding capture provenance sidecar names the wrong capture artifact"
    )
    assert isinstance(provenance["derivation"], str) and provenance["derivation"], (
        "grounding capture provenance sidecar derivation must be non-empty"
    )
    for field in ("earliest_matching_revision", "matching_range_end"):
        revision = provenance[field]
        assert isinstance(revision, str) and GIT_REVISION_PATTERN.fullmatch(revision), (
            f"grounding capture provenance sidecar {field} must be a full Git revision"
        )
    paths = provenance["verified_paths"]
    assert isinstance(paths, list) and paths == sorted(set(paths)) and paths, (
        "grounding capture provenance verified_paths must be unique and sorted"
    )
    assert path.read_bytes() == _canonical_json_bytes(provenance), (
        "grounding capture provenance sidecar must be canonical JSON with a trailing newline"
    )
    return provenance


def _require_revision(repository_root: Path, revision: str, field: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "cat-file", "-e", f"{revision}^{{commit}}"],
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"grounding capture provenance {field} {revision} is absent from the repository"
    )


def _assert_source_hashes_at_earliest_revision(
    capture: dict[str, Any], provenance: dict[str, Any], repository_root: Path
) -> None:
    source_hashes = capture.get("source_sha256")
    assert isinstance(source_hashes, dict) and source_hashes, (
        "grounding capture source_sha256 must be a non-empty object"
    )
    verified_paths = provenance["verified_paths"]
    assert verified_paths == sorted(source_hashes), (
        "grounding capture provenance verified_paths must exactly match source_sha256 paths"
    )
    revision = provenance["earliest_matching_revision"]
    _require_revision(repository_root, revision, "earliest_matching_revision")

    for relative in verified_paths:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repository_root),
                "cat-file",
                "blob",
                f"{revision}:{relative}",
            ],
            check=False,
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"{relative}: source is absent at earliest matching revision {revision}"
        )
        actual = hashlib.sha256(result.stdout).hexdigest()
        recorded = source_hashes[relative]
        assert actual == recorded, (
            f"{relative}: source hash mismatch at earliest matching revision {revision}: "
            f"expected {recorded}, got {actual}"
        )


def test_grounding_capture_source_hashes_match_historical_revision() -> None:
    provenance = _load_provenance(REPOSITORY_ROOT / PROVENANCE_RELATIVE_PATH)
    capture = _load_json_object(REPOSITORY_ROOT / CAPTURE_RELATIVE_PATH, "grounding capture")
    for field in ("earliest_matching_revision", "matching_range_end"):
        _require_revision(REPOSITORY_ROOT, provenance[field], field)
    _assert_source_hashes_at_earliest_revision(capture, provenance, REPOSITORY_ROOT)


def test_grounding_capture_provenance_sidecar_is_frozen() -> None:
    actual = hashlib.sha256(
        (REPOSITORY_ROOT / PROVENANCE_RELATIVE_PATH).read_bytes()
    ).hexdigest()
    assert actual == PROVENANCE_SHA256, "grounding capture provenance sidecar hash changed"


def test_grounding_capture_provenance_fails_closed_when_sidecar_is_missing(
    tmp_path: Path,
) -> None:
    with pytest.raises(AssertionError, match="missing or unreadable"):
        _load_provenance(tmp_path / PROVENANCE_RELATIVE_PATH.name)


def test_grounding_capture_provenance_fails_closed_when_revision_is_missing() -> None:
    with pytest.raises(AssertionError, match="is absent from the repository"):
        _require_revision(REPOSITORY_ROOT, "0" * 40, "earliest_matching_revision")


def test_grounding_capture_source_hash_mismatch_names_path() -> None:
    provenance = _load_provenance(REPOSITORY_ROOT / PROVENANCE_RELATIVE_PATH)
    capture = _load_json_object(REPOSITORY_ROOT / CAPTURE_RELATIVE_PATH, "grounding capture")
    tampered_path = provenance["verified_paths"][0]
    capture["source_sha256"][tampered_path] = "0" * 64

    with pytest.raises(AssertionError, match=re.escape(tampered_path)):
        _assert_source_hashes_at_earliest_revision(capture, provenance, REPOSITORY_ROOT)
