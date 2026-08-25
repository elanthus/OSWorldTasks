"""Versioned, strict coordinate adapters used in v5 policy identities."""

from __future__ import annotations

from dataclasses import dataclass

from pixelgym.grounding.v5.contracts import SCREEN_HEIGHT, SCREEN_WIDTH, content_digest


@dataclass(frozen=True)
class CoordinateAdapter:
    name: str
    input_width: int
    input_height: int
    output_width: int = SCREEN_WIDTH
    output_height: int = SCREEN_HEIGHT

    def __post_init__(self) -> None:
        if min(self.input_width, self.input_height, self.output_width, self.output_height) <= 0:
            raise ValueError("coordinate dimensions must be positive")

    @property
    def source_digest(self) -> str:
        return content_digest(
            {
                "algorithm": "inclusive-grid-endpoints-v1",
                "name": self.name,
                "input": [self.input_width, self.input_height],
                "output": [self.output_width, self.output_height],
            }
        )

    def transform(self, x: int, y: int) -> tuple[int, int]:
        if type(x) is not int or type(y) is not int:
            raise TypeError("coordinates must be plain integers")
        if not (0 <= x < self.input_width and 0 <= y < self.input_height):
            raise ValueError("input coordinate is outside the declared grid")
        # Map inclusive endpoint to inclusive endpoint.  The identity adapter
        # is exact; normalized grids reach all four screen corners.
        output_x = round(x * (self.output_width - 1) / max(1, self.input_width - 1))
        output_y = round(y * (self.output_height - 1) / max(1, self.input_height - 1))
        return output_x, output_y


IDENTITY_ADAPTER = CoordinateAdapter("native-1024x768", SCREEN_WIDTH, SCREEN_HEIGHT)
NORMALIZED_1000_ADAPTER = CoordinateAdapter("normalized-1000x1000", 1000, 1000)
