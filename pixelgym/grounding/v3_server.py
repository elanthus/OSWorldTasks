"""Capture-only servers for v3b and v3c variant pages.

Serves variant static assets for build-time grounding capture.
These servers are NEVER mounted by the task app or the OSWorld adapter.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pixelgym.tasks.vendor_form import generator
from pixelgym.tasks.vendor_form.normalization import normalize_submitted_values

V3B_STATIC_DIR = Path(__file__).parent / "v3b_app" / "static"
V3C_STATIC_DIR = Path(__file__).parent / "v3c_app" / "static"

V3B_READY_SELECTOR = '#ready-sentinel[data-ready="true"]'
V3C_READY_SELECTOR = '#ready-sentinel[data-ready="true"]'


class _ResetRequest(BaseModel):
    seed: int


class _SubmitRequest(BaseModel):
    task_id: str
    company_name: str
    contact_email: str
    contact_phone: str
    tax_id: str
    country: str
    payment_terms: str
    expedited_onboarding: bool


class _V3bState:
    def __init__(self) -> None:
        self.task: dict[str, Any] | None = None
        self.submission_count: int = 0

    def reset(self, seed: int) -> dict[str, Any]:
        self.task = generator.generate_task(seed)
        self.submission_count = 0
        return self.task

    def require_task(self) -> dict[str, Any]:
        if self.task is None:
            raise HTTPException(status_code=409, detail="No active task.")
        return self.task


def create_v3b_app() -> FastAPI:
    app = FastAPI(title="PixelGym v3b Grounding Capture")
    state = _V3bState()

    @app.post("/api/reset")
    def reset(payload: _ResetRequest) -> dict[str, Any]:
        task = state.reset(payload.seed)
        return {"task_id": task["task_id"], "seed": task["seed"]}

    @app.get("/api/task")
    def get_task() -> dict[str, Any]:
        task = state.require_task()
        return {
            "task_id": task["task_id"],
            "fields": task["fields"],
            "options": task["options"],
        }

    @app.post("/api/submit")
    def submit(payload: _SubmitRequest) -> dict[str, Any]:
        task = state.require_task()
        if payload.task_id != task["task_id"]:
            raise HTTPException(status_code=409, detail="task_id mismatch")
        state.submission_count += 1
        return {"submission_number": state.submission_count}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(V3B_STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=V3B_STATIC_DIR), name="static")

    return app


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def local_v3b_server() -> Iterator[str]:
    """Serve the v3b variant page on loopback for capture."""
    port = _free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(create_v3b_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="v3b-capture-server", daemon=True)
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
        raise RuntimeError("v3b capture server did not become ready")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("v3b capture server did not shut down")


class _V3cState:
    def __init__(self) -> None:
        self.seed: int | None = None
        self.task_id: str | None = None

    def reset(self, seed: int) -> dict[str, Any]:
        self.seed = seed
        self.task_id = f"v3c-{seed}"
        return {"task_id": self.task_id, "seed": seed}


def create_v3c_app() -> FastAPI:
    app = FastAPI(title="PixelGym v3c Grounding Capture")
    state = _V3cState()

    @app.post("/api/reset")
    def reset(payload: _ResetRequest) -> dict[str, Any]:
        return state.reset(payload.seed)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(V3C_STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=V3C_STATIC_DIR), name="static")

    return app


@contextlib.contextmanager
def local_v3c_server() -> Iterator[str]:
    """Serve the v3c data-table variant page on loopback for capture."""
    port = _free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(create_v3c_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="v3c-capture-server", daemon=True)
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
        raise RuntimeError("v3c capture server did not become ready")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("v3c capture server did not shut down")
