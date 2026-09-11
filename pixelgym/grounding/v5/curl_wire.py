"""One cancellable curl process per request, without curl-owned retries."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.panel_policy import ENDPOINT

WIRE_VERSION = "curl-single-send-v1"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class WireReceipt:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)
    exit_code: int = 0
    metrics: Mapping[str, Any] = field(default_factory=dict)


def quote_config(value: str) -> str:
    """curl config quoting, not shell quoting; config travels through stdin."""
    return (
        '"'
        + value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        + '"'
    )


def response_headers(raw: bytes) -> dict[str, str]:
    """Use the last HTTP header block (after proxy/100-continue headers)."""
    result: dict[str, str] = {}
    for line in raw.decode("iso-8859-1").splitlines():
        if line.startswith("HTTP/"):
            result = {}
        elif ":" in line:
            name, value = line.split(":", 1)
            if name.lower() == "retry-after":
                result[name.lower()] = value.strip()
    return result


class CurlWire:
    def __init__(
        self,
        *,
        executable: str = "/usr/bin/curl",
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.executable, self._popen = executable, popen
        self._lock = threading.RLock()
        self._process: Any = None
        self._aborted = False

    @property
    def idle(self) -> bool:
        with self._lock:
            return self._process is None

    def abort(self) -> None:
        """Permanently prevent another process; stop any already active process."""
        with self._lock:
            self._aborted = True
            if self._process is not None and self._process.poll() is None:
                self._process.kill()

    def perform(self, body: bytes, *, headers: Mapping[str, str], timeout: float) -> WireReceipt:
        if timeout <= 0 or any("\n" in v or "\r" in v for v in headers.values()):
            raise ValueError("invalid wire timeout or header")
        with tempfile.TemporaryDirectory(prefix="pixelgym-wire-") as directory:
            output = Path(directory) / "response"
            header_file = Path(directory) / "headers"
            # Do not put the credential in argv, environment, files, or logs.
            options = {
                "url": ENDPOINT,
                "request": "POST",
                "proto": "=https",
                "retry": "0",
                "connect-timeout": str(min(20.0, timeout)),
                "max-time": str(timeout),
                "max-filesize": str(MAX_RESPONSE_BYTES),
                "output": str(output),
                "dump-header": str(header_file),
                "data-binary": body.decode("utf-8"),
                "write-out": '{"http_code":%{http_code},"uploaded_bytes":%{size_upload},'
                '"connect_seconds":%{time_connect},"tls_seconds":%{time_appconnect},'
                '"total_seconds":%{time_total}}',
            }
            config = (
                "silent\nshow-error\nhttp1.1\n"
                + "".join(f"{key} = {quote_config(value)}\n" for key, value in options.items())
                + "".join(
                    f"header = {quote_config(name + ': ' + value)}\n"
                    for name, value in headers.items()
                )
            )
            environment = {
                k: v
                for k, v in os.environ.items()
                if k
                in {
                    "PATH",
                    "HOME",
                    "TMPDIR",
                    "LANG",
                    "LC_ALL",
                    "HTTPS_PROXY",
                    "https_proxy",
                    "ALL_PROXY",
                    "all_proxy",
                    "NO_PROXY",
                    "no_proxy",
                }
            }
            with self._lock:
                if self._aborted or self._process is not None:
                    raise RuntimeError("wire lifecycle is aborted or busy")
                process = self._popen(
                    [self.executable, "-q", "--config", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    shell=False,
                )
                self._process = process
            timed_out = False
            try:
                try:
                    stdout, _ = process.communicate(config.encode(), timeout=timeout + 2)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    process.kill()
                    stdout, _ = process.communicate(timeout=5)
                if process.poll() is None:
                    raise RuntimeError("curl was not reaped")
                try:
                    metrics = json.loads(stdout)
                    status = metrics["http_code"]
                    if type(status) is not int or not 0 <= status <= 599:
                        raise ValueError("invalid status")
                except (ValueError, KeyError, TypeError):
                    metrics, status = {}, 0
                data = output.read_bytes() if output.exists() else b""
                if len(data) > MAX_RESPONSE_BYTES:
                    return WireReceipt(status, b"", exit_code=63)
                raw_headers = header_file.read_bytes()[:65536] if header_file.exists() else b""
                return WireReceipt(
                    status,
                    data,
                    response_headers(raw_headers),
                    28 if timed_out else process.returncode,
                    metrics,
                )
            finally:
                # Never clear active state until the child has exited and been reaped.
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
                with self._lock:
                    self._process = None
