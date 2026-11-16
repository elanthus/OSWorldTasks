#!/usr/bin/env python3
"""Reset the real vendor-form page in local Chromium many times and record every frame's digest.

The only stored bitwise reset evidence is five resets of seed 7 on one host against an older
guest bundle (``artifacts/day-2-rev-2026-09-06-issues-95-101/raw/real-reset.json``). This script
records the same kind of evidence for the current code without the OSWorld guest: it serves the
task app on loopback, drives it with the test-only Playwright backend
(``tests/support/browser_backend.py``, the same host contract the OSWorld backend runs), and for
each seed resets the page several times.

For every reset it stores:

- the SHA-256 of the RGB frame bytes returned by ``screenshot()`` (two bitwise-equal captures);
- the SHA-256 of the task record the server returns on reset and of the privileged DOM form
  state, for semantic determinism; neither is an observation;
- for every pair of resets of the same seed whose frames differ, the differing-pixel count, the
  maximum per-channel difference and the bounding box, before any tolerance.

No perceptual or tolerance-based score is computed. Each reset uses a new browser context in one
Chromium process. With ``--frames-dir`` the first frame of each seed is also written as a PNG so
another host's frames can be compared pixel by pixel.

The report is generated from the JSON by ``scripts/generate_chromium_reset_determinism_report.py``.

    .venv/bin/python scripts/run_chromium_reset_determinism.py \\
        --output artifacts/chromium-reset-determinism-v1/macos-arm64.json \\
        --frames-dir artifacts/chromium-reset-determinism-v1/frames-macos-arm64
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import itertools
import json
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_VERSION = "pixelgym-chromium-reset-determinism-v1"
DEFAULT_SEEDS = (0, 7, 11, 23, 42, 101, 1000, 10000)
DEFAULT_RESETS = 5


def frame_sha256(frame: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest()


def record_sha256(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def raw_difference(a: np.ndarray, b: np.ndarray) -> dict[str, Any]:
    """Raw RGB difference before any tolerance."""

    if a.shape != b.shape or a.dtype != b.dtype:
        return {
            "comparable": False,
            "shapes": [list(a.shape), list(b.shape)],
            "dtypes": [str(a.dtype), str(b.dtype)],
        }
    delta = np.abs(a.astype(np.int16) - b.astype(np.int16))
    differing = np.any(delta != 0, axis=-1)
    count = int(differing.sum())
    bbox = None
    if count:
        ys, xs = np.nonzero(differing)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    return {
        "comparable": True,
        "differing_pixel_count": count,
        "max_per_channel_delta": int(delta.max()),
        "differing_pixel_bbox_xyxy": bbox,
    }


def summarize_seed(seed: int, resets: list[dict[str, Any]], frames: list[np.ndarray]) -> dict:
    """Per-seed record: every reset, and the raw difference of every differing frame pair."""

    differing_pairs = []
    for i, j in itertools.combinations(range(len(frames)), 2):
        if resets[i]["frame_sha256"] != resets[j]["frame_sha256"]:
            labels = [resets[i]["reset_index"], resets[j]["reset_index"]]
            differing_pairs.append({"resets": labels, **raw_difference(frames[i], frames[j])})
    return {
        "seed": seed,
        "resets": resets,
        "distinct_frame_sha256": sorted({r["frame_sha256"] for r in resets}),
        "distinct_task_sha256": sorted({r["task_sha256"] for r in resets}),
        "distinct_form_state_sha256": sorted({r["form_state_sha256"] for r in resets}),
        "differing_frame_pairs": differing_pairs,
        "pair_count": len(frames) * (len(frames) - 1) // 2,
    }


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def _provenance(browser_version: str) -> dict[str, Any]:
    from pixelgym.tasks.vendor_form import render
    from pixelgym.tasks.vendor_form.browser_contract import (
        BROWSER_ARGS,
        chromium_renderer_contract,
    )
    from pixelgym.tasks.vendor_form.ui import DESIGN_HEIGHT, DESIGN_WIDTH, INPUT_CONTRACT_VERSION

    status = _git("status", "--porcelain")
    return {
        "recorded_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_revision": _git("rev-parse", "HEAD").strip(),
        "worktree_clean": status == "",
        "git_status_porcelain": status,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "playwright": importlib.metadata.version("playwright"),
        "chromium_version": browser_version,
        "browser_args": list(BROWSER_ARGS),
        "renderer_contract": chromium_renderer_contract(),
        "viewport": [DESIGN_WIDTH, DESIGN_HEIGHT],
        "input_contract": INPUT_CONTRACT_VERSION,
        "fake_renderer": render.RENDERER_VERSION,
        "isolation": "one Chromium process; a new browser context and page for every reset",
    }


def record(seeds: list[int], resets: int, frames_dir: Path | None) -> dict[str, Any]:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    from pixelgym.tasks.vendor_form.ui import DESIGN_HEIGHT, DESIGN_WIDTH
    from tests.support.browser_backend import (
        PlaywrightVendorFormBackend,
        launch_chromium,
        vendor_form_server,
    )

    started = time.monotonic()
    per_seed = []
    with vendor_form_server() as base_url, sync_playwright() as playwright:
        browser = launch_chromium(playwright)
        try:
            provenance = _provenance(browser.version)
            for seed in seeds:
                seed_resets: list[dict[str, Any]] = []
                frames: list[np.ndarray] = []
                for index in range(resets):
                    reset_started = time.monotonic()
                    context = browser.new_context(
                        viewport={"width": DESIGN_WIDTH, "height": DESIGN_HEIGHT}
                    )
                    try:
                        backend = PlaywrightVendorFormBackend(context.new_page(), base_url)
                        task = backend.reset(seed)
                        frame = backend.screenshot()
                        form_state = backend.read_form_state()
                        backend.close()
                    finally:
                        context.close()
                    frames.append(frame)
                    seed_resets.append(
                        {
                            "reset_index": index,
                            "frame_sha256": frame_sha256(frame),
                            "frame_shape": list(frame.shape),
                            "frame_dtype": str(frame.dtype),
                            "task_sha256": record_sha256(task),
                            "form_state_sha256": record_sha256(form_state),
                            "seconds": round(time.monotonic() - reset_started, 3),
                        }
                    )
                if frames_dir is not None:
                    frames_dir.mkdir(parents=True, exist_ok=True)
                    Image.fromarray(frames[0]).save(frames_dir / f"seed-{seed}-reset-0.png")
                per_seed.append(summarize_seed(seed, seed_resets, frames))
        finally:
            browser.close()
    return {
        "schema_version": SCHEMA_VERSION,
        "provenance": provenance,
        "seeds": seeds,
        "resets_per_seed": resets,
        "frames_dir": None if frames_dir is None else frames_dir.as_posix(),
        "metric_definition": {
            "bitwise": "SHA-256 of the RGB uint8 frame bytes; equal digests are bitwise identity",
            "raw": "RGB differing-pixel count and maximum absolute per-channel delta, no tolerance",
            "semantic": "SHA-256 of the reset task record and of the privileged DOM form state",
            "perceptual": "not computed",
        },
        "per_seed": per_seed,
        "total_seconds": round(time.monotonic() - started, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames-dir", type=Path, default=None)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    parser.add_argument("--resets", type=int, default=DEFAULT_RESETS)
    args = parser.parse_args(argv)
    if len(args.seeds) < 8 or args.resets < 5:
        parser.error("record at least 8 seeds and 5 resets per seed")
    result = record(args.seeds, args.resets, args.frames_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
