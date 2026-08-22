from __future__ import annotations

import inspect

import pytest
from PIL import Image

from pixelgym.grounding.overlays import (
    ordered_candidates,
    proposal_match,
    render_overlay,
    validate_marks,
)


def _candidate(semantic_id: str, bbox: list[int], element_type: str = "button") -> dict:
    return {
        "semantic_id": semantic_id,
        "element_type": element_type,
        "visible_label": semantic_id,
        "css_bbox": [float(value) for value in bbox],
        "bbox": bbox,
    }


def test_overlay_api_cannot_receive_a_target() -> None:
    assert list(inspect.signature(render_overlay).parameters) == ["raw_image", "candidates"]


def test_candidate_order_is_spatial_with_semantic_tiebreaker() -> None:
    candidates = [
        _candidate("z", [20, 10, 30, 20]),
        _candidate("b", [10, 10, 20, 20]),
        _candidate("a", [10, 10, 20, 20]),
        _candidate("top", [100, 1, 110, 9]),
    ]

    assert [item["semantic_id"] for item in ordered_candidates(candidates)] == [
        "top",
        "a",
        "b",
        "z",
    ]


def test_overlay_bytes_dimensions_ids_and_mapping_are_deterministic() -> None:
    raw = Image.new("RGB", (200, 100), "white")
    candidates = [
        _candidate("second", [50, 40, 90, 60]),
        _candidate("first", [10, 10, 40, 30]),
    ]

    first_image, first_marks = render_overlay(raw, candidates)
    second_image, second_marks = render_overlay(raw, list(reversed(candidates)))

    assert first_image.size == raw.size
    assert first_image.tobytes() == second_image.tobytes()
    assert first_marks == second_marks
    assert [mark["mark_id"] for mark in first_marks] == [1, 2]
    assert [mark["semantic_id"] for mark in first_marks] == ["first", "second"]
    assert len({mark["semantic_id"] for mark in first_marks}) == len(candidates)


def test_badges_do_not_overlap_any_candidate_or_prior_badge() -> None:
    candidates = [
        _candidate("left", [10, 30, 60, 50]),
        _candidate("middle", [62, 30, 112, 50]),
        _candidate("right", [114, 30, 164, 50]),
    ]

    _, marks = render_overlay(Image.new("RGB", (200, 100), "white"), candidates)

    def overlaps(first: list[int], second: list[int]) -> bool:
        return (
            max(first[0], second[0]) < min(first[2], second[2])
            and max(first[1], second[1]) < min(first[3], second[3])
        )

    candidate_boxes = [candidate["bbox"] for candidate in candidates]
    badge_boxes = [mark["badge_bbox"] for mark in marks]
    assert all(
        not overlaps(badge, candidate)
        for badge in badge_boxes
        for candidate in candidate_boxes
    )
    assert all(
        not overlaps(first, second)
        for index, first in enumerate(badge_boxes)
        for second in badge_boxes[index + 1 :]
    )


def test_proposal_coverage_is_separate_from_selection() -> None:
    _, marks = render_overlay(
        Image.new("RGB", (200, 100), "white"),
        [_candidate("country", [10, 10, 50, 30])],
    )

    assert proposal_match("country", marks) == (True, 1)
    assert proposal_match("submit", marks) == (False, None)


def test_mark_validation_rejects_duplicate_or_unparseable_ids() -> None:
    mark = {
        "mark_id": 1,
        "semantic_id": "country",
        "element_type": "select",
        "visible_label": "Country",
        "bbox": [10, 10, 40, 30],
        "badge_bbox": [42, 10, 50, 20],
    }
    with pytest.raises(ValueError, match="unique sequential"):
        validate_marks([mark, dict(mark)], width=100, height=100)
    with pytest.raises(ValueError, match="unique sequential"):
        validate_marks([{**mark, "mark_id": "1"}], width=100, height=100)


@pytest.mark.parametrize(
    ("bbox", "element_type"),
    [
        ([85, 85, 99, 99], "radio"),
        ([85, 1, 99, 12], "button"),
        ([1, 85, 12, 99], "button"),
    ],
)
def test_badges_near_image_edges_always_remain_in_bounds(
    bbox: list[int], element_type: str
) -> None:
    _, marks = render_overlay(
        Image.new("RGB", (100, 100), "white"),
        [_candidate("edge", bbox, element_type)],
    )

    x0, y0, x1, y1 = marks[0]["badge_bbox"]
    assert 0 <= x0 < x1 <= 100
    assert 0 <= y0 < y1 <= 100
