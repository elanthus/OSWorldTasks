#!/usr/bin/env python3
"""One check of the current task bundle on the pinned OSWorld guest. Boots the guest; run by hand.

Successor to the driver in ``artifacts/osworld-guest-check-2026-11-11/``. That run exercised an
older guest bundle (revision 627e09b); this one exercises whatever revision it is started from,
and records that revision, the worktree state, and the bundle digest before booting anything.

What it records, all as raw values in ``results.json`` (nothing is graded here):

1. ``resets``: for a few seeds, reset the guest and record the acknowledgement count the page
   reports right after reset (the host requires exactly
   ``osworld_task.SETUP_ACKNOWLEDGED_INPUTS``), and the pointer-park program's stdout and stderr
   exactly as the guest returned them. The 2026-11-11 run could not show whether the old park
   program ran or clicked, because setup discarded its output.
2. ``golden``: the OSWorld golden trajectory replay (``scripts/osworld_golden_trajectory.py``).
3. ``cases``: short input sequences run on the guest and on the fake backend, comparing the
   submitted values; the seven cases from the 2026-11-11 run plus cases for this change (a key
   first with no click, a trailing space, Enter and Space on the country select).
4. ``oracle``: the oracle episode for one seed with each guest frame digest paired with the
   oracle's next action, so frames that precede two different actions can be counted.

``provider_closed`` is recorded from what happened: each provider close is attempted in a
``finally`` block, and the record holds whether it returned and the error if it raised. The
driver never sets it unconditionally. The exit status is non-zero if any close failed.

Usage (from the repository root, with the ``osworld`` extra installed in this checkout's venv)::

    .venv/bin/python scripts/osworld_guest_check.py OUTPUT_DIR \\
        --guest-image /path/to/.cache/osworld/osworld-v2-ubuntu-x86.qcow2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pixelgym
from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.backends.osworld import OSWorldBackend, OSWorldBackendConfig
from pixelgym.env import PixelGuiEnv
from pixelgym.tasks.vendor_form import generator, osworld_task, render
from pixelgym.tasks.vendor_form.ui import INPUT_CONTRACT_VERSION

SCHEMA_VERSION = "pixelgym-osworld-guest-check-v2"
KEY = {key: index for index, key in enumerate(KEY_ALLOWLIST)}
RESET_SEEDS = (7, 11, 23)
ORACLE_SEED = 7
SUBMIT = ("click", (578, 486))
CASES: dict[str, list[tuple[str, Any]]] = {
    # The seven cases of the 2026-11-11 run, unchanged.
    "caret ArrowLeft": [
        ("click", (768, 94)),
        *[("key", k) for k in "ab"],
        ("key", "ArrowLeft"),
        ("key", "c"),
        SUBMIT,
    ],
    "caret ArrowUp": [
        ("click", (768, 94)),
        *[("key", k) for k in "ab"],
        ("key", "ArrowUp"),
        ("key", "c"),
        SUBMIT,
    ],
    "caret click near left edge": [
        ("click", (768, 94)),
        *[("key", k) for k in "abc"],
        ("click", (768, 280)),
        ("click", (540, 94)),
        ("key", "X"),
        SUBMIT,
    ],
    "label click focuses": [("click", (768, 280)), ("click", (768, 67)), ("key", "Z"), SUBMIT],
    "Tab into radios then ArrowDown": [("key", "Tab")] * 6 + [("key", "ArrowDown"), SUBMIT],
    "Tab into radios then Space": [("key", "Tab")] * 6 + [("key", " "), SUBMIT],
    "select ArrowDown twice": [
        ("click", (768, 343)),
        ("key", "ArrowDown"),
        ("key", "ArrowDown"),
        SUBMIT,
    ],
    # New for input contract v3 and the park change.
    "key first without any click": [("key", "Tab"), ("key", "Q"), SUBMIT],
    "trailing space": [("click", (768, 94)), ("key", "A"), ("key", " "), SUBMIT],
    "select Enter and Space inert": [
        ("click", (768, 343)),
        ("key", "a"),
        ("key", "Enter"),
        ("key", " "),
        SUBMIT,
    ],
}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def _digest(array: Any) -> str:
    return hashlib.sha256(array.tobytes()).hexdigest()


def _action(item: tuple[str, Any]) -> dict[str, int]:
    kind, argument = item
    if kind == "click":
        return {"action_type": int(ActionType.CLICK), "x": argument[0], "y": argument[1], "key": 0}
    return {"action_type": int(ActionType.KEY), "x": 0, "y": 0, "key": KEY[argument]}


def _close(env: PixelGuiEnv, backend: OSWorldBackend) -> dict[str, Any]:
    """Close one provider and report what happened, never raising.

    A failed reset already closed its provider and, to keep the reset error, suppressed any close
    error (``OSWorldBackend.suppressed_close_errors``). The close here then finds no provider and
    returns, so those earlier failures are reported too and make ``returned`` false.
    """

    try:
        env.close()
    except Exception:  # noqa: BLE001 - recorded, not swallowed: the exit status reflects it
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
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", "utf-8")


def _provenance(guest_image: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        bundle_sha256 = osworld_task.build_guest_bundle(
            generator.generate_task(7), Path(tmp) / "bundle.zip"
        )
    return {
        "run_started_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_revision": _git("rev-parse", "HEAD").strip(),
        "git_status_porcelain": _git("status", "--porcelain"),
        "worktree_clean_at_start": _git("status", "--porcelain") == "",
        "pixelgym_module_path": str(Path(pixelgym.__file__).resolve()),
        "guest_bundle_sha256_seed7": bundle_sha256,
        "input_contract": INPUT_CONTRACT_VERSION,
        "fake_renderer": render.RENDERER_VERSION,
        "setup_acknowledged_inputs": osworld_task.SETUP_ACKNOWLEDGED_INPUTS,
        "guest_image_path": str(guest_image),
        "host": {"python": platform.python_version(), "machine": platform.machine()},
    }


def _setup_cache_dir(backend: OSWorldBackend) -> Path | None:
    """The directory setup wrote its output to: OSWorld's per-task ``DesktopEnv.cache_dir``.

    OSWorld joins the configured ``desktop-env`` directory with the task id, so setup output lands
    in ``desktop-env/<task id>/``, not ``desktop-env/``. The 2026-11-12 run read the latter and
    recorded every park report as missing.
    """

    env = backend._env
    return None if env is None else Path(env.cache_dir)


def _park_report(directory: Path | None) -> dict[str, str | None]:
    report: dict[str, str | None] = {}
    if directory is None:
        return {"pointer-park.json": None, "pointer-park-error.txt": None}
    for name in ("pointer-park.json", "pointer-park-error.txt"):
        path = directory / f"pixelgym-vendor-form-{name}"
        report[name] = path.read_text(encoding="utf-8") if path.is_file() else None
    return report


def _run_resets(guest_image: Path, results: dict[str, Any]) -> None:
    config = OSWorldBackendConfig(guest_image_path=guest_image)
    backend = OSWorldBackend(config)
    env = PixelGuiEnv(backend)
    records = []
    try:
        for seed in RESET_SEEDS:
            started = time.monotonic()
            try:
                observation, _info = env.reset(seed=seed)
                task = backend._task
                records.append(
                    {
                        "seed": seed,
                        "reset_returned": True,
                        "acknowledged_after_reset": int(
                            task.read_input_acknowledgements(backend._env)
                        ),
                        "pointer_park": _park_report(_setup_cache_dir(backend)),
                        "initial_frame_sha256": _digest(observation),
                        "seconds": round(time.monotonic() - started, 1),
                    }
                )
            except Exception:  # noqa: BLE001 - a failed reset is a result
                records.append(
                    {
                        "seed": seed,
                        "reset_returned": False,
                        "error": traceback.format_exc(),
                        "pointer_park": _park_report(_setup_cache_dir(backend)),
                        "seconds": round(time.monotonic() - started, 1),
                    }
                )
    finally:
        results["resets"] = {"records": records, "provider_close": _close(env, backend)}


def _run_golden(guest_image: Path, output: Path, results: dict[str, Any]) -> None:
    from scripts.osworld_golden_trajectory import DEFAULT_FIXTURE, record_real

    started = time.monotonic()
    try:
        golden = record_real(DEFAULT_FIXTURE, guest_image, output / "real-golden")
        results["golden"] = {
            "fixture": str(DEFAULT_FIXTURE),
            "summary": golden["summary"],
            "rewards": [step["reward"] for step in golden["trace"]],
            "backend_metadata": golden["backend_metadata"],
            "seconds": round(time.monotonic() - started, 1),
            # record_real closes its own provider and raises if that close fails, so reaching
            # this line means its close returned.
            "provider_close": {"attempted": True, "returned": True, "error": None},
        }
    except Exception:  # noqa: BLE001
        results["golden"] = {
            "error": traceback.format_exc(),
            "seconds": round(time.monotonic() - started, 1),
            "provider_close": {
                "attempted": True,
                "returned": None,
                "error": "record_real raised; see error for whether its close was reached",
            },
        }


def _run_cases(guest_image: Path, results: dict[str, Any], deadline: float) -> None:
    backend = OSWorldBackend(OSWorldBackendConfig(guest_image_path=guest_image))
    env = PixelGuiEnv(backend)
    cases: dict[str, Any] = {}
    try:
        for name, items in CASES.items():
            if time.monotonic() > deadline:
                cases[name] = {"skipped": "time budget"}
                continue
            started = time.monotonic()
            try:
                observation, _info = env.reset(seed=7)
                fake = FakeBackend()
                fake_env = PixelGuiEnv(fake)
                fake_env.reset(seed=7)
                steps = []
                frames = [_digest(observation)]
                for item in items:
                    step_started = time.monotonic()
                    observation, reward, terminated, truncated, _ = env.step(_action(item))
                    fake_env.step(_action(item))
                    frames.append(_digest(observation))
                    steps.append(
                        {
                            "action": [item[0], list(item[1]) if item[0] == "click" else item[1]],
                            "reward": reward,
                            "terminated": terminated,
                            "truncated": truncated,
                            "seconds": round(time.monotonic() - step_started, 2),
                            "ack_reads": backend.last_input_ack_reads,
                            "stabilization_seconds": backend.last_stabilization_seconds,
                        }
                    )
                    if terminated:
                        break
                guest_submissions = [dict(s.values) for s in backend.read_submissions()]
                fake_submissions = [dict(s.values) for s in fake.read_submissions()]
                cases[name] = {
                    "match": guest_submissions == fake_submissions,
                    "guest_submissions": guest_submissions,
                    "fake_submissions": fake_submissions,
                    "frame_sha256": frames,
                    "steps": steps,
                    "seconds": round(time.monotonic() - started, 1),
                }
            except Exception:  # noqa: BLE001
                cases[name] = {
                    "error": traceback.format_exc(),
                    "seconds": round(time.monotonic() - started, 1),
                }
    finally:
        results["cases"] = {"records": cases, "provider_close": _close(env, backend)}


def _run_oracle(guest_image: Path, results: dict[str, Any], deadline: float) -> None:
    from tests.support.golden_solver import build_dynamic_solve_actions

    if time.monotonic() > deadline:
        results["oracle"] = {"skipped": "time budget"}
        return
    fake = FakeBackend()
    fake.reset(ORACLE_SEED)
    actions = build_dynamic_solve_actions(fake)
    backend = OSWorldBackend(OSWorldBackendConfig(guest_image_path=guest_image))
    env = PixelGuiEnv(backend)
    pairs = []
    record: dict[str, Any] = {"seed": ORACLE_SEED, "action_count": len(actions)}
    try:
        observation, _info = env.reset(seed=ORACLE_SEED)
        for action in actions:
            if time.monotonic() > deadline:
                record["stopped"] = "time budget"
                break
            key = [int(action["action_type"]), action["x"], action["y"], action["key"]]
            pairs.append({"frame_sha256": _digest(observation), "action": key})
            observation, reward, terminated, _truncated, _ = env.step(
                {name: int(action[name]) for name in ("action_type", "x", "y", "key")}
            )
            if terminated:
                record["terminated_reward"] = reward
                break
        record["pairs"] = pairs
    except Exception:  # noqa: BLE001
        record["error"] = traceback.format_exc()
        record["pairs"] = pairs
    finally:
        record["provider_close"] = _close(env, backend)
        results["oracle"] = record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--guest-image", type=Path, required=True)
    parser.add_argument("--budget-minutes", type=float, default=90.0)
    args = parser.parse_args(argv)
    if not args.guest_image.is_file():
        parser.error(f"guest image does not exist: {args.guest_image}")
    args.output.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    deadline = started + args.budget_minutes * 60
    results: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    results["provenance"] = _provenance(args.guest_image.resolve())
    _write(args.output / "results.json", results)

    for part in (
        lambda: _run_resets(args.guest_image, results),
        lambda: _run_golden(args.guest_image, args.output, results),
        lambda: _run_cases(args.guest_image, results, deadline),
        lambda: _run_oracle(args.guest_image, results, deadline),
    ):
        part()
        results["total_seconds"] = round(time.monotonic() - started, 1)
        _write(args.output / "results.json", results)

    closes = [
        results["resets"]["provider_close"],
        results["golden"]["provider_close"],
        results["cases"]["provider_close"],
        results["oracle"].get("provider_close", {"attempted": False, "returned": True}),
    ]
    results["provider_closed"] = all(close["returned"] is True for close in closes)
    _write(args.output / "results.json", results)
    return 0 if results["provider_closed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
