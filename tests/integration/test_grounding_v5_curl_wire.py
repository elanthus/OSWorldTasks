"""Real system curl, local TLS, and a large POST; no provider or model access."""

import http.server
import json
import shutil
import ssl
import subprocess
import threading

import pytest

from pixelgym.grounding.v5.curl_wire import CurlWire, quote_config
from pixelgym.grounding.v5.panel_policy import ENDPOINT


def test_real_curl_config_preserves_large_post_and_verifies_local_certificate(tmp_path):
    curl, openssl = shutil.which("curl"), shutil.which("openssl")
    if not curl or not openssl:
        pytest.skip("optional integration requires curl and openssl")
    cert, key, config = (tmp_path / name for name in ("cert.pem", "key.pem", "openssl.cnf"))
    config.write_text(
        "[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost\nbasicConstraints=critical,CA:TRUE\n"
    )
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-config",
            str(config),
        ],
        capture_output=True,
        check=True,
    )
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers["Authorization"] == "Bearer integration-fixture"
            response = b'{"fixture":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(response)))
            self.send_header("Retry-After", "600")
            self.end_headers()
            self.wfile.write(response)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class LocalCurl:
        def __init__(self, args, **kwargs):
            self.process = subprocess.Popen(args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.process, name)

        def communicate(self, data=None, **kwargs):
            if data is not None:
                # Test-only endpoint/CA injection; production endpoint stays fixed.
                data = data.replace(
                    ENDPOINT.encode(), f"https://localhost:{server.server_port}/".encode()
                )
                data += f"cacert = {quote_config(str(cert))}\n".encode()
            return self.process.communicate(data, **kwargs)

    body = json.dumps({"payload": 'quotes " slashes \\ newlines\n é ' * 20000}).encode()
    wire = CurlWire(executable=curl, popen=LocalCurl)
    try:
        result = wire.perform(
            body,
            headers={
                "Authorization": "Bearer integration-fixture",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        assert result.exit_code == 0 and result.status == 200
        assert result.headers == {"retry-after": "600"} and result.body == b'{"fixture":true}'
        assert received == [body] and wire.idle
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
