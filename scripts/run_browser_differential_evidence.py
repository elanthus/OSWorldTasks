#!/usr/bin/env python3
"""Record the fake-versus-Chromium differential comparison and the barrier controls as evidence.

The opt-in browser tests assert and stop; they leave no stored run. This script runs the same
comparison (`tests/support/vendor_form_differential.py`) once derandomized (Hypothesis derives
that run's sequence from this script's own test function, so it is not the suite's replay) and
once per explicit Hypothesis seed, and writes every generated sequence, every action, and every
divergence to one JSON file, with the revision, worktree state, platform, and Chromium version.
It does not shrink: each run keeps the sequences Hypothesis generated, divergent or not.

It also records the raw results of the acknowledgement-barrier checks from
`tests/integration/test_vendor_form_input_barrier.py`: the negative control (barrier off, the
submission held at the server until after the in-step read) and the matching positive control
(barrier on), plus the seed-7 golden trajectory's reward timeline under 50 ms and 300 ms of submit
latency.

The report is generated from the JSON by `scripts/generate_browser_differential_report.py`.

    .venv/bin/python scripts/run_browser_differential_evidence.py \\
        --output artifacts/browser-differential-v1/evidence.json --hypothesis-seeds 1 2 3
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hypothesis import HealthCheck, Phase, given, seed, settings
from hypothesis import strategies as st

from pixelgym.tasks.vendor_form import render
from pixelgym.tasks.vendor_form.ui import (
    DESIGN_HEIGHT,
    DESIGN_WIDTH,
    INCOMPLETE_SUBMISSION_MESSAGE,
    INPUT_CONTRACT_VERSION,
)
from tests.support.vendor_form_differential import (
    SEEDS,
    action_to_json,
    actions_strategy,
    compare,
)

SCHEMA_VERSION = "pixelgym-browser-differential-evidence-v1"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def _differential_run(
    real: Any, *, label: str, hypothesis_seed: int | None, examples: int, max_actions: int
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []

    @settings(
        max_examples=examples,
        derandomize=hypothesis_seed is None,
        database=None,
        deadline=None,
        phases=[Phase.generate],
        suppress_health_check=list(HealthCheck),
    )
    @given(task_seed=st.sampled_from(SEEDS), actions=actions_strategy(max_actions))
    def run(task_seed: int, actions: list[Any]) -> None:
        divergence = compare(real, task_seed, actions)
        records.append(
            {
                "task_seed": task_seed,
                "actions": [action_to_json(action) for action in actions],
                "divergence": divergence,
            }
        )

    if hypothesis_seed is not None:
        run = seed(hypothesis_seed)(run)
    started = time.monotonic()
    run()
    return {
        "label": label,
        "hypothesis_seed": hypothesis_seed,
        "derandomize": hypothesis_seed is None,
        "max_examples": examples,
        "max_actions": max_actions,
        "sequences": records,
        "sequence_count": len(records),
        "action_count": sum(len(record["actions"]) for record in records),
        "divergence_count": sum(record["divergence"] is not None for record in records),
        "seconds": round(time.monotonic() - started, 1),
    }


def _barrier_controls() -> dict[str, Any]:
    from tests.integration.test_vendor_form_input_barrier import _GOLDEN, _run, _submit_click

    negative = _run(
        0.0,
        [_submit_click()],
        expected_submissions=1,
        expected_status=INCOMPLETE_SUBMISSION_MESSAGE,
        submit_gate=threading.Event(),
        acknowledge_inputs=False,
    )
    gate, arrived = threading.Event(), threading.Event()

    def release_after_arrival() -> None:
        arrived.wait(30.0)
        gate.set()

    releaser = threading.Thread(target=release_after_arrival, daemon=True)
    releaser.start()
    try:
        positive = _run(
            0.0,
            [_submit_click()],
            expected_submissions=1,
            expected_status=INCOMPLETE_SUBMISSION_MESSAGE,
            submit_gate=gate,
            submit_arrived=arrived,
        )
    finally:
        gate.set()
        releaser.join(timeout=5.0)

    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    latency_runs = []
    for delay in (0.05, 0.3):
        result = _run(
            delay, golden["actions"], expected_submissions=1, expected_status="Submitted."
        )
        timeline = result["timeline"]
        latency_runs.append(
            {
                "submit_delay_seconds": delay,
                "steps": len(timeline),
                "positive_reward_steps": [i + 1 for i, row in enumerate(timeline) if row[0] > 0],
                "terminated_steps": [i + 1 for i, row in enumerate(timeline) if row[1]],
                "truncated_steps": [i + 1 for i, row in enumerate(timeline) if row[2]],
                "in_step_reads_last": result["in_step_reads"][-1],
                "observation_equals_later_frame": bool(
                    (result["final_observation"] == result["later_frame"]).all()
                ),
            }
        )
    return {
        "source": "tests/integration/test_vendor_form_input_barrier.py",
        "golden_fixture": str(_GOLDEN.relative_to(REPO_ROOT)),
        "negative_control_barrier_off": {
            "acknowledge_inputs": False,
            "submit_held_until_after_in_step_read": True,
            "in_step_reads": negative["in_step_reads"],
            "timeline": negative["timeline"],
        },
        "positive_control_barrier_on": {
            "acknowledge_inputs": True,
            "submit_released_after_arrival": True,
            "in_step_reads": positive["in_step_reads"],
            "timeline": positive["timeline"],
        },
        "golden_under_submit_latency": latency_runs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hypothesis-seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--examples", type=int, default=120)
    parser.add_argument("--max-actions", type=int, default=30)
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    from tests.support.browser_backend import (
        PlaywrightVendorFormBackend,
        launch_chromium,
        vendor_form_server,
    )

    porcelain = _git("status", "--porcelain")
    evidence: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generator": "scripts/run_browser_differential_evidence.py",
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_revision": _git("rev-parse", "HEAD").strip(),
        "worktree_clean": porcelain == "",
        "git_status_porcelain": porcelain,
        "input_contract": INPUT_CONTRACT_VERSION,
        "fake_renderer": render.RENDERER_VERSION,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "playwright": importlib.metadata.version("playwright"),
            "hypothesis": importlib.metadata.version("hypothesis"),
        },
        "task_seeds": list(SEEDS),
    }
    started = time.monotonic()
    with vendor_form_server() as base_url, sync_playwright() as playwright:
        browser = launch_chromium(playwright)
        try:
            evidence["platform"]["chromium"] = browser.version
            page = browser.new_page(viewport={"width": DESIGN_WIDTH, "height": DESIGN_HEIGHT})
            real = PlaywrightVendorFormBackend(page, base_url)
            runs = [(f"hypothesis-seed-{value}", value) for value in args.hypothesis_seeds]
            evidence["runs"] = [
                _differential_run(
                    real,
                    label=label,
                    hypothesis_seed=value,
                    examples=args.examples,
                    max_actions=args.max_actions,
                )
                for label, value in [("derandomized", None), *runs]
            ]
        finally:
            browser.close()
    evidence["barrier"] = _barrier_controls()
    evidence["total_seconds"] = round(time.monotonic() - started, 1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
