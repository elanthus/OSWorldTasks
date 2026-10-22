"""Runner-owned loopback CONNECT proxy: the only network path out of a policy sandbox.

The proxy tunnels TLS end to end, so the runner never sees request bodies or the
credential. It allows ``CONNECT`` to the frozen provider targets, refuses everything
else, and records every attempt with a sequence number, timestamp, target, and verdict.
Field names avoid ``host`` and ``endpoint`` so publishable redaction keeps them.
"""

from __future__ import annotations

import re
import select
import socket
import socketserver
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from pixelgym.grounding.v5.contracts import content_digest

EGRESS_PROXY_VERSION = "pixelgym-agent-v5-egress-proxy-v1"
_REQUEST_LINE = re.compile(r"^CONNECT ([A-Za-z0-9.-]{1,253}):(\d{1,5}) HTTP/1\.[01]$")
_MAX_HEADER_BYTES = 8192
_RELAY_CHUNK = 65536


def egress_allowlist_digest(targets: Iterable[str]) -> str:
    """Bind a sorted ``host:port`` allowlist to a content digest."""

    return content_digest(
        {
            "version": EGRESS_PROXY_VERSION,
            "targets": sorted({_validated(target) for target in targets}),
        }
    )


def _validated(target: str) -> str:
    host, separator, port = target.rpartition(":")
    if not separator or not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("egress target must be host:port")
    if host != host.lower() or host.startswith(".") or host.endswith("."):
        raise ValueError("egress target host must be lowercase and fully qualified")
    return f"{host}:{int(port)}"


@dataclass(frozen=True)
class EgressAttempt:
    """One CONNECT attempt observed at the proxy; never carries request content."""

    sequence: int
    observed_at_utc: str
    target: str
    verdict: str  # allowed | refused
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "observed_at_utc": self.observed_at_utc,
            "target": self.target,
            "verdict": self.verdict,
            "reason": self.reason,
        }


class _EmptyConnection(Exception):
    """The client closed without sending a byte."""


class _Handler(socketserver.BaseRequestHandler):
    server: EgressProxy

    def handle(self) -> None:
        proxy = self.server
        client: socket.socket = self.request
        client.settimeout(proxy.header_timeout_seconds)
        try:
            head = self._read_head(client)
        except _EmptyConnection:
            return  # a connection that sent nothing attempted no egress
        except (OSError, ValueError) as exc:
            proxy._record("", "refused", f"malformed_request:{type(exc).__name__}")
            self._refuse(client, 400)
            return
        request_line = head.split("\r\n", 1)[0]
        match = _REQUEST_LINE.match(request_line)
        if match is None:
            proxy._record("", "refused", "not_connect")
            self._refuse(client, 405)
            return
        target = f"{match.group(1).lower()}:{int(match.group(2))}"
        if target not in proxy.allowlist:
            proxy._record(target, "refused", "target_not_allowlisted")
            self._refuse(client, 403)
            return
        host, port = match.group(1), int(match.group(2))
        try:
            upstream = socket.create_connection((host, port), timeout=proxy.connect_timeout_seconds)
        except OSError as exc:
            proxy._record(target, "refused", f"upstream_unreachable:{type(exc).__name__}")
            self._refuse(client, 502)
            return
        proxy._record(target, "allowed", "tunnel")
        try:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            client.settimeout(None)
            upstream.settimeout(None)
            self._relay(client, upstream)
        finally:
            upstream.close()

    @staticmethod
    def _read_head(client: socket.socket) -> str:
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = client.recv(1024)
            if not chunk:
                if not buffer:
                    raise _EmptyConnection
                raise ValueError("connection closed before request head")
            buffer += chunk
            if len(buffer) > _MAX_HEADER_BYTES:
                raise ValueError("request head too large")
        return buffer.decode("latin-1")

    @staticmethod
    def _refuse(client: socket.socket, status: int) -> None:
        reasons = {
            400: "Bad Request",
            403: "Forbidden",
            405: "Method Not Allowed",
            502: "Bad Gateway",
        }
        try:
            client.sendall(
                f"HTTP/1.1 {status} {reasons[status]}\r\nContent-Length: 0\r\n"
                "Connection: close\r\n\r\n".encode()
            )
        except OSError:
            pass

    @staticmethod
    def _relay(client: socket.socket, upstream: socket.socket) -> None:
        sockets = [client, upstream]
        while True:
            readable, _, errored = select.select(sockets, [], sockets, 1.0)
            if errored:
                return
            for source in readable:
                destination = upstream if source is client else client
                try:
                    data = source.recv(_RELAY_CHUNK)
                except OSError:
                    return
                if not data:
                    return
                try:
                    destination.sendall(data)
                except OSError:
                    return


class EgressProxy(socketserver.ThreadingTCPServer):
    """Loopback CONNECT proxy with a frozen allowlist and an attempt log."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        allowlist: Iterable[str],
        *,
        header_timeout_seconds: float = 5.0,
        connect_timeout_seconds: float = 10.0,
    ) -> None:
        self.allowlist = frozenset(_validated(target) for target in allowlist)
        if not self.allowlist:
            raise ValueError("egress allowlist must name at least one target")
        if header_timeout_seconds <= 0 or connect_timeout_seconds <= 0:
            raise ValueError("egress proxy timeouts must be positive")
        self.header_timeout_seconds = header_timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self._attempts: list[EgressAttempt] = []
        self._attempt_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        super().__init__(("127.0.0.1", 0), _Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def allowlist_digest(self) -> str:
        return egress_allowlist_digest(self.allowlist)

    def start(self) -> EgressProxy:
        if self._thread is not None:
            raise RuntimeError("egress proxy already started")
        self._thread = threading.Thread(target=self.serve_forever, name="pixelgym-egress-proxy")
        self._thread.daemon = True
        self._thread.start()
        return self

    def close(self) -> None:
        if self._thread is not None:
            self.shutdown()
            self._thread.join(timeout=5)
            self._thread = None
        self.server_close()

    @property
    def sequence(self) -> int:
        """Sequence number of the latest attempt; zero before any attempt."""

        with self._attempt_lock:
            return len(self._attempts)

    def attempts_since(self, sequence: int) -> list[EgressAttempt]:
        with self._attempt_lock:
            return [attempt for attempt in self._attempts if attempt.sequence > sequence]

    def _record(self, target: str, verdict: str, reason: str) -> None:
        with self._attempt_lock:
            self._attempts.append(
                EgressAttempt(
                    sequence=len(self._attempts) + 1,
                    observed_at_utc=datetime.now(UTC).isoformat(),
                    target=target,
                    verdict=verdict,
                    reason=reason,
                )
            )


def summarize_attempts(attempts: Iterable[EgressAttempt]) -> dict[str, object]:
    """Counts and refused targets for a launch record; stable field names."""

    listed = list(attempts)
    refused = sorted({attempt.target for attempt in listed if attempt.verdict == "refused"})
    return {
        "proxy_version": EGRESS_PROXY_VERSION,
        "attempts": [attempt.to_dict() for attempt in listed],
        "allowed_count": sum(attempt.verdict == "allowed" for attempt in listed),
        "refused_count": sum(attempt.verdict == "refused" for attempt in listed),
        "refused_targets": refused,
    }
