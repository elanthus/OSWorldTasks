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


def test_cli_profile_is_deny_by_default_and_scoped_to_one_launch(tmp_path: Path) -> None:
    from pixelgym.grounding.v5.os_sandbox import (
        SBPL_CLI_PROFILE_VERSION,
        CliLaunchLayout,
        cli_profile_digest,
        cli_profile_template_digest,
        cli_project_key,
        cli_scratch_subdirectory,
        darwin_cli_profile,
        sandboxed_command,
    )

    cli_root = tmp_path / "cask" / "2.1.292"
    cli_root.mkdir(parents=True)
    executable = cli_root / "claude"
    executable.write_text("")
    layout = CliLaunchLayout(tmp_path / "workspace").create()
    scratch_root = Path("/private/tmp/claude-501")
    peer = tmp_path / "peer-workspace"
    runner = tmp_path / "runner-storage"
    profile = darwin_cli_profile(
        layout,
        cli_executable=executable,
        cli_root=cli_root,
        proxy_port=4321,
        scratch_root=scratch_root,
        denied_paths=(runner, peer),
    )
    lines = profile.splitlines()
    assert lines[0] == f";; {SBPL_CLI_PROFILE_VERSION}"
    assert "(deny default)" in lines
    assert "(allow default)" not in profile
    assert f'(allow process-exec (literal "{executable.resolve()}"))' in lines
    assert '(allow network-outbound (remote ip "localhost:4321"))' in lines
    assert "(deny network*)" in lines and "(deny network-bind)" in lines
    scratch = cli_scratch_subdirectory(scratch_root, layout.cwd)
    assert str(scratch).startswith("/private/tmp/claude-501/-")
    # The CLI's documented project key: only ASCII letters and digits survive.
    assert all(ch.isascii() and (ch.isalnum() or ch == "-") for ch in scratch.name)
    assert cli_project_key(Path("/Users/you/my_project.v1")) == "-Users-you-my-project-v1"
    assert f'(subpath "{scratch}")' in profile
    assert f'(allow file-write-create (literal "{scratch}")' in profile
    assert f'(subpath "{str(scratch).removeprefix("/private")}")' in profile
    assert (
        '(allow file-read* (literal "/private/tmp/claude-501") (literal "/tmp/claude-501"))'
        in lines
    )
    assert (
        '(allow file-read-metadata (subpath "/private/tmp/claude-501") (subpath "/tmp/claude-501"))'
        in lines
    )
    # No rule grants data access to the root's other entries.
    assert '(allow file-read* file-write* (subpath "/private/tmp/claude-501")' not in profile
    # Denials are sorted and the real home is never allowed.
    assert lines.index(f'(deny file-read* file-write* (subpath "{peer.resolve()}"))') < lines.index(
        f'(deny file-read* file-write* (subpath "{runner.resolve()}"))'
    )
    assert str(Path.home()) not in profile
    # Digests: the template digest is launch independent; the rendered digest is not.
    other = darwin_cli_profile(
        layout,
        cli_executable=executable,
        cli_root=cli_root,
        proxy_port=4322,
        scratch_root=scratch_root,
    )
    assert cli_profile_template_digest().startswith("sha256:")
    assert cli_profile_digest(profile) != cli_profile_digest(other)
    assert cli_profile_template_digest() == cli_profile_template_digest()
    wrapped = sandboxed_command(profile, ("claude", "--print"))
    assert wrapped[:2] == ("/usr/bin/sandbox-exec", "-p")
    assert wrapped[2] == profile and wrapped[3:] == ("claude", "--print")
    with pytest.raises(ValueError, match="frozen CLI template"):
        sandboxed_command("(version 1)", ("claude",))
    with pytest.raises(ValueError, match="inside the CLI root"):
        darwin_cli_profile(
            layout,
            cli_executable=tmp_path / "elsewhere",
            cli_root=cli_root,
            proxy_port=1,
            scratch_root=scratch_root,
        )
    with pytest.raises(ValueError, match="proxy port"):
        darwin_cli_profile(
            layout,
            cli_executable=executable,
            cli_root=cli_root,
            proxy_port=0,
            scratch_root=scratch_root,
        )
