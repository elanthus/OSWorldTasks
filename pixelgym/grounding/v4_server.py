"""Capture-only server for the v4 compositional grounding pilot.

The server is build-time instrumentation. It is never mounted by the task app,
the Gymnasium environment, or the OSWorld adapter.
"""

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
from pydantic import BaseModel

V4_STATIC_DIR = Path(__file__).parent / "v4_app" / "static"
SHARED_FONT_DIR = Path(__file__).parent / "v3c_app" / "static" / "fonts"
V4_READY_SELECTOR = '#ready-sentinel[data-ready="true"]'
V4_SEEDS = (30, 31)


class _ResetRequest(BaseModel):
    seed: int


class _V4State:
    def __init__(self) -> None:
        self.seed: int | None = None

    def reset(self, seed: int) -> dict[str, int | str]:
        if seed not in V4_SEEDS:
            raise HTTPException(status_code=422, detail="seed outside v4 pilot")
        self.seed = seed
        return {"task_id": f"v4-{seed}", "seed": seed}


def create_v4_app() -> FastAPI:
    app = FastAPI(title="PixelGym v4 Grounding Pilot Capture")
    state = _V4State()

    @app.post("/api/reset")
    def reset(payload: _ResetRequest) -> dict[str, int | str]:
        return state.reset(payload.seed)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(V4_STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=V4_STATIC_DIR), name="v4-static")
    app.mount("/fonts", StaticFiles(directory=SHARED_FONT_DIR), name="v4-fonts")
    return app


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def local_v4_server() -> Iterator[str]:
    """Serve the v4 pilot page on loopback for deterministic capture."""
    port = _free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(create_v4_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="v4-capture-server", daemon=True)
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
        raise RuntimeError("v4 capture server did not become ready")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("v4 capture server did not shut down")
