from __future__ import annotations

from collections import Counter

import pytest

from pixelgym.grounding.schema import (
    SCREEN_STATES,
    TARGET_SPECS,
    TASK_SEEDS,
    css_bbox_to_screenshot,
    target_for_example_index,
    validate_bbox,
    validate_candidate_set,
)


def test_frozen_grid_is_100_examples_balanced_across_targets_and_states() -> None:
    count = len(TASK_SEEDS) * len(SCREEN_STATES)
    targets = Counter(target_for_example_index(index).semantic_id for index in range(count))

    assert count == 100
    assert targets == Counter({spec.semantic_id: 10 for spec in TARGET_SPECS})


def test_css_bbox_transform_contains_fractional_browser_bounds() -> None:
    assert css_bbox_to_screenshot(
        [615.84375, 391.0, 683.6875, 410.0],
        css_width=1024,
        css_height=768,
        screen_width=1024,
        screen_height=768,
    ) == [615, 391, 684, 410]
    assert css_bbox_to_screenshot(
        [10.25, 20.25, 30.75, 40.75],
        css_width=100,
        css_height=100,
        screen_width=200,
        screen_height=300,
    ) == [20, 60, 62, 123]


@pytest.mark.parametrize(
    "bbox",
    ([0, 0, 0, 1], [-1, 0, 1, 1], [0, 0, 11, 1], [0.0, 0, 1, 1]),
)
def test_bbox_validation_rejects_zero_area_out_of_bounds_and_nonintegers(bbox: list[int]) -> None:
    with pytest.raises(ValueError):
        validate_bbox(bbox, width=10, height=10)


def test_candidate_validation_rejects_duplicates_and_extra_fields() -> None:
    candidate = {
        "semantic_id": "country",
        "element_type": "select",
        "visible_label": "Country",
        "css_bbox": [1.0, 1.0, 4.0, 4.0],
        "bbox": [1, 1, 4, 4],
    }
    with pytest.raises(ValueError, match="duplicate"):
        validate_candidate_set([candidate, dict(candidate)], width=10, height=10)
    with pytest.raises(ValueError, match="fields"):
        validate_candidate_set([{**candidate, "target": True}], width=10, height=10)
