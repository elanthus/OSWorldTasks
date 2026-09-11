"""Exercise curl invocation, exact body forwarding, and child reaping offline."""

import json
import subprocess
from pathlib import Path

import pytest

from pixelgym.grounding.v5.curl_wire import CurlWire, response_headers


@pytest.mark.parametrize("timeout", [False, True])
def test_process_has_no_argv_secret_no_hidden_retries_and_is_reaped(timeout):
    calls = []
    body = json.dumps({"messages": ['quote " and slash \\ and newline\n', "é"]}).encode()

    class Process:
        returncode = None
        killed = False
        communicated = 0

        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True

        def communicate(self, data=None, **kwargs):
            self.communicated += 1
            if data is not None:
                config = data.decode()
                options = {}
                for line in config.splitlines():
                    if " = " in line:
                        name, value = line.split(" = ", 1)
                        options[name] = json.loads(value)
                assert options["data-binary"].encode() == body
                assert options["retry"] == "0" and "http1.1\n" in config
                assert "insecure" not in config and options["proto"] == "=https"
                assert "Bearer fixture-secret" in config
                Path(options["output"]).write_bytes(b'{"ok":true}')
                Path(options["dump-header"]).write_bytes(
                    b"HTTP/1.1 200 OK\r\nRetry-After: 120\r\n\r\n"
                )
            if timeout and not self.killed:
                raise subprocess.TimeoutExpired("curl", 10)
            self.returncode = -9 if self.killed else 0
            return b'{"http_code":200}', b"private stderr must not escape"

    processes = []

    def create(*args, **kwargs):
        p = Process(*args, **kwargs)
        processes.append(p)
        return p

    wire = CurlWire(popen=create)
    result = wire.perform(body, headers={"Authorization": "Bearer fixture-secret"}, timeout=10)
    assert len(calls) == 1 and wire.idle
    assert calls[0][0] == ["/usr/bin/curl", "-q", "--config", "-"]
    assert "fixture-secret" not in repr(calls)
    assert result.headers == {"retry-after": "120"}
    assert result.body == b'{"ok":true}'
    assert result.exit_code == (28 if timeout else 0)
    assert processes[0].communicated == (2 if timeout else 1)
    assert processes[0].poll() is not None
    wire.abort()
    with pytest.raises(RuntimeError):
        wire.perform(body, headers={}, timeout=10)
    assert len(calls) == 1


def test_only_last_http_headers_control_retry_delay():
    raw = b"HTTP/1.1 200 Connection established\r\nRetry-After: 1\r\n\r\nHTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 429 Limited\r\nRetry-After: 300\r\n\r\n"
    assert response_headers(raw) == {"retry-after": "300"}
