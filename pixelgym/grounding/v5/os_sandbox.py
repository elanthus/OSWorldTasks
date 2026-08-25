"""No-cost Darwin policy sandbox used by the v5 enforcement integration suite."""

from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pixelgym.grounding.v5.sandbox import SANDBOX_POLICY_VERSION


@dataclass(frozen=True)
class SandboxProbeResult:
    provider_reachable: bool
    unauthorized_egress_denied: bool
    listener_denied: bool
    cross_policy_file_denied: bool
    runner_storage_denied: bool
    returncode: int


def darwin_profile(
    *, provider_port: int, peer_path: Path, runner_storage_path: Path
) -> str:
    if not 1 <= provider_port <= 65535:
        raise ValueError("provider port must be in [1, 65535]")
    escaped_peer = str(peer_path.resolve()).replace('"', '\\"')
    escaped_runner = str(runner_storage_path.resolve()).replace('"', '\\"')
    return "\n".join(
        (
            f";; {SANDBOX_POLICY_VERSION}",
            "(version 1)",
            "(allow default)",
            "(deny network*)",
            f'(allow network-outbound (remote ip "localhost:{provider_port}"))',
            "(deny network-bind)",
            f'(deny file-read* file-write* (subpath "{escaped_peer}"))',
            f'(deny file-read* file-write* (subpath "{escaped_runner}"))',
        )
    )


_PROBE = r"""
import json, socket, sys, urllib.request
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
print(json.dumps(result, sort_keys=True))
"""


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
    profile = darwin_profile(
        provider_port=parts.port,
        peer_path=peer_path,
        runner_storage_path=runner_storage_path,
    )
    completed = subprocess.run(
        [
            "/usr/bin/sandbox-exec",
            "-p",
            profile,
            str(python_executable.resolve()),
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
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "OS sandbox probe could not execute; run outside a parent process sandbox: "
            f"exit {completed.returncode}"
        )
    value = json.loads(completed.stdout)
    return SandboxProbeResult(returncode=completed.returncode, **value)
