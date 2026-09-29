"""The macOS sandbox profile and probe decoding."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import os_sandbox
from pixelgym.grounding.v5.os_sandbox import (
    SBPL_PROBE_PROFILE_VERSION,
    _decode_probe_result,
    _escaped_sbpl_path,
    darwin_profile,
)


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
    assert profile.splitlines()[0] == f";; {SBPL_PROBE_PROFILE_VERSION}"
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
