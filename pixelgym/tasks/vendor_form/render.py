"""Deterministic rendering of the vendor form to an RGB frame (D1.6).

The fake backend has no browser, so it draws the same screen itself. Colors,
panel split, and control geometry follow the real app's stylesheet
(`app/static/style.css`), and the glyphs come from the same DejaVu font files
the stylesheet bundles -- so the fake frame is a recognizable stand-in for a
real screenshot rather than an unrelated picture.

Determinism (AGENTS.md invariant 10) comes from what is *absent* as much as
from what is drawn: no clock, no animation, no transition, and no text caret --
focus is shown as a static accent border, so a frame captured while a text
field is focused is identical every time. The only inputs are the task record
and the form state.

Scope note: this is bit-for-bit reproducible for a given Pillow build, which is
what the fake path needs. It is *not* evidence about the real browser path --
Day 2's visual-determinism work measures that separately, and neither number
may be reported as the other (AGENTS.md section 7).
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pixelgym.tasks.vendor_form.ui import (
    COUNTRY_PLACEHOLDER,
    REQUEST_CARD_FIELDS,
    TEXT_WIDGETS,
    FormState,
    Layout,
    Rect,
    WidgetId,
)

FONT_DIR = Path(__file__).parent / "app" / "static" / "fonts"
REGULAR_FONT = FONT_DIR / "DejaVuSans.ttf"
BOLD_FONT = FONT_DIR / "DejaVuSans-Bold.ttf"

# Straight from style.css, so the fake frame and the real page agree on color.
PAGE_BG = (244, 245, 247)  # #f4f5f7
REQUEST_PANEL_BG = (238, 241, 246)  # #eef1f6
FORM_PANEL_BG = (255, 255, 255)  # #ffffff
SOFT_BORDER = (199, 205, 214)  # #c7cdd6
CONTROL_BORDER = (148, 160, 179)  # #94a0b3
TEXT_COLOR = (28, 30, 33)  # #1c1e21
BUTTON_BG = (60, 140, 95)  # #3c8c5f
BUTTON_BORDER = (47, 111, 79)  # #2f6f4f
BUTTON_TEXT = (255, 255, 255)
# Not in style.css: focus and dropdown-highlight styling exist only in the
# fake, which has to make focus visible without a caret.
FOCUS_BORDER = (47, 111, 159)
PLACEHOLDER_TEXT = (130, 138, 150)
HIGHLIGHT_BG = (222, 233, 245)

_BASE_FONT_SIZE = 14
_HEADING_FONT_SIZE = 16
_TEXT_INSET = 8  # design px; matches the 6px/8px control padding in style.css
_MAX_RENDERED_CHARS = 200  # cap measurement work on absurdly long typed strings


@lru_cache(maxsize=32)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _scaled(value: int, scale: float) -> int:
    return max(1, round(value * scale))


def _fit(text: str, font: ImageFont.FreeTypeFont, max_width: int, *, keep_tail: bool) -> str:
    """Trim `text` until it fits `max_width` pixels.

    `keep_tail` keeps the end of the string, which is what a text input shows
    while you type past its right edge; labels and static values keep the head.
    """
    if not text:
        return text
    text = text[-_MAX_RENDERED_CHARS:] if keep_tail else text[:_MAX_RENDERED_CHARS]
    while text and font.getlength(text) > max_width:
        text = text[1:] if keep_tail else text[:-1]
    return text


class _Painter:
    """Drawing helpers bound to one image, layout, and font scale."""

    def __init__(self, image: Image.Image, layout: Layout) -> None:
        self.draw = ImageDraw.Draw(image)
        self.layout = layout
        self.scale = layout.scale
        self.inset = _scaled(_TEXT_INSET, layout.scale_x)
        self.body = _font(str(REGULAR_FONT), _scaled(_BASE_FONT_SIZE, self.scale))
        self.bold = _font(str(BOLD_FONT), _scaled(_BASE_FONT_SIZE, self.scale))
        self.heading = _font(str(BOLD_FONT), _scaled(_HEADING_FONT_SIZE, self.scale))

    def box(
        self,
        rect: Rect,
        *,
        fill: tuple[int, int, int] | None,
        outline: tuple[int, int, int] | None,
        focused: bool = False,
    ) -> None:
        self.draw.rectangle(
            (rect.x, rect.y, rect.right - 1, rect.bottom - 1),
            fill=fill,
            outline=FOCUS_BORDER if focused else outline,
            width=_scaled(2, self.scale) if focused else 1,
        )

    def text_in(
        self,
        rect: Rect,
        text: str,
        *,
        font: ImageFont.FreeTypeFont,
        color: tuple[int, int, int] = TEXT_COLOR,
        keep_tail: bool = False,
        centered: bool = False,
    ) -> None:
        available = max(1, rect.width - 2 * self.inset)
        shown = _fit(text, font, available, keep_tail=keep_tail)
        if not shown:
            return
        _, center_y = rect.center
        if centered:
            self.draw.text(rect.center, shown, font=font, fill=color, anchor="mm")
        else:
            self.draw.text(
                (rect.x + self.inset, center_y), shown, font=font, fill=color, anchor="lm"
            )

    def label(self, rect: Rect, text: str) -> None:
        self.text_in(rect, text, font=self.bold)


def render(task_record: Mapping[str, Any], state: FormState, layout: Layout) -> np.ndarray:
    """Draw the current screen as an `(height, width, 3)` uint8 RGB array."""
    image = Image.new("RGB", (layout.width, layout.height), PAGE_BG)
    painter = _Painter(image, layout)

    painter.box(layout.request_panel, fill=REQUEST_PANEL_BG, outline=SOFT_BORDER)
    painter.box(layout.form_panel, fill=FORM_PANEL_BG, outline=None)
    painter.text_in(layout.request_heading, "New Vendor Request", font=painter.heading)
    painter.text_in(layout.form_heading, "Enter Vendor Details", font=painter.heading)

    _render_request_card(painter, task_record["fields"], layout)
    _render_form(painter, state, layout)

    return np.asarray(image, dtype=np.uint8)


def _render_request_card(painter: _Painter, fields: Mapping[str, Any], layout: Layout) -> None:
    for field in REQUEST_CARD_FIELDS:
        painter.label(layout.request_labels[field], _LABELS[field])
        value_rect = layout.request_values[field]
        painter.box(value_rect, fill=FORM_PANEL_BG, outline=SOFT_BORDER)
        value = fields[field]
        # The card shows the checkbox answer the way a person would read it;
        # the submitted value is still a bool.
        shown = ("Yes" if value else "No") if isinstance(value, bool) else str(value)
        painter.text_in(value_rect, shown, font=painter.body)


def _render_form(painter: _Painter, state: FormState, layout: Layout) -> None:
    for widget in TEXT_WIDGETS:
        painter.label(layout.labels[widget], _LABELS[widget.value])
        rect = layout.controls[widget]
        painter.box(rect, fill=FORM_PANEL_BG, outline=CONTROL_BORDER, focused=state.focus is widget)
        painter.text_in(rect, state.text[widget], font=painter.body, keep_tail=True)

    _render_country(painter, state, layout)
    _render_payment_terms(painter, state, layout)
    _render_checkbox(painter, state, layout)

    submit = layout.controls[WidgetId.SUBMIT]
    painter.box(
        submit,
        fill=BUTTON_BG,
        outline=BUTTON_BORDER,
        focused=state.focus is WidgetId.SUBMIT,
    )
    painter.text_in(submit, "Submit", font=painter.body, color=BUTTON_TEXT, centered=True)

    if state.status:
        painter.text_in(layout.status, state.status, font=painter.body)

    # Last, so the open dropdown occludes the controls it floats over -- the
    # same z-order a native `<select>` popup has, and the one `Layout.hit_test`
    # assumes when it gives the popup priority for clicks.
    if state.country_open:
        _render_country_popup(painter, state, layout)


def _render_country(painter: _Painter, state: FormState, layout: Layout) -> None:
    painter.label(layout.labels[WidgetId.COUNTRY], _LABELS["country"])
    rect = layout.controls[WidgetId.COUNTRY]
    painter.box(
        rect,
        fill=FORM_PANEL_BG,
        outline=CONTROL_BORDER,
        focused=state.focus is WidgetId.COUNTRY,
    )
    if state.country_index is None:
        painter.text_in(rect, COUNTRY_PLACEHOLDER, font=painter.body, color=PLACEHOLDER_TEXT)
    else:
        painter.text_in(rect, state.country_value, font=painter.body)

    # Dropdown arrow, drawn rather than taken from a font glyph.
    size = _scaled(6, painter.scale)
    tip_x = rect.right - painter.inset
    _, center_y = rect.center
    painter.draw.polygon(
        [
            (tip_x - 2 * size, center_y - size),
            (tip_x, center_y - size),
            (tip_x - size, center_y + size),
        ],
        fill=CONTROL_BORDER,
    )


def _render_country_popup(painter: _Painter, state: FormState, layout: Layout) -> None:
    painter.box(layout.country_popup, fill=FORM_PANEL_BG, outline=CONTROL_BORDER)
    for index, option_rect in enumerate(layout.country_options):
        if index == state.country_index:
            painter.box(option_rect, fill=HIGHLIGHT_BG, outline=None)
        painter.text_in(option_rect, state.country_options[index], font=painter.body)


def _render_payment_terms(painter: _Painter, state: FormState, layout: Layout) -> None:
    painter.label(layout.labels[WidgetId.PAYMENT_TERMS], _LABELS["payment_terms"])
    focused = state.focus is WidgetId.PAYMENT_TERMS
    radius = _scaled(6, painter.scale)
    for index, rect in enumerate(layout.payment_options):
        center_x = rect.x + painter.inset + radius
        _, center_y = rect.center
        circle = (
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
        )
        painter.draw.ellipse(
            circle,
            fill=FORM_PANEL_BG,
            outline=FOCUS_BORDER if focused else CONTROL_BORDER,
            width=_scaled(2, painter.scale) if focused else 1,
        )
        if index == state.payment_index:
            inner = max(1, radius // 2)
            painter.draw.ellipse(
                (center_x - inner, center_y - inner, center_x + inner, center_y + inner),
                fill=TEXT_COLOR,
            )
        text_rect = Rect(
            x=center_x + radius,
            y=rect.y,
            width=max(1, rect.right - (center_x + radius)),
            height=rect.height,
        )
        painter.text_in(text_rect, state.payment_options[index], font=painter.body)


def _render_checkbox(painter: _Painter, state: FormState, layout: Layout) -> None:
    rect = layout.controls[WidgetId.EXPEDITED_ONBOARDING]
    side = min(_scaled(14, painter.scale), rect.height)
    _, center_y = rect.center
    top = center_y - side // 2
    box = Rect(x=rect.x, y=top, width=side, height=side)
    painter.box(
        box,
        fill=FORM_PANEL_BG,
        outline=CONTROL_BORDER,
        focused=state.focus is WidgetId.EXPEDITED_ONBOARDING,
    )
    if state.expedited:
        painter.draw.line(
            [
                (box.x + side // 4, box.y + side // 2),
                (box.x + side // 2, box.bottom - side // 4),
                (box.right - side // 5, box.y + side // 5),
            ],
            fill=TEXT_COLOR,
            width=max(1, side // 7),
        )
    text_rect = Rect(
        x=box.right,
        y=rect.y,
        width=max(1, rect.right - box.right),
        height=rect.height,
    )
    painter.text_in(text_rect, _LABELS["expedited_onboarding"], font=painter.body)


_LABELS: dict[str, str] = {
    "company_name": "Company name",
    "contact_email": "Contact email",
    "contact_phone": "Contact phone",
    "tax_id": "Tax ID",
    "country": "Country",
    "payment_terms": "Payment terms",
    "expedited_onboarding": "Expedited onboarding",
}
"""Visible labels, matching `app/static/index.html`."""
