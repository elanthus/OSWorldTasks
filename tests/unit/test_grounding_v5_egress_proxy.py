"""The runner-owned CONNECT proxy tunnels only allowlisted targets and logs every attempt."""

from __future__ import annotations

import socket
import socketserver
import threading
import time
from collections.abc import Iterator
from contextlib import closing

import pytest

from pixelgym.grounding.v5.egress_proxy import (
    EGRESS_PROXY_VERSION,
    EgressProxy,
    egress_allowlist_digest,
    summarize_attempts,
)
from pixelgym.grounding.v5.evidence import redact_publishable, validate_credential_free


class _Echo(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data = self.request.recv(1024)
        self.request.sendall(b"echo:" + data)


@pytest.fixture
def echo_server() -> Iterator[int]:
    server = socketserver.TCPServer(("127.0.0.1", 0), _Echo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()


def _connect(port: int, request: bytes) -> bytes:
    with closing(socket.create_connection(("127.0.0.1", port), timeout=3)) as client:
        client.sendall(request)
        client.settimeout(3)
        return client.recv(4096)


def _tunnel(port: int, target: str, payload: bytes) -> tuple[bytes, bytes]:
    with closing(socket.create_connection(("127.0.0.1", port), timeout=3)) as client:
        client.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        client.settimeout(3)
        status = client.recv(4096)
        client.sendall(payload)
        return status, client.recv(4096)


def test_allowed_target_is_tunnelled_both_ways_and_logged(echo_server: int) -> None:
    target = f"localhost:{echo_server}"
    with closing(EgressProxy([target]).start()) as proxy:
        status, reply = _tunnel(proxy.port, target, b"ping")
        assert status.startswith(b"HTTP/1.1 200")
        assert reply == b"echo:ping"
        attempts = proxy.attempts_since(0)
    assert [(a.sequence, a.target, a.verdict, a.reason) for a in attempts] == [
        (1, target, "allowed", "tunnel")
    ]


def test_unlisted_target_is_refused_with_403_and_logged(echo_server: int) -> None:
    with closing(EgressProxy([f"localhost:{echo_server}"]).start()) as proxy:
        reply = _connect(
            proxy.port, b"CONNECT example.invalid:443 HTTP/1.1\r\nHost: example.invalid\r\n\r\n"
        )
        assert reply.startswith(b"HTTP/1.1 403")
        attempts = proxy.attempts_since(0)
    assert [(a.target, a.verdict, a.reason) for a in attempts] == [
        ("example.invalid:443", "refused", "target_not_allowlisted")
    ]


def test_non_connect_and_malformed_requests_are_refused(echo_server: int) -> None:
    with closing(EgressProxy([f"localhost:{echo_server}"]).start()) as proxy:
        get = _connect(proxy.port, b"GET http://example.invalid/ HTTP/1.1\r\n\r\n")
        assert get.startswith(b"HTTP/1.1 405")
        with closing(socket.create_connection(("127.0.0.1", proxy.port), timeout=3)) as client:
            client.sendall(b"CONNECT localhost:1 HTTP/1.1\r\n")  # head never completes
        attempts = proxy.attempts_since(0)
        assert [a.verdict for a in attempts][:1] == ["refused"]
        assert attempts[0].reason == "not_connect"
        assert attempts[0].target == ""
        for _ in range(50):
            if len(proxy.attempts_since(0)) == 2:
                break
            time.sleep(0.05)
        attempts = proxy.attempts_since(0)
    assert len(attempts) == 2
    assert attempts[1].reason.startswith("malformed_request:")


def test_attempt_windows_follow_the_sequence(echo_server: int) -> None:
    target = f"localhost:{echo_server}"
    with closing(EgressProxy([target]).start()) as proxy:
        _tunnel(proxy.port, target, b"a")
        marker = proxy.sequence
        _tunnel(proxy.port, target, b"b")
        _connect(proxy.port, b"CONNECT other.invalid:443 HTTP/1.1\r\n\r\n")
        window = proxy.attempts_since(marker)
    summary = summarize_attempts(window)
    assert marker == 1
    assert [a.sequence for a in window] == [2, 3]
    assert summary["allowed_count"] == 1
    assert summary["refused_count"] == 1
    assert summary["refused_targets"] == ["other.invalid:443"]
    assert summary["proxy_version"] == EGRESS_PROXY_VERSION
    # The record is journal-safe and survives publishable redaction intact.
    validate_credential_free(summary)
    assert redact_publishable(summary) == summary


def test_unreachable_allowed_target_is_refused_as_upstream_unreachable() -> None:
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    target = f"localhost:{closed_port}"
    with closing(EgressProxy([target], connect_timeout_seconds=1).start()) as proxy:
        reply = _connect(proxy.port, f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
        assert reply.startswith(b"HTTP/1.1 502")
        attempts = proxy.attempts_since(0)
    assert attempts[0].verdict == "refused"
    assert attempts[0].reason.startswith("upstream_unreachable:")


def test_allowlist_validation_and_digest_are_strict_and_stable() -> None:
    digest = egress_allowlist_digest(["api.anthropic.com:443"])
    assert digest.startswith("sha256:")
    assert egress_allowlist_digest(["api.anthropic.com:443", "api.anthropic.com:443"]) == digest
    for bad in ("api.anthropic.com", "API.anthropic.com:443", ":443", "x:0", "x:70000"):
        with pytest.raises(ValueError):
            egress_allowlist_digest([bad])
    with pytest.raises(ValueError, match="at least one target"):
        EgressProxy([])
    with pytest.raises(ValueError, match="timeouts must be positive"):
        EgressProxy(["a.example:443"], header_timeout_seconds=0)
