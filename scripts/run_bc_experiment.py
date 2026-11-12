#!/usr/bin/env python3
"""Behaviour cloning from the scripted oracle on the fake vendor form, as a function of demos.

Design (stated before the run):

- Oracle: ``tests.support.golden_solver.build_dynamic_solve_actions``, the privileged solver
  that fills and submits any seed's form with CLICK and KEY actions. It is privileged only at
  data-generation time; the policy sees pixels alone.
- Observations: the environment frame, converted to grayscale and 2x area-downsampled to
  384x512 uint8. Demonstrations are (frame before the action, oracle action) pairs.
- Seeds: demonstrations from the pool 0..255 (nested subsets of N episodes); validation seeds
  256..287 select the checkpoint; test seeds 10000..10049 are used only for the reported numbers.
- Model: a strided CNN with three heads -- action type (3-way), click cell on a 64x48 grid of
  16-pixel cells (predicted fully convolutionally from the 48x64 feature map), and key (one of
  the allowlisted keys). Cross-entropy losses, masked by the oracle's action type.
- Training: AdamW, fixed number of steps, best checkpoint by validation per-step accuracy.
- Metrics on test seeds: teacher-forced per-step accuracy on the oracle's own states (action
  type; click lands on the same widget/option as the oracle's; key), and closed-loop rollouts:
  episode success (terminated with reward 1.0), and fields correct at the end (host-side).

Writes ``artifacts/bc-experiment/results.json``; ``scripts/generate_bc_report.py`` renders it.
Demonstration arrays are cached under ``.cache/bc-experiment/`` (gitignored).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import platform
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    # Run as `python scripts/run_bc_experiment.py`, sys.path[0] is `scripts/`, so the repo root
    # (and with it `tests.support`) is not importable yet.
    sys.path.insert(0, str(REPO_ROOT))

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.env import PixelGuiEnv
from pixelgym.tasks.vendor_form.normalization import normalize_submitted_values
from tests.support.golden_solver import build_dynamic_solve_actions

SCHEMA_VERSION = "pixelgym-bc-experiment-v1"
DEFAULT_OUTPUT = Path("artifacts/bc-experiment/results.json")
CACHE_DIR = Path(".cache/bc-experiment")
HEIGHT, WIDTH = 384, 512  # observation after 2x downsampling of 768x1024
CELL = 16  # click-cell size in screen pixels
GRID_W, GRID_H = 1024 // CELL, 768 // CELL  # 64 x 48 click cells
TRAIN_POOL = tuple(range(256))
VALIDATION_SEEDS = tuple(range(256, 288))
TEST_SEEDS = tuple(range(10_000, 10_050))
DEMO_COUNTS = (4, 16, 64, 256)
TRAINING_SEEDS = (0, 1)
MAX_EPISODE_STEPS = 200


# -- Observations and action encoding (no torch) ---------------------------------------------


def preprocess(frame: np.ndarray) -> np.ndarray:
    """RGB 768x1024 uint8 -> grayscale 384x512 uint8 (Rec. 601 luma, 2x2 area mean)."""

    luma = (
        frame[..., 0].astype(np.uint32) * 299
        + frame[..., 1].astype(np.uint32) * 587
        + frame[..., 2].astype(np.uint32) * 114
    ) // 1000
    return (luma.reshape(HEIGHT, 2, WIDTH, 2).sum(axis=(1, 3)) // 4).astype(np.uint8)


def click_cell(x: int, y: int) -> int:
    return (y // CELL) * GRID_W + (x // CELL)


def cell_center(cell: int) -> tuple[int, int]:
    return (cell % GRID_W) * CELL + CELL // 2, (cell // GRID_W) * CELL + CELL // 2


@dataclass(frozen=True)
class Demos:
    frames: np.ndarray  # (n, 384, 512) uint8
    action_type: np.ndarray  # (n,) int64
    cell: np.ndarray  # (n,) int64, -1 unless CLICK
    key: np.ndarray  # (n,) int64, -1 unless KEY
    click_xy: np.ndarray  # (n, 2) int64, oracle click pixel (or -1)
    seed: np.ndarray  # (n,) int64 episode seed per frame

    def __len__(self) -> int:
        return int(self.action_type.shape[0])


def demo_episode(seed: int) -> dict[str, np.ndarray]:
    backend = FakeBackend()
    env = PixelGuiEnv(backend, max_episode_steps=MAX_EPISODE_STEPS)
    observation, _info = env.reset(seed=seed)
    actions = build_dynamic_solve_actions(backend)
    frames, types, cells, keys, xys = [], [], [], [], []
    reward = 0.0
    for action in actions:
        plain = {name: int(value) for name, value in action.items()}
        frames.append(preprocess(observation))
        types.append(plain["action_type"])
        is_click = plain["action_type"] == ActionType.CLICK
        is_key = plain["action_type"] == ActionType.KEY
        cells.append(click_cell(plain["x"], plain["y"]) if is_click else -1)
        keys.append(plain["key"] if is_key else -1)
        xys.append((plain["x"], plain["y"]) if is_click else (-1, -1))
        observation, reward, _terminated, _truncated, _info = env.step(plain)
    if reward != 1.0:
        raise RuntimeError(f"oracle failed to solve seed {seed}")
    return {
        "frames": np.stack(frames),
        "action_type": np.asarray(types, dtype=np.int64),
        "cell": np.asarray(cells, dtype=np.int64),
        "key": np.asarray(keys, dtype=np.int64),
        "click_xy": np.asarray(xys, dtype=np.int64),
        "seed": np.full(len(types), seed, dtype=np.int64),
    }


def generate_demos(seeds: Sequence[int], *, processes: int) -> Demos:
    context = multiprocessing.get_context("spawn")
    with context.Pool(processes) as pool:
        episodes: list[dict[str, np.ndarray] | None] = list(pool.map(demo_episode, list(seeds)))
    first = episodes[0]
    assert first is not None
    total = sum(len(episode["action_type"]) for episode in episodes if episode is not None)
    stacked = {
        name: np.empty((total, *value.shape[1:]), dtype=value.dtype)
        for name, value in first.items()
    }
    # Copy each episode into place and drop it, so the frames are held about once, not twice.
    offset = 0
    for index, episode in enumerate(episodes):
        assert episode is not None
        length = len(episode["action_type"])
        for name, value in episode.items():
            stacked[name][offset : offset + length] = value
        offset += length
        episodes[index] = None
    return Demos(**stacked)


# Code whose change alters the demonstrations; its contents are part of the cache key.
DEMO_PRODUCERS = (
    "pixelgym/actions.py",
    "pixelgym/env.py",
    "pixelgym/backends/fake.py",
    "pixelgym/tasks/vendor_form",
    "tests/support/golden_solver.py",
    "scripts/run_bc_experiment.py",
)


def producer_fingerprint(root: Path = REPO_ROOT) -> str:
    """SHA-256 over the demonstration-producing source files, in path order."""

    digest = hashlib.sha256()
    for entry in DEMO_PRODUCERS:
        path = root / entry
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for file in files:
            digest.update(file.relative_to(root).as_posix().encode() + b"\0")
            digest.update(file.read_bytes() + b"\0")
    return digest.hexdigest()


def cached_demos(name: str, seeds: Sequence[int], *, processes: int) -> Demos:
    key = [name, list(seeds), SCHEMA_VERSION, producer_fingerprint()]
    digest = hashlib.sha256(json.dumps(key).encode()).hexdigest()
    path = CACHE_DIR / f"{name}-{digest[:16]}.npz"
    if path.is_file():
        with np.load(path) as stored:
            return Demos(**{key: stored[key] for key in stored.files})
    demos = generate_demos(seeds, processes=processes)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(path, **demos.__dict__)
    return demos


def subset(demos: Demos, seeds: Sequence[int]) -> Demos:
    mask = np.isin(demos.seed, np.asarray(seeds))
    if mask.all():
        return demos  # every seed requested: reuse the arrays instead of copying them
    return Demos(**{name: value[mask] for name, value in demos.__dict__.items()})


def click_target(layout: Any, x: int, y: int) -> Any:
    """The widget (and payment option) a click lands on; `None` for empty space."""

    return layout.hit_test(x, y)


def step_metrics(
    demos: Demos,
    predicted_type: np.ndarray,
    predicted_cell: np.ndarray,
    predicted_key: np.ndarray,
    layout: Any,
) -> dict[str, Any]:
    """Teacher-forced accuracy on the oracle's states, split by action component."""

    type_ok = predicted_type == demos.action_type
    clicks = demos.action_type == ActionType.CLICK
    keys = demos.action_type == ActionType.KEY
    click_ok = np.zeros(len(demos), dtype=bool)
    for index in np.flatnonzero(clicks):
        x, y = cell_center(int(predicted_cell[index]))
        ox, oy = (int(v) for v in demos.click_xy[index])
        click_ok[index] = click_target(layout, x, y) == click_target(layout, ox, oy)
    key_ok = predicted_key == demos.key
    step_ok = type_ok & np.where(clicks, click_ok, np.where(keys, key_ok, True))
    return {
        "frames": len(demos),
        "click_frames": int(clicks.sum()),
        "key_frames": int(keys.sum()),
        "action_type_accuracy": float(type_ok.mean()),
        "click_target_accuracy": float(click_ok[clicks].mean()) if clicks.any() else None,
        "key_accuracy": float(key_ok[keys].mean()) if keys.any() else None,
        "step_accuracy": float(step_ok.mean()),
    }


def fields_correct(backend: FakeBackend) -> int:
    expected = normalize_submitted_values(backend.current_fields())
    actual = normalize_submitted_values(backend.form.values())
    return sum(actual[name] == value for name, value in expected.items())


def decode_action(action_type: int, cell: int, key: int) -> dict[str, int]:
    if action_type == ActionType.CLICK:
        x, y = cell_center(cell)
        return {"action_type": action_type, "x": x, "y": y, "key": 0}
    if action_type == ActionType.KEY:
        return {"action_type": action_type, "x": 0, "y": 0, "key": key}
    return {"action_type": int(ActionType.NOOP), "x": 0, "y": 0, "key": 0}


# -- Model, training, and evaluation (torch) --------------------------------------------------


def _torch() -> Any:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - optional extra
        raise SystemExit("install the 'train' extra: pip install -e '.[train]'") from exc
    return torch


def build_model(torch: Any) -> Any:
    nn = torch.nn

    class Policy(nn.Module):  # type: ignore[misc, name-defined]
        def __init__(self) -> None:
            super().__init__()
            self.stem = nn.Sequential(
                nn.Conv2d(1, 32, 5, stride=2, padding=2),  # 192x256
                nn.ReLU(),
                nn.Conv2d(32, 64, 3, stride=2, padding=1),  # 96x128
                nn.ReLU(),
                nn.Conv2d(64, 64, 3, stride=2, padding=1),  # 48x64 == click grid
                nn.ReLU(),
            )
            self.click_head = nn.Conv2d(64, 1, 1)
            self.trunk = nn.Sequential(
                nn.Conv2d(64, 128, 3, stride=2, padding=1),  # 24x32
                nn.ReLU(),
                nn.Conv2d(128, 64, 3, stride=2, padding=1),  # 12x16
                nn.ReLU(),
                nn.Flatten(),
                nn.Linear(64 * 12 * 16, 256),
                nn.ReLU(),
            )
            self.type_head = nn.Linear(256, len(ActionType))
            self.key_head = nn.Linear(256, len(KEY_ALLOWLIST))

        def forward(self, frames: Any) -> tuple[Any, Any, Any]:
            features = self.stem(frames)
            click = self.click_head(features).flatten(1)  # (batch, 48*64)
            pooled = self.trunk(features)
            return self.type_head(pooled), click, self.key_head(pooled)

    return Policy()


def _device(torch: Any) -> Any:
    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


def _batch(torch: Any, frames: np.ndarray, device: Any) -> Any:
    return torch.from_numpy(frames).to(device).unsqueeze(1).float().div_(255.0)


def predict(torch: Any, model: Any, frames: np.ndarray, device: Any) -> tuple[np.ndarray, ...]:
    model.eval()
    types, cells, keys = [], [], []
    with torch.no_grad():
        for start in range(0, len(frames), 256):
            logits = model(_batch(torch, frames[start : start + 256], device))
            types.append(logits[0].argmax(1).cpu().numpy())
            cells.append(logits[1].argmax(1).cpu().numpy())
            keys.append(logits[2].argmax(1).cpu().numpy())
    model.train()
    return np.concatenate(types), np.concatenate(cells), np.concatenate(keys)


def train_run(
    torch: Any,
    train: Demos,
    validation: Demos,
    *,
    training_seed: int,
    steps: int,
    batch_size: int,
    eval_every: int,
    layout: Any,
) -> tuple[Any, dict[str, Any]]:
    torch.manual_seed(training_seed)
    rng = np.random.default_rng(training_seed)
    device = _device(torch)
    model = build_model(torch).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-4)
    targets = {
        "type": torch.from_numpy(train.action_type).to(device),
        "cell": torch.from_numpy(train.cell).to(device),
        "key": torch.from_numpy(train.key).to(device),
    }
    loss_fn = torch.nn.CrossEntropyLoss(reduction="none")
    curve, best_state, best = [], None, (-1.0, 0)
    started = time.perf_counter()
    for step in range(1, steps + 1):
        index = rng.integers(0, len(train), size=batch_size)
        type_logits, click_logits, key_logits = model(_batch(torch, train.frames[index], device))
        idx = torch.from_numpy(index).to(device)
        types, cells, keys = targets["type"][idx], targets["cell"][idx], targets["key"][idx]
        loss = loss_fn(type_logits, types).mean()
        click_mask = types == int(ActionType.CLICK)
        if click_mask.any():
            loss = loss + loss_fn(click_logits[click_mask], cells[click_mask]).mean()
        key_mask = types == int(ActionType.KEY)
        if key_mask.any():
            loss = loss + loss_fn(key_logits[key_mask], keys[key_mask]).mean()
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        if step % eval_every == 0 or step == steps:
            metrics = step_metrics(
                validation, *predict(torch, model, validation.frames, device), layout
            )
            curve.append({"step": step, "loss": float(loss.item()), **metrics})
            if metrics["step_accuracy"] > best[0]:
                best = (metrics["step_accuracy"], step)
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    assert best_state is not None
    model.load_state_dict(best_state)
    return model, {
        "train_seconds": time.perf_counter() - started,
        "best_validation_step": best[1],
        "best_validation_step_accuracy": best[0],
        "validation_curve": curve,
        "parameters": int(sum(p.numel() for p in model.parameters())),
        "device": str(device),
    }


def rollout(torch: Any, model: Any, seed: int, device: Any) -> dict[str, Any]:
    backend = FakeBackend()
    env = PixelGuiEnv(backend, max_episode_steps=MAX_EPISODE_STEPS)
    observation, _info = env.reset(seed=seed)
    model.eval()
    steps, success = 0, False
    with torch.no_grad():
        while True:
            logits = model(_batch(torch, preprocess(observation)[None], device))
            action = decode_action(
                int(logits[0].argmax(1)), int(logits[1].argmax(1)), int(logits[2].argmax(1))
            )
            observation, reward, terminated, truncated, _info = env.step(action)
            steps += 1
            if terminated or truncated:
                success = terminated and reward == 1.0
                break
    model.train()
    return {
        "seed": seed,
        "success": success,
        "steps": steps,
        "fields_correct": fields_correct(backend),
        "submissions": len(backend.read_submissions()),
    }


def evaluate(torch: Any, model: Any, test: Demos, layout: Any) -> dict[str, Any]:
    device = _device(torch)
    teacher_forced = step_metrics(test, *predict(torch, model, test.frames, device), layout)
    episodes = [rollout(torch, model, seed, device) for seed in TEST_SEEDS]
    return {
        "teacher_forced": teacher_forced,
        "closed_loop": {
            "episodes": len(episodes),
            "successes": sum(episode["success"] for episode in episodes),
            "mean_fields_correct": float(np.mean([e["fields_correct"] for e in episodes])),
            "fields_total": 7,
            "mean_steps": float(np.mean([e["steps"] for e in episodes])),
            "episodes_with_any_submission": sum(e["submissions"] > 0 for e in episodes),
            "per_episode": episodes,
        },
    }


# -- Driver ----------------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def run(args: argparse.Namespace) -> dict[str, Any]:
    torch = _torch()
    started = time.perf_counter()
    pool = cached_demos("train-pool", TRAIN_POOL[: max(args.demo_counts)], processes=args.processes)
    validation = cached_demos("validation", VALIDATION_SEEDS, processes=args.processes)
    test = cached_demos("test", TEST_SEEDS, processes=args.processes)
    generation_seconds = time.perf_counter() - started
    layout = FakeBackend()
    layout.reset(0)
    runs = []
    for demo_count in args.demo_counts:
        train = subset(pool, TRAIN_POOL[:demo_count])
        for training_seed in args.training_seeds:
            print(f"training N={demo_count} seed={training_seed} frames={len(train)}", flush=True)
            model, training = train_run(
                torch,
                train,
                validation,
                training_seed=training_seed,
                steps=args.steps,
                batch_size=args.batch_size,
                eval_every=args.eval_every,
                layout=layout.layout,
            )
            evaluation = evaluate(torch, model, test, layout.layout)
            closed = evaluation["closed_loop"]
            print(
                f"  test step acc {evaluation['teacher_forced']['step_accuracy']:.4f} "
                f"success {closed['successes']}/{closed['episodes']} "
                f"fields {closed['mean_fields_correct']:.2f}/7",
                flush=True,
            )
            runs.append(
                {
                    "demo_episodes": demo_count,
                    "demo_frames": len(train),
                    "training_seed": training_seed,
                    **training,
                    **evaluation,
                }
            )
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_revision": _git("rev-parse", "HEAD"),
        "worktree_clean": status == "",
        "command": " ".join([Path(sys.argv[0]).as_posix(), *sys.argv[1:]]),
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "torch": torch.__version__,
        },
        "design": {
            "oracle": "tests.support.golden_solver.build_dynamic_solve_actions",
            "observation": "grayscale, 2x area-downsampled to 384x512 uint8",
            "train_pool_seeds": [TRAIN_POOL[0], TRAIN_POOL[-1]],
            "validation_seeds": [VALIDATION_SEEDS[0], VALIDATION_SEEDS[-1]],
            "test_seeds": [TEST_SEEDS[0], TEST_SEEDS[-1]],
            "click_grid": [GRID_W, GRID_H],
            "click_cell_pixels": CELL,
            "max_episode_steps": MAX_EPISODE_STEPS,
            "steps": args.steps,
            "batch_size": args.batch_size,
            "eval_every": args.eval_every,
            "optimizer": "AdamW lr=3e-4 weight_decay=1e-4",
            "checkpoint_selection": "best validation teacher-forced step accuracy",
        },
        "data": {
            "pool_frames": len(pool),
            "validation_frames": len(validation),
            "test_frames": len(test),
            "generation_seconds": generation_seconds,
        },
        "runs": runs,
        "total_seconds": time.perf_counter() - started,
        "scope_note": (
            "Behaviour cloning from a privileged scripted oracle on the in-process fake backend. "
            "No recovery data (no DAgger); one host; no hyperparameter search. Not a claim about "
            "OSWorld, browsers, or other tasks."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--demo-counts", type=int, nargs="+", default=list(DEMO_COUNTS))
    parser.add_argument("--training-seeds", type=int, nargs="+", default=list(TRAINING_SEEDS))
    parser.add_argument("--steps", type=int, default=6000)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--eval-every", type=int, default=500)
    parser.add_argument("--processes", type=int, default=8)
    args = parser.parse_args(argv)
    invalid = [count for count in args.demo_counts if not 1 <= count <= len(TRAIN_POOL)]
    if invalid:
        parser.error(f"--demo-counts must be between 1 and {len(TRAIN_POOL)}; got {invalid}")
    results = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
