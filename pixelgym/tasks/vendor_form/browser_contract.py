"""Public deterministic-browser settings shared by capture and validation.

``POST /api/reset`` invalidates the currently rendered page and returns
``requires_reload: true``. Consumers must reload the document after every
successful reset and must not treat the old ``READY_SELECTOR`` match as current.
The reload fetches the active task and posts its ``task_id`` to
``/api/page-ready``. Until the current task's post succeeds,
``GET /api/page-ready`` reports ``ready: false``; a post for a task displayed
before the reset is rejected as stale.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator

import uvicorn

from pixelgym.tasks.vendor_form.app.server import create_app

READY_SELECTOR = 'body[data-pixelgym-ready="true"]'

BROWSER_ARGS = (
    "--disable-font-subpixel-positioning",
    "--disable-gpu",
    "--disable-lcd-text",
    "--disable-skia-runtime-opts",
    "--force-color-profile=srgb",
    "--hide-scrollbars",
)


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def local_vendor_form_server() -> Iterator[str]:
    """Serve an isolated vendor form on loopback and always shut it down."""
    port = _free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="vendor-form-browser-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    health_url = f"http://127.0.0.1:{port}/"
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(health_url, timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.02)
    else:
        server.should_exit = True
        thread.join(timeout=2.0)
        raise RuntimeError("vendor-form browser server did not become ready")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("vendor-form browser server did not shut down")
