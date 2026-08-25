"""Frozen policy-sandbox identity and capability-contract validation."""

from __future__ import annotations

from pixelgym.grounding.v5.contracts import (
    SandboxManifest,
    sandbox_endpoint_allowlist_digest,
)

SANDBOX_POLICY_VERSION = "pixelgym-agent-v5-sandbox-v1"
DENIED_CAPABILITIES = tuple(sorted(SandboxManifest.REQUIRED_DENIALS))


def endpoint_allowlist_digest(endpoint: str) -> str:
    return sandbox_endpoint_allowlist_digest(
        endpoint,
        policy_version=SANDBOX_POLICY_VERSION,
    )


def build_sandbox_manifest(*, runtime_digest: str, provider_endpoint: str) -> SandboxManifest:
    return SandboxManifest(
        runtime_digest=runtime_digest,
        network_policy_version=SANDBOX_POLICY_VERSION,
        provider_endpoint=provider_endpoint,
        endpoint_allowlist_digest=endpoint_allowlist_digest(provider_endpoint),
        denied_capabilities=DENIED_CAPABILITIES,
    )


def validate_capability_handles(handles: dict[str, object]) -> None:
    forbidden = SandboxManifest.REQUIRED_DENIALS & set(handles)
    if forbidden:
        raise ValueError(f"policy package requested forbidden capabilities: {sorted(forbidden)}")
    allowed = {"provider_transport", "attempt_journal", "screenshot"}
    unexpected = set(handles) - allowed
    if unexpected:
        raise ValueError(f"policy package requested unknown capabilities: {sorted(unexpected)}")
