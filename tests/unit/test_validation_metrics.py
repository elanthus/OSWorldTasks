from __future__ import annotations

import numpy as np

from pixelgym.backends.fake import FakeBackend
from pixelgym.validation.metrics import raw_pixel_difference, structural_similarity
from pixelgym.validation.reset import validate_resets


def test_visual_metrics_keep_bitwise_and_perceptual_results_separate():
    reference = np.zeros((16, 16, 3), dtype=np.uint8)
    changed = reference.copy()
    changed[4, 5, 2] = 17

    assert raw_pixel_difference(reference, changed) == {
        "differing_pixel_count": 1,
        "max_per_channel_delta": 17,
        "differing_pixel_bbox_xyxy": [5, 4, 6, 5],
    }
    assert structural_similarity(reference, reference) == 1.0
    assert structural_similarity(reference, changed) < 1.0


class _DriftingPrivilegedState(FakeBackend):
    def __init__(self):
        super().__init__()
        self._state_reads = 0

    def read_privileged_state(self):
        state = super().read_privileged_state()
        self._state_reads += 1
        if self._state_reads > 1:
            state["task"]["seed"] = 999
        return state


def test_reset_validator_fails_semantic_state_drift_even_when_pixels_match():
    result = validate_resets(
        _DriftingPrivilegedState,
        backend_name="drifting-fake",
        seed=7,
        reset_count=2,
    )

    assert result["summary"]["semantic_task_state_exact"] is False
    assert result["summary"]["privileged_application_state_exact"] is False
    assert result["summary"]["bitwise_visual_determinism"] is True
    assert result["summary"]["passed"] is False
