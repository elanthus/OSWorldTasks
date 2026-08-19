from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from pixelgym.grounding.benchmark_v2 import (
    derive_records,
    target_for_seed_state,
    validate_image_artifacts,
)
from pixelgym.grounding.schema import SCREEN_STATES, TARGET_SPECS, TASK_SEEDS
from pixelgym.serialization import load_jsonl


def test_v2_allocation_crosses_every_target_with_every_screen_state() -> None:
    cells = Counter(
        (target_for_seed_state(seed, state).semantic_id, state)
        for seed in TASK_SEEDS
        for state in SCREEN_STATES
    )

    assert len(cells) == len(TARGET_SPECS) * len(SCREEN_STATES) == 50
    assert set(cells.values()) == {2}
    assert Counter(target for target, _state in cells.elements()) == Counter(
        {target.semantic_id: 10 for target in TARGET_SPECS}
    )


def test_v2_allocation_rejects_unknown_seed_and_state() -> None:
    with pytest.raises(ValueError, match="seed"):
        target_for_seed_state(20, SCREEN_STATES[0])
    with pytest.raises(ValueError, match="state"):
        target_for_seed_state(0, "dropdown_open")


def test_checked_in_v1_assets_derive_a_joined_balanced_v2() -> None:
    repository_root = Path(__file__).parents[2]
    artifact_root = repository_root / "artifacts"

    examples, candidates, overlays = derive_records(
        load_jsonl(artifact_root / "grounding-dataset.jsonl"),
        load_jsonl(artifact_root / "grounding-candidates.jsonl"),
        load_jsonl(artifact_root / "grounding-overlays.jsonl"),
    )

    assert len(examples) == len(candidates) == len(overlays) == 100
    assert len({row["image_path"] for row in examples}) == 100
    assert {row["protocol_version"] for row in examples + candidates + overlays} == {
        "pixelgym-grounding-v2"
    }
    assert all(row["target_proposed"] for row in overlays)


def test_v2_image_validation_rejects_changed_reused_pixels(tmp_path: Path) -> None:
    raw = tmp_path / "raw.png"
    marked = tmp_path / "marked.png"
    raw.write_bytes(b"raw-pixels")
    marked.write_bytes(b"marked-pixels")
    examples = [
        {
            "example_id": "example-1",
            "image_path": "raw.png",
            "image_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        }
    ]
    overlays = [
        {
            "example_id": "example-1",
            "marked_image_path": "marked.png",
            "marked_image_sha256": hashlib.sha256(marked.read_bytes()).hexdigest(),
        }
    ]
    validate_image_artifacts(examples, overlays, repository_root=tmp_path)

    marked.write_bytes(b"changed")
    with pytest.raises(ValueError, match="marked image digest"):
        validate_image_artifacts(examples, overlays, repository_root=tmp_path)
