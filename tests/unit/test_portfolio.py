from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from pixelgym.portfolio import generate_episode_gif


def _evidence(rewards: list[float]) -> dict:
    return {
        "backend_metadata": {
            "upstream_repository": "https://github.com/xlang-ai/OSWorld-V2",
            "upstream_tag": "v2026.06.24",
            "release": "osworld-v2-2026.06.24",
        },
        "trace": [
            {
                "step": index,
                "action": {"action_type": 1 if index == len(rewards) else 2},
                "reward": reward,
                "terminated": index == len(rewards),
            }
            for index, reward in enumerate(rewards, start=1)
        ]
    }


def _write_inputs(root: Path, evidence: dict) -> tuple[Path, Path]:
    evidence_path = root / "artifacts/day-2/raw/real-golden-episode.json"
    frames = root / "artifacts/day-2/real-golden/frames"
    evidence_path.parent.mkdir(parents=True)
    frames.mkdir(parents=True)
    evidence_path.write_text(json.dumps(evidence))
    Image.new("RGB", (100, 80), "white").save(frames / "step-000.png")
    for row in evidence["trace"]:
        Image.new("RGB", (100, 80), (240, 240 - row["step"], 240)).save(
            frames / f"step-{row['step']:03d}.png"
        )
    return evidence_path, frames


def test_demo_gif_is_reproducible_and_records_pending_human_review(tmp_path: Path) -> None:
    evidence_path, frames = _write_inputs(tmp_path, _evidence([0.0, 0.0, 1.0]))
    output = tmp_path / "artifacts/day-3/review/demo.gif"
    metadata = tmp_path / "artifacts/day-3/review/demo.json"
    first = generate_episode_gif(
        repository_root=tmp_path,
        evidence_path=evidence_path,
        frames_directory=frames,
        output_path=output,
        metadata_path=metadata,
        screenshot_size=(160, 90),
    )
    first_bytes = output.read_bytes()
    second = generate_episode_gif(
        repository_root=tmp_path,
        evidence_path=evidence_path,
        frames_directory=frames,
        output_path=output,
        metadata_path=metadata,
        screenshot_size=(160, 90),
    )

    assert first == second
    assert output.read_bytes() == first_bytes
    assert first["source_frame_count"] == 4
    assert first["duration_seconds"] == pytest.approx(3.56)
    assert first["positive_reward_count"] == 1
    assert first["credential_or_private_ui_review"] == "pending_human_review"
    assert first["approved_for_public_readme"] is False


def test_demo_rejects_nonterminal_or_repeated_positive_reward(tmp_path: Path) -> None:
    evidence_path, frames = _write_inputs(tmp_path, _evidence([1.0, 0.0, 1.0]))
    with pytest.raises(ValueError, match="exactly one positive reward"):
        generate_episode_gif(
            repository_root=tmp_path,
            evidence_path=evidence_path,
            frames_directory=frames,
            output_path=tmp_path / "demo.gif",
            metadata_path=tmp_path / "demo.json",
        )


def test_demo_rejects_trace_without_pinned_osworld_provenance(tmp_path: Path) -> None:
    evidence = _evidence([0.0, 1.0])
    evidence["backend_metadata"] = {"provider": "fake"}
    evidence_path, frames = _write_inputs(tmp_path, evidence)

    with pytest.raises(ValueError, match="not attributable.*OSWorld"):
        generate_episode_gif(
            repository_root=tmp_path,
            evidence_path=evidence_path,
            frames_directory=frames,
            output_path=tmp_path / "demo.gif",
            metadata_path=tmp_path / "demo.json",
        )
