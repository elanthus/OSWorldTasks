"""Deterministic rendering of the vendor form to an RGB frame.

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
The OSWorld validation path measures browser visual determinism separately,
and neither number may be reported as the other (AGENTS.md section 7).
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
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
# Not in style.css: the fake makes focus visible without a caret.
FOCUS_BORDER = (47, 111, 159)
PLACEHOLDER_TEXT = (130, 138, 150)

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


def render_static(task_record: Mapping[str, Any], layout: Layout) -> Image.Image | None:
    """Draw every pixel that does not depend on form state, once per task, if that is exact.

    That is the page and panel backgrounds, both headings, the whole read-only request card, the
    form labels, and the fixed captions beside the radios and the checkbox. When none of those
    rectangles comes within a pixel of a dynamic widget, drawing them first and the widgets
    afterwards gives the same pixels as the single-pass order, so `render` can start each frame
    from a copy of this image. At very small fake-backend sizes the scaled rectangles collide,
    the order matters, and this returns `None`; `render` then draws every frame in one pass.
    Treat the returned image as read-only.
    """
    if not _layers_are_disjoint(layout):
        return None
    image = Image.new("RGB", (layout.width, layout.height), PAGE_BG)
    painter = _Painter(image, layout)
    _render_page(painter, task_record, layout)
    for widget in (*TEXT_WIDGETS, WidgetId.COUNTRY, WidgetId.PAYMENT_TERMS):
        _render_label(painter, layout, widget)
    for index in range(len(layout.payment_options)):
        _render_radio_caption(painter, task_record, layout, index)
    _render_checkbox_caption(painter, layout)
    return image


def render(
    task_record: Mapping[str, Any],
    state: FormState,
    layout: Layout,
    *,
    static: Image.Image | None = None,
) -> npt.NDArray[np.uint8]:
    """Draw the current screen as an `(height, width, 3)` uint8 RGB array.

    `static` is `render_static(task_record, layout)`; callers that render many frames of one
    task compute it once and pass it in. With a static layer, each dynamic widget is redrawn from
    an opaque fill over its own rectangle on a copy of it. Without one, the whole screen is drawn
    in a single pass. Both paths are pinned byte-for-byte to a frozen single-pass reference by
    `tests/unit/test_render_static.py`.
    """
    base = static if static is not None else render_static(task_record, layout)
    if base is None:
        image = Image.new("RGB", (layout.width, layout.height), PAGE_BG)
        _render_single_pass(_Painter(image, layout), task_record, state, layout)
    else:
        image = base.copy()
        _render_dynamic(_Painter(image, layout), state, layout)
    return np.asarray(image, dtype=np.uint8)


def _render_single_pass(
    painter: _Painter, task_record: Mapping[str, Any], state: FormState, layout: Layout
) -> None:
    """Static and dynamic pieces interleaved in the original drawing order."""
    _render_page(painter, task_record, layout)
    for widget in TEXT_WIDGETS:
        _render_label(painter, layout, widget)
        _render_text_control(painter, state, layout, widget)
    _render_label(painter, layout, WidgetId.COUNTRY)
    _render_country(painter, state, layout)
    _render_label(painter, layout, WidgetId.PAYMENT_TERMS)
    for index in range(len(layout.payment_options)):
        _render_radio(painter, state, layout, index)
        _render_radio_caption(painter, task_record, layout, index)
    _render_checkbox(painter, state, layout)
    _render_checkbox_caption(painter, layout)
    _render_submit(painter, state, layout)
    _render_status(painter, state, layout)


def _render_dynamic(painter: _Painter, state: FormState, layout: Layout) -> None:
    for widget in TEXT_WIDGETS:
        _render_text_control(painter, state, layout, widget)
    _render_country(painter, state, layout)
    for index in range(len(layout.payment_options)):
        _render_radio(painter, state, layout, index)
    _render_checkbox(painter, state, layout)
    _render_submit(painter, state, layout)
    _render_status(painter, state, layout)


def _layers_are_disjoint(layout: Layout) -> bool:
    """Whether every static draw rectangle stays at least a pixel clear of every dynamic one."""
    probe = _Painter(Image.new("RGB", (1, 1)), layout)
    static = [
        layout.request_panel,
        layout.request_heading,
        layout.form_heading,
        *layout.request_labels.values(),
        *layout.request_values.values(),
        *(layout.labels[w] for w in (*TEXT_WIDGETS, WidgetId.COUNTRY, WidgetId.PAYMENT_TERMS)),
        # `text_in` draws captions from `rect.x + inset`, so the glyphs start one inset to the
        # right of a caption rectangle, which itself begins at the control's right edge.
        *(_inset(probe, _radio_caption_rect(probe, rect)) for rect in layout.payment_options),
        _inset(probe, _checkbox_caption_rect(probe, layout)),
    ]
    dynamic = [
        *(layout.controls[w] for w in (*TEXT_WIDGETS, WidgetId.COUNTRY, WidgetId.SUBMIT)),
        *(_radio_circle_rect(probe, rect) for rect in layout.payment_options),
        _checkbox_box(probe, layout),
        layout.status,
    ]
    return not any(_near(a, b) for a in static for b in dynamic)


def _inset(painter: _Painter, rect: Rect) -> Rect:
    return Rect(
        x=rect.x + painter.inset,
        y=rect.y,
        width=max(1, rect.width - painter.inset),
        height=rect.height,
    )


def _near(a: Rect, b: Rect) -> bool:
    return a.x - 1 < b.right and b.x - 1 < a.right and a.y - 1 < b.bottom and b.y - 1 < a.bottom


def _render_page(painter: _Painter, task_record: Mapping[str, Any], layout: Layout) -> None:
    painter.box(layout.request_panel, fill=REQUEST_PANEL_BG, outline=SOFT_BORDER)
    painter.box(layout.form_panel, fill=FORM_PANEL_BG, outline=None)
    painter.text_in(layout.request_heading, "New Vendor Request", font=painter.heading)
    painter.text_in(layout.form_heading, "Enter Vendor Details", font=painter.heading)
    fields = task_record["fields"]
    for field in REQUEST_CARD_FIELDS:
        painter.label(layout.request_labels[field], _LABELS[field])
        value_rect = layout.request_values[field]
        painter.box(value_rect, fill=FORM_PANEL_BG, outline=SOFT_BORDER)
        value = fields[field]
        # The card shows the checkbox answer the way a person would read it;
        # the submitted value is still a bool.
        shown = ("Yes" if value else "No") if isinstance(value, bool) else str(value)
        painter.text_in(value_rect, shown, font=painter.body)


def _render_label(painter: _Painter, layout: Layout, widget: WidgetId) -> None:
    painter.label(layout.labels[widget], _LABELS[widget.value])


def _render_text_control(
    painter: _Painter, state: FormState, layout: Layout, widget: WidgetId
) -> None:
    rect = layout.controls[widget]
    painter.box(rect, fill=FORM_PANEL_BG, outline=CONTROL_BORDER, focused=state.focus is widget)
    painter.text_in(rect, state.text[widget], font=painter.body, keep_tail=True)


def _render_country(painter: _Painter, state: FormState, layout: Layout) -> None:
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


def _radio_geometry(painter: _Painter, rect: Rect) -> tuple[int, int, int]:
    radius = _scaled(6, painter.scale)
    _, center_y = rect.center
    return rect.x + painter.inset + radius, center_y, radius


def _radio_circle_rect(painter: _Painter, rect: Rect) -> Rect:
    center_x, center_y, radius = _radio_geometry(painter, rect)
    return Rect(
        x=center_x - radius, y=center_y - radius, width=2 * radius + 1, height=2 * radius + 1
    )


def _radio_caption_rect(painter: _Painter, rect: Rect) -> Rect:
    center_x, _center_y, radius = _radio_geometry(painter, rect)
    return Rect(
        x=center_x + radius,
        y=rect.y,
        width=max(1, rect.right - (center_x + radius)),
        height=rect.height,
    )


def _render_radio(painter: _Painter, state: FormState, layout: Layout, index: int) -> None:
    # Exactly one radio holds keyboard focus, as in the browser; it can be unchecked.
    focused = state.focus is WidgetId.PAYMENT_TERMS and index == state.payment_focus_index
    center_x, center_y, radius = _radio_geometry(painter, layout.payment_options[index])
    painter.draw.ellipse(
        (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
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


def _render_radio_caption(
    painter: _Painter, task_record: Mapping[str, Any], layout: Layout, index: int
) -> None:
    painter.text_in(
        _radio_caption_rect(painter, layout.payment_options[index]),
        task_record["options"]["payment_terms"][index],
        font=painter.body,
    )


def _checkbox_box(painter: _Painter, layout: Layout) -> Rect:
    rect = layout.controls[WidgetId.EXPEDITED_ONBOARDING]
    side = min(_scaled(14, painter.scale), rect.height)
    _, center_y = rect.center
    return Rect(x=rect.x, y=center_y - side // 2, width=side, height=side)


def _checkbox_caption_rect(painter: _Painter, layout: Layout) -> Rect:
    rect = layout.controls[WidgetId.EXPEDITED_ONBOARDING]
    box = _checkbox_box(painter, layout)
    return Rect(x=box.right, y=rect.y, width=max(1, rect.right - box.right), height=rect.height)


def _render_checkbox(painter: _Painter, state: FormState, layout: Layout) -> None:
    box = _checkbox_box(painter, layout)
    side = box.width
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


def _render_checkbox_caption(painter: _Painter, layout: Layout) -> None:
    painter.text_in(
        _checkbox_caption_rect(painter, layout), _LABELS["expedited_onboarding"], font=painter.body
    )


def _render_submit(painter: _Painter, state: FormState, layout: Layout) -> None:
    submit = layout.controls[WidgetId.SUBMIT]
    painter.box(
        submit,
        fill=BUTTON_BG,
        outline=BUTTON_BORDER,
        focused=state.focus is WidgetId.SUBMIT,
    )
    painter.text_in(submit, "Submit", font=painter.body, color=BUTTON_TEXT, centered=True)


def _render_status(painter: _Painter, state: FormState, layout: Layout) -> None:
    if state.status:
        painter.text_in(layout.status, state.status, font=painter.body)


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
