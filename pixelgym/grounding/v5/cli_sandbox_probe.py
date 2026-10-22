"""No-cost probe of the real Claude Code CLI under the v5 kernel sandbox.

Three launches, none of which can reach a provider:

1. ``claude auth status --json`` with a placeholder token and the real home hidden:
   the token route must report logged in, so the Keychain is not needed.
2. ``claude --print`` through a proxy that refuses every target: the only target the
   CLI may ask for is the provider; the request never leaves the machine.
3. ``claude --print`` with no proxy variables: the kernel must deny direct egress.

Outputs keep marker words from the CLI's error text, never the full text, and never the
placeholder token. Design record: docs/v5-policy-sandbox.md.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.egress_proxy import EgressProxy, egress_allowlist_digest
from pixelgym.grounding.v5.os_sandbox import (
    CliLaunchLayout,
    cli_profile_digest,
    cli_profile_template_digest,
    cli_scratch_root,
    darwin_cli_profile,
    prepare_cli_scratch_subdirectory,
    sandboxed_command,
)
from pixelgym.grounding.v5.sandbox import OS_SANDBOX_CLI_LAUNCH_MECHANISM, ProbeResult

CLI_SANDBOX_PROBE_VERSION = "pixelgym-agent-v5-cli-sandbox-probe-v1"
PROVIDER_TARGET = "api.anthropic.com:443"
# A refusing allowlist: the proxy exists, but no real target is on it.
REFUSING_ALLOWLIST = ("refused.invalid:443",)
_RESULT_MARKERS = (
    "ERR_PROXY_TUNNEL",
    "ENOTFOUND",
    "ECONNREFUSED",
    "ECONNRESET",
    "EAI_AGAIN",
    "ETIMEDOUT",
    "401",
    "403",
)
_PRINT_ARGS = (
    "--print",
    "--output-format",
    "json",
    "--tools",
    "",
    "--max-turns",
    "1",
    "--permission-mode",
    "dontAsk",
    "--setting-sources",
    "",
    "--safe-mode",
    "--disable-slash-commands",
    "--strict-mcp-config",
    "--no-chrome",
    "--no-session-persistence",
    "Reply with the single word OK.",
)
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, check=False, **kwargs)


def result_marker(stdout: str) -> str:
    """The first known marker in the CLI's JSON ``result`` text, or ``none``."""

    text = stdout
    try:
        payload = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
        if isinstance(payload, dict) and isinstance(payload.get("result"), str):
            text = payload["result"]
    except (ValueError, IndexError):
        pass
    for marker in _RESULT_MARKERS:
        if marker in text:
            return marker
    return "none"


@dataclass(frozen=True)
class CliSandboxProbeResult:
    probe_version: str
    auth_logged_in: bool
    auth_method: str
    auth_exit_code: int | None
    proxied_exit_code: int | None
    proxied_result_marker: str
    proxied_targets: tuple[str, ...]
    proxied_refused_count: int
    direct_exit_code: int | None
    direct_result_marker: str
    direct_targets_seen: int
    config_entries_created: tuple[str, ...]
    profile_template_digest: str
    profile_digest: str
    egress_allowlist_digest: str

    @property
    def passed(self) -> bool:
        return (
            self.auth_logged_in
            and self.auth_method == "oauth_token"
            and self.proxied_targets == (PROVIDER_TARGET,)
            and self.proxied_refused_count >= 1
            and self.proxied_result_marker == "ERR_PROXY_TUNNEL"
            and self.direct_targets_seen == 0
            and self.direct_result_marker in {"ENOTFOUND", "EAI_AGAIN"}
        )

    def declared_result(self) -> ProbeResult:
        return ProbeResult(
            status="passed" if self.passed else "failed",
            mechanism_name=OS_SANDBOX_CLI_LAUNCH_MECHANISM,
            profile_digest=self.profile_digest,
            applied_to_cli_launch=True,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "probe_version": self.probe_version,
            "passed": self.passed,
            "auth_logged_in": self.auth_logged_in,
            "auth_method": self.auth_method,
            "auth_exit_code": self.auth_exit_code,
            "proxied_exit_code": self.proxied_exit_code,
            "proxied_result_marker": self.proxied_result_marker,
            "proxied_targets": list(self.proxied_targets),
            "proxied_refused_count": self.proxied_refused_count,
            "direct_exit_code": self.direct_exit_code,
            "direct_result_marker": self.direct_result_marker,
            "direct_targets_seen": self.direct_targets_seen,
            "config_entries_created": list(self.config_entries_created),
            "profile_template_digest": self.profile_template_digest,
            "profile_digest": self.profile_digest,
            "egress_allowlist_digest": self.egress_allowlist_digest,
        }


def _environment(
    layout: CliLaunchLayout, *, placeholder_token: str, proxy_url: str | None
) -> dict[str, str]:
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(layout.home),
        "TMPDIR": str(layout.tmp),
        "CLAUDE_CONFIG_DIR": str(layout.config),
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        # Zero internal API retries, as the frozen D5.9 policies require; without it the CLI
        # backs off across its retry budget and a refused tunnel takes minutes to settle.
        "CLAUDE_CODE_MAX_RETRIES": "0",
        "CLAUDE_CODE_OAUTH_TOKEN": placeholder_token,
    }
    if proxy_url is not None:
        environment["HTTPS_PROXY"] = proxy_url
        environment["HTTP_PROXY"] = proxy_url
    return environment


def _auth_status(stdout: str) -> tuple[bool, str]:
    try:
        payload = json.loads(stdout)
    except ValueError:
        return False, "unparseable"
    if not isinstance(payload, Mapping):
        return False, "unparseable"
    return payload.get("loggedIn") is True, str(payload.get("authMethod", "none"))


def run_cli_sandbox_probe(
    *,
    cli_executable: Path,
    placeholder_token: str,
    runner: Runner = _run,
    timeout_seconds: float = 90.0,
    uid: int | None = None,
) -> CliSandboxProbeResult:
    """Run the three launches; raises when the host cannot apply the sandbox at all."""

    if platform.system() != "Darwin":
        raise RuntimeError("the CLI sandbox probe requires Darwin sandbox-exec")
    if not placeholder_token:
        raise ValueError("a placeholder token value is required")
    executable = cli_executable.resolve()
    scratch_root = cli_scratch_root(os.getuid() if uid is None else uid)
    proxy = EgressProxy(REFUSING_ALLOWLIST).start()
    try:
        with tempfile.TemporaryDirectory(prefix="pixelgym-cli-sandbox-probe-") as temporary:
            layout = CliLaunchLayout(Path(temporary)).create()
            prepare_cli_scratch_subdirectory(scratch_root, layout.cwd)
            profile = darwin_cli_profile(
                layout,
                cli_executable=executable,
                cli_root=executable.parent,
                proxy_port=proxy.port,
                scratch_root=scratch_root,
            )
            auth = runner(
                sandboxed_command(profile, (str(executable), "auth", "status", "--json")),
                cwd=layout.cwd,
                env=_environment(layout, placeholder_token=placeholder_token, proxy_url=proxy.url),
                timeout=timeout_seconds,
            )
            logged_in, auth_method = _auth_status(auth.stdout)
            marker = proxy.sequence
            proxied = runner(
                sandboxed_command(profile, (str(executable), *_PRINT_ARGS)),
                cwd=layout.cwd,
                env=_environment(layout, placeholder_token=placeholder_token, proxy_url=proxy.url),
                timeout=timeout_seconds,
                input="",
            )
            proxied_attempts = proxy.attempts_since(marker)
            marker = proxy.sequence
            direct = runner(
                sandboxed_command(profile, (str(executable), *_PRINT_ARGS)),
                cwd=layout.cwd,
                env=_environment(layout, placeholder_token=placeholder_token, proxy_url=None),
                timeout=timeout_seconds,
                input="",
            )
            direct_attempts = proxy.attempts_since(marker)
            created = tuple(sorted(path.name for path in layout.config.iterdir()))
    finally:
        proxy.close()
    return CliSandboxProbeResult(
        probe_version=CLI_SANDBOX_PROBE_VERSION,
        auth_logged_in=logged_in,
        auth_method=auth_method,
        auth_exit_code=auth.returncode,
        proxied_exit_code=proxied.returncode,
        proxied_result_marker=result_marker(proxied.stdout),
        proxied_targets=tuple(sorted({attempt.target for attempt in proxied_attempts})),
        proxied_refused_count=sum(attempt.verdict == "refused" for attempt in proxied_attempts),
        direct_exit_code=direct.returncode,
        direct_result_marker=result_marker(direct.stdout),
        direct_targets_seen=len(direct_attempts),
        config_entries_created=created,
        profile_template_digest=cli_profile_template_digest(),
        profile_digest=cli_profile_digest(profile),
        egress_allowlist_digest=egress_allowlist_digest(REFUSING_ALLOWLIST),
    )
