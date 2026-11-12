"""The checked-in OSWorld guest checks are self-consistent and name their revisions.

2026-11-11 ran once at commit ``627e09b``. 2026-11-12 ran on merged ``main`` in three attempts;
the complete result combines attempt 1 (six review sequences) with attempt 3 (the golden replay
and the seventh sequence), and the incomplete attempts are kept.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIRST = ROOT / "artifacts/osworld-guest-check-2026-11-11"
MERGED = ROOT / "artifacts/osworld-guest-check-2026-11-12"
REVIEW_CASES = 7


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_ancestor(revision: str, of: str = "HEAD") -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", revision, of], cwd=ROOT, check=False
    )
    return result.returncode == 0


def _assert_golden_pays_once_at_the_terminal_step(golden: dict, episode: dict) -> None:
    assert golden["summary"] == {
        "first_positive_reward_step": 112,
        "passed": True,
        "positive_reward_count": 1,
        "provider_closed": True,
        "terminal_step": 112,
    }
    rewards = golden["rewards"]
    assert len(rewards) == 112 and rewards[-1] == 1.0 and sum(rewards) == 1.0
    assert [step["reward"] for step in episode["trace"]] == rewards
    subset = {row["name"]: row for row in episode["real_reward_timing_subset"]}
    assert subset["correct-fields-without-submit"]["reward"] == 0.0
    assert subset["correct-fields-without-submit"]["step"] == 111


def _assert_case_matches(name: str, case: dict) -> None:
    assert case["match"] is True, name
    assert case["guest_submissions"] == case["fake_submissions"], name
    assert all(step["reward"] == 0.0 for step in case["steps"]), name


def _assert_pinned(metadata: dict) -> None:
    assert metadata["upstream_tag"] == "v2026.06.24"
    assert metadata["provider"] == "docker-local"
    assert metadata["screen_size"] == [1024, 768]
    assert metadata["input_acknowledgement_barrier"] is True


def _assert_image_manifest(folder: Path) -> None:
    manifest = _json(folder / "images.manifest.json")
    assert manifest["file_count"] == len(manifest["files"]) == 114
    sheet = next(f for f in manifest["files"] if f["path"] == "contact-sheet.png")
    digest = hashlib.sha256((folder / "contact-sheet.png").read_bytes()).hexdigest()
    assert digest == sheet["sha256"]


# -- 2026-11-11, commit 627e09b ---------------------------------------------------------------


def test_first_run_golden_replay_pays_once_at_the_terminal_step() -> None:
    golden = _json(FIRST / "results.json")["golden"]
    _assert_golden_pays_once_at_the_terminal_step(golden, _json(FIRST / "real-golden-episode.json"))
    _assert_pinned(golden["backend_metadata"])


def test_first_run_every_review_sequence_submits_what_the_fake_submits() -> None:
    results = _json(FIRST / "results.json")
    assert len(results["cases"]) == REVIEW_CASES
    for name, case in results["cases"].items():
        _assert_case_matches(name, case)
    assert results["provider_closed"] is True


def test_first_run_provenance_names_its_revision_and_the_later_guest_change() -> None:
    provenance = _json(FIRST / "provenance.json")
    metadata = _json(FIRST / "results.json")["golden"]["backend_metadata"]
    assert provenance["guest_bundle_sha256_recorded"] == metadata["task_bundle_sha256"]
    assert (
        provenance["guest_bundle_sha256_rebuilt_at_git_revision"] == metadata["task_bundle_sha256"]
    )
    later = provenance["later_guest_changes"]
    assert later["guest_bundle_sha256_at_merge"] != metadata["task_bundle_sha256"]
    assert _is_ancestor(provenance["git_revision"]) and _is_ancestor(later["commit"])
    digest = hashlib.sha256((FIRST / "results.json").read_bytes()).hexdigest()
    assert digest == provenance["results_sha256"]
    _assert_image_manifest(FIRST)


# -- 2026-11-12, merged main ------------------------------------------------------------------


def test_merged_run_golden_replay_pays_once_at_the_terminal_step() -> None:
    golden = _json(MERGED / "attempt-3/results.json")["golden"]
    episode = _json(MERGED / "attempt-3/real-golden-episode.json")
    _assert_golden_pays_once_at_the_terminal_step(golden, episode)
    _assert_pinned(golden["backend_metadata"])
    _assert_image_manifest(MERGED / "attempt-3")


def test_merged_run_covers_all_seven_review_sequences_across_attempts() -> None:
    first = _json(MERGED / "attempt-1/results.json")["cases"]
    retry = _json(MERGED / "attempt-3/results.json")["cases"]
    failed = {name for name, case in first.items() if "error" in case}
    assert failed == {"caret click near left edge"}
    assert "VM failed to become ready" in first["caret click near left edge"]["error"]
    assert set(retry) == failed
    combined = {name: case for name, case in first.items() if name not in failed} | retry
    assert len(combined) == REVIEW_CASES
    for name, case in combined.items():
        _assert_case_matches(name, case)


def test_merged_run_keeps_the_incomplete_attempts() -> None:
    attempt_1 = _json(MERGED / "attempt-1/results.json")
    assert "VM failed to become ready" in attempt_1["golden"]["error"]
    attempt_2 = _json(MERGED / "attempt-2/results.json")
    assert attempt_2["selection"] == {"golden": True, "cases": ["caret click near left edge"]}
    assert "error" in attempt_2["golden"]
    assert all("error" in case for case in attempt_2["cases"].values())
    for attempt in ("attempt-1", "attempt-2", "attempt-3"):
        assert _json(MERGED / attempt / "results.json")["provider_closed"] is True


def test_merged_run_provenance_binds_the_revision_bundle_and_files() -> None:
    provenance = _json(MERGED / "provenance.json")
    metadata = _json(MERGED / "attempt-3/results.json")["golden"]["backend_metadata"]
    assert provenance["guest_bundle_sha256_recorded"] == metadata["task_bundle_sha256"]
    assert (
        provenance["guest_bundle_sha256_rebuilt_at_git_revision"] == metadata["task_bundle_sha256"]
    )
    first = _json(FIRST / "provenance.json")
    assert (
        first["later_guest_changes"]["guest_bundle_sha256_at_merge"]
        == metadata["task_bundle_sha256"]
    )
    assert _is_ancestor(provenance["git_revision"])
    assert _is_ancestor(first["later_guest_changes"]["commit"], provenance["git_revision"])
    for name, attempt in provenance["attempts"].items():
        assert attempt["git_revision"] == provenance["git_revision"], name
        assert attempt["worktree_clean_at_start"] is True, name
        for file_name, record in attempt["files"].items():
            data = (MERGED / name / file_name).read_bytes()
            assert hashlib.sha256(data).hexdigest() == record["sha256_checked_in"], file_name


# -- Both -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    sorted(
        p.relative_to(ROOT).as_posix() for folder in (FIRST, MERGED) for p in folder.rglob("*.json")
    ),
)
def test_checked_in_json_holds_no_local_paths(path: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "/private/" not in text and "/Users/" not in text
