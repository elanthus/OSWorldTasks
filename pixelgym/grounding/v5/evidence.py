"""Credential-safe authoritative and publishable v5 evidence handling."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pixelgym.grounding.v5.contracts import REDACTION_POLICY_VERSION, content_digest
from pixelgym.platform.contracts import ArtifactRef
from pixelgym.platform.immutable_store import ImmutableStore, LocalImmutableStore
from pixelgym.serialization import canonical_json_bytes

_SECRET_FIELD = re.compile(
    r"(?:^|_)(?:api_?key|authorization|credential|password|private_?key|secret|token)(?:$|_)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._~-]{12,}|-----BEGIN [A-Z ]+PRIVATE KEY-----)"
)
_SENSITIVE_QUERY = re.compile(r"key|token|secret|password|credential|signature", re.IGNORECASE)
_REDACT_KEYS = re.compile(
    r"(?:^|_)host(?:name)?(?:$|_)|username|account_?id|provider_private|policy_state|app_url|endpoint",
    re.IGNORECASE,
)

JOURNAL_INTEGRITY_SCHEMA_VERSION = "pixelgym-agent-v5-journal-integrity-v1"
JOURNAL_DIGEST_VERSION_V1 = "v1"
JOURNAL_DIGEST_VERSION_V2 = "v2"


def journal_integrity_audit_record(report: Mapping[str, Any]) -> dict[str, Any]:
    """Make the event-chain digest version explicit in publishable audit evidence.

    Historical v1 journal reports predate the ``digest_version`` field. Their reports
    must remain unchanged for frozen-evidence comparison, so publication code records
    the implied v1 version in a separate derivative rather than rewriting the journal
    report itself.
    """

    if report.get("schema_version") != JOURNAL_INTEGRITY_SCHEMA_VERSION:
        raise ValueError("unsupported journal integrity schema version")
    digest_version = report.get("digest_version", JOURNAL_DIGEST_VERSION_V1)
    if digest_version not in {JOURNAL_DIGEST_VERSION_V1, JOURNAL_DIGEST_VERSION_V2}:
        raise ValueError("unsupported journal event-chain digest version")
    return {**report, "digest_version": digest_version}


def repository_relative_path(repository_root: Path, path: Path) -> str:
    """Record an evidence path as a repository-relative POSIX string.

    Evidence files are published. Recording ``str(path)`` stores whatever the operator
    typed on the command line, so an absolute invocation leaks the operator's home
    directory into a committed artifact and makes the value differ between machines.
    Normalising against the repository root keeps the recorded value invariant to how
    the run was invoked.

    A path outside the repository has no portable form, so it is returned resolved. That
    case is unreachable for published evidence, which is always written inside the
    repository; ``test_checked_in_text_artifacts_have_no_local_absolute_paths`` remains
    the backstop that keeps such a path from ever being committed.
    """

    resolved = path.resolve()
    try:
        return resolved.relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


class CredentialValidationError(ValueError):
    """A credential-shaped field class was found; candidate bytes are never included."""


def validate_credential_free(value: Any, *, field_class: str = "root") -> None:
    """Reject credential-bearing values without echoing the candidate secret."""

    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if _SECRET_FIELD.search(key_text):
                raise CredentialValidationError(f"credential-shaped field class: {key_text}")
            validate_credential_free(child, field_class=key_text)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            validate_credential_free(child, field_class=field_class)
        return
    if not isinstance(value, str):
        return
    if value.startswith(("secret://", "vault://", "keychain://")):
        raise CredentialValidationError(f"runtime secret handle in field class: {field_class}")
    if _SECRET_VALUE.search(value):
        raise CredentialValidationError(f"credential-shaped value in field class: {field_class}")
    parts = urlsplit(value)
    if parts.scheme and parts.netloc:
        if parts.username is not None or parts.password is not None:
            raise CredentialValidationError(f"URL userinfo in field class: {field_class}")
        if any(_SENSITIVE_QUERY.search(key) for key, _ in parse_qsl(parts.query)):
            raise CredentialValidationError(
                f"credential-shaped URL parameter in field class: {field_class}"
            )


def redact_publishable(value: Any) -> Any:
    """Pure deterministic redaction for a checked-in/publishable derivative."""

    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _REDACT_KEYS.search(str(key)) else redact_publishable(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list):
        return [redact_publishable(child) for child in value]
    if isinstance(value, tuple):
        return [redact_publishable(child) for child in value]
    if isinstance(value, str):
        parts = urlsplit(value)
        if parts.scheme and parts.netloc:
            return "[redacted-url]"
    return value


@dataclass(frozen=True)
class EvidenceRelation:
    authoritative_digest: str
    derivative_digest: str
    redaction_policy_version: str = REDACTION_POLICY_VERSION

    def to_dict(self) -> dict[str, str]:
        return {
            "authoritative_digest": self.authoritative_digest,
            "derivative_digest": self.derivative_digest,
            "redaction_policy_version": self.redaction_policy_version,
        }


class V5EvidenceStore:
    """Content-bound evidence facade over the project's immutable store."""

    def __init__(self, root: Path | None = None, *, store: ImmutableStore | None = None) -> None:
        if store is None and root is None:
            raise ValueError("root or store is required")
        if store is None:
            assert root is not None
            store = LocalImmutableStore(Path(root))
        self.store = store
        self._references: list[ArtifactRef] = []

    def put_authoritative(self, logical_key: str, value: Any) -> ArtifactRef:
        validate_credential_free(value)
        reference = self.store.put_once(
            f"authoritative/{logical_key}",
            canonical_json_bytes(value),
            media_type="application/json",
        )
        self._references.append(reference)
        return reference

    def put_bytes(self, logical_key: str, data: bytes, *, media_type: str) -> ArtifactRef:
        reference = self.store.put_once(
            f"authoritative/{logical_key}", data, media_type=media_type
        )
        self._references.append(reference)
        return reference

    def publish_derivative(self, logical_key: str, authoritative: Any) -> tuple[ArtifactRef, ArtifactRef]:
        validate_credential_free(authoritative)
        authoritative_ref = self.store.get_reference(f"authoritative/{logical_key}")
        if authoritative_ref is None:
            raise ValueError("authoritative evidence must be stored before publication")
        if self.store.get_verified(authoritative_ref) != canonical_json_bytes(authoritative):
            raise ValueError("publishable source does not match stored authoritative evidence")
        derivative = redact_publishable(authoritative)
        authoritative_digest = content_digest(authoritative)
        derivative_digest = content_digest(derivative)
        derivative_ref = self.store.put_once(
            f"publishable/{logical_key}",
            canonical_json_bytes(derivative),
            media_type="application/json",
        )
        relation = EvidenceRelation(authoritative_digest, derivative_digest)
        relation_ref = self.store.put_once(
            f"relations/{logical_key}",
            canonical_json_bytes(relation.to_dict()),
            media_type="application/json",
        )
        self._references.extend((derivative_ref, relation_ref))
        return derivative_ref, relation_ref

    def integrity_report(self) -> dict[str, Any]:
        checked: list[dict[str, Any]] = []
        for reference in self._references:
            data = self.store.get_verified(reference)
            checked.append(
                {
                    "logical_key": reference.logical_key,
                    "sha256": reference.sha256,
                    "size": len(data),
                    "verified": True,
                }
            )
        return {
            "schema_version": "pixelgym-agent-v5-integrity-v1",
            "object_count": len(checked),
            "objects": checked,
        }
