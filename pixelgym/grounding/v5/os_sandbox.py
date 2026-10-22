"""No-cost Darwin policy sandbox used by the v5 enforcement integration suite."""

from __future__ import annotations

import json
import platform
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from pixelgym.grounding.v5.contracts import sha256_bytes
from pixelgym.grounding.v5.sandbox import ProbeResult

# This identity is independent of the manifest schema and preserves the frozen
# profile bytes until the SBPL policy itself changes.
SBPL_PROBE_PROFILE_VERSION = "pixelgym-agent-v5-sandbox-v2"


@dataclass(frozen=True)
class SandboxProbeResult:
    provider_reachable: bool
    unauthorized_egress_denied: bool
    listener_denied: bool
    cross_policy_file_denied: bool
    runner_storage_denied: bool
    shell_denied: bool
    returncode: int
    profile_digest: str | None = None

    def declared_result(self) -> ProbeResult:
        passed = self.returncode == 0 and all(
            (
                self.provider_reachable,
                self.unauthorized_egress_denied,
                self.listener_denied,
                self.cross_policy_file_denied,
                self.runner_storage_denied,
                self.shell_denied,
            )
        )
        return ProbeResult(
            status="passed" if passed else "failed",
            profile_digest=self.profile_digest,
        )


def _escaped_sbpl_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')


def darwin_profile(
    *,
    provider_port: int,
    peer_path: Path,
    runner_storage_path: Path,
    runtime_root: Path,
    runtime_executable: Path,
    policy_workspace: Path,
) -> str:
    if not 1 <= provider_port <= 65535:
        raise ValueError("provider port must be in [1, 65535]")
    escaped_peer = _escaped_sbpl_path(peer_path)
    escaped_runner = _escaped_sbpl_path(runner_storage_path)
    escaped_runtime_root = _escaped_sbpl_path(runtime_root)
    escaped_runtime_executable = _escaped_sbpl_path(runtime_executable)
    escaped_workspace = _escaped_sbpl_path(policy_workspace)
    return "\n".join(
        (
            f";; {SBPL_PROBE_PROFILE_VERSION}",
            "(version 1)",
            "(deny default)",
            '(import "system.sb")',
            "(allow process-info*)",
            f'(allow process-exec (literal "{escaped_runtime_executable}"))',
            f'(allow file-read* file-map-executable (subpath "{escaped_runtime_root}"))',
            f'(allow file-read* file-write* (subpath "{escaped_workspace}"))',
            "(deny network*)",
            f'(allow network-outbound (remote ip "localhost:{provider_port}"))',
            "(deny network-bind)",
            f'(deny file-read* file-write* (subpath "{escaped_peer}"))',
            f'(deny file-read* file-write* (subpath "{escaped_runner}"))',
        )
    )


_PROBE = r"""
import json, socket, subprocess, sys, urllib.request
provider, denied_url, peer, runner_storage = sys.argv[1:]
result = {}
try:
    urllib.request.urlopen(provider, timeout=2).read()
    result['provider_reachable'] = True
except Exception:
    result['provider_reachable'] = False
try:
    urllib.request.urlopen(denied_url, timeout=0.3).read()
    result['unauthorized_egress_denied'] = False
except Exception:
    result['unauthorized_egress_denied'] = True
listener = socket.socket()
try:
    listener.bind(('127.0.0.1', 0))
    result['listener_denied'] = False
except Exception:
    result['listener_denied'] = True
finally:
    listener.close()
try:
    open(peer, 'rb').read()
    result['cross_policy_file_denied'] = False
except Exception:
    result['cross_policy_file_denied'] = True
try:
    open(runner_storage, 'rb').read()
    result['runner_storage_denied'] = False
except Exception:
    result['runner_storage_denied'] = True
try:
    subprocess.run(['/bin/sh', '-c', 'true'], check=False)
    result['shell_denied'] = False
except Exception:
    result['shell_denied'] = True
print(json.dumps(result, sort_keys=True))
"""


def _sandbox_runtime(python_executable: Path) -> tuple[Path, Path]:
    discovery = subprocess.run(
        [str(python_executable), "-I", "-B", "-c", "import sys; print(sys.executable)"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if discovery.returncode != 0:
        raise RuntimeError(
            "could not resolve the sandbox Python runtime: "
            f"exit {discovery.returncode}: {discovery.stderr.strip()}"
        )
    resolved = Path(discovery.stdout.strip()).resolve()
    if not resolved.is_file():
        raise RuntimeError("resolved sandbox Python executable is missing")
    framework_root = next(
        (parent for parent in resolved.parents if parent.name.endswith(".framework")),
        None,
    )
    if framework_root is None:
        return resolved, resolved.parent.parent
    version_root = next(
        (parent for parent in resolved.parents if parent.parent.name == "Versions"),
        None,
    )
    if version_root is None:
        raise RuntimeError("resolved framework Python version root is missing")
    executable = version_root / "Resources/Python.app/Contents/MacOS/Python"
    if not executable.is_file():
        raise RuntimeError("resolved framework Python runtime executable is missing")
    return executable, framework_root


def _decode_probe_result(stdout: str, *, returncode: int) -> SandboxProbeResult:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OS sandbox probe emitted non-JSON output: {stdout!r}") from exc
    if not isinstance(value, Mapping):
        raise RuntimeError(  # noqa: TRY004 - malformed subprocess output
            "OS sandbox probe result must be an object"
        )
    expected = {
        "provider_reachable",
        "unauthorized_egress_denied",
        "listener_denied",
        "cross_policy_file_denied",
        "runner_storage_denied",
        "shell_denied",
    }
    if set(value) != expected:
        raise RuntimeError(f"OS sandbox probe returned unexpected keys: {sorted(value)}")
    if any(type(value[key]) is not bool for key in expected):
        raise RuntimeError("OS sandbox probe result fields must be booleans")
    return SandboxProbeResult(returncode=returncode, **value)


def run_darwin_probe(
    *,
    provider_url: str,
    peer_path: Path,
    runner_storage_path: Path,
    python_executable: Path,
) -> SandboxProbeResult:
    if platform.system() != "Darwin":
        raise RuntimeError("the frozen v5 OS sandbox probe currently requires Darwin sandbox-exec")
    parts = urlsplit(provider_url)
    if parts.hostname not in {"127.0.0.1", "localhost"} or parts.port is None:
        raise ValueError("sandbox probe provider must be an explicit loopback endpoint")
    runtime_executable, runtime_root = _sandbox_runtime(python_executable)
    with tempfile.TemporaryDirectory(prefix="pixelgym-v5-policy-") as workspace:
        profile = darwin_profile(
            provider_port=parts.port,
            peer_path=peer_path,
            runner_storage_path=runner_storage_path,
            runtime_root=runtime_root,
            runtime_executable=runtime_executable,
            policy_workspace=Path(workspace),
        )
        completed = subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-p",
                profile,
                str(runtime_executable),
                "-I",
                "-B",
                "-c",
                _PROBE,
                provider_url,
                "http://203.0.113.1/denied",
                str(peer_path.resolve()),
                str(runner_storage_path.resolve()),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=workspace,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            "OS sandbox probe could not execute; run outside a parent process sandbox: "
            f"exit {completed.returncode}: {completed.stderr.strip()}"
        )
    result = _decode_probe_result(completed.stdout, returncode=completed.returncode)
    return replace(
        result,
        profile_digest="sha256:" + sha256_bytes(profile.encode("utf-8")),
    )


# --- CLI launch sandbox -----------------------------------------------------------------
#
# The probe profile above hosts a Python probe against a loopback provider stub. The CLI
# profile below hosts a real Claude Code CLI launch. Its only network path is the loopback
# egress proxy, its only writable paths are one per-launch workspace and the CLI's own
# per-working-directory scratch subdirectory, and the real home directory (and with it the
# Keychain) is unreachable. The CLI opens the shared scratch root itself, so the profile
# allows listing that root and reading entry metadata; other entries' contents stay denied.
# The CLI may create and use exactly one entry there, the project directory it derives from
# the launch working directory; the runner never creates or removes anything in that root.

SBPL_CLI_PROFILE_VERSION = "pixelgym-agent-v5-cli-sandbox-v1"
CLI_SCRATCH_ROOT_TEMPLATE = "/private/tmp/claude-{uid}"
_CLI_SYSTEM_READ_PATHS = (
    "/usr/lib",
    "/System/Library",
    "/private/var/db/timezone",
    "/usr/share",
    "/Library/Preferences",
)
CLI_PROFILE_TEMPLATE = "\n".join(
    (
        f";; {SBPL_CLI_PROFILE_VERSION}",
        "(version 1)",
        "(deny default)",
        '(import "system.sb")',
        "(allow process-info*)",
        '(allow process-exec (literal "{cli_executable}"))',
        '(allow file-read* file-map-executable (subpath "{cli_root}"))',
        "(allow file-read* {system_read_paths})",
        '(allow file-read* file-write* (subpath "{workspace}"))',
        '(allow file-read* (literal "{scratch_root}") (literal "{scratch_root_alias}"))',
        '(allow file-read-metadata (subpath "{scratch_root}") (subpath "{scratch_root_alias}"))',
        '(allow file-write-create (literal "{scratch}") (literal "{scratch_alias}"))',
        '(allow file-read* file-write* (subpath "{scratch}") (subpath "{scratch_alias}"))',
        "(deny network*)",
        '(allow network-outbound (remote ip "localhost:{proxy_port}"))',
        "(deny network-bind)",
        "{denials}",
    )
)


def cli_profile_template_digest() -> str:
    """Digest of the frozen template bytes; bound by a policy manifest."""

    return "sha256:" + sha256_bytes(CLI_PROFILE_TEMPLATE.encode("utf-8"))


def cli_profile_digest(profile: str) -> str:
    """Digest of one rendered profile; recorded per launch."""

    return "sha256:" + sha256_bytes(profile.encode("utf-8"))


def cli_scratch_root(uid: int) -> Path:
    if uid < 0:
        raise ValueError("uid must be non-negative")
    return Path(CLI_SCRATCH_ROOT_TEMPLATE.format(uid=uid))


def cli_project_key(cwd: Path) -> str:
    """The CLI's project key: every character of the resolved path that is not a letter or
    digit becomes a dash, as the Claude Code directory documentation states."""

    resolved = str(cwd.resolve())
    if not resolved.startswith("/"):
        raise ValueError("CLI working directory must be absolute")
    return "".join(ch if ch.isascii() and ch.isalnum() else "-" for ch in resolved)


def cli_scratch_subdirectory(root: Path, cwd: Path) -> Path:
    """The one entry under the shared scratch root a launch may create and use."""

    return root / cli_project_key(cwd)


@dataclass(frozen=True)
class CliLaunchLayout:
    """Per-launch directories inside one workspace the profile allows."""

    workspace: Path

    @property
    def home(self) -> Path:
        return self.workspace / "home"

    @property
    def config(self) -> Path:
        return self.workspace / "config"

    @property
    def tmp(self) -> Path:
        return self.workspace / "tmp"

    @property
    def cwd(self) -> Path:
        return self.workspace / "cwd"

    def create(self) -> CliLaunchLayout:
        for directory in (self.home, self.config, self.tmp, self.cwd):
            directory.mkdir(parents=True, exist_ok=True)
        return self


def _alias_private(path: Path) -> str:
    """The CLI spells the scratch root without the /private prefix; never resolved."""

    text = str(path)
    if text.startswith("/private/"):
        text = text[len("/private") :]
    return text.replace("\\", "\\\\").replace('"', '\\"')


def darwin_cli_profile(
    layout: CliLaunchLayout,
    *,
    cli_executable: Path,
    cli_root: Path,
    proxy_port: int,
    scratch_root: Path,
    denied_paths: Sequence[Path] = (),
) -> str:
    """Render the frozen CLI template for one launch; every path is resolved and escaped."""

    if not 1 <= proxy_port <= 65535:
        raise ValueError("proxy port must be in [1, 65535]")
    executable = cli_executable.resolve()
    root = cli_root.resolve()
    if not executable.is_relative_to(root):
        raise ValueError("CLI executable must live inside the CLI root")
    scratch = cli_scratch_subdirectory(scratch_root, layout.cwd)
    denials = "\n".join(
        f'(deny file-read* file-write* (subpath "{_escaped_sbpl_path(path)}"))'
        for path in sorted(denied_paths, key=lambda item: str(item.resolve()))
    )
    system_read = " ".join(f'(subpath "{path}")' for path in _CLI_SYSTEM_READ_PATHS)
    rendered = CLI_PROFILE_TEMPLATE.format(
        cli_executable=_escaped_sbpl_path(executable),
        cli_root=_escaped_sbpl_path(root),
        system_read_paths=system_read,
        workspace=_escaped_sbpl_path(layout.workspace),
        scratch_root=_escaped_sbpl_path(scratch_root),
        scratch_root_alias=_alias_private(scratch_root),
        scratch=_escaped_sbpl_path(scratch),
        scratch_alias=_alias_private(scratch),
        proxy_port=proxy_port,
        denials=denials,
    )
    return rendered.rstrip("\n") + "\n"


def sandboxed_command(profile: str, command: Sequence[str]) -> tuple[str, ...]:
    """Wrap a CLI argv so the kernel applies the rendered profile to the child."""

    if not command:
        raise ValueError("a command is required")
    if not profile.startswith(f";; {SBPL_CLI_PROFILE_VERSION}\n"):
        raise ValueError("profile must be rendered from the frozen CLI template")
    return ("/usr/bin/sandbox-exec", "-p", profile, *command)
