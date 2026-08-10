from __future__ import annotations

from pathlib import Path

from PIL import Image

from pixelgym.grounding.determinism import compare_png_directories


def test_raw_repeatability_comparison_reports_bytes_pixels_and_no_tolerance(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "reference"
    candidate = tmp_path / "candidate"
    reference.mkdir()
    candidate.mkdir()
    Image.new("RGB", (4, 3), (10, 20, 30)).save(reference / "same.png")
    Image.new("RGB", (4, 3), (10, 20, 30)).save(candidate / "same.png")
    Image.new("RGB", (4, 3), (10, 20, 30)).save(reference / "changed.png")
    changed = Image.new("RGB", (4, 3), (10, 20, 30))
    changed.putpixel((2, 1), (13, 18, 31))
    changed.save(candidate / "changed.png")

    result = compare_png_directories(
        reference,
        candidate,
        reference_label="run-1",
        candidate_label="run-2",
    )

    assert result["file_count"] == 2
    assert result["byte_identical_file_count"] == 1
    assert result["differing_file_count"] == 1
    assert result["differing_pixel_count"] == 1
    assert result["max_channel_delta"] == 3
    assert result["differing_pixel_bbox_xyxy"] == [2, 1, 3, 2]
    assert result["tolerance_applied"] is False
    assert result["mask_applied"] is False


def test_identical_image_sets_report_bitwise_identity(tmp_path: Path) -> None:
    reference = tmp_path / "reference"
    candidate = tmp_path / "candidate"
    reference.mkdir()
    candidate.mkdir()
    Image.new("RGB", (2, 2), "white").save(reference / "one.png")
    (candidate / "one.png").write_bytes((reference / "one.png").read_bytes())

    result = compare_png_directories(
        reference,
        candidate,
        reference_label="run-1",
        candidate_label="run-2",
    )

    assert result["byte_identical_file_count"] == 1
    assert result["differing_file_count"] == 0
    assert result["differing_pixel_count"] == 0
    assert result["max_channel_delta"] == 0
    assert result["differing_pixel_bbox_xyxy"] is None
    assert result["reference_aggregate_sha256"] == result["candidate_aggregate_sha256"]
