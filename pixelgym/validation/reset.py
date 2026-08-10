"""Repeated-reset determinism validation for fake and real backends."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from pixelgym.backends.base import Backend
from pixelgym.env import PixelGuiEnv
from pixelgym.validation.metrics import (
    privileged_hashes,
    raw_pixel_difference,
    structural_similarity,
)


def validate_resets(
    backend_factory: Callable[[], Backend],
    *,
    backend_name: str,
    seed: int,
    reset_count: int,
    screenshot_dir: Path | None = None,
) -> dict[str, Any]:
    if reset_count < 2:
        raise ValueError("reset_count must be at least two")
    backend = backend_factory()
    env = PixelGuiEnv(backend)
    records: list[dict[str, Any]] = []
    backend_metadata: dict[str, Any] | None = None
    reference: np.ndarray | None = None
    reference_hashes: dict[str, str] | None = None
    if screenshot_dir is not None:
        screenshot_dir.mkdir(parents=True, exist_ok=True)

    try:
        for index in range(reset_count):
            started = time.monotonic()
            observation, info = env.reset(seed=seed)
            reset_seconds = time.monotonic() - started
            state_reader = getattr(backend, "read_privileged_state", None)
            if not callable(state_reader):
                raise TypeError(f"{backend_name} has no privileged validation state probe")
            state = state_reader()
            hashes = privileged_hashes(state)
            record: dict[str, Any] = {
                "reset_index": index,
                "task_id": info["task_id"],
                **hashes,
                "screenshot_shape": list(observation.shape),
                "screenshot_dtype": str(observation.dtype),
                "reset_to_stable_frame_seconds": reset_seconds,
            }
            if reference is None:
                reference = observation.copy()
                reference_hashes = hashes
                record.update(
                    {
                        "differing_pixel_count": 0,
                        "max_per_channel_delta": 0,
                        "differing_pixel_bbox_xyxy": None,
                        "ssim": 1.0,
                    }
                )
            else:
                record.update(raw_pixel_difference(reference, observation))
                record["ssim"] = structural_similarity(reference, observation)
            if screenshot_dir is not None:
                path = screenshot_dir / f"{backend_name}-seed-{seed}-reset-{index}.png"
                Image.fromarray(observation).save(path)
                record["screenshot_path"] = str(path)
            records.append(record)
        metadata_reader = getattr(backend, "integration_metadata", None)
        if callable(metadata_reader):
            backend_metadata = metadata_reader()
    finally:
        env.close()

    assert reference_hashes is not None
    task_exact = all(
        item["task_spec_sha256"] == reference_hashes["task_spec_sha256"] for item in records
    )
    app_exact = all(
        item["application_state_sha256"] == reference_hashes["application_state_sha256"]
        for item in records
    )
    bitwise = all(item["differing_pixel_count"] == 0 for item in records)
    shapes_exact = len({tuple(item["screenshot_shape"]) for item in records}) == 1
    dtypes_exact = len({item["screenshot_dtype"] for item in records}) == 1
    result = {
        "schema_version": 1,
        "validator": "reset-determinism",
        "backend": backend_name,
        "seed": seed,
        "reset_count": reset_count,
        "metric_definition": {
            "raw": "RGB differing-pixel count and maximum absolute per-channel delta",
            "perceptual": (
                "11x11 uniform-window luminance SSIM (Rec. 601 luma, reflected edges, "
                "K1=0.01, K2=0.03, L=255); never used as bitwise evidence"
            ),
        },
        "records": records,
        "summary": {
            "semantic_task_state_exact": task_exact,
            "privileged_application_state_exact": app_exact,
            "screenshot_shape_exact": shapes_exact,
            "screenshot_dtype_exact": dtypes_exact,
            "bitwise_visual_determinism": bitwise,
            "minimum_ssim": min(item["ssim"] for item in records),
            "maximum_differing_pixel_count": max(item["differing_pixel_count"] for item in records),
            "maximum_per_channel_delta": max(item["max_per_channel_delta"] for item in records),
            "maximum_reset_to_stable_frame_seconds": max(
                item["reset_to_stable_frame_seconds"] for item in records
            ),
            "passed": task_exact and app_exact and shapes_exact and dtypes_exact,
        },
    }
    if backend_metadata is not None:
        result["backend_metadata"] = backend_metadata
    return result
