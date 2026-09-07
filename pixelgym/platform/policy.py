"""Immutable grounding policy packaging."""

from __future__ import annotations

import inspect
import re
from dataclasses import replace
from typing import Any

from pixelgym.grounding.evaluation import COMMON_PROMPT_TEMPLATE, RAW_PROMPT_SUFFIX
from pixelgym.platform.contracts import PolicyManifest
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.source_provenance import SourceProvenance

# Bumped from pixelgym-grounding-policy-v1 when renderer identity was bound into the
# manifest. Packages built under v1 remain fully readable (see PolicyManifest.identity_dict);
# the platform-policy schema accepts both values but only requires renderer fields for v2.
POLICY_SCHEMA_VERSION = "pixelgym-grounding-policy-v2"
LEGACY_POLICY_SCHEMA_VERSION = "pixelgym-grounding-policy-v1"
PROMPT_NAME = "pixelgym-grounding"
# Full, self-sufficient request templates -- base text plus any version-specific
# addendum -- with {target}/{screen_width}/{screen_height} placeholders. Each one is
# packaged verbatim as a candidate's prompt_template_text (see build_policy_manifest),
# so prompt_sha256 digests exactly what render_prompt sends, not a fragment of it.
PROMPT_TEMPLATES = {
    1: COMMON_PROMPT_TEMPLATE + RAW_PROMPT_SUFFIX,
    2: (
        COMMON_PROMPT_TEMPLATE
        + RAW_PROMPT_SUFFIX
        + " Locate the editable control named by the target, not a matching summary "
        "label. Target: {target}. Return only integer screenshot-pixel coordinates as JSON."
    ),
}
# Identifies the renderer code that turns a packaged prompt_template_text plus a
# request's target/width/height into the exact bytes a provider sees (render_prompt,
# below). The template text itself is a manifest field already covered by
# prompt_sha256; this covers only the substitution code, so adding a new
# PROMPT_TEMPLATES entry for a future prompt_version cannot change this digest.
RENDERER_VERSION = "pixelgym-platform-renderer-v1"

_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def prompt_template(version: int) -> str:
    try:
        return PROMPT_TEMPLATES[version]
    except KeyError as exc:
        raise ValueError(f"unknown prompt version {version}") from exc


def render_prompt(manifest: PolicyManifest, *, target: str, width: int, height: int) -> str:
    """Render the exact request text a provider sees, entirely from the packaged renderer.

    Substitutes target/width/height into the candidate's own packaged
    ``prompt_template_text`` -- the frozen bytes recorded in the policy package -- with
    no dependency on whatever prompt-composition code is currently running. Evaluation
    and serving both call this, so they invoke the identical renderer/parser contract.
    """
    if manifest.renderer_version is None or manifest.prompt_template_text is None:
        raise ValueError("policy manifest does not carry packaged renderer identity")
    if manifest.condition != "raw":
        raise ValueError("the packaged renderer supports raw-coordinate policies only")
    return manifest.prompt_template_text.format(target=target, screen_width=width, screen_height=height)


def renderer_config() -> dict[str, Any]:
    """Return the declarative configuration that identifies the renderer implementation."""
    return {
        "renderer_version": RENDERER_VERSION,
        "composition": (
            "render_prompt(manifest, target, width, height) == "
            "manifest.prompt_template_text.format(target=target, screen_width=width, "
            "screen_height=height); no other code path determines served request bytes"
        ),
        "render_prompt_source": inspect.getsource(render_prompt),
    }


def renderer_config_sha256() -> str:
    return sha256_bytes(canonical_json_bytes(renderer_config()))


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
        renderer_version=RENDERER_VERSION,
        renderer_sha256=renderer_config_sha256(),
        prompt_template_text=prompt,
    )
    policy_id = "sha256:" + sha256_bytes(canonical_json_bytes(manifest.identity_dict()))
    return replace(manifest, policy_id=policy_id)


def verify_policy_manifest(manifest: PolicyManifest) -> None:
    actual = "sha256:" + sha256_bytes(canonical_json_bytes(manifest.identity_dict()))
    if actual != manifest.policy_id:
        raise ValueError("policy manifest digest mismatch")


def verify_renderer_binding(manifest: PolicyManifest) -> None:
    """Fail closed unless the manifest's packaged renderer matches the running renderer.

    This is stricter than ``verify_policy_manifest``: a policy built before renderer
    identity existed can still verify its own (unchanged) digest, but it cannot pass
    this check, so it cannot be newly activated. Called at candidate registration,
    deploy, rollback, and serving-startup restore -- never at read time -- so
    pre-renderer evidence stays readable while a candidate can only reach Eligible,
    Approved, or active-traffic state on a package whose renderer is bound and matches
    the code actually running the request.
    """
    if manifest.renderer_version is None or manifest.renderer_sha256 is None or manifest.prompt_template_text is None:
        raise ValueError("policy manifest is missing packaged renderer identity")
    if manifest.renderer_version != RENDERER_VERSION:
        raise ValueError(f"unsupported renderer version {manifest.renderer_version!r}")
    if manifest.renderer_sha256 != renderer_config_sha256():
        raise ValueError("renderer implementation digest mismatch")
    if manifest.prompt_sha256 != sha256_bytes(manifest.prompt_template_text.encode("utf-8")):
        raise ValueError("packaged prompt digest does not match the packaged prompt bytes")
    if manifest.prompt_template_text != PROMPT_TEMPLATES.get(manifest.prompt_version):
        raise ValueError(
            "packaged prompt template does not match the known template for "
            f"prompt_version {manifest.prompt_version}"
        )
