"""Loopback-only capture server for the deterministic v5 browser application."""

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

from pixelgym.grounding.v5.generator import generate_task
from pixelgym.tasks.vendor_form.render import FONT_DIR

STATIC_DIR = Path(__file__).parent / "v5_app" / "static"
READY_SELECTOR = '#ready-sentinel[data-ready="true"]'


def create_v5_capture_app() -> FastAPI:
    app = FastAPI(title="PixelGym Agent v5 Capture")

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index(seed: int = Query(...)) -> HTMLResponse:
        try:
            task = generate_task(seed)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        payload = {
            "public": task.public_dict(),
            # Capture-only application logic. The policy receives pixels, not
            # page internals, DOM state, this transition table, or candidates.
            "internal": {
                "targets": [stage.target_control_id for stage in task.stages],
                "required_text": [stage.required_text for stage in task.stages],
                "recovery_stages": [stage.recovery_stage for stage in task.stages],
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).replace(
            "</", "<\\/"
        )
        template = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(template.replace("__V5_TASK_JSON__", encoded))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="v5-static")
    app.mount("/fonts", StaticFiles(directory=FONT_DIR), name="v5-fonts")
    return app


def _bound_local_socket() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(2048)
    except BaseException:
        listener.close()
        raise
    return listener


@contextlib.contextmanager
def local_v5_server() -> Iterator[str]:
    listener = _bound_local_socket()
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(create_v5_capture_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name="v5-capture-server",
        daemon=True,
    )
    thread.start()
    health_url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(health_url, timeout=0.2).close()
            break
        except OSError:
            if not thread.is_alive():
                listener.close()
                raise RuntimeError("v5 capture server stopped before becoming ready")
            time.sleep(0.02)
    else:
        server.should_exit = True
        thread.join(timeout=2)
        listener.close()
        raise RuntimeError("v5 capture server did not become ready")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("v5 capture server did not shut down")
