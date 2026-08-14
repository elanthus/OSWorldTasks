from __future__ import annotations

import io
import json
from pathlib import Path
from typing import ClassVar

import pytest

from pixelgym.platform.fingerprints import (
    build_dataset_manifest,
    canonical_json_bytes,
    verify_dataset_manifest,
)
from pixelgym.platform.immutable_store import (
    ImmutableStoreError,
    LocalImmutableStore,
    S3ImmutableStore,
)

EXPECTED_FINGERPRINT = "sha256:01d7218c3657aa8d186a038b4c34c00cc227ebda91ee5af9ca238a8e1ed506a8"


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(row, sort_keys=False) + "\n" for row in rows)


def test_frozen_dataset_has_reproducible_authoritative_fingerprint(repository_root: Path) -> None:
    manifest, fingerprint = build_dataset_manifest(
        repository_root=repository_root,
        dataset_path=repository_root / "artifacts/grounding-dataset.jsonl",
        overlays_path=repository_root / "artifacts/grounding-overlays.jsonl",
    )
    assert fingerprint == EXPECTED_FINGERPRINT
    assert manifest["example_count"] == 100
    verify_dataset_manifest(manifest=manifest, expected_fingerprint=fingerprint)


def test_line_key_and_absolute_path_reordering_do_not_change_identity(
    repository_root: Path, tmp_path: Path
) -> None:
    examples = [json.loads(line) for line in (repository_root / "artifacts/grounding-dataset.jsonl").read_text().splitlines()]
    overlays = [json.loads(line) for line in (repository_root / "artifacts/grounding-overlays.jsonl").read_text().splitlines()]
    for row in examples:
        row["image_path"] = str((repository_root / row["image_path"]).resolve())
        row = {key: row[key] for key in reversed(row)}
    for row in overlays:
        row["marked_image_path"] = str((repository_root / row["marked_image_path"]).resolve())
        row["raw_image_path"] = str((repository_root / row["raw_image_path"]).resolve())
    dataset = tmp_path / "dataset.jsonl"
    overlay_path = tmp_path / "overlays.jsonl"
    dataset.write_text(_jsonl(list(reversed(examples))))
    overlay_path.write_text(_jsonl(list(reversed(overlays))))
    _, fingerprint = build_dataset_manifest(
        repository_root=tmp_path, dataset_path=dataset, overlays_path=overlay_path
    )
    assert fingerprint == EXPECTED_FINGERPRINT


def test_schema_valid_record_mutation_changes_fingerprint(repository_root: Path, tmp_path: Path) -> None:
    examples = [json.loads(line) for line in (repository_root / "artifacts/grounding-dataset.jsonl").read_text().splitlines()]
    overlays = [json.loads(line) for line in (repository_root / "artifacts/grounding-overlays.jsonl").read_text().splitlines()]
    examples[0]["screen_state"] = "completed_review"
    dataset = tmp_path / "dataset.jsonl"
    overlay_path = tmp_path / "overlays.jsonl"
    dataset.write_text(_jsonl(examples))
    overlay_path.write_text(_jsonl(overlays))
    _, fingerprint = build_dataset_manifest(
        repository_root=repository_root, dataset_path=dataset, overlays_path=overlay_path
    )
    assert fingerprint != EXPECTED_FINGERPRINT


def test_local_store_is_put_once_and_verifies_readback(tmp_path: Path) -> None:
    store = LocalImmutableStore(tmp_path)
    reference = store.put_once("raw/a.json", b'{"answer":1}\n', media_type="application/json")
    assert store.put_once("raw/a.json", b'{"answer":1}\n', media_type="application/json") == reference
    assert store.get_verified(reference) == b'{"answer":1}\n'
    assert store.get_reference("raw/a.json") == reference
    with pytest.raises(ImmutableStoreError, match="conflicting"):
        store.put_once("raw/a.json", b'{"answer":2}\n', media_type="application/json")


def test_corrupt_or_missing_bytes_fail_verification(tmp_path: Path) -> None:
    store = LocalImmutableStore(tmp_path)
    reference = store.put_once("raw/a.json", b"original", media_type="application/json")
    data_path = tmp_path / "objects/raw/a.json"
    data_path.write_bytes(b"tampered")
    with pytest.raises(ImmutableStoreError, match="digest"):
        store.get_verified(reference)
    data_path.unlink()
    with pytest.raises(ImmutableStoreError, match="missing"):
        store.get_verified(reference)


def test_canonical_json_rejects_nonfinite_numbers() -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes({"latency": float("nan")})


class _MissingObject(Exception):
    response: ClassVar = {"Error": {"Code": "NoSuchKey"}}


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}

    def head_object(self, *, Bucket, Key, VersionId=None):
        del Bucket, VersionId
        if Key not in self.objects:
            raise _MissingObject
        value = self.objects[Key]
        return {
            "Metadata": value["Metadata"],
            "VersionId": value["VersionId"],
            "ContentLength": len(value["Body"]),
            "ContentType": value["ContentType"],
            "ObjectLockMode": value.get("ObjectLockMode"),
        }

    def put_object(self, **arguments):
        key = arguments["Key"]
        if key in self.objects:
            raise RuntimeError("precondition failed")
        self.objects[key] = {**arguments, "VersionId": "version-1"}
        return {"VersionId": "version-1"}

    def get_object(self, *, Bucket, Key, VersionId=None):
        del Bucket, VersionId
        if Key not in self.objects:
            raise _MissingObject
        return {"Body": io.BytesIO(self.objects[Key]["Body"])}


def test_s3_adapter_pins_version_and_object_lock_and_rejects_conflicts() -> None:
    client = _FakeS3()
    store = S3ImmutableStore(bucket="immutable", client=client, retention_days=30)
    reference = store.put_once("raw/one.json", b"one", media_type="application/json")
    assert reference.version_id == "version-1"
    assert reference.retention_status == "object-lock-governance"
    assert store.put_once("raw/one.json", b"one", media_type="application/json") == reference
    with pytest.raises(ImmutableStoreError, match="conflicting"):
        store.put_once("raw/one.json", b"two", media_type="application/json")
    client.objects["pixelgym/raw/one.json"]["Body"] = b"tampered"
    with pytest.raises(ImmutableStoreError, match="digest"):
        store.get_verified(reference)


def test_s3_adapter_fails_closed_without_versioned_object_lock_metadata() -> None:
    client = _FakeS3()
    client.objects["pixelgym/raw/unlocked.json"] = {
        "Metadata": {"sha256": "0" * 64, "media-type": "application/json"},
        "VersionId": None,
        "Body": b"",
        "ContentType": "application/json",
    }
    store = S3ImmutableStore(bucket="immutable", client=client, retention_days=30)
    with pytest.raises(ImmutableStoreError, match="Object Lock"):
        store.get_reference("raw/unlocked.json")
