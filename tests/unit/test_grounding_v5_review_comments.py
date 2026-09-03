"""Regression tests for v5 review-summary findings."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import codex_cli_policy, os_sandbox
from pixelgym.grounding.v5.contracts import Partition, sandbox_endpoint_allowlist_digest
from pixelgym.grounding.v5.manifests import partition_manifest
from pixelgym.grounding.v5.os_sandbox import (
    _decode_probe_result,
    _escaped_sbpl_path,
    darwin_profile,
)
from pixelgym.grounding.v5.planning import _family_stratified_subset
from pixelgym.grounding.v5.resume import decode_resume_record
from pixelgym.grounding.v5.sandbox import (
    LEGACY_SANDBOX_MANIFEST_SCHEMA_VERSION,
    SANDBOX_MANIFEST_SCHEMA_VERSION,
    DeclaredSandboxManifest,
    build_sandbox_manifest,
    load_sandbox_manifest,
    unbound_runtime_enforcement,
)
from scripts.freeze_grounding_v5_scripted_policy import (
    fresh_output_paths,
    write_fresh_outputs,
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


def test_non_darwin_launch_metadata_never_claims_os_level_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os_sandbox.platform, "system", lambda: "Plan9")
    enforcement = codex_cli_policy._codex_launch_enforcement(
        codex_cli_policy.sanitized_command_contract(),
        {"PATH": "/bin"},
    )

    assert os_sandbox.platform.system() == "Plan9"
    assert enforcement.mechanism_name == "cli_flags_and_environment_allowlist"
    assert enforcement.cli_restrictions_applied is True
    assert enforcement.environment_allowlist_applied is True
    assert enforcement.os_sandbox_applied is False
    assert "denied_capabilities" not in enforcement.to_dict()


@pytest.mark.parametrize("payload", [b"[]", b"null", b'"record"', b"1"])
def test_resume_record_decoder_rejects_non_object_json(payload: bytes) -> None:
    with pytest.raises(ValueError, match="must be an object"):
        decode_resume_record(payload)


def test_confirmatory_subsets_are_family_stratified() -> None:
    confirmatory = tuple(partition_manifest(Partition.CONFIRMATORY)["records"])
    for per_family in (2, 4):
        subset = _family_stratified_subset(confirmatory, per_family=per_family)
        counts = {
            family: sum(record["seed_record"]["family"] == family for record in subset)
            for family in {record["seed_record"]["family"] for record in confirmatory}
        }
        assert set(counts.values()) == {per_family}


def test_scripted_policy_outputs_reject_resolved_path_aliases(tmp_path: Path) -> None:
    output = tmp_path / "outputs" / "policy.json"
    alias = output.parent / ".." / "outputs" / output.name
    with pytest.raises(RuntimeError, match="distinct fresh paths"):
        fresh_output_paths(output, alias)


def test_scripted_policy_output_creation_is_exclusive_and_preserves_partial_pair(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    plan = tmp_path / "plan.json"
    manifest_path, plan_path = fresh_output_paths(manifest, plan)
    plan_path.write_bytes(b"concurrent-writer")

    with pytest.raises(RuntimeError, match="output set is incomplete") as error:
        write_fresh_outputs(((manifest_path, b"manifest"), (plan_path, b"plan")))

    assert str(manifest_path) in str(error.value)
    assert manifest_path.read_bytes() == b"manifest"
    assert plan_path.read_bytes() == b"concurrent-writer"


def test_scripted_policy_output_failure_never_deletes_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = tmp_path / "manifest.json"
    plan = tmp_path / "plan.json"
    plan.write_bytes(b"concurrent-writer")
    original_open = Path.open

    def replacing_open(path: Path, *args: object, **kwargs: object):
        if path == plan:
            manifest.unlink()
            with original_open(manifest, "wb") as handle:
                handle.write(b"replacement")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", replacing_open)

    with pytest.raises(RuntimeError, match="output set is incomplete"):
        write_fresh_outputs(((manifest, b"manifest"), (plan, b"plan")))

    assert manifest.read_bytes() == b"replacement"
    assert plan.read_bytes() == b"concurrent-writer"


def test_sbpl_path_escaping_handles_backslashes_before_quotes() -> None:
    escaped = _escaped_sbpl_path(Path('/tmp/pixelgym\\"quoted'))
    assert escaped.endswith('pixelgym\\\\\\"quoted')


def test_os_sandbox_profile_is_deny_by_default_and_runtime_scoped(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    executable = runtime / "bin" / "python"
    workspace = tmp_path / "workspace"
    profile = darwin_profile(
        provider_port=8765,
        peer_path=tmp_path / "peer",
        runner_storage_path=tmp_path / "runner",
        runtime_root=runtime,
        runtime_executable=executable,
        policy_workspace=workspace,
    )
    assert "(deny default)" in profile
    assert "(allow default)" not in profile
    assert f'(allow process-exec (literal "{executable}"))' in profile
    assert '(allow network-outbound (remote ip "localhost:8765"))' in profile


def test_os_sandbox_probe_payload_requires_exact_boolean_fields() -> None:
    valid = {
        "provider_reachable": True,
        "unauthorized_egress_denied": True,
        "listener_denied": True,
        "cross_policy_file_denied": True,
        "runner_storage_denied": True,
        "shell_denied": True,
    }
    result = _decode_probe_result(json.dumps(valid), returncode=0)
    assert result.shell_denied
    declared = result.declared_result()
    assert declared.status == "passed"
    assert declared.applied_to_cli_launch is False

    invalid = dict(valid)
    invalid["unexpected"] = True
    with pytest.raises(RuntimeError, match="unexpected keys"):
        _decode_probe_result(json.dumps(invalid), returncode=0)

    invalid = dict(valid)
    invalid["shell_denied"] = 1
    with pytest.raises(RuntimeError, match="must be booleans"):
        _decode_probe_result(json.dumps(invalid), returncode=0)


def test_os_sandbox_probe_surfaces_execution_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os_sandbox.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        os_sandbox,
        "_sandbox_runtime",
        lambda _executable: (tmp_path / "python", tmp_path / "runtime"),
    )
    monkeypatch.setattr(
        os_sandbox.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=2,
            stdout="",
            stderr="profile rejected",
        ),
    )
    with pytest.raises(RuntimeError, match="exit 2: profile rejected"):
        os_sandbox.run_darwin_probe(
            provider_url="http://127.0.0.1:8765/",
            peer_path=tmp_path / "peer",
            runner_storage_path=tmp_path / "runner",
            python_executable=tmp_path / "python",
        )
