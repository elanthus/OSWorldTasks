"""Loopback-only browser server used to capture the frozen v4b state graph."""

from __future__ import annotations

import contextlib
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pixelgym.grounding.v4b_protocol import episode_for_seed
from pixelgym.tasks.vendor_form.render import FONT_DIR

V4B_STATIC_DIR = Path(__file__).parent / "v4b_app" / "static"
SHARED_FONT_DIR = FONT_DIR
V4B_READY_SELECTOR = '#ready-sentinel[data-ready="true"]'


def create_v4b_app() -> FastAPI:
    app = FastAPI(title="PixelGym v4b Pilot Capture")

    @app.get("/api/episode/{seed}")
    def episode(seed: int) -> dict[str, object]:
        try:
            return episode_for_seed(seed)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(V4B_STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=V4B_STATIC_DIR), name="v4b-static")
    app.mount("/fonts", StaticFiles(directory=SHARED_FONT_DIR), name="v4b-fonts")
    return app


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def local_v4b_server() -> Iterator[str]:
    port = _free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(create_v4b_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="v4b-capture-server", daemon=True)
    thread.start()
    health_url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(health_url, timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.02)
    else:
        server.should_exit = True
        thread.join(timeout=2.0)
        raise RuntimeError("v4b capture server did not become ready")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("v4b capture server did not shut down")
