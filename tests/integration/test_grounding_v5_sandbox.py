"""Opt-in no-internet OS enforcement test for the v5 policy sandbox."""

from __future__ import annotations

import http.server
import os
import platform
import threading
import urllib.request
from pathlib import Path

import pytest

from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.os_sandbox import run_darwin_probe

pytestmark = [
    pytest.mark.v5_sandbox_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_V5_SANDBOX_TESTS") != "1" or platform.system() != "Darwin",
        reason="set PIXELGYM_RUN_V5_SANDBOX_TESTS=1 on Darwin outside a parent sandbox",
    ),
]


class _ProviderStub(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def test_v5_os_sandbox_allows_fake_provider_and_denies_other_channels(tmp_path: Path) -> None:
    peer = tmp_path / "peer-policy"
    peer.mkdir()
    peer_file = peer / "private-state"
    peer_file.write_text("not-visible")
    runner_file = tmp_path / "runner-private-state"
    runner_file.write_text("not-visible-to-policy")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ProviderStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_darwin_probe(
            provider_url=f"http://127.0.0.1:{server.server_port}/",
            peer_path=peer,
            runner_storage_path=runner_file,
            python_executable=Path("/usr/bin/python3"),
        )
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/", timeout=2
        ) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    assert result.provider_reachable
    assert result.unauthorized_egress_denied
    assert result.listener_denied
    assert result.cross_policy_file_denied
    assert result.runner_storage_denied
    assert result.shell_denied
    # The runner/application side remains outside the policy sandbox and keeps
    # its required local channels after the policy probe exits.
    backend = V5FakeBackend()
    backend.reset(5000)
    assert backend.screenshot().shape == (768, 1024, 3)
    backend.close()
