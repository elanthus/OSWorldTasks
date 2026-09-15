"""Versioned static focus cues; task semantics and deferred feedback are unchanged."""

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pixelgym.backends.base import Frame
from pixelgym.grounding.v5.backend import _REGULAR_FONT
from pixelgym.grounding.v5.memory_backend import MemoryBackend


class FocusMemoryBackend(MemoryBackend):
    backend_identity = "pixelgym-v5-memory-focus-backend-v3"

    def _render(self) -> Frame:
        frame = super()._render()
        text_input = next(
            (control for control in self.visible_controls() if control.control_id == "text_input"),
            None,
        )
        if text_input is None or not self._focused:
            return frame
        image = Image.fromarray(frame)
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(_REGULAR_FONT), 20)
        x0, y0, x1, y1 = text_input.bbox
        draw.rounded_rectangle(
            text_input.bbox, radius=7, fill="#e3efff", outline="#005fcc", width=4
        )
        label = self._text_value or "Ready to type"
        draw.text((x0 + 18, y0 + 17), label, font=font, fill="#17253d")
        # A static caret and status depend only on visible input state. They do
        # not consult the expected code or reveal whether entered text is right.
        caret = x0 + 18 + int(draw.textlength(self._text_value, font=font))
        if self._text_value:
            draw.line((caret + 2, y0 + 14, caret + 2, y1 - 14), fill="#005fcc", width=2)
        draw.text(
            (x1 - 144, y0 + 20),
            "INPUT ACTIVE",
            font=ImageFont.truetype(str(_REGULAR_FONT), 16),
            fill="#005fcc",
        )
        return np.asarray(image, dtype=np.uint8).copy()
