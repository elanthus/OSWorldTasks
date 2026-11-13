#!/usr/bin/env python3
"""Behaviour cloning from the scripted oracle on the fake vendor form, version 2.

Successor to ``scripts/run_bc_experiment.py`` (kept unchanged for ``artifacts/bc-experiment/``).
The full configuration is ``CONFIG`` below and is written into every results file. Nothing is
tuned against test seeds: checkpoints are selected on validation seeds only, and there is no
hyperparameter search.

What changed from version 1, and why:

- **Click head.** Version 1 predicted the click cell with a 1x1 convolution on stem features (a
  17-pixel receptive field), so it could not see which fields were filled. Here the click branch
  sees the stem features, two coordinate channels, and the trunk's global feature vector broadcast
  to every cell, followed by dilated convolutions. The policy is still pixel-only.
- **Key head.** A linear layer on the global vector could not read characters off the request
  card. The key branch attends over the 96x128 feature map (one cell is about one character),
  steered by the global vector, and predicts the key from the attended value and the global vector.
  Added after the first small run (``artifacts/bc-experiment-v2/small/``), whose gates failed.
- **Click label.** Version 1 trained on the single oracle cell. Here the target is uniform over
  every grid cell whose centre lands on the same control (and option) as the oracle's click, the
  same rule the click metric uses.
- **Sanity gates** run before any training run and are stored: overfit 30 click frames, replay one
  demonstration closed-loop on its own seed, and a frame-to-action lookup table on the test seeds.
- **Baselines** are reported beside every metric: always-KEY, constant click, card-blind keys,
  lookup table, and the oracle.
- **Name-holdout split.** There are only 48 company names (8 prefixes x 6 suffixes), so every
  standard test name also occurs in training. A second split holds 12 full names out of training
  and validation and tests only on them.
- **Reproducibility.** Device and versions are recorded, per-frame test predictions are stored, and
  the selected weights are saved under ``.cache/`` with their SHA-256 recorded.

Phases: ``gates`` runs the sanity gates; ``small`` runs the gates, baselines, and ``CONFIG.small``
on the standard split; ``grid`` runs the gates, baselines, and every demo count and training seed
on both splits, and refuses to start if a gate fails.
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
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.env import PixelGuiEnv
from pixelgym.tasks.vendor_form import generator
from pixelgym.tasks.vendor_form.normalization import normalize_submitted_values
from tests.support.golden_solver import build_dynamic_solve_actions

SCHEMA_VERSION = "pixelgym-bc-experiment-v2"
OUTPUT_DIR = Path("artifacts/bc-experiment-v2")
CACHE_DIR = Path(".cache/bc-experiment-v2")


@dataclass(frozen=True)
class SmallConfig:
    split: str = "standard"
    demo_count: int = 16
    training_seed: int = 0
    steps: int = 2000


@dataclass(frozen=True)
class Config:
    # Observation: grayscale, 2x area-downsampled.
    observation_height: int = 384
    observation_width: int = 512
    # Click grid over the 1024x768 screen.
    cell_pixels: int = 16
    max_episode_steps: int = 200
    # Splits. Standard: train pool 0.., validation 256.., test 10000...
    train_pool_size: int = 256
    validation_size: int = 32
    test_size: int = 50
    standard_train_start: int = 0
    standard_validation_start: int = 256
    standard_test_start: int = 10_000
    # Name holdout: every 4th name in sorted order is held out of training and validation.
    holdout_every: int = 4
    holdout_train_start: int = 0
    holdout_validation_start: int = 50_000
    holdout_test_start: int = 20_000
    # Grid.
    demo_counts: tuple[int, ...] = (4, 16, 64, 256)
    training_seeds: tuple[int, ...] = (0, 1)
    # Optimisation (fixed; no search).
    steps: int = 6000
    batch_size: int = 64
    eval_every: int = 500
    learning_rate: float = 3e-4
    weight_decay: float = 1e-4
    # Model widths.
    global_dim: int = 256
    click_context_dim: int = 32
    click_branch_channels: int = 64
    click_dilations: tuple[int, ...] = (1, 2, 4)
    key_context_dim: int = 32
    key_branch_channels: int = 64
    key_dilations: tuple[int, ...] = (1, 2)
    key_value_dim: int = 128
    # Sanity gates. Budgets raised once after the first small run (400 and 1500 steps), where
    # both training gates under-fit; set by the owner's decision, not against test seeds.
    gate_overfit_frames: int = 30
    gate_overfit_steps: int = 2000
    gate_overfit_min_correct: int = 29
    gate_single_demo_seed: int = 0
    gate_single_demo_steps: int = 5000
    # The first configuration run and shown before the grid.
    small: SmallConfig = field(default_factory=SmallConfig)

    @property
    def grid_width(self) -> int:
        return 1024 // self.cell_pixels

    @property
    def grid_height(self) -> int:
        return 768 // self.cell_pixels


CONFIG = Config()
SPLITS = ("standard", "name_holdout")


# -- Splits ------------------------------------------------------------------------------------


def company_name(seed: int) -> str:
    return str(generator.generate_task(seed)["fields"]["company_name"])


def all_company_names() -> list[str]:
    prefixes = generator._COMPANY_PREFIXES
    suffixes = generator._COMPANY_SUFFIXES
    return sorted(f"{prefix} {suffix}" for prefix in prefixes for suffix in suffixes)


def held_out_names(config: Config = CONFIG) -> frozenset[str]:
    return frozenset(all_company_names()[:: config.holdout_every])


def _seeds_where(start: int, count: int, keep: Callable[[str], bool]) -> tuple[int, ...]:
    seeds: list[int] = []
    seed = start
    while len(seeds) < count:
        if keep(company_name(seed)):
            seeds.append(seed)
        seed += 1
    return tuple(seeds)


def split_seeds(split: str, config: Config = CONFIG) -> dict[str, tuple[int, ...]]:
    if split == "standard":
        return {
            "train": tuple(
                range(
                    config.standard_train_start,
                    config.standard_train_start + config.train_pool_size,
                )
            ),
            "validation": tuple(
                range(
                    config.standard_validation_start,
                    config.standard_validation_start + config.validation_size,
                )
            ),
            "test": tuple(
                range(config.standard_test_start, config.standard_test_start + config.test_size)
            ),
        }
    if split == "name_holdout":
        held = held_out_names(config)
        return {
            "train": _seeds_where(
                config.holdout_train_start, config.train_pool_size, lambda n: n not in held
            ),
            "validation": _seeds_where(
                config.holdout_validation_start, config.validation_size, lambda n: n not in held
            ),
            "test": _seeds_where(config.holdout_test_start, config.test_size, lambda n: n in held),
        }
    raise ValueError(f"unknown split {split!r}")


def name_overlap(split: str, config: Config = CONFIG) -> dict[str, Any]:
    """How many distinct test names (and test episodes) also occur in the training pool."""

    seeds = split_seeds(split, config)
    train_names = {company_name(seed) for seed in seeds["train"]}
    test_names = [company_name(seed) for seed in seeds["test"]]
    return {
        "distinct_names_total": len(all_company_names()),
        "train_distinct_names": len(train_names),
        "test_distinct_names": len(set(test_names)),
        "test_distinct_names_in_train": len(set(test_names) & train_names),
        "test_episodes_with_name_in_train": sum(name in train_names for name in test_names),
        "test_episodes": len(test_names),
    }


# -- Observations, demonstrations, and click targets (no torch) --------------------------------


def preprocess(frame: np.ndarray, config: Config = CONFIG) -> np.ndarray:
    """RGB 768x1024 uint8 -> grayscale 384x512 uint8 (Rec. 601 luma, 2x2 area mean)."""

    luma = (
        frame[..., 0].astype(np.uint32) * 299
        + frame[..., 1].astype(np.uint32) * 587
        + frame[..., 2].astype(np.uint32) * 114
    ) // 1000
    h, w = config.observation_height, config.observation_width
    return (luma.reshape(h, 2, w, 2).sum(axis=(1, 3)) // 4).astype(np.uint8)


def click_cell(x: int, y: int, config: Config = CONFIG) -> int:
    return (y // config.cell_pixels) * config.grid_width + (x // config.cell_pixels)


def cell_center(cell: int, config: Config = CONFIG) -> tuple[int, int]:
    size = config.cell_pixels
    return (cell % config.grid_width) * size + size // 2, (
        cell // config.grid_width
    ) * size + size // 2


def layout() -> Any:
    backend = FakeBackend()
    backend.reset(0)
    return backend.layout


def cell_targets(form_layout: Any, config: Config = CONFIG) -> list[Any]:
    """The control (and option) each grid cell's centre lands on; ``None`` for empty space."""

    cells = config.grid_width * config.grid_height
    return [form_layout.hit_test(*cell_center(cell, config)) for cell in range(cells)]


@dataclass(frozen=True)
class Demos:
    frames: np.ndarray  # (n, H, W) uint8
    action_type: np.ndarray  # (n,) int64
    cell: np.ndarray  # (n,) int64, -1 unless CLICK
    key: np.ndarray  # (n,) int64, -1 unless KEY
    click_xy: np.ndarray  # (n, 2) int64, oracle click pixel or -1
    seed: np.ndarray  # (n,) int64 episode seed per frame

    def __len__(self) -> int:
        return int(self.action_type.shape[0])


def demo_episode(seed: int) -> dict[str, np.ndarray]:
    backend = FakeBackend()
    env = PixelGuiEnv(backend, max_episode_steps=CONFIG.max_episode_steps)
    observation, _info = env.reset(seed=seed)
    frames, types, cells, keys, xys = [], [], [], [], []
    reward = 0.0
    for action in build_dynamic_solve_actions(backend):
        plain = {name: int(value) for name, value in action.items()}
        frames.append(preprocess(observation))
        types.append(plain["action_type"])
        is_click = plain["action_type"] == ActionType.CLICK
        cells.append(click_cell(plain["x"], plain["y"]) if is_click else -1)
        keys.append(plain["key"] if plain["action_type"] == ActionType.KEY else -1)
        xys.append((plain["x"], plain["y"]) if is_click else (-1, -1))
        observation, reward, _terminated, _truncated, _info = env.step(plain)
    if reward != 1.0:
        raise RuntimeError(f"oracle failed to solve seed {seed}")
    return {
        "frames": np.stack(frames),
        "action_type": np.asarray(types, dtype=np.int64),
        "cell": np.asarray(cells, dtype=np.int64),
        "key": np.asarray(keys, dtype=np.int64),
        "click_xy": np.asarray(xys, dtype=np.int64).reshape(-1, 2),
        "seed": np.full(len(types), seed, dtype=np.int64),
    }


def generate_demos(seeds: Sequence[int], *, processes: int) -> Demos:
    if processes <= 1:
        episodes = [demo_episode(seed) for seed in seeds]
    else:
        context = multiprocessing.get_context("spawn")
        with context.Pool(processes) as pool:
            episodes = pool.map(demo_episode, list(seeds))
    return Demos(**{name: np.concatenate([e[name] for e in episodes]) for name in episodes[0]})


DEMO_PRODUCERS = (
    "pixelgym/actions.py",
    "pixelgym/env.py",
    "pixelgym/backends/fake.py",
    "pixelgym/tasks/vendor_form",
    "tests/support/golden_solver.py",
    "scripts/run_bc_experiment_v2.py",
)


def producer_fingerprint(root: Path = REPO_ROOT) -> str:
    """SHA-256 over the demonstration-producing files (source and fonts), in path order."""

    digest = hashlib.sha256()
    for entry in DEMO_PRODUCERS:
        path = root / entry
        files = (
            sorted(f for f in path.rglob("*") if f.is_file() and "__pycache__" not in f.parts)
            if path.is_dir()
            else [path]
        )
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
            return Demos(**{k: stored[k] for k in stored.files})
    demos = generate_demos(seeds, processes=processes)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(path, **demos.__dict__)
    return demos


def subset(demos: Demos, seeds: Sequence[int]) -> Demos:
    mask = np.isin(demos.seed, np.asarray(seeds))
    return Demos(**{name: value[mask] for name, value in demos.__dict__.items()})


def click_label_masks(demos: Demos, targets: list[Any]) -> np.ndarray:
    """(n, cells) bool: the cells whose centre lands on the oracle click's control and option."""

    form_layout = layout()
    by_target: dict[Any, np.ndarray] = {}
    masks = np.zeros((len(demos), len(targets)), dtype=bool)
    for index in np.flatnonzero(demos.action_type == ActionType.CLICK):
        target = form_layout.hit_test(*(int(v) for v in demos.click_xy[index]))
        if target is None:
            raise RuntimeError(f"oracle click at {demos.click_xy[index]} lands on empty space")
        if target not in by_target:
            by_target[target] = np.asarray([t == target for t in targets], dtype=bool)
            if not by_target[target].any():
                raise RuntimeError(f"no grid cell centre lands on {target}")
        masks[index] = by_target[target]
    return masks


# -- Metrics ---------------------------------------------------------------------------------


def step_metrics(
    demos: Demos,
    predicted_type: np.ndarray,
    predicted_cell: np.ndarray,
    predicted_key: np.ndarray,
    targets: list[Any],
) -> dict[str, Any]:
    """Teacher-forced accuracy on the oracle's own states, split by action component."""

    form_layout = layout()
    type_ok = predicted_type == demos.action_type
    clicks = demos.action_type == ActionType.CLICK
    keys = demos.action_type == ActionType.KEY
    click_ok = np.zeros(len(demos), dtype=bool)
    for index in np.flatnonzero(clicks):
        oracle = form_layout.hit_test(*(int(v) for v in demos.click_xy[index]))
        click_ok[index] = targets[int(predicted_cell[index])] == oracle
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
        return {"action_type": int(ActionType.CLICK), "x": x, "y": y, "key": 0}
    if action_type == ActionType.KEY:
        return {"action_type": int(ActionType.KEY), "x": 0, "y": 0, "key": key}
    return {"action_type": int(ActionType.NOOP), "x": 0, "y": 0, "key": 0}


FramePolicy = Callable[[np.ndarray], tuple[int, int, int]]


def rollout(policy: FramePolicy, seed: int) -> dict[str, Any]:
    """Closed-loop episode: the policy sees only the preprocessed frame."""

    backend = FakeBackend()
    env = PixelGuiEnv(backend, max_episode_steps=CONFIG.max_episode_steps)
    observation, _info = env.reset(seed=seed)
    steps, success = 0, False
    while True:
        observation, reward, terminated, truncated, _info = env.step(
            decode_action(*policy(preprocess(observation)))
        )
        steps += 1
        if terminated or truncated:
            success = bool(terminated and reward == 1.0)
            break
    return {
        "seed": seed,
        "success": success,
        "steps": steps,
        "fields_correct": fields_correct(backend),
        "submissions": len(backend.read_submissions()),
    }


def closed_loop(policy: FramePolicy, seeds: Sequence[int]) -> dict[str, Any]:
    episodes = [rollout(policy, seed) for seed in seeds]
    return {
        "episodes": len(episodes),
        "successes": sum(e["success"] for e in episodes),
        "mean_fields_correct": float(np.mean([e["fields_correct"] for e in episodes])),
        "fields_total": 7,
        "mean_steps": float(np.mean([e["steps"] for e in episodes])),
        "episodes_with_any_submission": sum(e["submissions"] > 0 for e in episodes),
        "per_episode": episodes,
    }


# -- Baselines (no torch) ------------------------------------------------------------------------


def frame_digest(frame: np.ndarray) -> str:
    return hashlib.sha256(frame.tobytes()).hexdigest()


def lookup_table(demos: Demos) -> tuple[dict[str, tuple[int, int, int]], int]:
    """Frame digest -> oracle action, and the number of digests seen with two different actions."""

    table: dict[str, tuple[int, int, int]] = {}
    conflicts: set[str] = set()
    for index in range(len(demos)):
        digest = frame_digest(demos.frames[index])
        action = (
            int(demos.action_type[index]),
            max(int(demos.cell[index]), 0),
            max(int(demos.key[index]), 0),
        )
        if table.setdefault(digest, action) != action:
            conflicts.add(digest)
    return table, len(conflicts)


def _episodes(demos: Demos) -> Iterator[np.ndarray]:
    """Frame indices per episode, in recorded order."""

    boundaries = np.flatnonzero(np.diff(demos.seed)) + 1
    yield from np.split(np.arange(len(demos)), boundaries)


def _typing_contexts(demos: Demos, form_layout: Any) -> list[tuple[Any, tuple[int, ...]] | None]:
    """For each KEY frame, (control last clicked, keys typed since); ``None`` for other frames."""

    contexts: list[tuple[Any, tuple[int, ...]] | None] = [None] * len(demos)
    for episode in _episodes(demos):
        control: Any = None
        typed: list[int] = []
        for index in episode:
            if demos.action_type[index] == ActionType.CLICK:
                control = form_layout.hit_test(*(int(v) for v in demos.click_xy[index]))
                typed = []
            elif demos.action_type[index] == ActionType.KEY:
                contexts[index] = (control, tuple(typed))
                typed.append(int(demos.key[index]))
    return contexts


def card_blind_predictions(train: Demos, test: Demos, targets: list[Any]) -> tuple[np.ndarray, ...]:
    """Oracle action types; keys from the training continuation of what is already typed; and a
    click on empty space for every click, so every click counts as wrong. Uses privileged form
    state (which control is focused and what has been typed), never the request card."""

    form_layout = layout()
    continuations: dict[Any, Counter[int]] = defaultdict(Counter)
    for index, context in enumerate(_typing_contexts(train, form_layout)):
        if context is not None:
            continuations[context][int(train.key[index])] += 1
    overall = Counter(int(k) for k in train.key[train.key >= 0]).most_common(1)[0][0]
    empty_cell = next(cell for cell, target in enumerate(targets) if target is None)
    keys = np.zeros(len(test), dtype=np.int64)
    for index, context in enumerate(_typing_contexts(test, form_layout)):
        if context is not None:
            seen = continuations.get(context)
            keys[index] = seen.most_common(1)[0][0] if seen else overall
    return test.action_type.copy(), np.full(len(test), empty_cell, dtype=np.int64), keys


def baselines(
    train: Demos, test: Demos, test_seeds: Sequence[int], targets: list[Any]
) -> dict[str, Any]:
    most_key = Counter(int(k) for k in train.key[train.key >= 0]).most_common(1)[0][0]
    most_cell = Counter(int(c) for c in train.cell[train.cell >= 0]).most_common(1)[0][0]
    n = len(test)

    def constant(action_type: int, cell: int, key: int) -> dict[str, Any]:
        tf = step_metrics(
            test,
            np.full(n, action_type),
            np.full(n, cell),
            np.full(n, key),
            targets,
        )
        return {
            "teacher_forced": tf,
            "closed_loop": closed_loop(lambda _frame: (action_type, cell, key), test_seeds),
        }

    table, conflicts = lookup_table(test)

    def lookup_policy(frame: np.ndarray) -> tuple[int, int, int]:
        return table.get(frame_digest(frame), (int(ActionType.NOOP), 0, 0))

    table_predictions = np.asarray([table[frame_digest(f)] for f in test.frames])
    return {
        "always_key": {
            "definition": "KEY with the most frequent training key on every frame",
            "key": KEY_ALLOWLIST[most_key],
            **constant(int(ActionType.KEY), 0, most_key),
        },
        "constant_click": {
            "definition": "CLICK on the most frequent training click cell on every frame",
            "cell": most_cell,
            **constant(int(ActionType.CLICK), most_cell, 0),
        },
        "card_blind": {
            "definition": (
                "oracle action type; for KEY, the most frequent training continuation of the keys "
                "already typed into the clicked control; every click wrong. Uses privileged form "
                "state, never the request card, so it has no closed-loop rollout"
            ),
            "teacher_forced": step_metrics(
                test, *card_blind_predictions(train, test, targets), targets
            ),
        },
        "lookup_table": {
            "definition": "frame digest -> oracle action, built on the test demonstrations",
            "conflicting_frames": conflicts,
            "teacher_forced": step_metrics(
                test,
                table_predictions[:, 0],
                table_predictions[:, 1],
                table_predictions[:, 2],
                targets,
            ),
            "closed_loop": closed_loop(lookup_policy, test_seeds),
        },
        "oracle": {
            "definition": "the scripted oracle that produced the demonstrations",
            "teacher_forced": step_metrics(
                test, test.action_type, np.maximum(test.cell, 0), test.key, targets
            ),
            "closed_loop": {"episodes": len(test_seeds), "successes": len(test_seeds)},
        },
    }


# -- Model, training, and evaluation (torch) ----------------------------------------------------


def _torch() -> Any:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - optional extra
        raise SystemExit("install the 'train' extra: pip install -e '.[train]'") from exc
    return torch


def device(torch: Any) -> Any:
    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


def build_model(torch: Any, config: Config = CONFIG) -> Any:
    nn = torch.nn
    grid_h, grid_w = config.grid_height, config.grid_width

    class Policy(nn.Module):  # type: ignore[misc, name-defined]
        def __init__(self) -> None:
            super().__init__()
            self.stem_fine = nn.Sequential(
                nn.Conv2d(1, 32, 5, stride=2, padding=2),  # 192x256
                nn.ReLU(),
                nn.Conv2d(32, 64, 3, stride=2, padding=1),  # 96x128: one cell ~ one character
                nn.ReLU(),
            )
            self.stem = nn.Sequential(
                nn.Conv2d(64, 64, 3, stride=2, padding=1),  # 48x64 == click grid
                nn.ReLU(),
            )
            self.trunk = nn.Sequential(
                nn.Conv2d(64, 128, 3, stride=2, padding=1),  # 24x32
                nn.ReLU(),
                nn.Conv2d(128, 64, 3, stride=2, padding=1),  # 12x16
                nn.ReLU(),
                nn.Flatten(),
                nn.Linear(64 * (grid_h // 4) * (grid_w // 4), config.global_dim),
                nn.ReLU(),
            )
            self.type_head = nn.Linear(config.global_dim, len(ActionType))
            # Key branch: attention over the fine (96x128) feature map, steered by the global
            # vector, so the head can read one character of the request card or form; the
            # attended value and the global vector together predict the key.
            fine_in = 64 + 2 + config.key_context_dim
            self.key_context = nn.Linear(config.global_dim, config.key_context_dim)
            key_layers: list[Any] = []
            in_channels = fine_in
            for dilation in config.key_dilations:
                key_layers += [
                    nn.Conv2d(
                        in_channels,
                        config.key_branch_channels,
                        3,
                        padding=dilation,
                        dilation=dilation,
                    ),
                    nn.ReLU(),
                ]
                in_channels = config.key_branch_channels
            key_layers.append(nn.Conv2d(config.key_branch_channels, 1, 1))
            self.key_attention = nn.Sequential(*key_layers)
            self.key_values = nn.Sequential(
                nn.Conv2d(64 + 2, config.key_value_dim, 3, padding=1), nn.ReLU()
            )
            self.key_head = nn.Linear(config.key_value_dim + config.global_dim, len(KEY_ALLOWLIST))
            fine_h, fine_w = 2 * grid_h, 2 * grid_w
            fy = (
                torch.linspace(-1.0, 1.0, fine_h).view(1, 1, fine_h, 1).expand(1, 1, fine_h, fine_w)
            )
            fx = (
                torch.linspace(-1.0, 1.0, fine_w).view(1, 1, 1, fine_w).expand(1, 1, fine_h, fine_w)
            )
            self.register_buffer("fine_coords", torch.cat([fx, fy], dim=1).contiguous())
            self.click_context = nn.Linear(config.global_dim, config.click_context_dim)
            channels = config.click_branch_channels
            layers: list[Any] = []
            in_channels = 64 + 2 + config.click_context_dim
            for dilation in config.click_dilations:
                layers += [
                    nn.Conv2d(in_channels, channels, 3, padding=dilation, dilation=dilation),
                    nn.ReLU(),
                ]
                in_channels = channels
            layers.append(nn.Conv2d(channels, 1, 1))
            self.click_branch = nn.Sequential(*layers)
            ys = (
                torch.linspace(-1.0, 1.0, grid_h).view(1, 1, grid_h, 1).expand(1, 1, grid_h, grid_w)
            )
            xs = (
                torch.linspace(-1.0, 1.0, grid_w).view(1, 1, 1, grid_w).expand(1, 1, grid_h, grid_w)
            )
            self.register_buffer("coords", torch.cat([xs, ys], dim=1).contiguous())

        def forward(self, frames: Any) -> tuple[Any, Any, Any]:
            fine = self.stem_fine(frames)
            features = self.stem(fine)
            pooled = self.trunk(features)
            batch = frames.shape[0]
            context = self.click_context(pooled)[:, :, None, None].expand(-1, -1, grid_h, grid_w)
            coords = self.coords.expand(batch, -1, -1, -1)
            click = self.click_branch(torch.cat([features, coords, context], dim=1)).flatten(1)
            fine_coords = self.fine_coords.expand(batch, -1, -1, -1)
            fine_h, fine_w = fine.shape[2], fine.shape[3]
            key_context = self.key_context(pooled)[:, :, None, None].expand(-1, -1, fine_h, fine_w)
            weights = torch.softmax(
                self.key_attention(torch.cat([fine, fine_coords, key_context], dim=1)).flatten(1),
                dim=1,
            )
            values = self.key_values(torch.cat([fine, fine_coords], dim=1)).flatten(2)
            read = torch.bmm(values, weights.unsqueeze(2)).squeeze(2)
            key = self.key_head(torch.cat([read, pooled], dim=1))
            return self.type_head(pooled), click, key

    return Policy()


def _batch(torch: Any, frames: np.ndarray, target_device: Any) -> Any:
    return torch.from_numpy(frames).to(target_device).unsqueeze(1).float().div_(255.0)


def predict(
    torch: Any, model: Any, frames: np.ndarray, target_device: Any
) -> tuple[np.ndarray, ...]:
    model.eval()
    types, cells, keys = [], [], []
    with torch.no_grad():
        for start in range(0, len(frames), 256):
            logits = model(_batch(torch, frames[start : start + 256], target_device))
            types.append(logits[0].argmax(1).cpu().numpy())
            cells.append(logits[1].argmax(1).cpu().numpy())
            keys.append(logits[2].argmax(1).cpu().numpy())
    model.train()
    return np.concatenate(types), np.concatenate(cells), np.concatenate(keys)


def model_policy(torch: Any, model: Any, target_device: Any) -> FramePolicy:
    def policy(frame: np.ndarray) -> tuple[int, int, int]:
        model.eval()
        with torch.no_grad():
            logits = model(_batch(torch, frame[None], target_device))
        return int(logits[0].argmax(1)), int(logits[1].argmax(1)), int(logits[2].argmax(1))

    return policy


def train_model(
    torch: Any,
    train: Demos,
    validation: Demos | None,
    targets: list[Any],
    *,
    training_seed: int,
    steps: int,
    batch_size: int,
    eval_every: int,
    config: Config = CONFIG,
) -> tuple[Any, dict[str, Any]]:
    """Train; with ``validation``, keep the checkpoint with the best validation step accuracy."""

    torch.manual_seed(training_seed)
    rng = np.random.default_rng(training_seed)
    target_device = device(torch)
    model = build_model(torch, config).to(target_device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    masks = click_label_masks(train, targets)
    label = masks / np.maximum(masks.sum(axis=1, keepdims=True), 1)
    tensors = {
        "type": torch.from_numpy(train.action_type).to(target_device),
        "key": torch.from_numpy(train.key).to(target_device),
        "label": torch.from_numpy(label.astype(np.float32)).to(target_device),
    }
    cross_entropy = torch.nn.CrossEntropyLoss()
    curve: list[dict[str, Any]] = []
    best_state, best = None, (-1.0, 0)
    started = time.perf_counter()
    for step in range(1, steps + 1):
        index = rng.integers(0, len(train), size=min(batch_size, len(train)))
        idx = torch.from_numpy(index).to(target_device)
        type_logits, click_logits, key_logits = model(
            _batch(torch, train.frames[index], target_device)
        )
        types = tensors["type"][idx]
        loss = cross_entropy(type_logits, types)
        is_click = types == int(ActionType.CLICK)
        if is_click.any():
            log_probs = torch.log_softmax(click_logits[is_click], dim=1)
            loss = loss - (tensors["label"][idx][is_click] * log_probs).sum(dim=1).mean()
        is_key = types == int(ActionType.KEY)
        if is_key.any():
            loss = loss + cross_entropy(key_logits[is_key], tensors["key"][idx][is_key])
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        if validation is not None and (step % eval_every == 0 or step == steps):
            metrics = step_metrics(
                validation, *predict(torch, model, validation.frames, target_device), targets
            )
            curve.append({"step": step, "loss": float(loss.item()), **metrics})
            if metrics["step_accuracy"] > best[0]:
                best = (metrics["step_accuracy"], step)
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    if best_state is not None:
        model.load_state_dict(best_state)
    return model, {
        "train_seconds": time.perf_counter() - started,
        "steps": steps,
        "final_loss": float(loss.item()),
        "best_validation_step": best[1] if validation is not None else None,
        "best_validation_step_accuracy": best[0] if validation is not None else None,
        "validation_curve": curve,
        "parameters": int(sum(p.numel() for p in model.parameters())),
        "device": str(target_device),
    }


def save_weights(torch: Any, model: Any, name: str) -> dict[str, Any]:
    path = CACHE_DIR / "weights" / f"{name}.pt"
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({k: v.detach().cpu() for k, v in model.state_dict().items()}, path)
    data = path.read_bytes()
    return {"path": path.as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


# -- Sanity gates ------------------------------------------------------------------------------


def run_gates(
    torch: Any, pool: Demos, tests: dict[str, tuple[Demos, tuple[int, ...]]], targets: list[Any]
) -> dict[str, Any]:
    config = CONFIG
    target_device = device(torch)

    # 1. Overfit the first 30 click frames of the standard training pool.
    click_index = np.flatnonzero(pool.action_type == ActionType.CLICK)[: config.gate_overfit_frames]
    clicks = Demos(**{name: value[click_index] for name, value in pool.__dict__.items()})
    model, training = train_model(
        torch,
        clicks,
        None,
        targets,
        training_seed=0,
        steps=config.gate_overfit_steps,
        batch_size=config.gate_overfit_frames,
        eval_every=config.gate_overfit_steps,
    )
    metrics = step_metrics(clicks, *predict(torch, model, clicks.frames, target_device), targets)
    correct = round((metrics["click_target_accuracy"] or 0.0) * len(clicks))
    overfit = {
        "frames": len(clicks),
        "click_targets_correct": correct,
        "min_correct": config.gate_overfit_min_correct,
        "training": training,
        "passed": correct >= config.gate_overfit_min_correct,
    }

    # 2. One demonstration, replayed closed-loop on its own seed.
    seed = config.gate_single_demo_seed
    demo = subset(pool, [seed])
    model, training = train_model(
        torch,
        demo,
        None,
        targets,
        training_seed=0,
        steps=config.gate_single_demo_steps,
        batch_size=config.batch_size,
        eval_every=config.gate_single_demo_steps,
    )
    episode = rollout(model_policy(torch, model, target_device), seed)
    single = {
        "seed": seed,
        "frames": len(demo),
        "teacher_forced": step_metrics(
            demo, *predict(torch, model, demo.frames, target_device), targets
        ),
        "closed_loop": episode,
        "training": training,
        "passed": episode["success"],
    }

    # 3. A frame-to-action lookup table on each split's test seeds.
    lookups = {}
    for split, (test, seeds) in tests.items():
        table, conflicts = lookup_table(test)
        result = closed_loop(
            lambda frame, t=table: t.get(frame_digest(frame), (int(ActionType.NOOP), 0, 0)), seeds
        )
        lookups[split] = {
            "conflicting_frames": conflicts,
            "successes": result["successes"],
            "episodes": result["episodes"],
            "passed": result["successes"] == result["episodes"],
        }
    return {
        "overfit_30_click_frames": overfit,
        "single_demo_closed_loop": single,
        "lookup_table_test_seeds": lookups,
        "all_passed": overfit["passed"]
        and single["passed"]
        and all(v["passed"] for v in lookups.values()),
    }


# -- Driver ------------------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def train_and_evaluate(
    torch: Any,
    split: str,
    pool: Demos,
    validation: Demos,
    test: Demos,
    test_seeds: tuple[int, ...],
    targets: list[Any],
    *,
    demo_count: int,
    training_seed: int,
    steps: int,
    output_dir: Path,
) -> dict[str, Any]:
    train_seeds = split_seeds(split)["train"][:demo_count]
    train = subset(pool, train_seeds)
    name = f"{split}-n{demo_count}-seed{training_seed}-steps{steps}"
    print(f"training {name} frames={len(train)}", flush=True)
    model, training = train_model(
        torch,
        train,
        validation,
        targets,
        training_seed=training_seed,
        steps=steps,
        batch_size=CONFIG.batch_size,
        eval_every=CONFIG.eval_every,
    )
    target_device = device(torch)
    predicted = predict(torch, model, test.frames, target_device)
    predictions_path = output_dir / "predictions" / f"{name}.json"
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    predictions_path.write_text(
        json.dumps(
            {
                "run": name,
                "action_type": predicted[0].tolist(),
                "cell": predicted[1].tolist(),
                "key": predicted[2].tolist(),
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    teacher_forced = step_metrics(test, *predicted, targets)
    loop = closed_loop(model_policy(torch, model, target_device), test_seeds)
    print(
        f"  test step {teacher_forced['step_accuracy']:.4f} "
        f"click {teacher_forced['click_target_accuracy']:.4f} "
        f"key {teacher_forced['key_accuracy']:.4f} "
        f"success {loop['successes']}/{loop['episodes']}",
        flush=True,
    )
    return {
        "name": name,
        "split": split,
        "demo_episodes": demo_count,
        "demo_frames": len(train),
        "training_seed": training_seed,
        **training,
        "weights": save_weights(torch, model, name),
        "predictions": predictions_path.as_posix(),
        "teacher_forced": teacher_forced,
        "closed_loop": loop,
    }


def run(phase: str, output_dir: Path, processes: int) -> dict[str, Any]:
    torch = _torch()
    started = time.perf_counter()
    # Recorded before anything runs, so edits made while a long run is in progress cannot change it.
    revision = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--untracked-files=no")
    targets = cell_targets(layout())
    splits = SPLITS if phase == "grid" else ("standard",)
    data: dict[str, dict[str, Any]] = {}
    for split in splits:
        seeds = split_seeds(split)
        data[split] = {
            "seeds": seeds,
            "pool": cached_demos(f"{split}-train", seeds["train"], processes=processes),
            "validation": cached_demos(
                f"{split}-validation", seeds["validation"], processes=processes
            ),
            "test": cached_demos(f"{split}-test", seeds["test"], processes=processes),
        }
    generation_seconds = time.perf_counter() - started
    tests = {split: (d["test"], d["seeds"]["test"]) for split, d in data.items()}
    gates = run_gates(torch, data["standard"]["pool"], tests, targets)
    print(f"gates all passed: {gates['all_passed']}", flush=True)

    output_dir.mkdir(parents=True, exist_ok=True)
    for split, d in data.items():
        test = d["test"]
        frames_path = output_dir / f"test-frames-{split}.json"
        frames_path.write_text(
            json.dumps(
                {
                    "seed": test.seed.tolist(),
                    "action_type": test.action_type.tolist(),
                    "cell": test.cell.tolist(),
                    "key": test.key.tolist(),
                    "click_xy": test.click_xy.tolist(),
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )

    split_baselines = {
        split: baselines(d["pool"], d["test"], d["seeds"]["test"], targets)
        for split, d in data.items()
    }
    runs: list[dict[str, Any]] = []
    if phase == "small":
        small = CONFIG.small
        d = data[small.split]
        runs.append(
            train_and_evaluate(
                torch,
                small.split,
                d["pool"],
                d["validation"],
                d["test"],
                d["seeds"]["test"],
                targets,
                demo_count=small.demo_count,
                training_seed=small.training_seed,
                steps=small.steps,
                output_dir=output_dir,
            )
        )
    elif phase == "grid":
        if not gates["all_passed"]:
            raise SystemExit(
                "a sanity gate failed; the grid does not start (see gates in the output)"
            )
        for split, d in data.items():
            for demo_count in CONFIG.demo_counts:
                for training_seed in CONFIG.training_seeds:
                    runs.append(
                        train_and_evaluate(
                            torch,
                            split,
                            d["pool"],
                            d["validation"],
                            d["test"],
                            d["seeds"]["test"],
                            targets,
                            demo_count=demo_count,
                            training_seed=training_seed,
                            steps=CONFIG.steps,
                            output_dir=output_dir,
                        )
                    )
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_revision": revision,
        "worktree_clean": status == "",
        "command": " ".join([Path(sys.argv[0]).as_posix(), *sys.argv[1:]]),
        "config": asdict(CONFIG),
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "torch": torch.__version__,
            "device": str(device(torch)),
            "mps_available": bool(torch.backends.mps.is_available()),
        },
        "data": {
            split: {
                "train_seeds": list(d["seeds"]["train"]),
                "validation_seeds": list(d["seeds"]["validation"]),
                "test_seeds": list(d["seeds"]["test"]),
                "pool_frames": len(d["pool"]),
                "validation_frames": len(d["validation"]),
                "test_frames": len(d["test"]),
                "name_overlap": name_overlap(split),
            }
            for split, d in data.items()
        },
        "held_out_names": sorted(held_out_names()),
        "generation_seconds": generation_seconds,
        "gates": gates,
        "baselines": split_baselines,
        "runs": runs,
        "total_seconds": time.perf_counter() - started,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", choices=("gates", "small", "grid"))
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--processes", type=int, default=8)
    args = parser.parse_args(argv)
    output_dir = args.output_dir or (OUTPUT_DIR / ("" if args.phase == "grid" else args.phase))
    results = run(args.phase, output_dir, args.processes)
    path = output_dir / "results.json"
    path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
