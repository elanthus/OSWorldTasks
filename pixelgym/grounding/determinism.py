"""Raw, tolerance-free image-set comparison for stored grounding evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

DETERMINISM_SCHEMA_VERSION = "pixelgym-grounding-repeatability-v1"


def _aggregate_hash(paths: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def compare_png_directories(
    reference_dir: Path,
    candidate_dir: Path,
    *,
    reference_label: str,
    candidate_label: str,
) -> dict[str, Any]:
    reference_paths = sorted(reference_dir.glob("*.png"))
    candidate_paths = sorted(candidate_dir.glob("*.png"))
    reference_names = [path.name for path in reference_paths]
    candidate_names = [path.name for path in candidate_paths]
    if reference_names != candidate_names:
        raise ValueError("image sets do not contain the same PNG filenames")
    differing_file_count = 0
    byte_identical_file_count = 0
    differing_pixel_count = 0
    max_channel_delta = 0
    union_x0: int | None = None
    union_y0: int | None = None
    union_x1: int | None = None
    union_y1: int | None = None
    for reference_path, candidate_path in zip(reference_paths, candidate_paths, strict=True):
        if reference_path.read_bytes() == candidate_path.read_bytes():
            byte_identical_file_count += 1
        reference = np.asarray(Image.open(reference_path).convert("RGB"), dtype=np.int16)
        candidate = np.asarray(Image.open(candidate_path).convert("RGB"), dtype=np.int16)
        if reference.shape != candidate.shape:
            raise ValueError(f"image dimensions differ for {reference_path.name}")
        changed = np.any(reference != candidate, axis=2)
        count = int(changed.sum())
        if not count:
            continue
        differing_file_count += 1
        differing_pixel_count += count
        max_channel_delta = max(max_channel_delta, int(np.abs(reference - candidate).max()))
        ys, xs = np.where(changed)
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)
        union_x0 = x0 if union_x0 is None else min(union_x0, x0)
        union_y0 = y0 if union_y0 is None else min(union_y0, y0)
        union_x1 = x1 if union_x1 is None else max(union_x1, x1)
        union_y1 = y1 if union_y1 is None else max(union_y1, y1)
    differing_bbox = None if union_x0 is None else [union_x0, union_y0, union_x1, union_y1]
    return {
        "schema_version": DETERMINISM_SCHEMA_VERSION,
        "comparison": "bitwise PNG bytes and decoded RGB pixels",
        "tolerance_applied": False,
        "mask_applied": False,
        "reference_label": reference_label,
        "candidate_label": candidate_label,
        "file_count": len(reference_paths),
        "byte_identical_file_count": byte_identical_file_count,
        "differing_file_count": differing_file_count,
        "differing_pixel_count": differing_pixel_count,
        "max_channel_delta": max_channel_delta,
        "differing_pixel_bbox_xyxy": differing_bbox,
        "reference_aggregate_sha256": _aggregate_hash(reference_paths, reference_dir),
        "candidate_aggregate_sha256": _aggregate_hash(candidate_paths, candidate_dir),
    }


def write_comparison(result: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
