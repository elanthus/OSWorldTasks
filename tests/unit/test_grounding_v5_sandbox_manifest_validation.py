"""Legacy and declared sandbox manifests load, and invalid claims are rejected."""

from __future__ import annotations

from copy import deepcopy

import pytest

from pixelgym.grounding.v5.contracts import sandbox_endpoint_allowlist_digest
from pixelgym.grounding.v5.sandbox import (
    LEGACY_SANDBOX_MANIFEST_SCHEMA_VERSION,
    SANDBOX_MANIFEST_SCHEMA_VERSION,
    DeclaredSandboxManifest,
    PolicyClaim,
    ProbeResult,
    RuntimeEnforcement,
    build_sandbox_manifest,
    load_sandbox_manifest,
    unbound_runtime_enforcement,
)


@pytest.fixture
def legacy_sandbox_manifest_fixture() -> dict[str, object]:
    endpoint = "https://provider.example.invalid"
    return {
        "runtime_digest": "sha256:" + "1" * 64,
        "network_policy_version": LEGACY_SANDBOX_MANIFEST_SCHEMA_VERSION,
        "provider_endpoint": endpoint,
        "endpoint_allowlist_digest": sandbox_endpoint_allowlist_digest(
            endpoint,
            policy_version=LEGACY_SANDBOX_MANIFEST_SCHEMA_VERSION,
        ),
        "denied_capabilities": [
            "browser_dom",
            "cross_policy_channel",
            "external_search",
            "inbound_listener",
            "shared_storage",
            "shell",
        ],
    }


@pytest.fixture
def declared_sandbox_manifest_fixture() -> dict[str, object]:
    return build_sandbox_manifest(
        runtime_digest="sha256:" + "2" * 64,
        provider_endpoint="https://provider.example.invalid",
        launch_enforcement=unbound_runtime_enforcement(),
        policy_claim=PolicyClaim(()),
    ).to_dict()


def test_legacy_and_declared_sandbox_manifest_fixtures_remain_loadable(
    legacy_sandbox_manifest_fixture: dict[str, object],
    declared_sandbox_manifest_fixture: dict[str, object],
) -> None:
    legacy = load_sandbox_manifest(legacy_sandbox_manifest_fixture)
    declared = load_sandbox_manifest(declared_sandbox_manifest_fixture)

    assert legacy.network_policy_version == LEGACY_SANDBOX_MANIFEST_SCHEMA_VERSION
    assert isinstance(declared, DeclaredSandboxManifest)
    assert declared.schema_version == SANDBOX_MANIFEST_SCHEMA_VERSION
    value = declared.to_dict()
    assert set(value) >= {"probe_result", "runtime_enforcement", "policy_claim"}
    assert "denied_capabilities" not in value


def test_declared_contract_structurally_rejects_os_level_launch_claims() -> None:
    unbound = unbound_runtime_enforcement()
    with pytest.raises(ValueError, match="not OS-sandbox enforced"):
        RuntimeEnforcement(
            **{
                **unbound.__dict__,
                "os_sandbox_applied": True,
            }  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="must not claim application"):
        ProbeResult(status="passed", applied_to_cli_launch=True)  # type: ignore[arg-type]


def test_sandbox_manifest_builder_fails_closed_on_unenforced_policy_claim() -> None:
    with pytest.raises(ValueError, match="does not apply controls"):
        build_sandbox_manifest(
            runtime_digest="sha256:" + "2" * 64,
            provider_endpoint="https://provider.example.invalid",
            launch_enforcement=unbound_runtime_enforcement(),
        )


@pytest.mark.parametrize(
    ("case", "exception", "message"),
    [
        ("probe_status", ValueError, "unsupported sandbox probe status"),
        ("probe_mechanism", ValueError, "unsupported sandbox probe mechanism"),
        ("probe_applied", ValueError, "must not claim application"),
        ("probe_digest_prefix", ValueError, "profile digest must be content addressed"),
        ("runtime_mechanism", ValueError, "unsupported runtime enforcement mechanism"),
        ("os_sandbox_applied", ValueError, "not OS-sandbox enforced"),
        ("argv_digest_prefix", ValueError, "argv digest must be content addressed"),
        (
            "environment_digest_prefix",
            ValueError,
            "environment allowlist digest must be content addressed",
        ),
        ("environment_names_unsorted", ValueError, "must be sorted and unique"),
        ("environment_names_duplicate", ValueError, "must be sorted and unique"),
        ("environment_digest_mismatch", ValueError, "does not match variable names"),
        ("cli_marker", TypeError, "CLI restrictions applied marker must be boolean"),
        ("environment_marker", TypeError, "allowlist applied marker must be boolean"),
        ("claim_names", ValueError, "capabilities must be sorted and unique"),
        ("unknown_schema", ValueError, "unsupported sandbox manifest schema version"),
        ("runtime_digest_prefix", ValueError, "runtime must be content addressed"),
        ("endpoint_digest_mismatch", ValueError, "does not match provider endpoint"),
        ("top_level_fields", ValueError, "manifest fields differ"),
        ("probe_not_object", TypeError, "probe result must be an object"),
        ("runtime_not_object", TypeError, "runtime enforcement must be an object"),
        ("claim_not_object", TypeError, "policy claim must be an object"),
        ("probe_fields", ValueError, "probe result fields differ"),
        ("runtime_fields", ValueError, "runtime enforcement fields differ"),
        ("claim_fields", ValueError, "policy claim fields differ"),
    ],
)
def test_declared_sandbox_manifest_rejects_every_invalid_v3_branch(
    declared_sandbox_manifest_fixture: dict[str, object],
    case: str,
    exception: type[Exception],
    message: str,
) -> None:
    value = deepcopy(declared_sandbox_manifest_fixture)
    probe = value["probe_result"]
    enforcement = value["runtime_enforcement"]
    claim = value["policy_claim"]
    assert isinstance(probe, dict)
    assert isinstance(enforcement, dict)
    assert isinstance(claim, dict)

    if case == "probe_status":
        probe["status"] = "indeterminate"
    elif case == "probe_mechanism":
        probe["mechanism_name"] = "sandbox_exec"
    elif case == "probe_applied":
        probe["applied_to_cli_launch"] = True
    elif case == "probe_digest_prefix":
        probe["profile_digest"] = "not-content-addressed"
    elif case == "runtime_mechanism":
        enforcement["mechanism_name"] = "sandbox_exec"
    elif case == "os_sandbox_applied":
        enforcement["os_sandbox_applied"] = True
    elif case == "argv_digest_prefix":
        enforcement["argv_digest"] = "not-content-addressed"
    elif case == "environment_digest_prefix":
        enforcement["environment_allowlist_digest"] = "not-content-addressed"
    elif case == "environment_names_unsorted":
        enforcement["environment_variable_names"] = ["PATH", "HOME"]
    elif case == "environment_names_duplicate":
        enforcement["environment_variable_names"] = ["HOME", "HOME"]
    elif case == "environment_digest_mismatch":
        enforcement["environment_allowlist_digest"] = "sha256:" + "0" * 64
    elif case == "cli_marker":
        enforcement["cli_restrictions_applied"] = 1
    elif case == "environment_marker":
        enforcement["environment_allowlist_applied"] = "true"
    elif case == "claim_names":
        claim["declared_unavailable_capabilities"] = ["shell", "browser_dom"]
    elif case == "unknown_schema":
        value["schema_version"] = "pixelgym-agent-v5-sandbox-v4"
    elif case == "runtime_digest_prefix":
        value["runtime_digest"] = "not-content-addressed"
    elif case == "endpoint_digest_mismatch":
        value["provider_endpoint_allowlist_digest"] = "sha256:" + "0" * 64
    elif case == "top_level_fields":
        del value["runtime_digest"]
    elif case == "probe_not_object":
        value["probe_result"] = []
    elif case == "runtime_not_object":
        value["runtime_enforcement"] = []
    elif case == "claim_not_object":
        value["policy_claim"] = []
    elif case == "probe_fields":
        del probe["status"]
    elif case == "runtime_fields":
        del enforcement["argv_digest"]
    elif case == "claim_fields":
        claim["unexpected"] = True
    else:  # pragma: no cover - the parametrization is exhaustive
        raise AssertionError(case)

    with pytest.raises(exception, match=message):
        load_sandbox_manifest(value)


def test_legacy_manifest_builder_rejects_v3_probe_result() -> None:
    with pytest.raises(ValueError, match="legacy sandbox manifests cannot record"):
        build_sandbox_manifest(
            runtime_digest="sha256:" + "1" * 64,
            provider_endpoint="https://provider.example.invalid",
            probe_result=ProbeResult(status="not_run"),
        )
