"""Tests for the behaviour-cloning experiment helpers and report (torch-dependent parts skip)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.fake import FakeBackend
from scripts import generate_bc_report as report
from scripts import run_bc_experiment as bc

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts/bc-experiment/results.json"


def test_preprocess_is_grayscale_2x_area_downsample() -> None:
    frame = np.zeros((768, 1024, 3), dtype=np.uint8)
    frame[0:2, 0:2] = (255, 255, 255)
    frame[2:4, 0:2, 0] = 255  # pure red: luma 76
    small = bc.preprocess(frame)
    assert small.shape == (384, 512) and small.dtype == np.uint8
    assert small[0, 0] == 255 and small[1, 0] == 76 and small[0, 1] == 0


@pytest.mark.parametrize(("x", "y"), [(0, 0), (15, 15), (16, 0), (768, 94), (1023, 767)])
def test_click_cell_round_trips_to_the_same_cell(x: int, y: int) -> None:
    cell = bc.click_cell(x, y)
    cx, cy = bc.cell_center(cell)
    assert bc.click_cell(cx, cy) == cell
    assert abs(cx - x) < bc.CELL and abs(cy - y) < bc.CELL


def test_demo_episode_records_pre_action_frames_and_oracle_actions() -> None:
    episode = bc.demo_episode(5)
    n = len(episode["action_type"])
    assert episode["frames"].shape == (n, 384, 512)
    clicks = episode["action_type"] == ActionType.CLICK
    keys = episode["action_type"] == ActionType.KEY
    assert clicks.any() and keys.any() and (clicks | keys).all()
    assert (episode["cell"][clicks] >= 0).all() and (episode["cell"][~clicks] == -1).all()
    assert (episode["key"][keys] >= 0).all() and (episode["key"][~keys] == -1).all()
    assert (episode["seed"] == 5).all()


def test_step_metrics_score_click_by_widget_not_exact_pixel() -> None:
    demos = bc.Demos(**{name: value for name, value in bc.demo_episode(5).items()})
    layout = FakeBackend()
    layout.reset(0)
    perfect = bc.step_metrics(
        demos,
        demos.action_type,
        np.where(demos.cell < 0, 0, demos.cell),
        np.where(demos.key < 0, 0, demos.key),
        layout.layout,
    )
    assert perfect["step_accuracy"] == 1.0
    # Shift every click by one cell to the right: still inside the 464 px-wide controls.
    shifted = np.where(demos.cell < 0, 0, demos.cell + 1)
    step = bc.step_metrics(
        demos, demos.action_type, shifted, np.where(demos.key < 0, 0, demos.key), layout.layout
    )
    assert step["click_target_accuracy"] > 0.5
    wrong_keys = np.full_like(demos.key, KEY_ALLOWLIST.index("~"))
    step = bc.step_metrics(
        demos,
        demos.action_type,
        np.where(demos.cell < 0, 0, demos.cell),
        wrong_keys,
        layout.layout,
    )
    assert step["key_accuracy"] < 0.05 and step["step_accuracy"] < 0.1


def test_decode_action_maps_cells_to_cell_centres_and_keys() -> None:
    click = bc.decode_action(int(ActionType.CLICK), bc.click_cell(768, 94), 7)
    assert click == {"action_type": 1, "x": 776, "y": 88, "key": 0}
    assert bc.decode_action(int(ActionType.KEY), 0, 7) == {
        "action_type": 2,
        "x": 0,
        "y": 0,
        "key": 7,
    }
    assert bc.decode_action(int(ActionType.NOOP), 3, 7)["action_type"] == 0


def test_fields_correct_counts_normalized_matches() -> None:
    backend = FakeBackend()
    backend.reset(9)
    assert bc.fields_correct(backend) == 1  # expedited False matches the unchecked default
    backend.install_form_values(backend.current_fields())
    assert bc.fields_correct(backend) == 7


def test_model_heads_have_the_declared_shapes() -> None:
    torch = pytest.importorskip("torch")
    model = bc.build_model(torch)
    type_logits, click_logits, key_logits = model(torch.zeros(2, 1, 384, 512))
    assert tuple(type_logits.shape) == (2, len(ActionType))
    assert tuple(click_logits.shape) == (2, bc.GRID_W * bc.GRID_H)
    assert tuple(key_logits.shape) == (2, len(KEY_ALLOWLIST))


def _fake_results() -> dict:
    def run(demos: int, seed: int, accuracy: float) -> dict:
        return {
            "demo_episodes": demos,
            "demo_frames": demos * 110,
            "training_seed": seed,
            "train_seconds": 60.0,
            "best_validation_step": 500,
            "best_validation_step_accuracy": accuracy,
            "validation_curve": [],
            "parameters": 3_376_938,
            "device": "mps",
            "teacher_forced": {
                "frames": 5501,
                "click_frames": 376,
                "key_frames": 5125,
                "action_type_accuracy": 0.99,
                "click_target_accuracy": 0.9,
                "key_accuracy": accuracy,
                "step_accuracy": accuracy,
            },
            "closed_loop": {
                "episodes": 50,
                "successes": 0,
                "mean_fields_correct": 1.5,
                "fields_total": 7,
                "mean_steps": 200.0,
                "episodes_with_any_submission": 3,
                "per_episode": [],
            },
        }

    return {
        "schema_version": report.SCHEMA_VERSION,
        "generated_at": "2026-11-12T00:00:00+00:00",
        "git_revision": "0" * 40,
        "worktree_clean": True,
        "command": "scripts/run_bc_experiment.py",
        "host": {
            "system": "Darwin",
            "machine": "arm64",
            "python": "3.12",
            "numpy": "1",
            "torch": "2",
        },
        "design": {
            "oracle": "tests.support.golden_solver.build_dynamic_solve_actions",
            "observation": "grayscale, 2x area-downsampled to 384x512 uint8",
            "train_pool_seeds": [0, 255],
            "validation_seeds": [256, 287],
            "test_seeds": [10000, 10049],
            "click_grid": [64, 48],
            "click_cell_pixels": 16,
            "max_episode_steps": 200,
            "steps": 6000,
            "batch_size": 64,
            "eval_every": 500,
            "optimizer": "AdamW lr=3e-4 weight_decay=1e-4",
            "checkpoint_selection": "best validation teacher-forced step accuracy",
        },
        "data": {
            "pool_frames": 1,
            "validation_frames": 2,
            "test_frames": 3,
            "generation_seconds": 1,
        },
        "runs": [run(4, 0, 0.2), run(4, 1, 0.4), run(16, 0, 0.6), run(16, 1, 0.8)],
        "total_seconds": 600.0,
        "scope_note": "Fake backend only.",
    }


def test_report_averages_training_seeds_and_lists_every_run() -> None:
    text = report.render(_fake_results())
    assert "| 4 | 440 | 99.0% | 90.0% | 30.0% | 30.0% | 0/100 | 1.50 |" in text
    assert "| 16 | 1760 | 99.0% | 90.0% | 70.0% | 70.0% | 0/100 | 1.50 |" in text
    assert text.count(" min |") == 4  # one row per run


def test_report_svg_plots_one_point_per_demo_count() -> None:
    svg = report.render_svg(_fake_results())
    assert svg.startswith("<svg") and svg.count("<polyline") == 4
    assert svg.count("<circle") == 4 * 4  # 4 metrics x 2 demo counts x 2 seeds


def test_report_rejects_an_unknown_schema() -> None:
    with pytest.raises(ValueError, match="expected pixelgym-bc-experiment-v1"):
        report.render({**_fake_results(), "schema_version": "other"})


@pytest.mark.skipif(not ARTIFACT.is_file(), reason="BC results not recorded yet")
def test_checked_in_report_and_curve_are_generated_from_the_checked_in_results() -> None:
    results = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert ARTIFACT.with_name("REPORT.md").read_text(encoding="utf-8") == report.render(results)
    assert ARTIFACT.with_name("curve.svg").read_text(encoding="utf-8") == report.render_svg(results)
    assert results["worktree_clean"] is True
    assert {run["demo_episodes"] for run in results["runs"]} == set(bc.DEMO_COUNTS)
    assert len(results["runs"]) == len(bc.DEMO_COUNTS) * len(bc.TRAINING_SEEDS)


@pytest.mark.skipif(not ARTIFACT.is_file(), reason="BC results not recorded yet")
def test_checked_in_closed_loop_fields_equal_the_untouched_form_score() -> None:
    """Pins what the checked-in closed-loop score means for the recorded run.

    In the recorded results no policy moved any test episode's fields score away from the score
    of the untouched form for that seed. If the experiment is re-run and a policy does edit
    fields, this test should be updated with the new artifact, not the other way round.
    """

    results = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    backend = FakeBackend()
    untouched = {}
    for seed in bc.TEST_SEEDS:
        backend.reset(seed)
        untouched[seed] = bc.fields_correct(backend)
    for run in results["runs"]:
        episodes = run["closed_loop"]["per_episode"]
        assert [episode["seed"] for episode in episodes] == list(bc.TEST_SEEDS)
        # The recorded mean must be consistent with the per-episode rows it summarizes.
        recorded = [episode["fields_correct"] for episode in episodes]
        assert run["closed_loop"]["mean_fields_correct"] == pytest.approx(
            sum(recorded) / len(recorded)
        )
        assert {episode["seed"]: episode["fields_correct"] for episode in episodes} == untouched


@pytest.mark.parametrize(
    ("seeds", "expected"),
    [
        ((0,), "one training seed per point"),
        ((0, 1), "two training seeds per point"),
        ((0, 1, 2), "three training seeds per point"),
    ],
)
def test_report_states_the_training_seed_count_from_the_runs(
    seeds: tuple[int, ...], expected: str
) -> None:
    results = _fake_results()
    template = results["runs"][0]
    results["runs"] = [
        {**template, "demo_episodes": n, "training_seed": s} for n in (4, 16) for s in seeds
    ]
    assert expected in report.render(results)


@pytest.mark.parametrize("count", [0, len(bc.TRAIN_POOL) + 1])
def test_cli_rejects_demo_counts_outside_the_training_pool(count: int) -> None:
    with pytest.raises(SystemExit) as raised:
        bc.main(["--demo-counts", "4", str(count)])
    assert raised.value.code == 2


def test_demo_cache_key_changes_when_producer_code_changes(tmp_path: Path) -> None:
    for entry in bc.DEMO_PRODUCERS:
        target = tmp_path / entry
        if target.suffix == ".py":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# {entry}\n")
        else:
            target.mkdir(parents=True)
            (target / "render.py").write_text("WIDTH = 1\n")
    before = bc.producer_fingerprint(tmp_path)
    assert bc.producer_fingerprint(tmp_path) == before
    (tmp_path / "pixelgym/tasks/vendor_form/render.py").write_text("WIDTH = 2\n")
    assert bc.producer_fingerprint(tmp_path) != before


def test_generated_demos_equal_the_episodes_in_seed_order() -> None:
    seeds = [3, 5]
    demos = bc.generate_demos(seeds, processes=1)
    episodes = [bc.demo_episode(seed) for seed in seeds]
    for name in episodes[0]:
        assert np.array_equal(getattr(demos, name), np.concatenate([e[name] for e in episodes]))
    assert bc.subset(demos, seeds) is demos
    partial = bc.subset(demos, [5])
    assert partial is not demos and set(partial.seed.tolist()) == {5}
    assert np.array_equal(partial.frames, episodes[1]["frames"])
