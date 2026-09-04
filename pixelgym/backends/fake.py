"""In-process fake backend (no VM, no browser, no network, no wall-clock).

Implements the `Backend` protocol entirely in process: `reset` installs a
freshly seeded task via the production generator
(`pixelgym.tasks.vendor_form.generator`, so determinism is genuine rather than
stipulated), `screenshot` returns a deterministic RGB frame, `click`/`key`
drive an actual model of the vendor form, and `read_submissions` exposes the
privileged submission state the evaluator reads.

The point of driving a real form model, rather than accepting and discarding
input events, is that reward has to be *reachable through the action space*:
the frozen golden trajectory submits a correct answer using only
`CLICK` and `KEY`, and every unit test asserting "reward fires exactly once, on
a valid submission" is worth little if the only way to produce a submission is
a privileged test hook. Widget geometry and interaction semantics live in
`pixelgym.tasks.vendor_form.ui`; drawing lives in
`pixelgym.tasks.vendor_form.render`.

Fidelity to the real app, where it matters:

- At 1024x768, every form-control and payment-option hit rectangle is pinned
  to Chromium `getBoundingClientRect()` output by an opt-in build-time test.
  The country select remains focused and closed after a click in both models;
  transferable selection is allowlisted type-ahead followed by Enter.
- Enter and Tab follow the task app's tested form semantics: Enter submits only
  from text inputs, the checkbox, and Submit, while Tab from Submit leaves the
  form with no modeled focus.
- The submission record is built exactly as `POST /api/submit` builds it --
  same field names, same `submitted_at_step` numbering, same whitespace
  normalization (shared via `pixelgym.tasks.vendor_form.normalization`).
- Submitting an incomplete or wrong form records a submission, as the real app
  does. Deciding whether it is *correct* is the evaluator's job alone.
- Unfilled text fields and unmade selections submit as `""`.

What this backend still does not attempt: rendering or hit-testing a native
select popup, font rasterization identical to a browser's, or the timing
behavior of a live VM. Those properties are outside the transferable contract
or measured separately on the OSWorld backend.

Two hooks exist for tests only and are not part of the `Backend` protocol:
`install_form_values` (put the form into a precise state without typing) and
`install_submission` (append a submission record directly, including the
deliberately malformed ones -- stale task, wrong seed -- that no legitimate
interaction can produce).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pixelgym.backends.base import Frame
from pixelgym.grounding.v5.contracts import EnvironmentResumeRecord, content_digest, sha256_bytes
from pixelgym.serialization import canonical_json_bytes
from pixelgym.task_spec import Submission
from pixelgym.tasks.vendor_form import generator, render, ui
from pixelgym.tasks.vendor_form.normalization import normalize_submitted_values

DEFAULT_WIDTH = ui.DESIGN_WIDTH
DEFAULT_HEIGHT = ui.DESIGN_HEIGHT

_NO_TASK = "FakeBackend has no active task; call reset(seed) first."


class FakeBackend:
    """Minimal in-process implementation of `pixelgym.backends.base.Backend`."""

    def __init__(self, *, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> None:
        self._width = width
        self._height = height
        self._task_record: Mapping[str, Any] | None = None
        self._layout: ui.Layout | None = None
        self._form: ui.FormState | None = None
        self._submissions: list[Submission] = []
        self._frame: Frame | None = None
        self.click_calls: list[tuple[int, int]] = []
        self.key_calls: list[str] = []
        self.noop_calls = 0
        self.closed = False

    # -- Backend protocol ---------------------------------------------------

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def app_url(self) -> str:
        return "fake://vendor-form"

    def reset(self, seed: int) -> Mapping[str, Any]:
        record = generator.generate_task(seed)
        self._task_record = record
        self._layout = ui.layout_for(record, self._width, self._height)
        self._form = ui.FormState(
            layout=self._layout,
            country_options=record["options"]["country"],
            payment_options=record["options"]["payment_terms"],
        )
        self._submissions = []
        self.click_calls = []
        self.key_calls = []
        self.noop_calls = 0
        self._frame = None
        return record

    def screenshot(self) -> Frame:
        """The current frame, rendered from the task and form state.

        Cached until the next state change, and copied on the way out so a
        caller holding an observation cannot mutate what the next screenshot
        returns.
        """
        record, layout, form = self._require_task()
        if self._frame is None:
            self._frame = render.render(record, form, layout)
        return self._frame.copy()

    def noop(self) -> None:
        self._require_task()
        self.noop_calls += 1

    def click(self, x: int, y: int) -> None:
        _record, _layout, form = self._require_task()
        self.click_calls.append((x, y))
        self._frame = None
        if form.click(x, y):
            self._record_submission()

    def key(self, key: str) -> None:
        _record, _layout, form = self._require_task()
        self.key_calls.append(key)
        self._frame = None
        if form.key(key):
            self._record_submission()

    def read_submissions(self) -> Sequence[Submission]:
        return list(self._submissions)

    def read_privileged_state(self) -> dict[str, Any]:
        """Validation-only copy of the host-side task/submission state.

        This method is not part of the agent-facing environment observation
        or ``info`` mapping.  It mirrors the real backend's privileged probe
        so reset determinism can hash equivalent state on both providers.
        """
        record, _layout, _form = self._require_task()
        return {
            "task": dict(record),
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
        }

    def checkpoint(self) -> bytes:
        """Content-addressable v5 checkpoint for the in-process backend."""

        record, _layout, form = self._require_task()
        return canonical_json_bytes(
            {
                "schema_version": "pixelgym-core-fake-checkpoint-v1",
                "width": self.width,
                "height": self.height,
                "seed": record["seed"],
                "task_id": record["task_id"],
                "text": {widget.value: form.text[widget] for widget in ui.TEXT_WIDGETS},
                "country_index": form.country_index,
                "payment_index": form.payment_index,
                "expedited": form.expedited,
                "focus": None if form.focus is None else form.focus.value,
                "country_open": form.country_open,
                "status": form.status,
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
                "click_calls": [list(call) for call in self.click_calls],
                "key_calls": self.key_calls,
                "noop_calls": self.noop_calls,
            }
        )

    def restore(self, checkpoint: bytes) -> None:
        try:
            value = json.loads(checkpoint)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid core fake-backend checkpoint") from exc
        if not isinstance(value, dict):
            raise ValueError(  # noqa: TRY004 - malformed serialized checkpoint value
                "core fake-backend checkpoint must be an object"
            )
        if value.get("schema_version") != "pixelgym-core-fake-checkpoint-v1":
            raise ValueError("unsupported core fake-backend checkpoint schema")
        required = {
            "width",
            "height",
            "seed",
            "task_id",
            "text",
            "country_index",
            "payment_index",
            "expedited",
            "focus",
            "country_open",
            "status",
            "submissions",
            "click_calls",
            "key_calls",
            "noop_calls",
        }
        if not required <= value.keys():
            raise ValueError("core fake-backend checkpoint is missing required fields")
        if (value["width"], value["height"]) != (self.width, self.height):
            raise ValueError("fake-backend checkpoint screen mismatch")
        if value["country_open"] is not False:
            raise ValueError("fake-backend checkpoint contains unsupported country popup state")
        self.closed = False
        record = self.reset(value["seed"])
        if record["task_id"] != value["task_id"]:
            raise ValueError("fake-backend checkpoint task mismatch")
        _record, _layout, form = self._require_task()
        form.text = {ui.WidgetId(name): text for name, text in value["text"].items()}
        form.country_index = value["country_index"]
        form.payment_index = value["payment_index"]
        form.expedited = value["expedited"]
        form.focus = None if value["focus"] is None else ui.WidgetId(value["focus"])
        form.status = value["status"]
        self._submissions = [Submission.from_record(row) for row in value["submissions"]]
        self.click_calls = [tuple(call) for call in value["click_calls"]]
        self.key_calls = list(value["key_calls"])
        self.noop_calls = value["noop_calls"]
        self._frame = None

    def environment_resume_record(self, *, step_count: int) -> EnvironmentResumeRecord:
        record, _layout, _form = self._require_task()
        checkpoint = self.checkpoint()
        return EnvironmentResumeRecord(
            task_id=record["task_id"],
            backend_identity=f"pixelgym-core-fake-{self.width}x{self.height}-v1",
            step_count=step_count,
            screenshot_digest="sha256:" + sha256_bytes(self.screenshot().tobytes()),
            application_state_digest=content_digest(json.loads(checkpoint)),
            mechanism="checkpoint_restore",
            checkpoint_digest="sha256:" + sha256_bytes(checkpoint),
        )

    def verify_resume_record(
        self, resume_record: EnvironmentResumeRecord, *, step_count: int
    ) -> None:
        if self.environment_resume_record(step_count=step_count) != resume_record:
            raise RuntimeError("core fake-backend resume binding mismatch")

    def close(self) -> None:
        self.closed = True

    # -- Internals ----------------------------------------------------------

    def _require_task(self) -> tuple[Mapping[str, Any], ui.Layout, ui.FormState]:
        if self._task_record is None or self._layout is None or self._form is None:
            raise RuntimeError(_NO_TASK)
        return self._task_record, self._layout, self._form

    def _record_submission(self) -> None:
        """Record a submission produced by a real click or keystroke.

        This path never takes overrides: a submission the agent caused is
        always attributed to the active task and seed, and always numbered one
        past the highest number already recorded. Numbering from the maximum
        rather than the list length keeps it monotonic even when a test has
        installed a record with an out-of-band step number -- an agent must not
        be able to produce a duplicate `submitted_at_step`, which is the
        ambiguity the evaluator refuses to resolve.
        """
        record, _layout, form = self._require_task()
        complete = form.is_complete()
        self._submissions.append(
            Submission(
                task_id=record["task_id"],
                seed=record["seed"],
                values=normalize_submitted_values(form.values()),
                submitted_at_step=self._next_submission_step(),
                final=True,
            )
        )
        form.status = "Submitted." if complete else ui.INCOMPLETE_SUBMISSION_MESSAGE

    def _next_submission_step(self) -> int:
        return max((s.submitted_at_step for s in self._submissions), default=0) + 1

    # -- Test-only accessors and hooks (not part of the Backend protocol) ----

    @property
    def form(self) -> ui.FormState:
        """The live form state, for asserting on focus/typed values in tests."""
        _record, _layout, form = self._require_task()
        return form

    @property
    def layout(self) -> ui.Layout:
        """Widget geometry, so a test can aim a click at a named control
        instead of hard-coding pixel coordinates."""
        _record, layout, _form = self._require_task()
        return layout

    def current_fields(self) -> dict[str, Any]:
        """The active task's expected field values, for building a correct or
        deliberately-wrong submission in a test."""
        record, _layout, _form = self._require_task()
        return dict(record["fields"])

    def install_form_values(self, values: Mapping[str, Any]) -> None:
        """Put the form into a precise state without typing it in.

        Accepts the same field names and value types a submission uses. Only
        states an agent could actually reach through `CLICK` and `KEY` may be
        installed: text fields take strings, the checkbox takes a real `bool`,
        and the two selection fields take either `""` (nothing chosen yet) or
        one of the current task's options. A shortcut into a state the action
        space cannot produce would let a test assert on behavior the real
        environment can never exhibit.

        Atomic: every field is validated before any is written, so a rejected
        call leaves the form -- values, selections, focus, status, and the
        cached frame -- exactly as it was.

        Raises `TypeError` for a wrong value type and `ValueError` for an
        unknown field name or an option that does not exist.
        """
        _record, _layout, form = self._require_task()
        updates = self._validated_updates(values, form)  # raises before mutating
        for widget, value in updates.items():
            if widget in ui.TEXT_WIDGETS:
                form.text[widget] = value
            elif widget is ui.WidgetId.COUNTRY:
                form.country_index = value
            elif widget is ui.WidgetId.PAYMENT_TERMS:
                form.payment_index = value
            else:
                form.expedited = value
        self._frame = None

    @staticmethod
    def _validated_updates(values: Mapping[str, Any], form: ui.FormState) -> dict[ui.WidgetId, Any]:
        """Resolve `values` into `{widget: value-to-assign}`, or raise.

        Selection fields resolve to an option index (or `None` for the empty
        choice) here, so the apply pass cannot fail partway through on a lookup.
        """
        updates: dict[ui.WidgetId, Any] = {}
        for name, value in values.items():
            widget = _form_widget(name)
            if widget in ui.TEXT_WIDGETS:
                if not isinstance(value, str):
                    raise TypeError(
                        f"{name!r} is a text field and takes a str, got "
                        f"{value!r} of type {type(value).__name__}"
                    )
                updates[widget] = value
            elif widget is ui.WidgetId.EXPEDITED_ONBOARDING:
                # `type(...) is bool`, not `isinstance`: the evaluator compares
                # types strictly, so an int 1 standing in for True here would
                # install a state no checkbox click could produce.
                if type(value) is not bool:
                    raise TypeError(
                        f"{name!r} is a checkbox and takes a bool, got "
                        f"{value!r} of type {type(value).__name__}"
                    )
                updates[widget] = value
            else:
                options = (
                    form.country_options if widget is ui.WidgetId.COUNTRY else form.payment_options
                )
                updates[widget] = _option_index(name, value, options)
        return updates

    def install_submission(
        self,
        values: Mapping[str, Any],
        *,
        task_id: str | None = None,
        seed: int | None = None,
        submitted_at_step: int | None = None,
        final: bool = True,
    ) -> Submission:
        """Append a submission record directly, bypassing the form, and return it.

        Every field of the record can be set explicitly, because the histories
        the evaluator must defend against are exactly the ones no legitimate
        interaction can produce: a submission carrying a *stale* `task_id` from
        a previous episode, one recorded under a *different seed*, a *non-final*
        draft, or two records sharing a `submitted_at_step`. A test that cannot
        construct those cannot prove the evaluator rejects them.

        Omitted fields default to what a real submission would carry -- the
        active task's `task_id` and `seed`, and the next step number -- so the
        ordinary "pretend the agent submitted this" call stays a one-liner.

        Whatever is passed is recorded verbatim; this hook never re-attributes
        a record to the active task. `values` are still whitespace-normalized,
        since that is what the app does to every submission it stores.
        """
        record, _layout, _form = self._require_task()
        submission = Submission(
            task_id=record["task_id"] if task_id is None else task_id,
            seed=record["seed"] if seed is None else seed,
            values=normalize_submitted_values(values),
            submitted_at_step=(
                self._next_submission_step() if submitted_at_step is None else submitted_at_step
            ),
            final=final,
        )
        self._submissions.append(submission)
        return submission


def _form_widget(name: Any) -> ui.WidgetId:
    """The form widget called `name`, or `ValueError`. `SUBMIT` is a control,
    not a field, so it is rejected alongside unknown names."""
    try:
        widget = ui.WidgetId(name)
    except ValueError:
        widget = None
    if widget is None or widget is ui.WidgetId.SUBMIT:
        known = sorted(w.value for w in ui.WidgetId if w is not ui.WidgetId.SUBMIT)
        raise ValueError(f"{name!r} is not a form field; expected one of {known}")
    return widget


def _option_index(name: str, value: Any, options: tuple[str, ...]) -> int | None:
    """The index of `value` in `options`, or `None` for the empty choice."""
    if not isinstance(value, str):
        raise TypeError(
            f"{name!r} is a selection field and takes a str, got "
            f"{value!r} of type {type(value).__name__}"
        )
    if value == "":
        return None
    if value not in options:
        raise ValueError(
            f"{value!r} is not an option for {name!r}; expected one of {list(options)}"
        )
    return options.index(value)
