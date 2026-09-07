"""Captured-state backend that executes v4b actions through ``PixelGuiEnv``."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from legacy.grounding.v4b_protocol import (
    V4B_HEIGHT,
    V4B_SEEDS,
    V4B_WIDTH,
    episode_for_seed,
    v4b_task_id,
)
from pixelgym.backends.base import Frame
from pixelgym.serialization import load_jsonl
from pixelgym.task_spec import Submission


class V4BReplayBackend:
    """A deterministic finite-state GUI backend backed by captured browser frames."""

    width = V4B_WIDTH
    height = V4B_HEIGHT
    app_url = "pixelgym://grounding-v4b-replay"

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        states = load_jsonl(self.repository_root / "artifacts" / "grounding-v4b-pilot-states.jsonl")
        candidates = load_jsonl(
            self.repository_root / "artifacts" / "grounding-v4b-pilot-candidates.jsonl"
        )
        self._states = {row["state_id"]: row for row in states}
        self._candidates = {row["state_id"]: row["candidates"] for row in candidates}
        if len(self._states) != len(states) or len(self._candidates) != len(candidates):
            raise ValueError("duplicate v4b state or candidate record")
        self._seed: int | None = None
        self._stage = 0
        self._recovery = False
        self._action_count = 0
        self._submissions: list[Submission] = []
        self._closed = False

    @property
    def state_id(self) -> str:
        if self._seed is None:
            raise RuntimeError("v4b backend has not been reset")
        return f"v4b-{self._seed}-s{self._stage}-{'recovery' if self._recovery else 'main'}"

    def reset(self, seed: int) -> Mapping[str, Any]:
        if seed not in V4B_SEEDS:
            raise ValueError("seed outside v4b pilot")
        if self._closed:
            raise RuntimeError("v4b backend is closed")
        self._seed = seed
        self._stage = 0
        self._recovery = False
        self._action_count = 0
        self._submissions = []
        return {
            "task_id": v4b_task_id(seed),
            "seed": seed,
            "fields": {"workflow_result": f"completed-{seed}"},
        }

    def screenshot(self) -> Frame:
        record = self._states.get(self.state_id)
        if record is None:
            raise RuntimeError(f"unknown captured v4b state {self.state_id!r}")
        path = self.repository_root / record["image_path"]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != record["image_sha256"]:
            raise ValueError("captured v4b screenshot digest mismatch")
        with Image.open(path) as image:
            import numpy as np

            frame = np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
        if hashlib.sha256(frame.tobytes()).hexdigest() != record["pixel_sha256"]:
            raise ValueError("captured v4b pixel digest mismatch")
        return frame

    def _record_wrong_action(self) -> None:
        self._action_count += 1
        if self._stage < 3:
            self._recovery = True

    def noop(self) -> None:
        self._record_wrong_action()

    def key(self, key: str) -> None:
        del key
        self._record_wrong_action()

    def click(self, x: int, y: int) -> None:
        if self._seed is None or self._stage >= 3:
            raise RuntimeError("click outside an active v4b episode")
        self._action_count += 1
        clicked_id = None
        for candidate in self._candidates[self.state_id]:
            x0, y0, x1, y1 = candidate["bbox"]
            if x0 <= x < x1 and y0 <= y < y1:
                clicked_id = candidate["semantic_id"]
                break
        episode = episode_for_seed(self._seed)
        target = episode["stages"][self._stage]["target"]
        if clicked_id != target:
            self._recovery = True
            return
        self._stage += 1
        self._recovery = False
        if self._stage == 3:
            self._submissions.append(
                Submission(
                    task_id=v4b_task_id(self._seed),
                    seed=self._seed,
                    values={"workflow_result": f"completed-{self._seed}"},
                    submitted_at_step=self._action_count,
                )
            )

    def read_submissions(self) -> Sequence[Submission]:
        return tuple(self._submissions)

    def close(self) -> None:
        self._closed = True
