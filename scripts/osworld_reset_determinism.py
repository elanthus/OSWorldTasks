#!/usr/bin/env python3
"""Reset the current task bundle on the pinned OSWorld guest many times. Boots the guest; by hand.

The guest counterpart of ``scripts/run_chromium_reset_determinism.py``. It replaces nothing: the
only stored guest bitwise reset evidence remains five resets of seed 7 on guest bundle
``6a3fd2bd…`` (``artifacts/day-2-rev-2026-09-06-issues-95-101/raw/real-reset.json``). This driver
records the same seeds and resets through ``OSWorldBackend`` on whatever revision it is started
from, writing raw values only:

- ``provenance``: revision, worktree state, per-seed guest bundle digest, and, once a provider is
  up, ``OSWorldBackend.integration_metadata()`` (upstream tag and commit, provider, Python
  version, screen size, runtime image and guest artifact digests).
- ``per_seed``: for every reset, the SHA-256 of the RGB frame, of the reset task record and of
  the privileged application state, the acknowledgement count the page reports after reset, and
  the elapsed seconds; for every pair of resets of a seed whose frames differ, the raw
  differing-pixel count, maximum per-channel delta and bounding box (no tolerance).
- ``provider_close``: recorded from what happened. The one provider is closed in a ``finally``
  block on every path (success, reset failure, time budget, Ctrl-C); the record holds whether
  the close returned and any error, including close errors a failed reset suppressed.

A time budget (default 85 minutes, under the 90-minute stop-loss) is checked before each reset
using the slowest reset seen so far; resets that would not fit are recorded as skipped, never
silently dropped. If a reset fails and the backend's cleanup of that provider also fails, no
further reset is attempted: the backend has already dropped its provider reference, so another
reset would start a second guest while the first may still be running. The remaining resets are
recorded as skipped with that reason. ``results.json`` is rewritten after every reset, with the
repository root, home directory and system temporary directory replaced by ``<repo>``,
``<home>`` and ``<system-temp>``.

Usage (from the repository root, with the ``osworld`` extra installed)::

    .venv/bin/python scripts/osworld_reset_determinism.py OUTPUT_DIR \\
        --guest-image /path/to/.cache/osworld/osworld-v2-ubuntu-x86.qcow2 \\
        --seeds 0 7 11 23 --resets 5
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pixelgym.backends.osworld import OSWorldBackend, OSWorldBackendConfig
from pixelgym.env import PixelGuiEnv
from pixelgym.evidence_redaction import redact_evidence_text, standard_path_replacements
from pixelgym.tasks.vendor_form import generator, osworld_task, render
from pixelgym.tasks.vendor_form.ui import INPUT_CONTRACT_VERSION
from scripts.run_chromium_reset_determinism import (
    DEFAULT_SEEDS,
    frame_sha256,
    record_sha256,
    summarize_seed,
)

SCHEMA_VERSION = "pixelgym-osworld-reset-determinism-v1"
DEFAULT_RESETS = 5
DEFAULT_BUDGET_MINUTES = 85.0
CLEANUP_FAILED_REASON = "provider cleanup failed after a reset failure"
BackendFactory = Callable[[Path], OSWorldBackend]


def _git(*args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def bundle_digests(seeds: list[int]) -> dict[str, str]:
    digests = {}
    with tempfile.TemporaryDirectory() as tmp:
        for seed in seeds:
            digests[str(seed)] = osworld_task.build_guest_bundle(
                generator.generate_task(seed), Path(tmp) / f"bundle-{seed}.zip"
            )
    return digests


def provenance(guest_image: Path, seeds: list[int]) -> dict[str, Any]:
    status = _git("status", "--porcelain")
    return {
        "run_started_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_revision": _git("rev-parse", "HEAD").strip(),
        "worktree_clean_at_start": status == "",
        "git_status_porcelain": status,
        "guest_bundle_sha256_by_seed": bundle_digests(seeds),
        "input_contract": INPUT_CONTRACT_VERSION,
        "fake_renderer": render.RENDERER_VERSION,
        "setup_acknowledged_inputs": osworld_task.SETUP_ACKNOWLEDGED_INPUTS,
        "guest_image_path": str(guest_image),
    }


def close_record(env: PixelGuiEnv, backend: OSWorldBackend) -> dict[str, Any]:
    """Close the provider and report what happened, never raising."""

    try:
        env.close()
    except Exception:  # noqa: BLE001 - recorded; the exit status reflects it
        error: str | None = traceback.format_exc()
    else:
        error = None
    earlier = list(backend.suppressed_close_errors)
    return {
        "attempted": True,
        "returned": error is None and not earlier,
        "error": error,
        "suppressed_close_errors": earlier,
    }


def _write(path: Path, value: dict[str, Any]) -> None:
    text = json.dumps(value, indent=2, sort_keys=True, default=str) + "\n"
    replacements = [(REPO_ROOT, "<repo>"), *standard_path_replacements([])]
    path.write_text(redact_evidence_text(text, replacements), "utf-8")


def default_backend(guest_image: Path) -> OSWorldBackend:
    return OSWorldBackend(OSWorldBackendConfig(guest_image_path=guest_image))


def run(
    output: Path,
    guest_image: Path,
    seeds: list[int],
    resets: int,
    *,
    budget_minutes: float = DEFAULT_BUDGET_MINUTES,
    backend_factory: BackendFactory = default_backend,
    clock: Callable[[], float] = time.monotonic,
    save_frames: bool = True,
) -> dict[str, Any]:
    """Record the resets; always closes the provider and writes ``results.json``.

    Returns the record as stored, after path redaction.
    """

    if len(set(seeds)) != len(seeds):
        raise ValueError(f"seeds must be distinct, got {seeds}")
    from PIL import Image

    output.mkdir(parents=True, exist_ok=True)
    started = clock()
    deadline = started + budget_minutes * 60
    results: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "provenance": provenance(guest_image, seeds),
        "seeds": seeds,
        "resets_per_seed": resets,
        "budget_minutes": budget_minutes,
        "metric_definition": {
            "bitwise": "SHA-256 of the RGB uint8 frame bytes after reset",
            "raw": "RGB differing-pixel count and maximum absolute per-channel delta, no tolerance",
            "semantic": "SHA-256 of the reset task record and of the privileged application state",
            "perceptual": "not computed",
        },
        "per_seed": [],
        "skipped": [],
        "integration_metadata": None,
    }
    _write(output / "results.json", results)
    backend = backend_factory(guest_image)
    env = PixelGuiEnv(backend)
    slowest = 0.0
    cleanup_failed = False
    try:
        for seed in seeds:
            records: list[dict[str, Any]] = []
            frames: list[np.ndarray] = []
            errors: list[dict[str, Any]] = []
            for index in range(resets):
                if cleanup_failed:
                    results["skipped"].append(
                        {"seed": seed, "reset_index": index, "reason": CLEANUP_FAILED_REASON}
                    )
                    continue
                now = clock()
                if now + slowest > deadline:
                    results["skipped"].append(
                        {"seed": seed, "reset_index": index, "reason": "time budget"}
                    )
                    continue
                close_errors_before = len(backend.suppressed_close_errors)
                try:
                    observation, _info = env.reset(seed=seed)
                    record = {
                        "reset_index": index,
                        "frame_sha256": frame_sha256(observation),
                        "frame_shape": list(observation.shape),
                        "frame_dtype": str(observation.dtype),
                        "task_sha256": record_sha256(backend._task_record),
                        "form_state_sha256": record_sha256(backend.read_privileged_state()),
                        "acknowledged_after_reset": int(
                            backend._task.read_input_acknowledgements(backend._env)
                        ),
                        "seconds": round(clock() - now, 1),
                    }
                    if results["integration_metadata"] is None:
                        results["integration_metadata"] = backend.integration_metadata()
                    if save_frames and (
                        not records or record["frame_sha256"] != records[0]["frame_sha256"]
                    ):
                        Image.fromarray(observation).save(output / f"seed-{seed}-reset-{index}.png")
                    records.append(record)
                    frames.append(observation)
                except Exception:  # noqa: BLE001 - a failed reset is a result
                    errors.append(
                        {
                            "reset_index": index,
                            "error": traceback.format_exc(),
                            "seconds": round(clock() - now, 1),
                        }
                    )
                    cleanup_failed = len(backend.suppressed_close_errors) > close_errors_before
                slowest = max(slowest, clock() - now)
                summary = summarize_seed(seed, records, frames) if records else {"seed": seed}
                summary["reset_errors"] = errors
                current = [s for s in results["per_seed"] if s["seed"] != seed]
                results["per_seed"] = [*current, summary]
                results["elapsed_seconds"] = round(clock() - started, 1)
                _write(output / "results.json", results)
    finally:
        results["provider_close"] = close_record(env, backend)
        results["provider_closed"] = results["provider_close"]["returned"] is True
        results["elapsed_seconds"] = round(clock() - started, 1)
        _write(output / "results.json", results)
    # Return what was stored, so callers see the same redacted record as the file.
    return json.loads((output / "results.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--guest-image", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    parser.add_argument("--resets", type=int, default=DEFAULT_RESETS)
    parser.add_argument("--budget-minutes", type=float, default=DEFAULT_BUDGET_MINUTES)
    args = parser.parse_args(argv)
    if len(set(args.seeds)) != len(args.seeds):
        parser.error(f"--seeds must not repeat a seed: {args.seeds}")
    if not args.guest_image.is_file():
        parser.error(f"guest image does not exist: {args.guest_image}")
    results = run(
        args.output,
        args.guest_image.resolve(),
        args.seeds,
        args.resets,
        budget_minutes=args.budget_minutes,
        backend_factory=lambda image: default_backend(image),
    )
    return 0 if results["provider_closed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
