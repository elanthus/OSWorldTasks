"""Tests for the fake-backend throughput evidence scripts (tiny real measurements, no network)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from pixelgym.backends.fake import FakeBackend
from pixelgym.env import PixelGuiEnv
from scripts.generate_throughput_report import render
from scripts.measure_fake_throughput import (
    SCHEMA_VERSION,
    _golden_actions,
    action_type_counts,
    build_evidence,
    byte_identity,
    frame_digests,
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


def test_byte_identity_pass_compares_the_first_reset_and_every_step(tiny_evidence: dict) -> None:
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


def test_identity_frames_include_the_reset_after_an_episode_ends() -> None:
    # A random policy never submits a correct form, so the episode truncates at step 200.
    digests = frame_digests("random", "static_layer", 200)
    assert len(digests) == 1 + 200 + 1
    observation, _info = PixelGuiEnv(FakeBackend()).reset(seed=1)
    assert digests[-1] == hashlib.sha256(observation.tobytes()).hexdigest()


def test_every_golden_episode_replays_the_recorded_trajectory_on_seed_7() -> None:
    steps = len(_golden_actions())  # the fixture's steps; the last one succeeds
    episode = 1 + steps  # reset frame plus every step
    digests = frame_digests("golden", "static_layer", 2 * steps)
    assert len(digests) == 2 * episode + 1
    assert digests[:episode] == digests[episode : 2 * episode]
    assert digests[-1] == digests[0]


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


@pytest.mark.parametrize("folder", [ARTIFACT.parent, ROOT / "artifacts/throughput-v2"])
def test_checked_in_report_is_generated_from_the_checked_in_evidence(folder: Path) -> None:
    evidence = json.loads((folder / "fake-throughput.json").read_text(encoding="utf-8"))
    assert (folder / "REPORT.md").read_text(encoding="utf-8") == render(evidence)
    assert evidence["worktree_clean"] is True
    assert all(row["mismatched_frames"] == 0 for row in evidence["byte_identity"])


def test_re_measurement_records_the_action_types_and_an_ancestor_revision() -> None:
    folder = ROOT / "artifacts/throughput-v2"
    evidence = json.loads((folder / "fake-throughput.json").read_text(encoding="utf-8"))
    for workload in ("random", "golden"):
        types = evidence["workloads"][workload]["action_types"]
        assert sum(types["counts"].values()) == types["steps"], workload
        assert types["steps"] == evidence["parameters"]["steps"], workload
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", evidence["git_revision"], "HEAD"],
        cwd=ROOT,
        check=False,
    )
    assert ancestor.returncode == 0
    load = json.loads((folder / "host-load.json").read_text(encoding="utf-8"))
    assert [sample["when"] for sample in load["samples"]] == ["before", "after"]


def test_action_type_counts_cover_every_step_and_report_the_noop_share() -> None:
    random_types = action_type_counts("random", 300)
    golden_types = action_type_counts("golden", 300)

    for types in (random_types, golden_types):
        assert sum(types["counts"].values()) == types["steps"] == 300
        assert types["noop_share"] == types["counts"]["NOOP"] / 300
    # The golden fixture has one leading NOOP per episode.
    assert golden_types["counts"]["NOOP"] == -(-300 // len(_golden_actions()))
