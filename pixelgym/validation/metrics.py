"""Raw visual/state comparison metrics used by reset validation."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import numpy as np
import numpy.typing as npt

from pixelgym.serialization import canonical_json_text


def canonical_json(value: Any) -> str:
    return canonical_json_text(value)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def raw_pixel_difference(
    reference: npt.NDArray[np.uint8], candidate: npt.NDArray[np.uint8]
) -> dict[str, int | list[int] | None]:
    if reference.shape != candidate.shape or reference.dtype != candidate.dtype:
        raise ValueError("raw pixel comparison requires matching shapes and dtypes")
    delta = np.abs(candidate.astype(np.int16) - reference.astype(np.int16))
    changed = np.any(delta != 0, axis=2)
    changed_y, changed_x = np.nonzero(changed)
    bounding_box = (
        None
        if changed_x.size == 0
        else [
            int(changed_x.min()),
            int(changed_y.min()),
            int(changed_x.max()) + 1,
            int(changed_y.max()) + 1,
        ]
    )
    return {
        "differing_pixel_count": int(np.count_nonzero(changed)),
        "max_per_channel_delta": int(delta.max(initial=0)),
        "differing_pixel_bbox_xyxy": bounding_box,
    }


def _uniform_mean(
    values: npt.NDArray[np.float64], size: int = 11
) -> npt.NDArray[np.float64]:
    """Local mean through an edge-reflected square window and integral image."""

    pad = size // 2
    padded = np.pad(values, ((pad, pad), (pad, pad)), mode="reflect")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant")
    integral = integral.cumsum(axis=0).cumsum(axis=1)
    total = (
        integral[size:, size:]
        - integral[:-size, size:]
        - integral[size:, :-size]
        + integral[:-size, :-size]
    )
    return np.asarray(total / (size * size), dtype=np.float64)


def structural_similarity(
    reference: npt.NDArray[np.uint8], candidate: npt.NDArray[np.uint8]
) -> float:
    """11x11-window luminance SSIM, in ``[-1, 1]`` (identical is 1).

    Frames are converted to Rec. 601 luma. Local means and variances use an
    edge-reflected uniform 11x11 window; constants are K1=0.01, K2=0.03,
    L=255.
    This is reported only as perceptual stability.  It never replaces or
    relabels the raw differing-pixel count or bitwise equality result.
    """

    if reference.shape != candidate.shape:
        raise ValueError("SSIM requires matching shapes")
    weights = np.array([0.299, 0.587, 0.114], dtype=np.float64)
    first = reference.astype(np.float64) @ weights
    second = candidate.astype(np.float64) @ weights
    mu_first = _uniform_mean(first)
    mu_second = _uniform_mean(second)
    sigma_first = _uniform_mean(first * first) - mu_first * mu_first
    sigma_second = _uniform_mean(second * second) - mu_second * mu_second
    covariance = _uniform_mean(first * second) - mu_first * mu_second
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    numerator = (2 * mu_first * mu_second + c1) * (2 * covariance + c2)
    denominator = (mu_first**2 + mu_second**2 + c1) * (sigma_first + sigma_second + c2)
    score = np.divide(
        numerator,
        denominator,
        out=np.ones_like(numerator),
        where=denominator != 0,
    ).mean()
    return float(np.clip(score, -1.0, 1.0))


def privileged_hashes(state: Mapping[str, Any]) -> dict[str, str]:
    if "task" not in state:
        raise ValueError("privileged state has no task")
    return {
        "task_spec_sha256": canonical_sha256(state["task"]),
        "application_state_sha256": canonical_sha256(state),
    }
