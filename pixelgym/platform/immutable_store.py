"""Verified put-once object storage interfaces."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from pixelgym.platform.contracts import ArtifactRef
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes


class ImmutableStoreError(RuntimeError):
    pass


class ImmutableStore(Protocol):
    def put_once(self, logical_key: str, data: bytes, *, media_type: str) -> ArtifactRef: ...
    def get_reference(self, logical_key: str) -> ArtifactRef | None: ...
    def get_verified(self, reference: ArtifactRef) -> bytes: ...


def _validate_key(key: str) -> tuple[str, ...]:
    parts = tuple(Path(key).parts)
    if not key or Path(key).is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("logical key must be a safe relative object key")
    return parts


class LocalImmutableStore:
    """Filesystem test/demo store with atomic create and verified readback.

    Production deployments use the same content identities with an S3 Object Lock adapter. The
    local implementation cannot claim WORM retention and labels that limitation in every ref.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()

    def _paths(self, logical_key: str) -> tuple[Path, Path]:
        parts = _validate_key(logical_key)
        data_path = self.root.joinpath("objects", *parts)
        metadata_base = self.root.joinpath("metadata", *parts)
        metadata_path = metadata_base.with_name(metadata_base.name + ".metadata.json")
        return data_path, metadata_path

    def put_once(self, logical_key: str, data: bytes, *, media_type: str) -> ArtifactRef:
        if not isinstance(data, bytes):
            raise TypeError("immutable object data must be bytes")
        if not media_type:
            raise ValueError("media_type is required")
        digest = sha256_bytes(data)
        data_path, metadata_path = self._paths(logical_key)
        reference = ArtifactRef(
            logical_key=logical_key,
            uri=f"immutable://local/{logical_key}",
            version_id=digest,
            sha256=digest,
            size=len(data),
            media_type=media_type,
            retention_status="application-put-once; no storage-enforced WORM retention",
        )
        with self._lock:
            data_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            if data_path.exists() or metadata_path.exists():
                if not data_path.is_file() or not metadata_path.is_file():
                    raise ImmutableStoreError("immutable object is incomplete")
                existing = data_path.read_bytes()
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if existing != data or metadata != reference.to_dict():
                    raise ImmutableStoreError("refusing conflicting bytes at immutable key")
                self.get_verified(reference)
                return reference
            temporary_data = data_path.with_name(f".{data_path.name}.{os.getpid()}.tmp")
            temporary_meta = metadata_path.with_name(f".{metadata_path.name}.{os.getpid()}.tmp")
            linked_data = False
            linked_metadata = False
            try:
                temporary_data.write_bytes(data)
                temporary_meta.write_bytes(canonical_json_bytes(reference.to_dict()) + b"\n")
                os.link(temporary_meta, metadata_path)
                linked_metadata = True
                os.link(temporary_data, data_path)
                linked_data = True
            except FileExistsError as exc:
                if linked_data:
                    data_path.unlink(missing_ok=True)
                if linked_metadata:
                    metadata_path.unlink(missing_ok=True)
                raise ImmutableStoreError("concurrent immutable put conflict") from exc
            except BaseException:
                if linked_data:
                    data_path.unlink(missing_ok=True)
                if linked_metadata:
                    metadata_path.unlink(missing_ok=True)
                raise
            finally:
                temporary_data.unlink(missing_ok=True)
                temporary_meta.unlink(missing_ok=True)
            self.get_verified(reference)
        return reference

    def get_verified(self, reference: ArtifactRef) -> bytes:
        data_path, metadata_path = self._paths(reference.logical_key)
        if not data_path.is_file() or not metadata_path.is_file():
            raise ImmutableStoreError("immutable object or metadata is missing")
        data = data_path.read_bytes()
        if len(data) != reference.size or sha256_bytes(data) != reference.sha256:
            raise ImmutableStoreError("immutable object failed size or digest verification")
        stored = json.loads(metadata_path.read_text(encoding="utf-8"))
        if stored != reference.to_dict():
            raise ImmutableStoreError("immutable object metadata does not match pinned reference")
        return data

    def get_reference(self, logical_key: str) -> ArtifactRef | None:
        data_path, metadata_path = self._paths(logical_key)
        if not data_path.exists() and not metadata_path.exists():
            return None
        if not data_path.is_file() or not metadata_path.is_file():
            raise ImmutableStoreError("immutable object is incomplete")
        value = json.loads(metadata_path.read_text(encoding="utf-8"))
        reference = ArtifactRef(**value)
        self.get_verified(reference)
        return reference


class S3ImmutableStore:
    """S3-compatible adapter with pinned versions and optional Object Lock retention."""

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "pixelgym",
        client: object | None = None,
        object_lock: bool = True,
        retention_days: int = 30,
    ) -> None:
        if not bucket or retention_days <= 0:
            raise ValueError("bucket and positive retention_days are required")
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - optional integration dependency
                raise RuntimeError('install pixelgym with the "platform" extra') from exc
            client = boto3.client("s3")
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.object_lock = object_lock
        self.retention_days = retention_days

    def _key(self, logical_key: str) -> str:
        parts = _validate_key(logical_key)
        suffix = "/".join(parts)
        return f"{self.prefix}/{suffix}" if self.prefix else suffix

    def _from_head(self, logical_key: str, head: dict[str, object]) -> ArtifactRef:
        metadata = head.get("Metadata") or {}
        digest = str(metadata.get("sha256", ""))
        version_id = str(head.get("VersionId") or metadata.get("version-id") or "null")
        if self.object_lock:
            mode = str(head.get("ObjectLockMode") or "").upper()
            if version_id == "null" or mode not in {"GOVERNANCE", "COMPLIANCE"}:
                raise ImmutableStoreError(
                    "S3 immutable object is missing a pinned version or Object Lock retention"
                )
            retention = f"object-lock-{mode.lower()}"
        else:
            retention = "application-put-once; Object Lock disabled"
        return ArtifactRef(
            logical_key=logical_key,
            uri=f"s3://{self.bucket}/{self._key(logical_key)}",
            version_id=version_id,
            sha256=digest,
            size=int(head.get("ContentLength", -1)),
            media_type=str(head.get("ContentType") or metadata.get("media-type") or ""),
            retention_status=retention,
        )

    def get_reference(self, logical_key: str) -> ArtifactRef | None:
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=self._key(logical_key))
        except Exception as exc:  # botocore is optional; inspect its stable error response shape.
            response = getattr(exc, "response", {})
            code = str(response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise ImmutableStoreError("S3 immutable metadata lookup failed") from exc
        reference = self._from_head(logical_key, head)
        self.get_verified(reference)
        return reference

    def put_once(self, logical_key: str, data: bytes, *, media_type: str) -> ArtifactRef:
        _validate_key(logical_key)
        existing = self.get_reference(logical_key)
        digest = sha256_bytes(data)
        if existing is not None:
            if existing.sha256 != digest or existing.size != len(data) or existing.media_type != media_type:
                raise ImmutableStoreError("refusing conflicting bytes at immutable S3 key")
            return existing
        arguments: dict[str, object] = {
            "Bucket": self.bucket,
            "Key": self._key(logical_key),
            "Body": data,
            "ContentType": media_type,
            "Metadata": {"sha256": digest, "media-type": media_type},
            "IfNoneMatch": "*",
        }
        if self.object_lock:
            arguments.update(
                {
                    "ObjectLockMode": "GOVERNANCE",
                    "ObjectLockRetainUntilDate": datetime.now(UTC) + timedelta(days=self.retention_days),
                }
            )
        try:
            result = self.client.put_object(**arguments)
        except Exception as exc:
            # A racing writer may win the conditional put. Accept only verified identical bytes.
            existing = self.get_reference(logical_key)
            if (
                existing
                and existing.sha256 == digest
                and existing.size == len(data)
                and existing.media_type == media_type
            ):
                return existing
            raise ImmutableStoreError("immutable S3 put failed") from exc
        head_arguments = {"Bucket": self.bucket, "Key": self._key(logical_key)}
        if result.get("VersionId"):
            head_arguments["VersionId"] = result["VersionId"]
        head = self.client.head_object(**head_arguments)
        reference = self._from_head(logical_key, head)
        self.get_verified(reference)
        return reference

    def get_verified(self, reference: ArtifactRef) -> bytes:
        arguments: dict[str, object] = {
            "Bucket": self.bucket,
            "Key": self._key(reference.logical_key),
        }
        if reference.version_id != "null":
            arguments["VersionId"] = reference.version_id
        try:
            result = self.client.get_object(**arguments)
            data = result["Body"].read()
        except Exception as exc:
            raise ImmutableStoreError("pinned immutable S3 object is missing") from exc
        if len(data) != reference.size or sha256_bytes(data) != reference.sha256:
            raise ImmutableStoreError("immutable S3 object failed size or digest verification")
        return data
