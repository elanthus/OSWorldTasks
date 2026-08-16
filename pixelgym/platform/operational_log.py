"""Privacy-preserving, immutable records for serving requests.

The serving API never stores request bodies, screenshots, prompts, targets, or provider response
text here.  It stores only the operational fields needed to reconstruct which approved policy
handled a request and how that request ended.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Protocol, cast

from pixelgym.platform.fingerprints import canonical_json_bytes
from pixelgym.platform.immutable_store import ImmutableStore

OPERATIONAL_RECORD_SCHEMA_VERSION = "pixelgym-serving-operational-record-v1"
_REQUEST_ID = re.compile(r"^srv-[0-9a-f]{32}$")
_PROVIDER_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_USAGE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class ProviderMetadata:
    """Provider fields admitted by the single operational-metadata boundary."""

    request_id: str
    latency_ms: float | None = None
    usage: Mapping[str, int | float] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not _PROVIDER_REQUEST_ID.fullmatch(
            self.request_id
        ):
            raise ValueError("provider request ID is malformed")
        if self.latency_ms is not None and (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, (int, float))
            or not math.isfinite(self.latency_ms)
            or self.latency_ms < 0
        ):
            raise ValueError("provider latency is malformed")
        if self.usage is None:
            normalized_usage = None
        elif not isinstance(self.usage, Mapping):
            raise ValueError("provider usage is malformed")
        else:
            normalized_usage = {}
            for key, value in self.usage.items():
                if (
                    not isinstance(key, str)
                    or not _USAGE_KEY.fullmatch(key)
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise ValueError("provider usage is malformed")
                normalized_usage[key] = value
        object.__setattr__(
            self,
            "latency_ms",
            float(self.latency_ms) if self.latency_ms is not None else None,
        )
        object.__setattr__(
            self,
            "usage",
            MappingProxyType(normalized_usage) if normalized_usage is not None else None,
        )


def normalize_provider_metadata(
    request_id: object, latency_ms: object, usage: object
) -> ProviderMetadata:
    return ProviderMetadata(
        cast(str, request_id),
        cast(float | None, latency_ms),
        cast(Mapping[str, int | float] | None, usage),
    )


class OperationalLogError(RuntimeError):
    """A serving record could not be written or verified."""


@dataclass(frozen=True)
class OperationalRecord:
    """A stable, redacted record of one request to the grounding endpoint."""

    schema_version: str
    request_id: str
    occurred_at: str
    policy_id: str | None
    deployment_id: str | None
    exact_policy_version: str | None
    terminal_status: str
    http_status: int
    latency_ms: float
    provider_metadata: ProviderMetadata | None

    def __post_init__(self) -> None:
        if self.schema_version != OPERATIONAL_RECORD_SCHEMA_VERSION:
            raise ValueError("unsupported operational record schema")
        if not _REQUEST_ID.fullmatch(self.request_id):
            raise ValueError("operational request ID is malformed")
        try:
            occurred_at = datetime.fromisoformat(self.occurred_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("operational timestamp is malformed") from exc
        if occurred_at.tzinfo is None or occurred_at.utcoffset() != UTC.utcoffset(occurred_at):
            raise ValueError("operational timestamp must be UTC")
        if not self.terminal_status or not 100 <= self.http_status <= 599:
            raise ValueError("operational terminal status and HTTP status are required")
        if not math.isfinite(self.latency_ms) or self.latency_ms < 0:
            raise ValueError("operational latency must be finite and nonnegative")
        if self.provider_metadata is not None and not isinstance(
            self.provider_metadata, ProviderMetadata
        ):
            raise TypeError("provider metadata must be normalized before record construction")

    @property
    def provider_latency_ms(self) -> float | None:
        return self.provider_metadata.latency_ms if self.provider_metadata is not None else None

    @property
    def provider_request_id(self) -> str | None:
        return self.provider_metadata.request_id if self.provider_metadata is not None else None

    @property
    def usage(self) -> Mapping[str, int | float] | None:
        return self.provider_metadata.usage if self.provider_metadata is not None else None

    @classmethod
    def from_dict(cls, value: object) -> OperationalRecord:
        if not isinstance(value, dict):
            raise TypeError("operational record must be a JSON object")
        payload = dict(value)
        provider_request_id = payload.pop("provider_request_id")
        provider_latency_ms = payload.pop("provider_latency_ms")
        usage = payload.pop("usage")
        if provider_request_id is None:
            if provider_latency_ms is not None or usage is not None:
                raise ValueError("provider metadata without a request ID is malformed")
            provider_metadata = None
        else:
            provider_metadata = normalize_provider_metadata(
                provider_request_id, provider_latency_ms, usage
            )
        return cls(**payload, provider_metadata=provider_metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "occurred_at": self.occurred_at,
            "policy_id": self.policy_id,
            "deployment_id": self.deployment_id,
            "exact_policy_version": self.exact_policy_version,
            "terminal_status": self.terminal_status,
            "http_status": self.http_status,
            "latency_ms": self.latency_ms,
            "provider_latency_ms": self.provider_latency_ms,
            "provider_request_id": self.provider_request_id,
            "usage": dict(self.usage) if self.usage is not None else None,
        }


class OperationalLog(Protocol):
    def append(self, record: OperationalRecord) -> None: ...

    def get(self, request_id: str) -> OperationalRecord | None: ...


class ImmutableOperationalLog:
    """Append-only operational records backed by the configured immutable store.

    Record IDs are collision-resistant random UUID material generated by the service.  The
    deterministic logical key makes a record independently retrievable and each read rechecks the
    immutable store's pinned digest/version boundary.  Appending intentionally makes exactly two
    ImmutableStore API calls: ``put_once`` followed by ``get_verified``.  The read-back is required
    evidence that the returned pinned reference resolves to the bytes just recorded; do not reduce
    this to a write-only acknowledgement.
    """

    def __init__(self, store: ImmutableStore) -> None:
        self.store = store

    @staticmethod
    def _key(request_id: str) -> str:
        if not _REQUEST_ID.fullmatch(request_id):
            raise ValueError("operational request ID is malformed")
        return f"serving-operational-records/{request_id}.json"

    def append(self, record: OperationalRecord) -> None:
        key = self._key(record.request_id)
        data = canonical_json_bytes(record.to_dict()) + b"\n"
        # Request IDs are random UUID material, so distinct requests use distinct immutable keys.
        # Serializing remote/filesystem I/O here only creates head-of-line blocking and supplies no
        # integrity property; the immutable store remains the authority for a duplicate key.
        try:
            reference = self.store.put_once(key, data, media_type="application/json")
            if self.store.get_verified(reference) != data:
                raise OperationalLogError("operational record verification returned changed bytes")
        except Exception as exc:
            raise OperationalLogError("failed to durably record serving operation") from exc

    def get(self, request_id: str) -> OperationalRecord | None:
        key = self._key(request_id)
        try:
            reference = self.store.get_reference(key)
            if reference is None:
                return None
            value = json.loads(self.store.get_verified(reference))
            record = OperationalRecord.from_dict(value)
        except OperationalLogError:
            raise
        except Exception as exc:
            raise OperationalLogError("failed to retrieve verified serving operation") from exc
        if record.request_id != request_id:
            raise OperationalLogError("operational record identity does not match its key")
        return record


class MemoryOperationalLog:
    """Explicit test/smoke-only log; traffic serving must use ImmutableOperationalLog."""

    def __init__(self) -> None:
        self._records: dict[str, OperationalRecord] = {}
        self._lock = threading.RLock()

    def append(self, record: OperationalRecord) -> None:
        with self._lock:
            if record.request_id in self._records:
                raise OperationalLogError("duplicate operational request ID")
            self._records[record.request_id] = record

    def get(self, request_id: str) -> OperationalRecord | None:
        with self._lock:
            return self._records.get(request_id)
