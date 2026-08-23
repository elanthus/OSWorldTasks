"""Loopback-only browser server used to capture the frozen v4c state graph."""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from pixelgym.grounding.v4c_protocol import episode_for_seed
from pixelgym.tasks.vendor_form.render import FONT_DIR

V4C_STATIC_DIR = Path(__file__).parent / "v4c_app" / "static"
SHARED_FONT_DIR = FONT_DIR
V4C_READY_SELECTOR = '#ready-sentinel[data-ready="true"]'


def create_v4c_app() -> FastAPI:
    app = FastAPI(title="PixelGym v4c Pilot Capture")

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index(seed: int = Query(...)) -> HTMLResponse:
        try:
            episode = episode_for_seed(seed)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        public_episode = {
            "seed": episode["seed"],
            "family": episode["family"],
            "title": episode["title"],
            "carrier": episode["carrier"],
            "stages": [
                {
                    "heading": stage["heading"],
                    "instruction": stage["instruction"],
                    "facts": stage["facts"],
                    "options": stage["options"],
                }
                for stage in episode["stages"]
            ],
        }
        payload = json.dumps(public_episode, sort_keys=True).replace("</", "<\\/")
        template = (V4C_STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(template.replace("__V4C_EPISODE_JSON__", payload))

    app.mount("/static", StaticFiles(directory=V4C_STATIC_DIR), name="v4c-static")
    app.mount("/fonts", StaticFiles(directory=SHARED_FONT_DIR), name="v4c-fonts")
    return app


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def local_v4c_server() -> Iterator[str]:
    port = _free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(create_v4c_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="v4c-capture-server", daemon=True)
    thread.start()
    health_url = f"http://127.0.0.1:{port}/health"
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
        raise RuntimeError("v4c capture server did not become ready")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("v4c capture server did not shut down")
