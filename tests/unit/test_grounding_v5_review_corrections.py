"""CodeRabbit regressions: checkpoint validation, admission bounds, and provenance."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import CallCaps
from pixelgym.grounding.v5.journal import JournalEvent, V5AttemptJournal
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.panel_policy import GEMINI_STATEFUL
from pixelgym.grounding.v5.runner import ScriptedTransport
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    ScreenshotMemoryRunner,
    build_screenshot_policy_manifest,
)
from scripts import prepare_grounding_v5_memory as admission
from scripts.prepare_grounding_v5_review_corrections import (
    corrected_admission_summary,
    corrected_pilot_analysis,
)
from tests.unit.test_grounding_v5_memory import advance

ROOT = Path(__file__).resolve().parents[2]


def test_event_lookup_waits_for_another_threads_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Rollback(Exception):
        pass

    reader_reached = threading.Event()
    owner = threading.get_ident()
    results: list[JournalEvent | None] = []
    with closing(V5AttemptJournal(tmp_path / "isolation.sqlite")) as journal:
        lock = journal._lock

        class ObservedLock:
            def __enter__(self):
                if threading.get_ident() != owner:
                    reader_reached.set()
                lock.acquire()

            def __exit__(self, *args):
                lock.release()

        monkeypatch.setattr(journal, "_lock", ObservedLock())

        def read() -> None:
            try:
                results.append(journal.event("uncommitted"))
            finally:
                # Also release the writer if a broken reader skips the lock.
                reader_reached.set()

        worker = threading.Thread(target=read)
        try:
            with pytest.raises(Rollback), journal._write_transaction():
                journal._connection.execute(
                    "INSERT INTO events(event_key, kind, trial_id, step_index, payload) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("uncommitted", "fixture", "fixture", 0, b"{}"),
                )
                worker.start()
                assert reader_reached.wait(5)
                raise Rollback
        finally:
            worker.join(5)
        assert not worker.is_alive()
        assert results == [None]  # The other thread must never see the rolled-back row.


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


@pytest.mark.parametrize("retain", [True, False])
def test_oversized_horizon_rejected_before_reset_or_requests(tmp_path: Path, retain: bool) -> None:
    class MustNotReset(MemoryBackend):
        def reset(self, seed):
            raise AssertionError("preflight must reject before backend reset")

    task = replace(
        generate_memory_task(5000),
        optimal_low_level_actions=40,
        correction_slack=10,
        max_episode_steps=50,
    )
    manifest = build_screenshot_policy_manifest(
        ROOT, config=GEMINI_STATEFUL, code_revision="test", retain_screenshots=retain
    )
    transport = ScriptedTransport()
    with closing(V5AttemptJournal(tmp_path / "horizon.sqlite")) as journal:
        runner = ScreenshotMemoryRunner(
            manifest=manifest,
            journal=journal,
            policy=ScreenshotMemoryPolicy(GEMINI_STATEFUL, retain_screenshots=retain),
            transport=transport,
            approved_caps=CallCaps(50, 50, 0, 50),
        )
        with pytest.raises(ValueError, match="frozen screenshot capacity"):
            runner.run(trial_id="too-long", task=task, backend=MustNotReset())
        assert transport.model_requests == []
        assert journal.events() == ()
        runner._preflight(task, MustNotReset(), required_action_limit=32)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def manifest_checkout(tmp_path: Path) -> Path:
    # Real local Git state; no network, provider, or dependency installation.
    root = tmp_path / "source"
    root.mkdir()
    for name in ("panel_policy.py", "openrouter_policy.py", "runner.py", "screenshot_memory.py"):
        relative = Path("pixelgym/grounding/v5") / name
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, root / relative)
    for relative in (Path("pyproject.toml"), Path("requirements/platform-py312.lock")):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, root / relative)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )
    return root


@pytest.mark.parametrize("staged", [True, False])
def test_dirty_source_rejected_by_manifest_and_admission(
    manifest_checkout: Path, monkeypatch: pytest.MonkeyPatch, staged: bool
) -> None:
    root = manifest_checkout
    path = root / "pixelgym/grounding/v5/screenshot_memory.py"
    path.write_text(path.read_text() + "\n# tracked edit\n")
    if staged:
        git(root, "add", str(path.relative_to(root)))
    with pytest.raises(ValueError, match="tracked worktree must be clean"):
        build_screenshot_policy_manifest(
            root, config=GEMINI_STATEFUL, code_revision="fixture", retain_screenshots=True
        )
    monkeypatch.setattr(admission, "ROOT", root)
    monkeypatch.setattr(admission, "OUTPUT", root / "new-evidence")
    # A missing source file would fail hashing first if the guard were too late.
    monkeypatch.setattr(admission, "SOURCE_PATHS", ("missing-file",))
    with pytest.raises(ValueError, match="tracked worktree must be clean"):
        admission.build()
    assert not (root / "new-evidence").exists()


def test_clean_source_and_untracked_output_are_allowed(manifest_checkout: Path) -> None:
    root = manifest_checkout
    (root / "untracked-output.json").write_text("{}")
    manifest = build_screenshot_policy_manifest(
        root, config=GEMINI_STATEFUL, code_revision="fixture", retain_screenshots=True
    )
    assert manifest.dirty_worktree_policy == "reject-tracked-changes"


def test_pilot_correction_separates_analysis_control_and_wire_counts() -> None:
    summary = json.loads(
        (ROOT / "artifacts/grounding-v5-d58-calibration-pilot/summary.json").read_text()
    )
    result = corrected_pilot_analysis(summary)
    assert "provider_calls" not in result
    assert result["provider_calls_by_analysis"] == result["provider_control_requests"] == 0
    assert result["wire_requests_sent"] == 20
    assert result["remaining_aggregate_ceiling_usd"] == "4.80991250"
    summary["provider_control_requests"] = 3
    summary["spend"]["wire_requests_sent"] = 23
    changed = corrected_pilot_analysis(summary)
    assert changed["provider_control_requests"] == 3 and changed["wire_requests_sent"] == 23


def test_report_denominators_come_from_stored_task_and_choice_rows() -> None:
    value = {
        "generator_version": "fixture",
        "tasks": [{"seed": 1}, {"seed": 2}, {"seed": 3}, {"seed": 101}],
        "counterfactuals": [{"base_seed": seed} for seed in (1, 2, 3)],
        "baselines": {"fixture-rule": [{"choices": [1, 2]}, {"choices": [1]}]},
        "summary": {
            "admitted_task_count": 4,
            "counterfactual_pair_count": 3,
            "memory_target_positions": {"0": 3, "1": 2, "2": 1},
            "baselines": {"fixture-rule": {"first_attempt_correct": 2, "successes": 1}},
        },
    }
    value["summary"] = corrected_admission_summary(value)
    report = admission.render_report(value)
    assert "3 base development tasks (6 choices)" in report
    assert "| fixture-rule | 2 / 3 | 1 / 2 |" in report


@pytest.mark.parametrize("corruption", ["none", "hash", "report"])
def test_public_verifier_under_optimization(tmp_path: Path, corruption: str) -> None:
    directory = tmp_path / "bundle"
    original = ROOT / "artifacts/grounding-v5-d58-full-calibration"
    shutil.copytree(original, directory, ignore=shutil.ignore_patterns("__pycache__"))
    if corruption != "none":
        with (directory / "report.md").open("a") as stream:
            stream.write("\ncorrupted report\n")
    code = "from pathlib import Path\nfrom scripts import verify_grounding_v5_full_calibration as v\nimport json\n"
    code += f"v.DIRECTORY = Path({str(directory)!r})\n"
    if corruption == "report":
        code += "(v.DIRECTORY / 'files.json').write_text(json.dumps(v.hashes()))\n"
    code += "v.verify_public()\n"
    result = subprocess.run(
        [sys.executable, "-O", "-c", code], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if corruption == "none":
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "ValueError" in result.stderr
        assert (
            "hashes differ" if corruption == "hash" else "report does not match"
        ) in result.stderr


def test_journal_verifier_rejects_wrong_amendment_under_optimization(tmp_path: Path) -> None:
    journal = tmp_path / "present.sqlite"
    journal.touch()
    code = (
        "from pathlib import Path\nfrom scripts import verify_grounding_v5_full_calibration as v\n"
    )
    code += f"v.JOURNAL = Path({str(journal)!r})\n"
    code += "v.canonical_plan = lambda: {}\nv.execution_amendment = lambda *args: {'wrong': True}\nv.audit_journal()\n"
    result = subprocess.run(
        [sys.executable, "-O", "-c", code], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "ValueError: execution amendment differs from the approved record" in result.stderr
