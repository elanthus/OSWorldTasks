"""The CLI sandbox probe decodes launches faithfully and never retains the token."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5 import cli_sandbox_probe as probe
from pixelgym.grounding.v5.evidence import validate_credential_free


def _token() -> str:
    return "".join(("s", "k", "-ant-oat01-synthetic-", "1" * 24))


def _cli(tmp_path: Path) -> Path:
    root = tmp_path / "cask" / "2.1.292"
    root.mkdir(parents=True)
    executable = root / "claude"
    executable.write_text("")
    return executable


def _result(text: str) -> str:
    return json.dumps({"type": "result", "is_error": True, "result": text})


def test_result_marker_reads_the_json_result_field_only() -> None:
    assert probe.result_marker(_result("API Error: tunnel (ERR_PROXY_TUNNEL) refused")) == (
        "ERR_PROXY_TUNNEL"
    )
    assert probe.result_marker(_result("Can't reach the API server (ENOTFOUND)")) == "ENOTFOUND"
    assert probe.result_marker("plain text with ENOTFOUND") == "ENOTFOUND"
    assert probe.result_marker("") == "none"
    assert probe.result_marker(_result("fine")) == "none"


def _fake_runner(tmp_path: Path, calls: list[dict[str, Any]]):
    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": list(command), **kwargs})
        index = len(calls)
        if index == 1:
            Path(kwargs["env"]["CLAUDE_CONFIG_DIR"], ".claude.json").write_text("{}")
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"loggedIn": True, "authMethod": "oauth_token"}), ""
            )
        if index == 2:
            # Simulate the CLI asking the proxy for the provider.
            import socket
            from urllib.parse import urlsplit

            proxy = urlsplit(kwargs["env"]["HTTPS_PROXY"])
            assert proxy.hostname is not None and proxy.port is not None
            with socket.create_connection((proxy.hostname, proxy.port), timeout=3) as client:
                client.sendall(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n")
                client.settimeout(3)
                client.recv(4096)
            return subprocess.CompletedProcess(
                command, 1, _result("Couldn't connect (ERR_PROXY_TUNNEL)"), ""
            )
        assert "HTTPS_PROXY" not in kwargs["env"]
        return subprocess.CompletedProcess(command, 1, _result("DNS (ENOTFOUND)"), "")

    return runner


def test_probe_passes_on_the_expected_observations_and_records_no_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe.platform, "system", lambda: "Darwin")
    calls: list[dict[str, Any]] = []
    result = probe.run_cli_sandbox_probe(
        cli_executable=_cli(tmp_path),
        placeholder_token=_token(),
        runner=_fake_runner(tmp_path, calls),
        uid=501,
    )
    assert result.passed
    assert result.auth_method == "oauth_token"
    assert result.proxied_targets == ("api.anthropic.com:443",)
    assert result.proxied_refused_count == 1
    assert result.proxied_result_marker == "ERR_PROXY_TUNNEL"
    assert result.direct_result_marker == "ENOTFOUND"
    assert result.direct_targets_seen == 0
    assert result.config_entries_created == (".claude.json",)
    assert len(calls) == 3
    for call in calls:
        assert call["command"][:2] == ["/usr/bin/sandbox-exec", "-p"]
        assert call["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == _token()
        assert str(Path.home()) not in call["env"]["HOME"]
    assert "HTTPS_PROXY" in calls[1]["env"] and "HTTPS_PROXY" not in calls[2]["env"]
    declared = result.declared_result()
    assert declared.status == "passed" and declared.applied_to_cli_launch is True
    assert declared.profile_digest == result.profile_digest
    serialized = json.dumps(result.to_dict())
    assert _token() not in serialized and "oat01" not in serialized
    validate_credential_free(result.to_dict())


def test_probe_fails_when_auth_falls_back_to_keychain_or_egress_leaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe.platform, "system", lambda: "Darwin")

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "auth" in command:
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"loggedIn": True, "authMethod": "claude.ai"}), ""
            )
        return subprocess.CompletedProcess(command, 0, _result("OK"), "")

    result = probe.run_cli_sandbox_probe(
        cli_executable=_cli(tmp_path), placeholder_token=_token(), runner=runner, uid=501
    )
    assert not result.passed
    assert result.declared_result().status == "failed"


def test_probe_refuses_off_darwin_and_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _cli(tmp_path)
    monkeypatch.setattr(probe.platform, "system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="requires Darwin"):
        probe.run_cli_sandbox_probe(cli_executable=executable, placeholder_token=_token())
    monkeypatch.setattr(probe.platform, "system", lambda: "Darwin")
    with pytest.raises(ValueError, match="placeholder token"):
        probe.run_cli_sandbox_probe(cli_executable=executable, placeholder_token="")
