from __future__ import annotations

import io
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
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


def test_interrupted_commit_is_repaired_by_a_new_store_instance(
    tmp_path: Path, monkeypatch
) -> None:
    first_store = LocalImmutableStore(tmp_path)
    original_link = os.link
    failed = False

    def fail_metadata_link_once(source, destination):
        nonlocal failed
        if not failed and "metadata" in Path(destination).parts:
            failed = True
            raise OSError("simulated interrupted commit")
        return original_link(source, destination)

    monkeypatch.setattr(os, "link", fail_metadata_link_once)
    with pytest.raises(OSError, match="interrupted"):
        first_store.put_once("raw/retry.json", b"evidence", media_type="application/json")
    assert (tmp_path / "objects/raw/retry.json").read_bytes() == b"evidence"
    assert not (tmp_path / "metadata/raw/retry.json.metadata.json").exists()

    second_store = LocalImmutableStore(tmp_path)
    reference = second_store.put_once(
        "raw/retry.json", b"evidence", media_type="application/json"
    )
    assert second_store.get_verified(reference) == b"evidence"


def test_multiple_store_instances_serialize_same_key_without_inode_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    first_store = LocalImmutableStore(tmp_path)
    second_store = LocalImmutableStore(tmp_path)
    original_link = os.link
    first_data_linked = threading.Event()
    release_first = threading.Event()

    def pause_first_data_publication(source, destination):
        result = original_link(source, destination)
        if "objects" in Path(destination).parts and not first_data_linked.is_set():
            first_data_linked.set()
            assert release_first.wait(timeout=2)
        return result

    monkeypatch.setattr(os, "link", pause_first_data_publication)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            first_store.put_once,
            "raw/shared.json",
            b"first-writer",
            media_type="application/json",
        )
        assert first_data_linked.wait(timeout=2)
        second = executor.submit(
            second_store.put_once,
            "raw/shared.json",
            b"second-writer",
            media_type="application/json",
        )
        assert not second.done()
        release_first.set()
        reference = first.result(timeout=2)
        with pytest.raises(ImmutableStoreError, match="conflicting"):
            second.result(timeout=2)

    assert LocalImmutableStore(tmp_path).get_verified(reference) == b"first-writer"
    assert not list(tmp_path.rglob("*.tmp"))


def test_canonical_json_rejects_nonfinite_numbers() -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes({"latency": float("nan")})


class _MissingObject(Exception):
    response: ClassVar = {"Error": {"Code": "NoSuchKey"}}


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.operations: list[str] = []

    def head_object(self, *, Bucket, Key, VersionId=None):
        self.operations.append("head_object")
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
        self.operations.append("put_object")
        key = arguments["Key"]
        if key in self.objects:
            raise RuntimeError("precondition failed")
        self.objects[key] = {**arguments, "VersionId": "version-1"}
        return {"VersionId": "version-1"}

    def get_object(self, *, Bucket, Key, VersionId=None):
        self.operations.append("get_object")
        del Bucket, VersionId
        if Key not in self.objects:
            raise _MissingObject
        return {"Body": io.BytesIO(self.objects[Key]["Body"])}


class _RecordingLocalStore(LocalImmutableStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.operations: list[str] = []

    def get_reference(self, logical_key: str):
        self.operations.append("get_reference")
        return super().get_reference(logical_key)

    def _get_verified_unlocked(self, reference):
        self.operations.append("get_verified")
        return super()._get_verified_unlocked(reference)


@pytest.fixture(params=["local", "s3"])
def recording_store(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "local":
        store = _RecordingLocalStore(tmp_path / "local")
        return store, store.operations, ("get_reference", "get_verified")
    client = _FakeS3()
    store = S3ImmutableStore(bucket="immutable", client=client, retention_days=30)
    return store, client.operations, ("head_object", "get_object")


@pytest.mark.parametrize(
    ("data", "media_type", "conflicts"),
    [
        (b"one", "application/json", False),
        (b"two", "application/json", True),
        (b"one-more", "application/json", True),
        (b"one", "text/plain", True),
    ],
    ids=["identical", "digest", "size", "media-type"],
)
def test_adapters_match_under_existing_object_fingerprint_mutations(
    recording_store, data: bytes, media_type: str, conflicts: bool
) -> None:
    store, operations, (lookup_operation, payload_get_operation) = recording_store
    reference = store.put_once("raw/one.json", b"one", media_type="application/json")
    assert operations[-1] == payload_get_operation

    operations.clear()
    assert store.get_reference("raw/one.json") == reference
    assert operations == [lookup_operation]

    operations.clear()
    if conflicts:
        with pytest.raises(ImmutableStoreError, match="conflicting"):
            store.put_once("raw/one.json", data, media_type=media_type)
        assert operations == [lookup_operation]
    else:
        assert store.put_once("raw/one.json", data, media_type=media_type) == reference
        assert operations == [lookup_operation, payload_get_operation]


def test_local_reference_lookup_fails_closed_for_missing_or_corrupt_metadata(
    tmp_path: Path,
) -> None:
    store = LocalImmutableStore(tmp_path)
    store.put_once("raw/a.json", b"one", media_type="application/json")
    metadata_path = tmp_path / "metadata/raw/a.json.metadata.json"
    metadata_path.write_text("{not-json")

    with pytest.raises(ImmutableStoreError, match="metadata is malformed"):
        store.get_reference("raw/a.json")

    metadata_path.unlink()
    assert store.get_reference("raw/a.json") is None


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


@pytest.mark.parametrize(
    ("retry_max_attempts", "expected_retries"),
    [
        (1, {"total_max_attempts": 1, "mode": "standard"}),
        (None, None),
    ],
    ids=["serving", "evaluation"],
)
def test_s3_client_retries_are_opt_in_for_bounded_serving_writes(
    monkeypatch: pytest.MonkeyPatch,
    retry_max_attempts: int | None,
    expected_retries: dict[str, object] | None,
) -> None:
    boto3 = pytest.importorskip("boto3")
    captured: dict[str, object] = {}

    def client(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return _FakeS3()

    monkeypatch.setattr(boto3, "client", client)
    S3ImmutableStore(bucket="immutable", retry_max_attempts=retry_max_attempts)

    config = captured["config"]
    assert config.connect_timeout == 10.0
    assert config.read_timeout == 10.0
    assert config.retries == expected_retries
