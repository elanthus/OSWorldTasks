"""Widget geometry and interaction semantics for the vendor-onboarding form.

This module is what lets the fake backend (`pixelgym.backends.fake`) turn a
bounded `CLICK` or an allowlisted `KEY` into real form state, and finally into
a submission -- without a browser, a VM, or a network socket. It is the fake's
model of "what a browser would have done", not a second implementation of the
task: the authoritative task values still come from
`pixelgym.tasks.vendor_form.generator`, and submissions are still scored only
by the privileged host-side evaluator.

Two things live here:

- `Layout` -- where every widget is, in pixels. At the 1024x768 design size,
  every control and payment-option rectangle is pinned by an opt-in Chromium
  `getBoundingClientRect()` test. Geometry is scaled linearly for other fake
  backend sizes. The synthetic country popup is intentionally excluded: native
  popup rectangles are not exposed portably, so country selection transfers by
  clicking the select and sending allowlisted type-ahead/Enter keys instead.
- `FormState` -- focus, typed text, selection, and checkbox state, advanced by
  `click()` and `key()`.

Interaction semantics deliberately mirror the task app in Chromium rather than
inventing shortcuts: Tab walks the form's tab order and leaves the form after
Submit, printable keys append to the focused text field, Backspace deletes one
character, arrows move a `<select>` value or a radio group, Space toggles a
focused checkbox, and Enter submits only from a text input, the checkbox, or
the Submit button.

What is deliberately *not* modeled, since no allowlisted action can reach it:
text carets and caret movement (typing always appends -- ArrowLeft/ArrowRight
are inert in a text field), text selection, Shift+Tab (the action space has no
modifiers), and mouse drag.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

# The real app fixes its layout at 1024x768 (`style.css`). Every constant below
# is expressed in that design space and scaled to the backend's actual size.
DESIGN_WIDTH = 1024
DESIGN_HEIGHT = 768


class WidgetId(Enum):
    """Every focusable/clickable control on the form panel.

    Values match `pixelgym.tasks.vendor_form.generator.FIELD_NAMES` where a
    widget corresponds to a field, so a widget maps to a submission key
    directly. `SUBMIT` is the one control that is not a field.
    """

    COMPANY_NAME = "company_name"
    CONTACT_EMAIL = "contact_email"
    CONTACT_PHONE = "contact_phone"
    TAX_ID = "tax_id"
    COUNTRY = "country"
    PAYMENT_TERMS = "payment_terms"
    EXPEDITED_ONBOARDING = "expedited_onboarding"
    SUBMIT = "submit"


TEXT_WIDGETS: tuple[WidgetId, ...] = (
    WidgetId.COMPANY_NAME,
    WidgetId.CONTACT_EMAIL,
    WidgetId.CONTACT_PHONE,
    WidgetId.TAX_ID,
)

TAB_ORDER: tuple[WidgetId, ...] = (
    WidgetId.COMPANY_NAME,
    WidgetId.CONTACT_EMAIL,
    WidgetId.CONTACT_PHONE,
    WidgetId.TAX_ID,
    WidgetId.COUNTRY,
    WidgetId.PAYMENT_TERMS,
    WidgetId.EXPEDITED_ONBOARDING,
    WidgetId.SUBMIT,
)
"""Document tab order, matching the control order in `app/static/index.html`."""

REQUEST_CARD_FIELDS: tuple[str, ...] = (
    "company_name",
    "contact_email",
    "contact_phone",
    "tax_id",
    "country",
    "payment_terms",
    "expedited_onboarding",
)
"""Row order of the read-only request card, matching `index.html`."""

COUNTRY_PLACEHOLDER = "Select a country"
"""Label of the `<select>`'s disabled placeholder option. It is a label only --
the submitted value while it is showing is the empty string, exactly as the
real app's ``<option value="" selected disabled>`` produces."""

INCOMPLETE_SUBMISSION_MESSAGE = "Complete all required fields before submitting."
"""Settled status after the app records a submission missing a required string value.

The browser cannot import this Python constant, so ``app/static/app.js`` carries the same text
with a synchronization comment. Build-time capture imports this value and asserts the rendered
browser state; the fake backend uses it directly.
"""


# -- Geometry ---------------------------------------------------------------

_PAD = 24
_PANEL_WIDTH = DESIGN_WIDTH // 2
_H1_HEIGHT = 19
_H1_MARGIN = 16
_LABEL_HEIGHT = 16
_LABEL_GAP = 4
_CONTROL_HEIGHT = 30
_COUNTRY_HEIGHT = 32
_ROW_MARGIN = 12
_TEXT_ROW_PITCH = _LABEL_HEIGHT + _LABEL_GAP + _CONTROL_HEIGHT + _ROW_MARGIN  # 62
_FIRST_ROW_Y = _PAD + _H1_HEIGHT + _H1_MARGIN  # 59
_FIELD_WIDTH = _PANEL_WIDTH - 2 * _PAD  # 464

# Fake-only visualization dimensions. Native `<select>` popup rectangles are
# deliberately outside the browser-equivalence contract.
_OPTION_HEIGHT = 26  # one row of the open country dropdown
_RADIO_WIDTH = 68
_RADIO_GAP = 12
_RADIO_HEIGHT = 19
_RADIO_GROUP_HEIGHT = 23
_CHECKBOX_ROW_WIDTH = 179
_CHECKBOX_ROW_HEIGHT = 19
_CHECKBOX_FIELD_HEIGHT = 23
_SUBMIT_TOP_MARGIN = 8
_SUBMIT_WIDTH = 84
_SUBMIT_HEIGHT = 34
_STATUS_HEIGHT = 16


@dataclass(frozen=True)
class Rect:
    """A pixel rectangle. `contains` uses half-open bounds, so a `width`-wide
    rect at `x` covers `x .. x + width - 1` -- the same convention as the
    action space's `[0, width)` coordinates."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom


class Layout:
    """Widget geometry for a `width` x `height` screen.

    Control and payment-option rectangles at the 1024x768 design size are
    browser-derived CSS-pixel hit regions, with a top-left origin. The opt-in
    browser integration test is the build-time instrumentation that pins them;
    no DOM geometry is reachable from an evaluation backend or observation.

    Synthetic country-option rectangles exist only so the fake screenshot can
    render a deterministic open-select approximation. They are not equivalent
    to native popup geometry and must not be used by transferable trajectories.

    Rectangles are scaled linearly for the small screens unit tests use.
    Scaling rounds independently per rectangle and clamps extents to at least
    one pixel; at very small sizes controls become tiny and may abut, which is
    fine -- those sizes exercise space and validation logic, not click fidelity.
    """

    def __init__(
        self,
        width: int,
        height: int,
        *,
        country_option_count: int,
        payment_option_count: int,
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"width and height must be positive, got {width}x{height}")
        if country_option_count <= 0 or payment_option_count <= 0:
            raise ValueError("option counts must be positive")

        self.width = width
        self.height = height
        self.scale_x = width / DESIGN_WIDTH
        self.scale_y = height / DESIGN_HEIGHT
        self.scale = min(self.scale_x, self.scale_y)

        self.request_panel = self._rect(0, 0, _PANEL_WIDTH, DESIGN_HEIGHT)
        self.form_panel = self._rect(_PANEL_WIDTH, 0, _PANEL_WIDTH, DESIGN_HEIGHT)
        self.request_heading = self._rect(_PAD, _PAD, _FIELD_WIDTH, _H1_HEIGHT)
        self.form_heading = self._rect(_PANEL_WIDTH + _PAD, _PAD, _FIELD_WIDTH, _H1_HEIGHT)

        # Read-only request card: one label + value box per field, left panel.
        self.request_labels: dict[str, Rect] = {}
        self.request_values: dict[str, Rect] = {}
        for index, field in enumerate(REQUEST_CARD_FIELDS):
            top = _FIRST_ROW_Y + index * _TEXT_ROW_PITCH
            self.request_labels[field] = self._rect(_PAD, top, _FIELD_WIDTH, _LABEL_HEIGHT)
            self.request_values[field] = self._rect(
                _PAD, top + _LABEL_HEIGHT + _LABEL_GAP, _FIELD_WIDTH, _CONTROL_HEIGHT
            )

        # Editable form, right panel. Rows 0-3 are text inputs, row 4 the
        # country select, row 5 the payment-terms radio group, then the
        # checkbox row, the Submit button, and the status line.
        form_x = _PANEL_WIDTH + _PAD
        self.labels: dict[WidgetId, Rect] = {}
        self.controls: dict[WidgetId, Rect] = {}

        for index, widget in enumerate((*TEXT_WIDGETS, WidgetId.COUNTRY)):
            top = _FIRST_ROW_Y + index * _TEXT_ROW_PITCH
            self.labels[widget] = self._rect(form_x, top, _FIELD_WIDTH, _LABEL_HEIGHT)
            control_height = _COUNTRY_HEIGHT if widget is WidgetId.COUNTRY else _CONTROL_HEIGHT
            self.controls[widget] = self._rect(
                form_x, top + _LABEL_HEIGHT + _LABEL_GAP, _FIELD_WIDTH, control_height
            )

        country_top = _FIRST_ROW_Y + 4 * _TEXT_ROW_PITCH + _LABEL_HEIGHT + _LABEL_GAP
        country_bottom = country_top + _COUNTRY_HEIGHT
        # Deterministic fake-only popup rendering; no browser geometry claim.
        self.country_options: tuple[Rect, ...] = tuple(
            self._rect(
                form_x,
                country_bottom + index * _OPTION_HEIGHT,
                _FIELD_WIDTH,
                _OPTION_HEIGHT,
            )
            for index in range(country_option_count)
        )
        self.country_popup = self._rect(
            form_x, country_bottom, _FIELD_WIDTH, _OPTION_HEIGHT * country_option_count
        )

        payment_label_top = country_bottom + _ROW_MARGIN
        payment_top = payment_label_top + _LABEL_HEIGHT + _LABEL_GAP
        self.labels[WidgetId.PAYMENT_TERMS] = self._rect(
            form_x, payment_label_top, _FIELD_WIDTH, _LABEL_HEIGHT
        )
        self.payment_options: tuple[Rect, ...] = tuple(
            self._rect(
                form_x + index * (_RADIO_WIDTH + _RADIO_GAP),
                payment_top,
                _RADIO_WIDTH,
                _RADIO_HEIGHT,
            )
            for index in range(payment_option_count)
        )
        self.controls[WidgetId.PAYMENT_TERMS] = self._rect(
            form_x,
            payment_top,
            _FIELD_WIDTH,
            _RADIO_GROUP_HEIGHT,
        )

        checkbox_top = payment_top + _RADIO_GROUP_HEIGHT + _ROW_MARGIN
        self.controls[WidgetId.EXPEDITED_ONBOARDING] = self._rect(
            form_x, checkbox_top, _CHECKBOX_ROW_WIDTH, _CHECKBOX_ROW_HEIGHT
        )

        submit_top = (
            checkbox_top + _CHECKBOX_FIELD_HEIGHT + _ROW_MARGIN + _SUBMIT_TOP_MARGIN
        )
        self.controls[WidgetId.SUBMIT] = self._rect(
            form_x, submit_top, _SUBMIT_WIDTH, _SUBMIT_HEIGHT
        )
        self.status = self._rect(
            form_x, submit_top + _SUBMIT_HEIGHT + _ROW_MARGIN, _FIELD_WIDTH, _STATUS_HEIGHT
        )

    def _rect(self, x: int, y: int, width: int, height: int) -> Rect:
        return Rect(
            x=round(x * self.scale_x),
            y=round(y * self.scale_y),
            width=max(1, round(width * self.scale_x)),
            height=max(1, round(height * self.scale_y)),
        )

    def hit_test(self, x: int, y: int, *, country_open: bool) -> tuple[WidgetId, int | None] | None:
        """Which control (and which of its options) a click at `(x, y)` lands on.

        Returns `None` for a click on empty space. The open-country branch is a
        fake-only deterministic popup approximation, not native browser popup
        geometry; transferable interactions use the select rectangle and keys.
        """
        if country_open:
            for index, rect in enumerate(self.country_options):
                if rect.contains(x, y):
                    return WidgetId.COUNTRY, index
            return None

        for index, rect in enumerate(self.payment_options):
            if rect.contains(x, y):
                return WidgetId.PAYMENT_TERMS, index

        for widget, rect in self.controls.items():
            if widget is WidgetId.PAYMENT_TERMS:
                continue  # already resolved to a specific radio above
            if rect.contains(x, y):
                return widget, None

        return None


# -- Interaction state ------------------------------------------------------


class FormState:
    """Focus, typed text, and selections for one episode's form.

    Mutated only through `click` and `key`, both of which return whether the
    interaction requested a form submission. Recording that submission is the
    backend's job, not this class's -- the UI does not decide reward, and does
    not know whether what it holds is correct.
    """

    def __init__(
        self,
        *,
        layout: Layout,
        country_options: Sequence[str],
        payment_options: Sequence[str],
    ) -> None:
        self.layout = layout
        self.country_options = tuple(country_options)
        self.payment_options = tuple(payment_options)

        self.text: dict[WidgetId, str] = {widget: "" for widget in TEXT_WIDGETS}
        self.country_index: int | None = None
        self.payment_index: int | None = None
        self.expedited: bool = False
        self.focus: WidgetId | None = None
        self.country_open: bool = False
        self.status: str = ""

    # -- Reads -------------------------------------------------------------

    @property
    def country_value(self) -> str:
        """The submitted country value: `""` while the disabled placeholder
        is showing, mirroring the real `<select>`'s empty option value."""
        if self.country_index is None:
            return ""
        return self.country_options[self.country_index]

    @property
    def payment_terms_value(self) -> str:
        if self.payment_index is None:
            return ""
        return self.payment_options[self.payment_index]

    def values(self) -> dict[str, Any]:
        """The form's current values, in the shape `POST /api/submit` accepts
        (see `SubmitRequest` in `app/server.py`). Unfilled text fields and
        unmade selections are empty strings, as the real form reads them."""
        return {
            "company_name": self.text[WidgetId.COMPANY_NAME],
            "contact_email": self.text[WidgetId.CONTACT_EMAIL],
            "contact_phone": self.text[WidgetId.CONTACT_PHONE],
            "tax_id": self.text[WidgetId.TAX_ID],
            "country": self.country_value,
            "payment_terms": self.payment_terms_value,
            "expedited_onboarding": self.expedited,
        }

    def is_complete(self) -> bool:
        """Whether every browser-required string or selection has a non-empty value.

        The checkbox is deliberately excluded: unchecked ``False`` is a complete value. Like the
        browser's truthiness check, whitespace-only text is considered present here and is only
        stripped when the submission event is normalized for privileged storage.
        """
        values = self.values()
        return all(
            values[name]
            for name in (
                "company_name",
                "contact_email",
                "contact_phone",
                "tax_id",
                "country",
                "payment_terms",
            )
        )

    # -- Events ------------------------------------------------------------

    def click(self, x: int, y: int) -> bool:
        """Apply a click at `(x, y)`. Returns True if it submitted the form.

        Coordinates are already validated against the action space by
        `PixelGuiEnv`; this method does not re-check or clip them.
        """
        hit = self.layout.hit_test(x, y, country_open=self.country_open)

        if self.country_open:
            # Any click closes the popup. One that landed on an option also
            # commits it; one that missed just dismisses.
            self.country_open = False
            if hit is not None:
                _widget, index = hit
                self.focus = WidgetId.COUNTRY
                self.country_index = index
            return False

        if hit is None:
            self.focus = None  # clicking the page background blurs
            return False

        widget, index = hit
        self.focus = widget

        if widget in TEXT_WIDGETS:
            return False
        if widget is WidgetId.COUNTRY:
            self.country_open = True
            return False
        if widget is WidgetId.PAYMENT_TERMS:
            self.payment_index = index
            return False
        if widget is WidgetId.EXPEDITED_ONBOARDING:
            self.expedited = not self.expedited
            return False
        if widget is WidgetId.SUBMIT:
            return True
        raise AssertionError(f"unhandled widget {widget!r}")  # pragma: no cover

    def key(self, key: str) -> bool:
        """Apply one keystroke. Returns True if it submitted the form.

        `key` is a literal member of `pixelgym.actions.KEY_ALLOWLIST`, already
        resolved from the action's allowlist index by `PixelGuiEnv`.
        """
        if key == "Tab":
            self.country_open = False
            self._advance_focus()
            return False

        if key == "Enter":
            if self.country_open:
                self.country_open = False  # commit the shown option, close
                return False
            return self.focus in (
                *TEXT_WIDGETS,
                WidgetId.EXPEDITED_ONBOARDING,
                WidgetId.SUBMIT,
            )

        if key == "Backspace":
            if self.focus in TEXT_WIDGETS:
                self.text[self.focus] = self.text[self.focus][:-1]
            return False

        if key.startswith("Arrow"):
            self._arrow(key)
            return False

        return self._printable(key)

    def _printable(self, key: str) -> bool:
        if self.focus in TEXT_WIDGETS:
            self.text[self.focus] += key
            return False
        if self.focus is WidgetId.COUNTRY and key != " ":
            # Chromium's closed native select supports prefix type-ahead even
            # when its disabled placeholder is showing. The task's countries
            # have unique initial letters, so this is the portable selection
            # path used by trajectories; native popup rows have no geometry
            # contract.
            folded = key.casefold()
            for index, option in enumerate(self.country_options):
                if option.casefold().startswith(folded):
                    self.country_index = index
                    break
            return False
        if key != " ":
            # Other non-space printable keys on non-text controls do nothing.
            return False
        # Space activates the focused control, as it does in a browser.
        if self.focus is WidgetId.EXPEDITED_ONBOARDING:
            self.expedited = not self.expedited
        elif self.focus is WidgetId.COUNTRY:
            self.country_open = not self.country_open
        elif self.focus is WidgetId.SUBMIT:
            return True
        return False

    def _arrow(self, key: str) -> None:
        if self.focus is WidgetId.COUNTRY and key in ("ArrowDown", "ArrowUp"):
            # A `<select>` clamps at its ends rather than wrapping, and cannot
            # move back onto the disabled placeholder once it has left it.
            last = len(self.country_options) - 1
            if key == "ArrowDown":
                self.country_index = (
                    0 if self.country_index is None else min(self.country_index + 1, last)
                )
            elif self.country_index is not None:
                self.country_index = max(self.country_index - 1, 0)
            return

        if self.focus is WidgetId.PAYMENT_TERMS:
            # A radio group wraps in both directions.
            count = len(self.payment_options)
            if key in ("ArrowDown", "ArrowRight"):
                self.payment_index = (
                    0 if self.payment_index is None else (self.payment_index + 1) % count
                )
            else:
                self.payment_index = (
                    count - 1 if self.payment_index is None else (self.payment_index - 1) % count
                )

    def _advance_focus(self) -> None:
        if self.focus is None:
            self.focus = TAB_ORDER[0]
            return
        if self.focus is WidgetId.SUBMIT:
            self.focus = None
            return
        self.focus = TAB_ORDER[TAB_ORDER.index(self.focus) + 1]


def layout_for(task_record: Mapping[str, Any], width: int, height: int) -> Layout:
    """Build the `Layout` matching a generated task record's option lists."""
    options = task_record["options"]
    return Layout(
        width,
        height,
        country_option_count=len(options["country"]),
        payment_option_count=len(options["payment_terms"]),
    )
