"""Fast tests for behaviour cloning v2: splits, click labels, baselines, gates, report, and a tiny
training and rollout run (torch-dependent tests skip without the ``train`` extra)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pixelgym.actions import ActionType
from scripts import generate_bc_report_v2 as report
from scripts import run_bc_experiment_v2 as bc

ROOT = Path(__file__).resolve().parents[2]
SMALL = ROOT / "artifacts/bc-experiment-v2/small"


@pytest.fixture(scope="module")
def targets() -> list[Any]:
    return bc.cell_targets(bc.layout())


@pytest.fixture(scope="module")
def demo() -> bc.Demos:
    return bc.generate_demos([3], processes=1)


def test_name_holdout_split_keeps_held_names_out_of_training_and_validation() -> None:
    held = bc.held_out_names()
    assert len(bc.all_company_names()) == 48 and len(held) == 12
    seeds = bc.split_seeds("name_holdout")
    assert len(seeds["train"]) == bc.CONFIG.train_pool_size
    assert len(seeds["test"]) == bc.CONFIG.test_size
    assert not {bc.company_name(s) for s in seeds["train"] + seeds["validation"]} & held
    assert {bc.company_name(s) for s in seeds["test"]} <= held
    assert not set(seeds["train"]) & (set(seeds["validation"]) | set(seeds["test"]))


def test_standard_split_overlap_is_measured_not_assumed() -> None:
    overlap = bc.name_overlap("standard")
    assert overlap["distinct_names_total"] == 48
    assert overlap["test_episodes"] == bc.CONFIG.test_size
    assert overlap["test_distinct_names_in_train"] <= overlap["test_distinct_names"]


def test_click_label_covers_every_cell_on_the_oracle_control(
    demo: bc.Demos, targets: list[Any]
) -> None:
    masks = bc.click_label_masks(demo, targets)
    form_layout = bc.layout()
    clicks = np.flatnonzero(demo.action_type == ActionType.CLICK)
    assert clicks.size
    for index in clicks:
        oracle = form_layout.hit_test(*(int(v) for v in demo.click_xy[index]))
        cells = np.flatnonzero(masks[index])
        assert cells.size >= 1
        assert all(targets[cell] == oracle for cell in cells)
        assert sum(t == oracle for t in targets) == cells.size
    assert not masks[demo.action_type != ActionType.CLICK].any()


def test_lookup_table_replays_its_own_episode_closed_loop(demo: bc.Demos) -> None:
    table, conflicts = bc.lookup_table(demo)
    assert conflicts == 0
    result = bc.closed_loop(
        lambda frame: table.get(bc.frame_digest(frame), (int(ActionType.NOOP), 0, 0)), [3]
    )
    assert result["successes"] == 1


def test_lookup_table_counts_a_frame_that_precedes_two_actions(demo: bc.Demos) -> None:
    # Negative control: duplicate one frame under a different action.
    doubled = bc.Demos(
        frames=np.concatenate([demo.frames, demo.frames[:1]]),
        action_type=np.concatenate([demo.action_type, [int(ActionType.NOOP)]]),
        cell=np.concatenate([demo.cell, [-1]]),
        key=np.concatenate([demo.key, [-1]]),
        click_xy=np.concatenate([demo.click_xy, [[-1, -1]]]),
        seed=np.concatenate([demo.seed, demo.seed[:1]]),
    )
    assert bc.lookup_table(doubled)[1] == 1


def test_baselines_bracket_the_task(demo: bc.Demos, targets: list[Any]) -> None:
    result = bc.baselines(demo, demo, [3], targets)
    assert result["oracle"]["teacher_forced"]["step_accuracy"] == 1.0
    assert result["lookup_table"]["teacher_forced"]["step_accuracy"] == 1.0
    assert result["always_key"]["closed_loop"]["successes"] == 0
    card_blind = result["card_blind"]["teacher_forced"]
    assert card_blind["action_type_accuracy"] == 1.0
    assert card_blind["click_target_accuracy"] == 0.0


def test_report_rejects_an_unknown_schema() -> None:
    with pytest.raises(ValueError, match="expected pixelgym-bc-experiment-v2"):
        report.render({"schema_version": "other"})


STORED = [ROOT / "artifacts/bc-experiment-v2" / name for name in ("small", "small-2", "")]


def test_at_least_one_v2_run_is_stored() -> None:
    assert (SMALL / "results.json").is_file()


@pytest.mark.parametrize("folder", STORED, ids=lambda f: f.name or "grid")
def test_checked_in_report_is_generated_from_its_results(folder: Path) -> None:
    if not (folder / "results.json").is_file():
        pytest.skip(f"{folder.name or 'grid'} run not stored yet")
    results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    assert (folder / "REPORT.md").read_text(encoding="utf-8") == report.render(results)
    assert results["worktree_clean"] is True


@pytest.mark.parametrize("folder", STORED, ids=lambda f: f.name or "grid")
def test_limits_section_states_only_computed_values_or_design(folder: Path) -> None:
    if not (folder / "results.json").is_file():
        pytest.skip(f"{folder.name or 'grid'} run not stored yet")
    text = report.render(json.loads((folder / "results.json").read_text(encoding="utf-8")))
    limits = text.split("## Limits", 1)[1].strip().splitlines()
    for line in limits:
        assert line.startswith("- "), line
        assert (
            line.startswith("- Design: ")
            or any(ch.isdigit() for ch in line)
            or ("card-blind baseline" in line or "did not run" in line)
        ), line


def test_limits_disclose_that_the_lookup_gate_uses_test_seeds() -> None:
    text = report.render(json.loads((SMALL / "results.json").read_text(encoding="utf-8")))
    limits = text.split("## Limits", 1)[1]
    assert "only for the reported numbers" not in limits
    assert "lookup-table sanity gate, which builds its table from the test demonstrations" in limits


def test_git_metadata_comes_from_the_repository_wherever_the_script_is_launched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # tmp_path is outside any checkout; without cwd=REPO_ROOT, git would fail or read another repo.
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=ROOT
    ).stdout.strip()
    monkeypatch.chdir(tmp_path)
    assert bc._git("rev-parse", "HEAD") == expected


def test_refused_grid_stores_gate_results_apart_and_keeps_results_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stored = tmp_path / "results.json"
    stored.write_text('{"stored": "grid run"}\n', encoding="utf-8")
    refused = {"grid_refused": True, "gates": {"all_passed": False}, "runs": []}
    monkeypatch.setattr(bc, "run", lambda phase, output_dir, processes: refused)
    with pytest.raises(SystemExit, match=r"gate results in .*gates-refused\.json"):
        bc.main(["grid", "--output-dir", str(tmp_path)])
    assert json.loads((tmp_path / "gates-refused.json").read_text(encoding="utf-8")) == refused
    assert stored.read_text(encoding="utf-8") == '{"stored": "grid run"}\n'


def test_a_phase_whose_gates_pass_writes_results_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    done = {"grid_refused": False, "gates": {"all_passed": True}, "runs": []}
    monkeypatch.setattr(bc, "run", lambda phase, output_dir, processes: done)
    assert bc.main(["gates", "--output-dir", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "results.json").read_text(encoding="utf-8")) == done
    assert not (tmp_path / "gates-refused.json").exists()


# -- torch ---------------------------------------------------------------------------------------


def test_model_outputs_one_logit_per_click_cell() -> None:
    torch = pytest.importorskip("torch")
    model = bc.build_model(torch)
    frames = torch.zeros(2, 1, bc.CONFIG.observation_height, bc.CONFIG.observation_width)
    type_logits, click_logits, key_logits = model(frames)
    assert type_logits.shape == (2, len(ActionType))
    assert click_logits.shape == (2, bc.CONFIG.grid_width * bc.CONFIG.grid_height)
    assert key_logits.shape[0] == 2


def test_tiny_training_run_and_closed_loop_rollout(demo: bc.Demos, targets: list[Any]) -> None:
    torch = pytest.importorskip("torch")
    model, info = bc.train_model(
        torch, demo, demo, targets, training_seed=0, steps=3, batch_size=8, eval_every=3
    )
    assert info["best_validation_step"] == 3 and info["steps"] == 3
    episode = bc.rollout(bc.model_policy(torch, model, bc.device(torch)), 3)
    assert episode["steps"] >= 1 and isinstance(episode["success"], bool)
