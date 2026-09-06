"""Dependency-free guest service for the OSWorld vendor-form task.

The local application service in ``app/server.py`` uses FastAPI. The OSWorld
desktop image is deliberately treated as an appliance, though: task setup
must not depend on whatever third-party Python packages happen to be installed
in the guest or on a network package install. This module therefore exposes
the same small HTTP contract using only the Python standard library.

The task record is generated and hashed on the trusted host, bundled with the
static application, and passed with ``--task-json``.  ``POST /api/reset``
verifies that the requested seed is the bundled seed and atomically clears the
submission history.  No clocks enter task state and request logging is
disabled, so repeated resets cannot acquire timestamp-derived data.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlsplit

STATIC_DIR = Path(__file__).parent / "static"

_SUBMISSION_FIELDS = (
    "company_name",
    "contact_email",
    "contact_phone",
    "tax_id",
    "country",
    "payment_terms",
    "expedited_onboarding",
)


class PayloadValidationError(ValueError):
    """The request body does not match the public HTTP contract."""


class TaskConflictError(ValueError):
    """The request refers to a task other than the active bundled task."""


def _normalized_values(payload: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in _SUBMISSION_FIELDS:
        value = payload[name]
        if name == "expedited_onboarding":
            if type(value) is not bool:
                raise PayloadValidationError(f"{name} must be a bool")
            values[name] = value
        else:
            if not isinstance(value, str):
                raise PayloadValidationError(f"{name} must be a string")
            values[name] = value.strip()
    return values


class GuestTaskState:
    """Atomic task/submission state shared by request-handler threads."""

    def __init__(self, task: dict[str, Any]) -> None:
        self._task = task
        self._submissions: list[dict[str, Any]] = []
        self._page_ready = False
        self._lock = threading.RLock()

    def reset(self, seed: int) -> dict[str, Any]:
        with self._lock:
            if type(seed) is not int:
                raise PayloadValidationError("reset seed must be an int")
            if seed != self._task["seed"]:
                raise TaskConflictError("reset seed does not match the bundled task")
            self._submissions = []
            self._page_ready = False
            return {
                "task_id": self._task["task_id"],
                "seed": self._task["seed"],
                "requires_reload": True,
            }

    def public_task(self) -> dict[str, Any]:
        with self._lock:
            return {
                "task_id": self._task["task_id"],
                "schema_version": self._task["schema_version"],
                "fields": dict(self._task["fields"]),
                "options": {name: list(values) for name, values in self._task["options"].items()},
            }

    def submit(self, payload: dict[str, Any]) -> dict[str, int]:
        required = {"task_id", *_SUBMISSION_FIELDS}
        if set(payload) != required:
            raise PayloadValidationError(
                f"submission keys must be exactly {sorted(required)}"
            )

        with self._lock:
            if payload["task_id"] != self._task["task_id"]:
                raise TaskConflictError("submission task_id does not match the active task")
            record = {
                "task_id": self._task["task_id"],
                "seed": self._task["seed"],
                "values": _normalized_values(payload),
                "submitted_at_step": len(self._submissions) + 1,
                "final": True,
            }
            self._submissions.append(record)
            return {"submission_number": record["submitted_at_step"]}

    def mark_page_ready(self, task_id: str) -> None:
        with self._lock:
            if task_id != self._task["task_id"]:
                raise TaskConflictError("page-ready task_id does not match the active task")
            self._page_ready = True

    def page_ready(self) -> bool:
        with self._lock:
            return self._page_ready

    def privileged_state(self) -> dict[str, Any]:
        with self._lock:
            # JSON round-tripping gives callers a deep copy with exactly the
            # representation used across the guest/host boundary.
            value = json.loads(
                json.dumps(
                    {"task": self._task, "submissions": self._submissions},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            if not isinstance(value, dict):  # pragma: no cover - JSON object is constructed above
                raise TypeError("privileged state did not round-trip as an object")
            return cast(dict[str, Any], value)


class VendorFormRequestHandler(BaseHTTPRequestHandler):
    server: VendorFormHTTPServer

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/task":
            self._send_json(HTTPStatus.OK, self.server.state.public_task())
            return
        if path == "/api/state":
            self._send_json(HTTPStatus.OK, self.server.state.privileged_state())
            return
        if path == "/api/page-ready":
            self._send_json(HTTPStatus.OK, {"ready": self.server.state.page_ready()})
            return
        if path == "/healthz":
            self._send_json(HTTPStatus.OK, {"ready": True})
            return
        if path == "/":
            self._send_file(STATIC_DIR / "index.html")
            return
        if path.startswith("/static/"):
            relative = Path(unquote(path.removeprefix("/static/")))
            if relative.is_absolute() or ".." in relative.parts:
                self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
                return
            self._send_file(STATIC_DIR / relative)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/reset":
                if set(payload) != {"seed"}:
                    raise PayloadValidationError("reset payload must contain only seed")
                result = self.server.state.reset(payload["seed"])
            elif path == "/api/submit":
                result = self.server.state.submit(payload)
            elif path == "/api/page-ready":
                if set(payload) != {"task_id"} or not isinstance(payload["task_id"], str):
                    raise PayloadValidationError("page-ready payload must contain only task_id")
                self.server.state.mark_page_ready(payload["task_id"])
                result = {"ready": True}
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
                return
        except TaskConflictError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"detail": str(exc)})
            return
        except PayloadValidationError as exc:
            self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": str(exc)})
            return
        self._send_json(HTTPStatus.OK, result)

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise PayloadValidationError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise PayloadValidationError("Content-Length must be an int") from exc
        if not 0 <= length <= 64 * 1024:
            raise PayloadValidationError("request body is too large")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PayloadValidationError("request body must be valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise PayloadValidationError("request body must be a JSON object")
        return value

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        # BaseHTTPRequestHandler logs wall-clock timestamps.  They are not
        # task state, but suppressing them keeps guest evidence deterministic.
        return


class VendorFormHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: GuestTaskState) -> None:
        self.state = state
        super().__init__(address, VendorFormRequestHandler)


def load_task(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {"task_id", "schema_version", "seed", "fields", "options"}
    if not isinstance(value, dict) or not required.issubset(value):
        raise ValueError(f"task JSON must contain {sorted(required)}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the PixelGym vendor form in OSWorld")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--task-json", type=Path, required=True)
    args = parser.parse_args(argv)

    server = VendorFormHTTPServer((args.host, args.port), GuestTaskState(load_task(args.task_json)))
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a guest process
    raise SystemExit(main())
