"""Immutable grounding policy packaging."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from pixelgym.platform.contracts import PolicyManifest
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.source_provenance import SourceProvenance

POLICY_SCHEMA_VERSION = "pixelgym-grounding-policy-v1"
PROMPT_NAME = "pixelgym-grounding"
PROMPT_TEMPLATES = {
    1: (
        "Locate the requested control in the screenshot. Target: {{target}}. "
        "Return only integer screenshot-pixel coordinates as JSON."
    ),
    2: (
        "Locate the editable control named by the target, not a matching summary label. "
        "Target: {{target}}. Return only integer screenshot-pixel coordinates as JSON."
    ),
}

_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def prompt_template(version: int) -> str:
    try:
        return PROMPT_TEMPLATES[version]
    except KeyError as exc:
        raise ValueError(f"unknown prompt version {version}") from exc


def is_verified_clean_revision(value: str) -> bool:
    """Return whether a revision has valid Git-commit *format*, not provenance."""
    return bool(_GIT_COMMIT_RE.fullmatch(value))


def build_policy_manifest(
    *,
    provider: str,
    model: str,
    prompt_name: str,
    prompt_version: int,
    prompt: str,
    condition: str,
    parameters: dict[str, Any],
    parser_version: str,
    scorer_version: str,
    overlay_version: str,
    target_semantics: str,
    source_provenance: SourceProvenance,
    dependency_lock_sha256: str,
    model_alias_disclosure: str | None = None,
) -> PolicyManifest:
    if condition not in {"raw", "marks"}:
        raise ValueError("condition must be raw or marks")
    if prompt_version <= 0:
        raise ValueError("prompt_version must be positive")
    manifest = PolicyManifest(
        schema_version=POLICY_SCHEMA_VERSION,
        provider=provider,
        model=model,
        model_alias_disclosure=model_alias_disclosure,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        prompt_sha256=sha256_bytes(prompt.encode("utf-8")),
        condition=condition,
        parameters=dict(parameters),
        parser_version=parser_version,
        scorer_version=scorer_version,
        overlay_version=overlay_version,
        target_semantics=target_semantics,
        code_revision=source_provenance.revision or "unverifiable",
        code_state=source_provenance.state,
        source_tree_sha256=source_provenance.source_tree_sha256,
        source_provenance_verified=source_provenance.state != "unverifiable",
        dependency_lock_sha256=dependency_lock_sha256,
        source_provenance_failure_reason=source_provenance.failure_reason,
    )
    policy_id = "sha256:" + sha256_bytes(canonical_json_bytes(manifest.identity_dict()))
    return replace(manifest, policy_id=policy_id)


def verify_policy_manifest(manifest: PolicyManifest) -> None:
    actual = "sha256:" + sha256_bytes(canonical_json_bytes(manifest.identity_dict()))
    if actual != manifest.policy_id:
        raise ValueError("policy manifest digest mismatch")
