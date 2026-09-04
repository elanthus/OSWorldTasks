"""Integrity checks for the checked-in v4 pilot evidence."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CAPTURE_RELATIVE_PATH = Path("artifacts/grounding-v4-pilot-capture.json")
PROVENANCE_RELATIVE_PATH = Path(
    "artifacts/grounding-v4-pilot-capture.provenance.json"
)
PROVENANCE_SCHEMA_VERSION = "pixelgym-grounding-capture-provenance-v1"
GIT_REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _overlaps(first: list[int], second: list[int]) -> bool:
    return (
        max(first[0], second[0]) < min(first[2], second[2])
        and max(first[1], second[1]) < min(first[3], second[3])
    )


def _load_json_object(path: Path, description: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError(f"{description} contains malformed UTF-8: {path}") from exc
    except OSError as exc:
        raise AssertionError(
            f"{description} is missing or unreadable: {path}: {exc}"
        ) from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{description} contains malformed JSON: {path}: {exc}"
        ) from exc
    assert isinstance(value, dict), f"{description} must contain a JSON object: {path}"
    return value


def _load_provenance(path: Path) -> dict[str, Any]:
    provenance = _load_json_object(path, "v4 capture provenance sidecar")
    assert set(provenance) == {
        "schema_version",
        "capture_artifact",
        "source_revision",
        "verified_paths",
    }, "v4 capture provenance sidecar has malformed fields"
    assert provenance["schema_version"] == PROVENANCE_SCHEMA_VERSION, (
        "v4 capture provenance sidecar has an unsupported schema_version"
    )
    assert provenance["capture_artifact"] == CAPTURE_RELATIVE_PATH.as_posix(), (
        "v4 capture provenance sidecar names the wrong capture_artifact"
    )
    revision = provenance["source_revision"]
    assert isinstance(revision, str) and GIT_REVISION_PATTERN.fullmatch(revision), (
        "v4 capture provenance sidecar source_revision must be a 40-character "
        "lowercase hexadecimal Git revision"
    )
    verified_paths = provenance["verified_paths"]
    assert (
        isinstance(verified_paths, list)
        and verified_paths
        and all(isinstance(item, str) for item in verified_paths)
        and verified_paths == sorted(set(verified_paths))
    ), "v4 capture provenance sidecar verified_paths must be unique and sorted"
    canonical = json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n"
    assert path.read_text(encoding="utf-8") == canonical, (
        "v4 capture provenance sidecar must be canonical JSON with a trailing newline"
    )
    return provenance


def _require_git_checkout(repository_root: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "--is-inside-work-tree"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0 or result.stdout.strip() != b"true":
        pytest.skip("v4 capture source verification requires a Git checkout")


def _require_source_revision(repository_root: Path, revision: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "cat-file", "-e", f"{revision}^{{commit}}"],
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"v4 capture provenance source_revision {revision} is absent from the repository"
    )


def _assert_source_hashes_at_revision(
    evidence: dict[str, Any],
    provenance: dict[str, Any],
    repository_root: Path,
) -> None:
    source_hashes = evidence.get("source_sha256")
    assert isinstance(source_hashes, dict) and source_hashes, (
        "v4 capture source_sha256 must be a non-empty object"
    )
    verified_paths = provenance["verified_paths"]
    assert verified_paths == sorted(source_hashes), (
        "v4 capture provenance verified_paths must exactly match source_sha256 paths"
    )
    revision = provenance["source_revision"]
    _require_source_revision(repository_root, revision)

    for relative in verified_paths:
        result = subprocess.run(
            ["git", "-C", str(repository_root), "show", f"{revision}:{relative}"],
            check=False,
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"{relative}: source is absent at recorded revision {revision}"
        )
        actual = hashlib.sha256(result.stdout).hexdigest()
        recorded = source_hashes[relative]
        assert actual == recorded, (
            f"{relative}: source hash mismatch at recorded revision {revision}: "
            f"expected {recorded}, got {actual}"
        )


def test_v4_capture_source_hashes_match_recorded_revision() -> None:
    provenance = _load_provenance(REPOSITORY_ROOT / PROVENANCE_RELATIVE_PATH)
    _require_git_checkout(REPOSITORY_ROOT)
    evidence = _load_json_object(
        REPOSITORY_ROOT / CAPTURE_RELATIVE_PATH, "v4 pilot capture"
    )
    _assert_source_hashes_at_revision(evidence, provenance, REPOSITORY_ROOT)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (None, "missing or unreadable"),
        ("{", "contains malformed JSON"),
    ],
)
def test_v4_capture_provenance_fails_closed(
    tmp_path: Path, contents: str | None, message: str
) -> None:
    sidecar = tmp_path / PROVENANCE_RELATIVE_PATH.name
    if contents is not None:
        sidecar.write_text(contents, encoding="utf-8")

    with pytest.raises(AssertionError, match=message):
        _load_provenance(sidecar)


def test_v4_capture_provenance_rejects_absent_source_revision(tmp_path: Path) -> None:
    provenance = _load_provenance(REPOSITORY_ROOT / PROVENANCE_RELATIVE_PATH)
    _require_git_checkout(REPOSITORY_ROOT)
    provenance["source_revision"] = "0" * 40
    sidecar = tmp_path / PROVENANCE_RELATIVE_PATH.name
    sidecar.write_text(
        json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    invalid = _load_provenance(sidecar)
    with pytest.raises(AssertionError, match="is absent from the repository"):
        _require_source_revision(REPOSITORY_ROOT, invalid["source_revision"])


def test_v4_capture_source_hash_mismatch_names_path(tmp_path: Path) -> None:
    provenance = _load_provenance(REPOSITORY_ROOT / PROVENANCE_RELATIVE_PATH)
    _require_git_checkout(REPOSITORY_ROOT)
    evidence = _load_json_object(
        REPOSITORY_ROOT / CAPTURE_RELATIVE_PATH, "v4 pilot capture"
    )
    tampered_path = "pixelgym/serialization.py"
    evidence["source_sha256"] = dict(evidence["source_sha256"])
    evidence["source_sha256"][tampered_path] = "0" * 64
    capture_copy = tmp_path / CAPTURE_RELATIVE_PATH.name
    capture_copy.write_text(json.dumps(evidence), encoding="utf-8")

    tampered = _load_json_object(capture_copy, "tampered v4 pilot capture")
    with pytest.raises(AssertionError, match=re.escape(tampered_path)):
        _assert_source_hashes_at_revision(tampered, provenance, REPOSITORY_ROOT)


def test_v4_capture_live_sources_match_at_clean_recorded_revision() -> None:
    provenance = _load_provenance(REPOSITORY_ROOT / PROVENANCE_RELATIVE_PATH)
    _require_git_checkout(REPOSITORY_ROOT)
    evidence = _load_json_object(
        REPOSITORY_ROOT / CAPTURE_RELATIVE_PATH, "v4 pilot capture"
    )
    source_hashes = evidence["source_sha256"]
    verified_paths = provenance["verified_paths"]
    assert verified_paths == sorted(source_hashes), (
        "v4 capture provenance verified_paths must exactly match source_sha256 paths"
    )

    status = subprocess.run(
        [
            "git",
            "-C",
            str(REPOSITORY_ROOT),
            "status",
            "--porcelain",
            "--",
            *verified_paths,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert status.returncode == 0, "git status failed for v4 capture source paths"
    head = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert head.returncode == 0, "git rev-parse HEAD failed for v4 capture verification"
    revision = provenance["source_revision"]
    skip_reasons = []
    if status.stdout.strip():
        skip_reasons.append("recorded source paths are not clean")
    if head.stdout.strip() != revision:
        skip_reasons.append(
            f"HEAD {head.stdout.strip()} does not equal recorded revision {revision}"
        )
    if skip_reasons:
        pytest.skip("; ".join(skip_reasons))

    for relative, recorded in source_hashes.items():
        actual = hashlib.sha256((REPOSITORY_ROOT / relative).read_bytes()).hexdigest()
        assert actual == recorded, relative


def test_v4_capture_is_bitwise_repeatable_and_manifest_hashes_match() -> None:
    evidence = json.loads(
        (REPOSITORY_ROOT / "artifacts/grounding-v4-pilot-capture.json").read_text(
            encoding="utf-8"
        )
    )
    repeatability = evidence["repeatability"]
    assert repeatability["byte_identical_file_count"] == repeatability["file_count"] == 10
    assert repeatability["differing_file_count"] == 0
    assert repeatability["differing_pixel_count"] == 0
    assert (
        repeatability["reference_aggregate_sha256"]
        == repeatability["candidate_aggregate_sha256"]
    )

    manifest = json.loads(
        (REPOSITORY_ROOT / "artifacts/grounding-v4-pilot-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for output in manifest["outputs"].values():
        actual = hashlib.sha256((REPOSITORY_ROOT / output["path"]).read_bytes()).hexdigest()
        assert actual == output["sha256"], output["path"]


def test_v4_candidate_join_ids_are_opaque_in_checked_in_evidence() -> None:
    examples = _jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4-pilot-dataset.jsonl")
    candidates = _jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4-pilot-candidates.jsonl")

    assert [row["example_id"] for row in candidates] == [
        f"vendor-workbench-v4-{number:04d}" for number in range(1, 11)
    ]
    for example, candidate_record in zip(examples, candidates, strict=True):
        assert candidate_record["example_id"] == example["example_id"]
        assert example["target_id"] not in candidate_record["example_id"]
        assert set(candidate_record) == {
            "schema_version",
            "protocol_version",
            "example_id",
            "candidates",
        }


def test_v4_overlay_badges_do_not_overlap_candidates_or_each_other() -> None:
    records = _jsonl(REPOSITORY_ROOT / "artifacts/grounding-v4-pilot-overlays.jsonl")

    for record in records:
        element_boxes = [mark["bbox"] for mark in record["marks"]]
        badge_boxes = [mark["badge_bbox"] for mark in record["marks"]]
        assert all(
            not _overlaps(badge, element)
            for badge in badge_boxes
            for element in element_boxes
        ), record["example_id"]
        assert all(
            not _overlaps(first, second)
            for index, first in enumerate(badge_boxes)
            for second in badge_boxes[index + 1 :]
        ), record["example_id"]
