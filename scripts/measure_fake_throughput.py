#!/usr/bin/env python3
"""Measure fake-backend throughput and reset latency, and store them as structured evidence.

Writes ``artifacts/throughput/fake-throughput.json`` (render it with
``scripts/generate_throughput_report.py``). Every repeat is stored, not only a summary, together
with the host, Python and package versions, git revision, and whether the worktree was clean.

Workloads, each timed with ``time.perf_counter`` and repeated:

- ``random``: `PixelGuiEnv.step` with ``action_space.sample()`` (seeded). A third of these
  actions are NOOPs, which reuse the cached frame, so this is not a render-every-step number.
- ``golden``: the seed-7 golden trajectory replayed in a loop. Almost every step types a
  character, so every step renders; this is the workload closest to scripted demonstrations.
- ``reset``: `PixelGuiEnv.reset` over distinct seeds, reported as a latency distribution.
- ``sync_vector`` / ``async_vector``: Gymnasium 0.28 ``SyncVectorEnv`` and ``AsyncVectorEnv``
  over ``PixelGym/VendorForm-v0`` with a random policy, as aggregate environment steps per second.

``random``, ``golden``, and ``reset`` are measured with the current static-layer renderer and
with the frozen single-pass reference (``tests/support/reference_render.py``), so the speed-up
is measured on the same host in the same run. A separate untimed pass replays the same seeded
action streams through both renderers and compares every frame's SHA-256, which is the evidence
that the speed-up kept frames byte-identical. Numbers describe this host only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import gymnasium as gym
import numpy as np
import PIL

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    # Run as `python scripts/measure_fake_throughput.py`, sys.path[0] is `scripts/`, so the repo
    # root (and with it `tests.support`) is not importable yet.
    sys.path.insert(0, str(REPO_ROOT))

from pixelgym.backends.fake import FakeBackend
from pixelgym.env import PixelGuiEnv
from pixelgym.registration import VENDOR_FORM_ID
from tests.support.reference_render import render_reference

SCHEMA_VERSION = "pixelgym-fake-throughput-v1"
DEFAULT_OUTPUT = Path("artifacts/throughput/fake-throughput.json")
GOLDEN_FIXTURE = Path("tests/unit/fixtures/golden_trajectory_seed7.json")
RENDERERS = ("static_layer", "reference_single_pass")


class ReferenceRenderBackend(FakeBackend):
    """`FakeBackend` drawing every frame with the frozen pre-optimisation renderer."""

    def screenshot(self) -> Any:
        record, layout, form = self._require_task()
        if self._frame is None:
            self._frame = render_reference(record, form, layout)
        return self._frame.copy()


def _backend(renderer: str) -> FakeBackend:
    if renderer == "static_layer":
        return FakeBackend()
    if renderer == "reference_single_pass":
        return ReferenceRenderBackend()
    raise ValueError(f"unknown renderer {renderer!r}")


def _golden_actions() -> list[dict[str, int]]:
    return list(json.loads(GOLDEN_FIXTURE.read_text(encoding="utf-8"))["actions"])


def _random_stream(env: PixelGuiEnv, seed: int) -> Iterator[dict[str, Any]]:
    env.action_space.seed(seed)
    while True:
        yield env.action_space.sample()


def _golden_stream() -> Iterator[dict[str, int]]:
    actions = _golden_actions()
    while True:
        yield from actions


def _drive(
    env: PixelGuiEnv, actions: Iterator[dict[str, Any]], steps: int, *, seed: int
) -> Iterator[Any]:
    """Step `steps` times, resetting (with derived seeds) whenever an episode ends."""

    episode = 0
    env.reset(seed=seed)
    for _ in range(steps):
        observation, _reward, terminated, truncated, _info = env.step(next(actions))
        yield observation
        if terminated or truncated:
            episode += 1
            env.reset(seed=seed + episode)


def _stream(env: PixelGuiEnv, workload: str, seed: int) -> Iterator[dict[str, Any]]:
    if workload == "random":
        return _random_stream(env, seed)
    if workload == "golden":
        return _golden_stream()
    raise ValueError(f"unknown workload {workload!r}")


def _seed_for(workload: str) -> int:
    # The golden fixture was recorded for seed 7; other seeds would make it an incorrect form.
    return 7 if workload == "golden" else 0


def time_steps(workload: str, renderer: str, steps: int) -> dict[str, Any]:
    env = PixelGuiEnv(_backend(renderer))
    seed = _seed_for(workload)
    started = time.perf_counter()
    for _observation in _drive(env, _stream(env, workload, seed), steps, seed=seed):
        pass
    seconds = time.perf_counter() - started
    env.close()
    return {"steps": steps, "seconds": seconds, "steps_per_second": steps / seconds}


def time_resets(renderer: str, resets: int) -> dict[str, Any]:
    env = PixelGuiEnv(_backend(renderer))
    latencies = []
    for seed in range(resets):
        started = time.perf_counter()
        env.reset(seed=seed)
        latencies.append(time.perf_counter() - started)
    env.close()
    ordered = sorted(latencies)
    return {
        "resets": resets,
        "median_ms": statistics.median(ordered) * 1e3,
        "p95_ms": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))] * 1e3,
        "max_ms": ordered[-1] * 1e3,
        "mean_ms": statistics.fmean(ordered) * 1e3,
    }


def time_vector(kind: str, num_envs: int, steps: int) -> dict[str, Any]:
    factories: list[Callable[[], Any]] = [lambda: gym.make(VENDOR_FORM_ID)] * num_envs
    vector: Any
    if kind == "sync":
        vector = gym.vector.SyncVectorEnv(factories)
    else:
        vector = gym.vector.AsyncVectorEnv(factories, context="spawn")
    try:
        vector.reset(seed=list(range(num_envs)))
        vector.action_space.seed(0)
        started = time.perf_counter()
        for _ in range(steps):
            vector.step(vector.action_space.sample())
        seconds = time.perf_counter() - started
    finally:
        vector.close()
    return {
        "num_envs": num_envs,
        "vector_steps": steps,
        "env_steps": steps * num_envs,
        "seconds": seconds,
        "env_steps_per_second": steps * num_envs / seconds,
    }


def frame_digests(workload: str, renderer: str, steps: int) -> list[str]:
    env = PixelGuiEnv(_backend(renderer))
    seed = _seed_for(workload)
    observation, _info = env.reset(seed=seed)
    digests = [hashlib.sha256(observation.tobytes()).hexdigest()]
    digests.extend(
        hashlib.sha256(frame.tobytes()).hexdigest()
        for frame in _drive(env, _stream(env, workload, seed), steps, seed=seed)
    )
    env.close()
    return digests


def byte_identity(workload: str, steps: int) -> dict[str, Any]:
    fast, reference = (frame_digests(workload, renderer, steps) for renderer in RENDERERS)
    mismatched = [index for index, (a, b) in enumerate(zip(fast, reference, strict=True)) if a != b]
    return {
        "workload": workload,
        "frames_compared": len(fast),
        "mismatched_frames": len(mismatched),
        "first_mismatch_index": mismatched[0] if mismatched else None,
        "frame_sequence_sha256": hashlib.sha256("".join(fast).encode()).hexdigest(),
    }


def _summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def _cpu_model() -> str:
    if sys.platform == "darwin":
        try:
            return subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return platform.processor() or platform.machine()


def measure(
    *,
    steps: int,
    repeats: int,
    resets: int,
    vector_envs: int,
    vector_steps: int,
    identity_steps: int,
) -> dict[str, Any]:
    workloads: dict[str, Any] = {}
    for workload in ("random", "golden"):
        workloads[workload] = {}
        for renderer in RENDERERS:
            runs = [time_steps(workload, renderer, steps) for _ in range(repeats)]
            workloads[workload][renderer] = {
                "runs": runs,
                "steps_per_second": _summary([run["steps_per_second"] for run in runs]),
            }
        workloads[workload]["speedup_median"] = (
            workloads[workload]["static_layer"]["steps_per_second"]["median"]
            / workloads[workload]["reference_single_pass"]["steps_per_second"]["median"]
        )
    reset = {
        renderer: [time_resets(renderer, resets) for _ in range(repeats)] for renderer in RENDERERS
    }
    vector = {
        kind: [time_vector(kind, vector_envs, vector_steps) for _ in range(repeats)]
        for kind in ("sync", "async")
    }
    identity = [byte_identity(workload, identity_steps) for workload in ("random", "golden")]
    return {
        "workloads": workloads,
        "reset_latency": reset,
        "vector": vector,
        "byte_identity": identity,
    }


def build_evidence(parameters: dict[str, int], measurements: dict[str, Any]) -> dict[str, Any]:
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "schema_version": SCHEMA_VERSION,
        "validator": "fake-backend-throughput",
        "generated_at": datetime.now(UTC).isoformat(),
        "git_revision": _git("rev-parse", "HEAD"),
        "worktree_clean": status == "",
        "command": " ".join([Path(sys.argv[0]).as_posix(), *sys.argv[1:]]),
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu": _cpu_model(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pillow": PIL.__version__,
            "gymnasium": gym.__version__,
        },
        "environment": {
            "id": VENDOR_FORM_ID,
            "backend": "fake",
            "observation_shape": [768, 1024, 3],
            "observation_dtype": "uint8",
        },
        "parameters": parameters,
        "scope_note": (
            "Wall-clock measurements on one host, single process unless stated. They describe "
            "the in-process fake backend only, not the OSWorld or browser path."
        ),
        **measurements,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--steps", type=int, default=3000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--resets", type=int, default=300)
    parser.add_argument("--vector-envs", type=int, default=8)
    parser.add_argument("--vector-steps", type=int, default=300)
    parser.add_argument("--identity-steps", type=int, default=3000)
    args = parser.parse_args(argv)
    parameters = {
        "steps": args.steps,
        "repeats": args.repeats,
        "resets": args.resets,
        "vector_envs": args.vector_envs,
        "vector_steps": args.vector_steps,
        "identity_steps": args.identity_steps,
    }
    evidence = build_evidence(parameters, measure(**{k: v for k, v in parameters.items()}))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if all(row["mismatched_frames"] == 0 for row in evidence["byte_identity"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
