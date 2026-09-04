"""Minimal FastAPI service fronting the deterministic vendor-onboarding task.

Endpoints:

- ``POST /api/reset``  — install a new task for a seed; clears submissions.
- ``GET  /api/task``   — public "request card" view (the values a human/agent
  is meant to read and transcribe; not secret).
- ``POST /api/submit`` — record an immutable submission event.
- ``GET  /api/state``  — privileged view for the host-side evaluator (task +
  every submission). Not linked from the UI.
- ``GET  /``           — the form itself, served as static HTML.

State is held in-process per app instance (no database, no clock, no
network), which is what makes ``reset`` idempotent and submissions
deterministic.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import MappingProxyType
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from pixelgym.tasks.vendor_form import generator
from pixelgym.tasks.vendor_form.normalization import normalize_submitted_values

STATIC_DIR = Path(__file__).parent / "static"

_NO_ACTIVE_TASK = "No active task. Call POST /api/reset first."


class ResetRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    seed: int


class SubmitRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    task_id: str
    company_name: str
    contact_email: str
    contact_phone: str
    tax_id: str
    country: str
    payment_terms: str
    expedited_onboarding: bool


class PageReadyRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    task_id: str


@dataclasses.dataclass(frozen=True)
class SubmissionRecord:
    """An immutable submission event. Neither the record nor its ``values``
    can be mutated after creation, so a caller holding a reference cannot
    corrupt the privileged history the evaluator reads."""

    task_id: str
    seed: int
    values: MappingProxyType[str, Any]
    submitted_at_step: int
    final: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "seed": self.seed,
            "values": dict(self.values),
            "submitted_at_step": self.submitted_at_step,
            "final": self.final,
        }


class VendorFormState:
    """In-memory task + submission history for one running app instance."""

    def __init__(self) -> None:
        self.task: dict[str, Any] | None = None
        self.submissions: list[SubmissionRecord] = []
        self.page_ready = False

    def reset(self, seed: int) -> dict[str, Any]:
        self.task = generator.generate_task(seed)
        self.submissions = []
        self.page_ready = False
        return self.task

    def require_task(self) -> dict[str, Any]:
        if self.task is None:
            raise HTTPException(status_code=409, detail=_NO_ACTIVE_TASK)
        return self.task

    def submit(self, task_id: str, values: dict[str, Any]) -> SubmissionRecord:
        task = self.require_task()
        if task_id != task["task_id"]:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Submission task_id {task_id!r} does not match the active task "
                    f"{task['task_id']!r}. The task was reset since this was read; "
                    "re-fetch /api/task before submitting."
                ),
            )
        record = SubmissionRecord(
            task_id=task_id,
            seed=task["seed"],
            values=MappingProxyType(normalize_submitted_values(values)),
            submitted_at_step=len(self.submissions) + 1,
        )
        self.submissions.append(record)
        return record

    def mark_page_ready(self, task_id: str) -> None:
        task = self.require_task()
        if task_id != task["task_id"]:
            raise HTTPException(status_code=409, detail="page-ready task_id is stale")
        self.page_ready = True


def create_app() -> FastAPI:
    app = FastAPI(title="PixelGym Vendor Onboarding Task")
    state = VendorFormState()
    app.state.vendor_form = state

    @app.post("/api/reset")
    def reset(payload: ResetRequest) -> dict[str, Any]:
        task = state.reset(payload.seed)
        return {"task_id": task["task_id"], "seed": task["seed"]}

    @app.get("/api/task")
    def get_task() -> dict[str, Any]:
        task = state.require_task()
        return {
            "task_id": task["task_id"],
            "schema_version": task["schema_version"],
            "fields": task["fields"],
            "options": task["options"],
        }

    @app.post("/api/submit")
    def submit(payload: SubmitRequest) -> dict[str, Any]:
        body = payload.model_dump()
        task_id = body.pop("task_id")
        record = state.submit(task_id, body)
        return {"submission_number": record.submitted_at_step}

    @app.post("/api/page-ready")
    def mark_page_ready(payload: PageReadyRequest) -> dict[str, bool]:
        state.mark_page_ready(payload.task_id)
        return {"ready": True}

    @app.get("/api/page-ready")
    def get_page_ready() -> dict[str, bool]:
        state.require_task()
        return {"ready": state.page_ready}

    @app.get("/api/state")
    def get_state() -> dict[str, Any]:
        """Privileged evaluator view. Not reachable from any UI link."""
        return {
            "task": state.task,
            "submissions": [record.to_dict() for record in state.submissions],
        }

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
