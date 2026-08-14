from __future__ import annotations

from pathlib import Path

from PIL import Image

from pixelgym.grounding.pilot import _annotate_panel


def test_annotation_preserves_dimensions_and_draws_prediction() -> None:
    raw = Image.new("RGB", (100, 80), "white")

    correct = _annotate_panel(raw, bbox=[10, 10, 30, 30], point=[20.0, 20.0], correct=True)
    incorrect = _annotate_panel(raw, bbox=[10, 10, 30, 30], point=[50.0, 50.0], correct=False)

    assert correct.size == raw.size
    assert incorrect.size == raw.size
    assert correct.getpixel((20, 20)) != raw.getpixel((20, 20))
    assert incorrect.getpixel((50, 50)) != raw.getpixel((50, 50))


def test_annotation_png_bytes_are_deterministic(tmp_path: Path) -> None:
    raw = Image.new("RGB", (100, 80), "white")
    first = _annotate_panel(raw, bbox=[10, 10, 30, 30], point=[20.0, 20.0], correct=True)
    second = _annotate_panel(raw, bbox=[10, 10, 30, 30], point=[20.0, 20.0], correct=True)
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    first.save(first_path, format="PNG", optimize=False, compress_level=9)
    second.save(second_path, format="PNG", optimize=False, compress_level=9)

    assert first_path.read_bytes() == second_path.read_bytes()
