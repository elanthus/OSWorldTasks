"""MemoryBackend checkpoint validation and task-factory restore."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from tests.support.grounding_v5 import advance

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("missing", ["seed", "stage_index", "repair_pending"])
def test_missing_checkpoint_field_is_rejected_before_state_change(missing: str) -> None:
    backend = MemoryBackend()
    try:
        backend.reset(5000)
        before = backend.checkpoint()
        value = json.loads(before)
        del value[missing]
        with pytest.raises(ValueError, match="missing required fields"):
            backend.restore(json.dumps(value).encode())
        assert backend.checkpoint() == before
    finally:
        backend.close()


def test_restore_uses_the_backend_task_factory() -> None:
    task = generate_memory_task(5000)
    stages = list(task.stages)
    stage = stages[5]
    stages[5] = replace(
        stage,
        controls=tuple(replace(c, control_id="custom-" + c.control_id) for c in stage.controls),
        target_control_id="custom-" + stage.target_control_id,
    )
    custom = replace(task, stages=tuple(stages))
    backend, restored = MemoryBackend(), MemoryBackend()
    backend.task_factory = restored.task_factory = lambda seed: custom
    try:
        backend.reset(5000)
        advance(backend, 5)
        backend.click(*backend.control_center(stages[5].target_control_id))
        checkpoint = backend.checkpoint()
        restored.restore(checkpoint)
        assert restored.checkpoint() == checkpoint
    finally:
        backend.close()
        restored.close()
