"""Deterministic in-process v5 application backend and resume extension."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pixelgym.backends.base import (
    EnvironmentResumeRecord,
    Frame,
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5.contracts import (
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    StageKind,
    V5Task,
)
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.serialization import canonical_json_bytes
from pixelgym.task_spec import Submission
from pixelgym.tasks.vendor_form.render import FONT_DIR

_REGULAR_FONT = FONT_DIR / "DejaVuSans.ttf"
_BOLD_FONT = FONT_DIR / "DejaVuSans-Bold.ttf"
_NO_TASK = "V5FakeBackend has no active task; call reset(seed) first"


@dataclass(frozen=True)
class VisibleControl:
    control_id: str
    label: str
    bbox: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        x0, y0, x1, y1 = self.bbox
        return ((x0 + x1) // 2, (y0 + y1) // 2)


class V5FakeBackend:
    """Finite-state v5 GUI driven only by bounded click/key actions.

    The task targets and diagnostic annotations remain host-side.  Screenshots
    are rendered from policy-safe stage content and visible error messages.
    """

    width = SCREEN_WIDTH
    height = SCREEN_HEIGHT
    app_url = "fake://pixelgym-agent-v5"
    backend_identity = "pixelgym-v5-fake-backend-v1"

    def __init__(self, *, task_factory: Callable[[int], V5Task] = generate_task) -> None:
        self.task_factory = task_factory
        self._task: V5Task | None = None
        self._stage_index = 0
        self._focused = False
        self._text_value = ""
        self._visible_error: str | None = None
        self._repair_pending = False
        self._intentional_errors_entered: set[int] = set()
        self._irreversible_failure = False
        self._submissions: list[Submission] = []
        self._action_count = 0
        self._frame: Frame | None = None
        self._closed = False
        self._last_diagnostic = "reset"

    @property
    def task(self) -> V5Task:
        if self._task is None:
            raise RuntimeError(_NO_TASK)
        return self._task

    @property
    def stage_index(self) -> int:
        return self._stage_index

    @property
    def action_count(self) -> int:
        return self._action_count

    @property
    def irreversible_failure(self) -> bool:
        return self._irreversible_failure

    def reset(self, seed: int) -> Mapping[str, Any]:
        if self._closed:
            raise RuntimeError("V5FakeBackend is closed")
        self._task = self.task_factory(seed)
        self._stage_index = 0
        self._focused = False
        self._text_value = ""
        self._visible_error = None
        self._repair_pending = False
        self._intentional_errors_entered = set()
        self._irreversible_failure = False
        self._submissions = []
        self._action_count = 0
        self._frame = None
        self._last_diagnostic = "reset"
        return self.task.generated_record()

    def screenshot(self) -> Frame:
        self._require_active()
        if self._frame is None:
            self._frame = self._render()
        return self._frame.copy()

    def visible_controls(self) -> tuple[VisibleControl, ...]:
        task = self._require_active()
        if self._irreversible_failure or self._stage_index >= len(task.stages):
            return ()
        if self._repair_pending:
            return (
                VisibleControl(
                    "repair_implicated",
                    "Repair the implicated verification selection",
                    (190, 430, 834, 488),
                ),
            )
        stage = task.stages[self._stage_index]
        controls: list[VisibleControl] = []
        top = 430
        for index, control in enumerate(stage.controls):
            controls.append(
                VisibleControl(
                    control.control_id,
                    control.label,
                    (190, top + index * 72, 834, top + 58 + index * 72),
                )
            )
        return tuple(controls)

    def control_center(self, control_id: str) -> tuple[int, int]:
        for control in self.visible_controls():
            if control.control_id == control_id:
                return control.center
        raise ValueError(f"control {control_id!r} is not visible")

    def noop(self) -> None:
        self._action_count += 1
        self._last_diagnostic = "non_progressing_noop"

    def click(self, x: int, y: int) -> None:
        task = self._require_active()
        self._action_count += 1
        if self._irreversible_failure or self._stage_index >= len(task.stages):
            self._last_diagnostic = "action_after_irreversible_commit"
            return
        clicked = next(
            (
                control.control_id
                for control in self.visible_controls()
                if control.bbox[0] <= x < control.bbox[2] and control.bbox[1] <= y < control.bbox[3]
            ),
            None,
        )
        stage = task.stages[self._stage_index]
        if clicked is None:
            self._error("The click did not land on an actionable control.", "missed_control")
            return
        if clicked == "repair_implicated":
            if not self._repair_pending:
                self._error("No declared recovery is pending.", "unexpected_repair")
                return
            self._repair_pending = False
            self._visible_error = None
            self._advance("visible_error_repaired")
            return
        if stage.kind is StageKind.TEXT:
            if clicked == "text_input":
                self._focused = True
                self._visible_error = None
                self._last_diagnostic = "text_input_focused"
                self._frame = None
                return
            if clicked == "continue" and self._text_value == stage.required_text:
                self._advance("text_value_accepted")
            else:
                self._error(
                    "The short code does not match the visible source. Repair only this entry.",
                    "invalid_text_value",
                )
            return
        if clicked != stage.target_control_id:
            if stage.kind is StageKind.COMMIT:
                self._seal_wrong_commit(clicked)
            else:
                self._error(
                    "That choice conflicts with the visible evidence. Recheck this decision.",
                    "incorrect_choice",
                )
            return
        if stage.recovery_stage and self._stage_index not in self._intentional_errors_entered:
            self._intentional_errors_entered.add(self._stage_index)
            self._repair_pending = True
            self._error(
                "Verification rejected this selection: repair the implicated reference only.",
                "entered_declared_recovery",
            )
            return
        self._advance("correct_transition")

    def key(self, key: str) -> None:
        task = self._require_active()
        self._action_count += 1
        if self._irreversible_failure or self._stage_index >= len(task.stages):
            self._last_diagnostic = "key_after_irreversible_commit"
            return
        stage = task.stages[self._stage_index]
        if stage.kind is not StageKind.TEXT or not self._focused:
            self._error("No text entry is focused.", "key_without_focus")
            return
        if key == "Backspace":
            self._text_value = self._text_value[:-1]
        elif len(key) == 1 and key.isprintable():
            self._text_value += key
        else:
            self._last_diagnostic = "ignored_named_key"
        self._frame = None

    def read_submissions(self) -> Sequence[Submission]:
        return tuple(self._submissions)

    def install_submission(
        self,
        values: Mapping[str, Any],
        *,
        task_id: str | None = None,
        seed: int | None = None,
    ) -> Submission:
        """Privileged audit hook for stale-task and malformed-submission tests."""

        task = self._require_active()
        submission = Submission(
            task_id=task.task_id if task_id is None else task_id,
            seed=task.seed if seed is None else seed,
            values=values,
            submitted_at_step=max(1, self._action_count + len(self._submissions) + 1),
        )
        self._submissions.append(submission)
        return submission

    def close(self) -> None:
        self._closed = True

    def read_privileged_diagnostic(self) -> dict[str, Any]:
        """Runner-only diagnostic event; never supplied through environment info."""

        task = self._require_active()
        return {
            "task_id": task.task_id,
            "stage_index": self._stage_index,
            "event": self._last_diagnostic,
            "visible_error": self._visible_error is not None,
            "irreversible_failure": self._irreversible_failure,
        }

    def checkpoint(self) -> bytes:
        task = self._require_active()
        value = {
            "schema_version": "pixelgym-v5-fake-checkpoint-v1",
            "seed": task.seed,
            "task_id": task.task_id,
            "stage_index": self._stage_index,
            "focused": self._focused,
            "text_value": self._text_value,
            "visible_error": self._visible_error,
            "repair_pending": self._repair_pending,
            "intentional_errors_entered": sorted(self._intentional_errors_entered),
            "irreversible_failure": self._irreversible_failure,
            "submissions": [
                {
                    "task_id": submission.task_id,
                    "seed": submission.seed,
                    "values": dict(submission.values),
                    "submitted_at_step": submission.submitted_at_step,
                    "final": submission.final,
                }
                for submission in self._submissions
            ],
            "action_count": self._action_count,
            "last_diagnostic": self._last_diagnostic,
        }
        return canonical_json_bytes(value)

    def restore(self, checkpoint: bytes) -> None:
        try:
            value = json.loads(checkpoint)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid v5 fake-backend checkpoint") from exc
        if not isinstance(value, dict):
            raise ValueError(  # noqa: TRY004 - malformed serialized checkpoint value
                "v5 fake-backend checkpoint must be an object"
            )
        if value.get("schema_version") != "pixelgym-v5-fake-checkpoint-v1":
            raise ValueError("unsupported v5 fake-backend checkpoint schema")
        required = {
            "seed",
            "task_id",
            "stage_index",
            "focused",
            "text_value",
            "visible_error",
            "repair_pending",
            "intentional_errors_entered",
            "irreversible_failure",
            "submissions",
            "action_count",
            "last_diagnostic",
        }
        if not required <= value.keys():
            raise ValueError("v5 fake-backend checkpoint is missing required fields")
        task = self.task_factory(value["seed"])
        if task.task_id != value.get("task_id"):
            raise ValueError("checkpoint task identity mismatch")
        self._closed = False
        self._task = task
        self._stage_index = value["stage_index"]
        self._focused = value["focused"]
        self._text_value = value["text_value"]
        self._visible_error = value["visible_error"]
        self._repair_pending = value["repair_pending"]
        self._intentional_errors_entered = set(value["intentional_errors_entered"])
        self._irreversible_failure = value["irreversible_failure"]
        self._submissions = [Submission.from_record(row) for row in value["submissions"]]
        self._action_count = value["action_count"]
        self._last_diagnostic = value["last_diagnostic"]
        self._frame = None

    def environment_resume_record(self, *, step_count: int) -> EnvironmentResumeRecord:
        checkpoint = self.checkpoint()
        return EnvironmentResumeRecord(
            task_id=self.task.task_id,
            backend_identity=self.backend_identity,
            step_count=step_count,
            screenshot_digest="sha256:" + sha256_bytes(self.screenshot().tobytes()),
            application_state_digest=content_digest(json.loads(checkpoint)),
            mechanism="checkpoint_restore",
            checkpoint_digest="sha256:" + sha256_bytes(checkpoint),
        )

    def verify_resume_record(self, record: EnvironmentResumeRecord, *, step_count: int) -> None:
        current = self.environment_resume_record(step_count=step_count)
        if current != record:
            raise RuntimeError("restored v5 backend state does not match sealed resume record")

    def _require_active(self) -> V5Task:
        if self._task is None:
            raise RuntimeError(_NO_TASK)
        return self._task

    def _error(self, message: str, diagnostic: str) -> None:
        self._visible_error = message
        self._last_diagnostic = diagnostic
        self._frame = None

    def _advance(self, diagnostic: str) -> None:
        task = self._require_active()
        stage = task.stages[self._stage_index]
        self._stage_index += 1
        self._focused = False
        self._text_value = ""
        self._visible_error = None
        self._repair_pending = False
        self._last_diagnostic = diagnostic
        self._frame = None
        if stage.kind is StageKind.COMMIT:
            self._submissions.append(
                Submission(
                    task_id=task.task_id,
                    seed=task.seed,
                    values={"workflow_result": task.expected_result},
                    submitted_at_step=max(1, self._action_count),
                )
            )

    def _seal_wrong_commit(self, clicked: str) -> None:
        task = self._require_active()
        self._irreversible_failure = True
        self._visible_error = "The incorrect final commit is sealed and cannot be repaired."
        self._last_diagnostic = "wrong_irreversible_commit"
        self._submissions.append(
            Submission(
                task_id=task.task_id,
                seed=task.seed,
                values={"workflow_result": f"wrong-{clicked}"},
                submitted_at_step=max(1, self._action_count),
            )
        )
        self._frame = None

    def _render(self) -> Frame:
        task = self._require_active()
        image = Image.new("RGB", (self.width, self.height), "#f4f6fa")
        draw = ImageDraw.Draw(image)
        regular = ImageFont.truetype(str(_REGULAR_FONT), 18)
        small = ImageFont.truetype(str(_REGULAR_FONT), 15)
        bold = ImageFont.truetype(str(_BOLD_FONT), 27)
        draw.rectangle((0, 0, self.width, 82), fill="#17253d")
        draw.text((42, 24), "Vendor Onboarding · Agent Benchmark v5", font=bold, fill="white")
        draw.rounded_rectangle((54, 112, 970, 714), radius=12, fill="white", outline="#cbd3df", width=2)
        draw.text((84, 134), task.title, font=bold, fill="#17253d")
        if self._irreversible_failure:
            draw.text((84, 214), "Final commit rejected", font=bold, fill="#9f1d20")
            draw.multiline_text((84, 270), self._visible_error or "", font=regular, fill="#4b5563", spacing=8)
        elif self._stage_index >= len(task.stages):
            draw.text((84, 240), "Submission recorded for host evaluation.", font=bold, fill="#17633a")
        else:
            stage = task.stages[self._stage_index]
            draw.text(
                (84, 188),
                f"Decision {self._stage_index + 1} of {len(task.stages)} · {stage.heading}",
                font=regular,
                fill="#334155",
            )
            draw.multiline_text((84, 228), stage.instruction, font=regular, fill="#111827", spacing=6)
            y = 286
            for fact in stage.facts:
                draw.text((104, y), f"• {fact}", font=small, fill="#354153")
                y += 28
            if self._visible_error:
                draw.rounded_rectangle((84, 360, 940, 412), radius=8, fill="#fff0f0", outline="#b4232c")
                draw.text((102, 376), self._visible_error, font=small, fill="#8a1820")
            for control in self.visible_controls():
                x0, y0, _x1, _y1 = control.bbox
                fill = "#eef4ff" if control.control_id == "text_input" else "#f8fafc"
                if control.control_id == "text_input" and self._focused:
                    fill = "#e3efff"
                draw.rounded_rectangle(control.bbox, radius=7, fill=fill, outline="#486284", width=2)
                label = self._text_value if control.control_id == "text_input" else control.label
                if control.control_id == "text_input" and not label:
                    label = "Click, then enter the short code"
                draw.text((x0 + 18, y0 + 17), label, font=regular, fill="#17253d")
        draw.text(
            (70, 730),
            f"{PROTOCOL_FOOTER} · fixed 1024×768 · no network or clock",
            font=small,
            fill="#65748a",
        )
        return np.asarray(image, dtype=np.uint8).copy()


PROTOCOL_FOOTER = "pixelgym-agent-v5"
