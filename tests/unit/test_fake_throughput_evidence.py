"""Tests for the fake-backend throughput evidence scripts (tiny real measurements, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.generate_throughput_report import render
from scripts.measure_fake_throughput import (
    SCHEMA_VERSION,
    build_evidence,
    byte_identity,
    measure,
    time_steps,
)

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts/throughput/fake-throughput.json"
REPORT = ROOT / "artifacts/throughput/REPORT.md"


@pytest.fixture(scope="module")
def tiny_evidence() -> dict:
    parameters = {
        "steps": 20,
        "repeats": 2,
        "resets": 5,
        "vector_envs": 2,
        "vector_steps": 3,
        "identity_steps": 40,
    }
    return build_evidence(parameters, measure(**parameters))


def test_evidence_records_every_repeat_host_and_revision(tiny_evidence: dict) -> None:
    assert tiny_evidence["schema_version"] == SCHEMA_VERSION
    assert {"cpu", "python", "numpy", "pillow", "gymnasium"} <= set(tiny_evidence["host"])
    assert len(tiny_evidence["git_revision"]) == 40
    for workload in ("random", "golden"):
        for renderer in ("static_layer", "reference_single_pass"):
            assert len(tiny_evidence["workloads"][workload][renderer]["runs"]) == 2
    assert [len(tiny_evidence["vector"][kind]) for kind in ("sync", "async")] == [2, 2]
    assert tiny_evidence["vector"]["async"][0]["env_steps"] == 6


def test_byte_identity_pass_compares_every_frame_including_resets(tiny_evidence: dict) -> None:
    rows = {row["workload"]: row for row in tiny_evidence["byte_identity"]}
    assert rows["random"]["frames_compared"] == 41
    assert rows["golden"]["frames_compared"] == 41
    assert all(row["mismatched_frames"] == 0 for row in rows.values())


def test_byte_identity_detects_a_renderer_difference(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.measure_fake_throughput as measure_module

    def drifting_reference(record, form, layout):
        frame = measure_module.render_reference(record, form, layout).copy()
        frame[0, 0, 0] ^= 1
        return frame

    monkeypatch.setattr(
        measure_module.ReferenceRenderBackend,
        "screenshot",
        lambda self: drifting_reference(self.task_record, self.form, self.layout),
    )

    row = byte_identity("random", 5)

    assert row["mismatched_frames"] == row["frames_compared"] == 6
    assert row["first_mismatch_index"] == 0


def test_golden_workload_replays_the_seed_7_fixture() -> None:
    run = time_steps("golden", "static_layer", 250)  # crosses at least one episode boundary
    assert run["steps"] == 250 and run["steps_per_second"] > 0


def test_report_renders_from_stored_evidence_only(tiny_evidence: dict) -> None:
    text = render(json.loads(json.dumps(tiny_evidence)))
    assert "# Fake-backend throughput" in text
    assert "| random | 41 | 0 |" in text
    assert "`AsyncVectorEnv` (spawn)" in text


def test_report_rejects_an_unknown_schema(tiny_evidence: dict) -> None:
    with pytest.raises(ValueError, match="expected pixelgym-fake-throughput-v1"):
        render({**tiny_evidence, "schema_version": "other"})


def test_checked_in_report_is_generated_from_the_checked_in_evidence() -> None:
    evidence = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert REPORT.read_text(encoding="utf-8") == render(evidence)
    assert evidence["worktree_clean"] is True
    assert all(row["mismatched_frames"] == 0 for row in evidence["byte_identity"])
