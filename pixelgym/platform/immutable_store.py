"""Verified put-once object storage interfaces."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from pixelgym.platform.contracts import ArtifactRef
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes


class ImmutableStoreError(RuntimeError):
    pass


class ImmutableStore(Protocol):
    def put_once(self, logical_key: str, data: bytes, *, media_type: str) -> ArtifactRef: ...

    # Reference lookups inspect metadata only. Call get_verified when bytes are consumed.
    def get_reference(self, logical_key: str) -> ArtifactRef | None: ...
    def get_verified(self, reference: ArtifactRef) -> bytes: ...


class _S3Client(Protocol):
    """Small structural surface used from boto3's dynamically typed client."""

    def head_object(self, **kwargs: object) -> dict[str, Any]: ...
    def put_object(self, **kwargs: object) -> dict[str, Any]: ...
    def get_object(self, **kwargs: object) -> dict[str, Any]: ...


_ROOT_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[Path, threading.RLock] = {}


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
        lock_key = root.resolve()
        with _ROOT_LOCKS_GUARD:
            self._lock = _ROOT_LOCKS.setdefault(lock_key, threading.RLock())

    @contextmanager
    def _filesystem_lock(self) -> Iterator[None]:
        """Serialize one root across store instances, threads, and cooperating processes."""

        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.root / ".immutable-store.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    @staticmethod
    def _stage_exclusive(destination: Path, content: bytes) -> Path:
        """Write a unique staging inode that can never alias another writer's file."""

        for _ in range(100):
            temporary = destination.with_name(
                f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                descriptor = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
            except FileExistsError:
                continue
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
            return temporary
        raise ImmutableStoreError("could not allocate unique immutable staging file")

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
        existing = self.get_reference(logical_key)
        if existing is not None:
            if existing != reference:
                raise ImmutableStoreError("refusing conflicting bytes at immutable key")
            self.get_verified(existing)
            return existing

        with self._filesystem_lock():
            data_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            data_exists = data_path.exists()
            metadata_exists = metadata_path.exists()
            if data_exists and not data_path.is_file():
                raise ImmutableStoreError("immutable object data path is not a file")
            if metadata_exists and not metadata_path.is_file():
                raise ImmutableStoreError("immutable object metadata path is not a file")
            if data_exists and metadata_exists:
                existing = self._get_reference_unlocked(logical_key)
                if existing != reference:
                    raise ImmutableStoreError("refusing conflicting bytes at immutable key")
                reference = existing
            else:
                if data_exists and data_path.read_bytes() != data:
                    raise ImmutableStoreError("refusing conflicting bytes at immutable key")
                if metadata_exists:
                    metadata_reference = self._load_reference_unlocked(
                        logical_key, metadata_path
                    )
                    if metadata_reference != reference:
                        raise ImmutableStoreError("refusing conflicting bytes at immutable key")

            temporary_data: Path | None = None
            temporary_meta: Path | None = None
            try:
                if not data_exists:
                    temporary_data = self._stage_exclusive(data_path, data)
                    os.link(temporary_data, data_path)
                if not metadata_exists:
                    temporary_meta = self._stage_exclusive(
                        metadata_path, canonical_json_bytes(reference.to_dict()) + b"\n"
                    )
                    # Metadata is the commit marker for a new key. If publication is interrupted,
                    # the next identical put verifies the partial component and completes it.
                    os.link(temporary_meta, metadata_path)
            except FileExistsError as exc:
                raise ImmutableStoreError("concurrent immutable put conflict") from exc
            finally:
                if temporary_data is not None:
                    temporary_data.unlink(missing_ok=True)
                if temporary_meta is not None:
                    temporary_meta.unlink(missing_ok=True)
            self._get_verified_unlocked(reference)
        return reference

    def get_verified(self, reference: ArtifactRef) -> bytes:
        with self._filesystem_lock():
            return self._get_verified_unlocked(reference)

    def _get_verified_unlocked(self, reference: ArtifactRef) -> bytes:
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
        with self._filesystem_lock():
            return self._get_reference_unlocked(logical_key)

    def _get_reference_unlocked(self, logical_key: str) -> ArtifactRef | None:
        data_path, metadata_path = self._paths(logical_key)
        if not metadata_path.exists():
            return None
        if not metadata_path.is_file():
            raise ImmutableStoreError("immutable object metadata path is not a file")
        if not data_path.is_file():
            raise ImmutableStoreError("immutable object is incomplete")
        return self._load_reference_unlocked(logical_key, metadata_path)

    @staticmethod
    def _load_reference_unlocked(logical_key: str, metadata_path: Path) -> ArtifactRef:
        try:
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise TypeError("metadata root is not an object")
            reference = ArtifactRef(**value)
        except (OSError, TypeError, ValueError) as exc:
            raise ImmutableStoreError("immutable object metadata is malformed") from exc
        if reference.logical_key != logical_key:
            raise ImmutableStoreError("immutable object metadata key does not match lookup key")
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
        operation_timeout_seconds: float = 10.0,
        retry_max_attempts: int | None = None,
    ) -> None:
        if (
            not bucket
            or retention_days <= 0
            or operation_timeout_seconds <= 0
            or (retry_max_attempts is not None and retry_max_attempts < 1)
        ):
            raise ValueError(
                "bucket, positive retention_days and operation timeout, and a positive retry maximum are required"
            )
        if client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:  # pragma: no cover - optional integration dependency
                raise RuntimeError('install pixelgym with the "platform" extra') from exc
            config_arguments: dict[str, object] = {
                "connect_timeout": operation_timeout_seconds,
                "read_timeout": operation_timeout_seconds,
            }
            if retry_max_attempts is not None:
                config_arguments["retries"] = {
                    "total_max_attempts": retry_max_attempts,
                    "mode": "standard",
                }
            client = boto3.client("s3", config=Config(**config_arguments))
        self.client = cast(_S3Client, client)
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.object_lock = object_lock
        self.retention_days = retention_days
        self.operation_timeout_seconds = operation_timeout_seconds

    def _key(self, logical_key: str) -> str:
        parts = _validate_key(logical_key)
        suffix = "/".join(parts)
        return f"{self.prefix}/{suffix}" if self.prefix else suffix

    def _from_head(self, logical_key: str, head: dict[str, object]) -> ArtifactRef:
        metadata_value = head.get("Metadata") or {}
        if not isinstance(metadata_value, Mapping):
            raise ImmutableStoreError("S3 immutable metadata is not a mapping")
        metadata = metadata_value
        digest_value = metadata.get("sha256")
        media_type_value = metadata.get("media-type")
        if not isinstance(digest_value, str) or not isinstance(media_type_value, str):
            raise ImmutableStoreError("S3 immutable metadata is missing identity fields")
        digest = digest_value
        content_type = head.get("ContentType")
        if content_type is not None and content_type != media_type_value:
            raise ImmutableStoreError("S3 immutable media type metadata does not match HEAD")
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
        size_value = head.get("ContentLength", -1)
        if not isinstance(size_value, int) or isinstance(size_value, bool):
            raise ImmutableStoreError("S3 immutable object size is not an integer")
        try:
            return ArtifactRef(
                logical_key=logical_key,
                uri=f"s3://{self.bucket}/{self._key(logical_key)}",
                version_id=version_id,
                sha256=digest,
                size=size_value,
                media_type=media_type_value,
                retention_status=retention,
            )
        except ValueError as exc:
            raise ImmutableStoreError("S3 immutable metadata is malformed") from exc

    def get_reference(self, logical_key: str) -> ArtifactRef | None:
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=self._key(logical_key))
        except Exception as exc:  # botocore is optional; inspect its stable error response shape.
            response = getattr(exc, "response", {})
            code = str(response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise ImmutableStoreError("S3 immutable metadata lookup failed") from exc
        reference = self._from_head(logical_key, cast(dict[str, object], head))
        return reference

    def put_once(self, logical_key: str, data: bytes, *, media_type: str) -> ArtifactRef:
        _validate_key(logical_key)
        existing = self.get_reference(logical_key)
        digest = sha256_bytes(data)
        if existing is not None:
            if existing.sha256 != digest or existing.size != len(data) or existing.media_type != media_type:
                raise ImmutableStoreError("refusing conflicting bytes at immutable S3 key")
            self.get_verified(existing)
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
                self.get_verified(existing)
                return existing
            raise ImmutableStoreError("immutable S3 put failed") from exc
        head_arguments = {"Bucket": self.bucket, "Key": self._key(logical_key)}
        if result.get("VersionId"):
            head_arguments["VersionId"] = result["VersionId"]
        head = self.client.head_object(**head_arguments)
        reference = self._from_head(logical_key, cast(dict[str, object], head))
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
        if not isinstance(data, bytes):
            raise ImmutableStoreError("S3 immutable object body did not return bytes")
        if len(data) != reference.size or sha256_bytes(data) != reference.sha256:
            raise ImmutableStoreError("immutable S3 object failed size or digest verification")
        return data
