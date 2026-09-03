"""Versioned, declared capability contract for v5 policy launches."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pixelgym.grounding.v5.contracts import (
    SandboxManifest as LegacySandboxManifest,
)
from pixelgym.grounding.v5.contracts import (
    content_digest,
    sandbox_endpoint_allowlist_digest,
)

LEGACY_SANDBOX_MANIFEST_SCHEMA_VERSION = "pixelgym-agent-v5-sandbox-v2"
SANDBOX_MANIFEST_SCHEMA_VERSION = "pixelgym-agent-v5-sandbox-v3"
# Retained as the public policy-version name used by the probe profile.
SANDBOX_POLICY_VERSION = SANDBOX_MANIFEST_SCHEMA_VERSION
DECLARED_UNAVAILABLE_CAPABILITIES = tuple(sorted(LegacySandboxManifest.REQUIRED_DENIALS))
# Compatibility for callers which only use the values, not the old evidence claim.
DENIED_CAPABILITIES = DECLARED_UNAVAILABLE_CAPABILITIES

RuntimeMechanismName = Literal[
    "cli_flags_and_environment_allowlist",
    "not_bound_to_cli_launch",
]


@dataclass(frozen=True)
class ProbeResult:
    """Result of the separate no-cost probe, never evidence about a CLI child."""

    status: Literal["not_run", "passed", "failed", "unsupported"]
    mechanism_name: str = "darwin_sandbox_exec_probe_only"
    profile_digest: str | None = None
    applied_to_cli_launch: Literal[False] = False

    def __post_init__(self) -> None:
        if self.status not in {"not_run", "passed", "failed", "unsupported"}:
            raise ValueError("unsupported sandbox probe status")
        if self.mechanism_name != "darwin_sandbox_exec_probe_only":
            raise ValueError("unsupported sandbox probe mechanism")
        if self.applied_to_cli_launch is not False:
            raise ValueError("the probe sandbox must not claim application to a CLI launch")
        if self.profile_digest is not None and not self.profile_digest.startswith("sha256:"):
            raise ValueError("sandbox probe profile digest must be content addressed")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "mechanism_name": self.mechanism_name,
            "profile_digest": self.profile_digest,
            "applied_to_cli_launch": self.applied_to_cli_launch,
        }


@dataclass(frozen=True)
class RuntimeEnforcement:
    """Controls derived from the exact argv and environment passed to a child."""

    mechanism_name: RuntimeMechanismName
    argv_digest: str
    environment_allowlist_digest: str
    environment_variable_names: tuple[str, ...]
    cli_restrictions_applied: bool
    environment_allowlist_applied: bool
    os_sandbox_applied: Literal[False] = False

    def __post_init__(self) -> None:
        if self.mechanism_name not in {
            "cli_flags_and_environment_allowlist",
            "not_bound_to_cli_launch",
        }:
            raise ValueError("unsupported runtime enforcement mechanism")
        if self.os_sandbox_applied is not False:
            raise ValueError("v3 CLI launches are declared, not OS-sandbox enforced")
        if not self.argv_digest.startswith("sha256:"):
            raise ValueError("runtime argv digest must be content addressed")
        if not self.environment_allowlist_digest.startswith("sha256:"):
            raise ValueError("runtime environment allowlist digest must be content addressed")
        if self.environment_variable_names != tuple(sorted(set(self.environment_variable_names))):
            raise ValueError("runtime environment variable names must be sorted and unique")
        expected_environment_digest = content_digest(
            {"environment_variable_names": list(self.environment_variable_names)}
        )
        if self.environment_allowlist_digest != expected_environment_digest:
            raise ValueError("runtime environment allowlist digest does not match variable names")
        if type(self.cli_restrictions_applied) is not bool:
            raise TypeError("CLI restrictions applied marker must be boolean")
        if type(self.environment_allowlist_applied) is not bool:
            raise TypeError("environment allowlist applied marker must be boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "mechanism_name": self.mechanism_name,
            "argv_digest": self.argv_digest,
            "environment_allowlist_digest": self.environment_allowlist_digest,
            "environment_variable_names": list(self.environment_variable_names),
            "cli_restrictions_applied": self.cli_restrictions_applied,
            "environment_allowlist_applied": self.environment_allowlist_applied,
            "os_sandbox_applied": self.os_sandbox_applied,
        }


@dataclass(frozen=True)
class PolicyClaim:
    """Capabilities the policy requires to be unavailable to the model."""

    declared_unavailable_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.declared_unavailable_capabilities != tuple(
            sorted(set(self.declared_unavailable_capabilities))
        ):
            raise ValueError("declared unavailable capabilities must be sorted and unique")

    def to_dict(self) -> dict[str, object]:
        return {
            "declared_unavailable_capabilities": list(
                self.declared_unavailable_capabilities
            )
        }


@dataclass(frozen=True)
class DeclaredSandboxManifest:
    """Corrected manifest which separates probe, launch controls, and claim."""

    schema_version: str
    runtime_digest: str
    provider_endpoint: str
    provider_endpoint_allowlist_digest: str
    probe_result: ProbeResult
    runtime_enforcement: RuntimeEnforcement
    policy_claim: PolicyClaim

    REQUIRED_DENIALS: ClassVar[frozenset[str]] = LegacySandboxManifest.REQUIRED_DENIALS

    def __post_init__(self) -> None:
        if self.schema_version != SANDBOX_MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported declared sandbox manifest schema version")
        if not self.runtime_digest.startswith("sha256:"):
            raise ValueError("sandbox runtime must be content addressed")
        expected = sandbox_endpoint_allowlist_digest(
            self.provider_endpoint,
            policy_version=self.schema_version,
        )
        if self.provider_endpoint_allowlist_digest != expected:
            raise ValueError("sandbox endpoint allowlist digest does not match provider endpoint")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "runtime_digest": self.runtime_digest,
            "provider_endpoint": self.provider_endpoint,
            "provider_endpoint_allowlist_digest": self.provider_endpoint_allowlist_digest,
            "probe_result": self.probe_result.to_dict(),
            "runtime_enforcement": self.runtime_enforcement.to_dict(),
            "policy_claim": self.policy_claim.to_dict(),
        }


SandboxManifestVersion = LegacySandboxManifest | DeclaredSandboxManifest


def endpoint_allowlist_digest(endpoint: str) -> str:
    return sandbox_endpoint_allowlist_digest(
        endpoint,
        policy_version=SANDBOX_MANIFEST_SCHEMA_VERSION,
    )


def environment_allowlist_digest(environment: Mapping[str, str]) -> str:
    """Bind the allowlisted variable names without retaining credential values."""

    return content_digest({"environment_variable_names": sorted(environment)})


def runtime_enforcement(
    *,
    argv: Sequence[str],
    environment: Mapping[str, str],
    cli_restrictions_applied: bool,
    environment_allowlist_applied: bool,
    mechanism_name: RuntimeMechanismName = "cli_flags_and_environment_allowlist",
) -> RuntimeEnforcement:
    names = tuple(sorted(environment))
    return RuntimeEnforcement(
        mechanism_name=mechanism_name,
        argv_digest=content_digest(list(argv)),
        environment_allowlist_digest=environment_allowlist_digest(environment),
        environment_variable_names=names,
        cli_restrictions_applied=cli_restrictions_applied,
        environment_allowlist_applied=environment_allowlist_applied,
    )


def unbound_runtime_enforcement() -> RuntimeEnforcement:
    return runtime_enforcement(
        argv=(),
        environment={},
        cli_restrictions_applied=False,
        environment_allowlist_applied=False,
        mechanism_name="not_bound_to_cli_launch",
    )


def build_sandbox_manifest(
    *,
    runtime_digest: str,
    provider_endpoint: str,
    launch_enforcement: RuntimeEnforcement | None = None,
    probe_result: ProbeResult | None = None,
) -> SandboxManifestVersion:
    if launch_enforcement is None:
        if probe_result is not None:
            raise ValueError("legacy sandbox manifests cannot record a v3 probe result")
        return LegacySandboxManifest(
            runtime_digest=runtime_digest,
            network_policy_version=LEGACY_SANDBOX_MANIFEST_SCHEMA_VERSION,
            provider_endpoint=provider_endpoint,
            endpoint_allowlist_digest=sandbox_endpoint_allowlist_digest(
                provider_endpoint,
                policy_version=LEGACY_SANDBOX_MANIFEST_SCHEMA_VERSION,
            ),
            denied_capabilities=DECLARED_UNAVAILABLE_CAPABILITIES,
        )
    return DeclaredSandboxManifest(
        schema_version=SANDBOX_MANIFEST_SCHEMA_VERSION,
        runtime_digest=runtime_digest,
        provider_endpoint=provider_endpoint,
        provider_endpoint_allowlist_digest=endpoint_allowlist_digest(provider_endpoint),
        probe_result=probe_result or ProbeResult(status="not_run"),
        runtime_enforcement=launch_enforcement,
        policy_claim=PolicyClaim(DECLARED_UNAVAILABLE_CAPABILITIES),
    )


def validate_runtime_enforcement(
    policy_claim: PolicyClaim,
    launch_enforcement: RuntimeEnforcement,
) -> None:
    if policy_claim.declared_unavailable_capabilities and not (
        launch_enforcement.mechanism_name == "cli_flags_and_environment_allowlist"
        and launch_enforcement.cli_restrictions_applied
        and launch_enforcement.environment_allowlist_applied
    ):
        raise ValueError(
            "runtime does not apply controls required by the declared capability policy"
        )


def load_sandbox_manifest(value: Mapping[str, Any]) -> SandboxManifestVersion:
    """Load immutable v2 evidence or the corrected declared-contract v3 schema."""

    schema_version = value.get("schema_version")
    if schema_version is None:
        legacy = dict(value)
        denied = legacy.get("denied_capabilities")
        if isinstance(denied, list):
            legacy["denied_capabilities"] = tuple(denied)
        return LegacySandboxManifest(**legacy)
    if schema_version != SANDBOX_MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"unsupported sandbox manifest schema version: {schema_version!r}")
    expected_fields = {
        "schema_version",
        "runtime_digest",
        "provider_endpoint",
        "provider_endpoint_allowlist_digest",
        "probe_result",
        "runtime_enforcement",
        "policy_claim",
    }
    if set(value) != expected_fields:
        raise ValueError("declared sandbox manifest fields differ from the v3 schema")
    probe = value.get("probe_result")
    enforcement = value.get("runtime_enforcement")
    claim = value.get("policy_claim")
    if not isinstance(probe, Mapping):
        raise TypeError("sandbox probe result must be an object")
    if not isinstance(enforcement, Mapping):
        raise TypeError("sandbox runtime enforcement must be an object")
    if not isinstance(claim, Mapping):
        raise TypeError("sandbox policy claim must be an object")
    if set(probe) != {
        "status",
        "mechanism_name",
        "profile_digest",
        "applied_to_cli_launch",
    }:
        raise ValueError("sandbox probe result fields differ from the v3 schema")
    if set(enforcement) != {
        "mechanism_name",
        "argv_digest",
        "environment_allowlist_digest",
        "environment_variable_names",
        "cli_restrictions_applied",
        "environment_allowlist_applied",
        "os_sandbox_applied",
    }:
        raise ValueError("runtime enforcement fields differ from the v3 schema")
    if set(claim) != {"declared_unavailable_capabilities"}:
        raise ValueError("sandbox policy claim fields differ from the v3 schema")
    return DeclaredSandboxManifest(
        schema_version=str(schema_version),
        runtime_digest=str(value.get("runtime_digest", "")),
        provider_endpoint=str(value.get("provider_endpoint", "")),
        provider_endpoint_allowlist_digest=str(
            value.get("provider_endpoint_allowlist_digest", "")
        ),
        probe_result=ProbeResult(
            status=probe.get("status"),  # type: ignore[arg-type]
            mechanism_name=str(probe.get("mechanism_name", "")),
            profile_digest=(
                str(probe["profile_digest"])
                if probe.get("profile_digest") is not None
                else None
            ),
            applied_to_cli_launch=probe.get("applied_to_cli_launch"),  # type: ignore[arg-type]
        ),
        runtime_enforcement=RuntimeEnforcement(
            mechanism_name=enforcement.get("mechanism_name"),  # type: ignore[arg-type]
            argv_digest=str(enforcement.get("argv_digest", "")),
            environment_allowlist_digest=str(
                enforcement.get("environment_allowlist_digest", "")
            ),
            environment_variable_names=tuple(enforcement.get("environment_variable_names", ())),
            cli_restrictions_applied=enforcement.get(  # type: ignore[arg-type]
                "cli_restrictions_applied"
            ),
            environment_allowlist_applied=enforcement.get(  # type: ignore[arg-type]
                "environment_allowlist_applied"
            ),
            os_sandbox_applied=enforcement.get("os_sandbox_applied"),  # type: ignore[arg-type]
        ),
        policy_claim=PolicyClaim(
            tuple(claim.get("declared_unavailable_capabilities", ()))
        ),
    )


def validate_capability_handles(handles: dict[str, object]) -> None:
    forbidden = set(DECLARED_UNAVAILABLE_CAPABILITIES) & set(handles)
    if forbidden:
        raise ValueError(f"policy package requested forbidden capabilities: {sorted(forbidden)}")
    allowed = {"provider_transport", "attempt_journal", "screenshot"}
    unexpected = set(handles) - allowed
    if unexpected:
        raise ValueError(f"policy package requested unknown capabilities: {sorted(unexpected)}")
