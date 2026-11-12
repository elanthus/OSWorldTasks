"""The checked-in 2026-11-11 OSWorld guest check is self-consistent and names its revision."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "artifacts/osworld-guest-check-2026-11-11"


def _json(name: str) -> dict:
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def test_golden_replay_pays_once_at_the_terminal_step() -> None:
    golden = _json("results.json")["golden"]
    assert golden["summary"] == {
        "first_positive_reward_step": 112,
        "passed": True,
        "positive_reward_count": 1,
        "provider_closed": True,
        "terminal_step": 112,
    }
    rewards = golden["rewards"]
    assert len(rewards) == 112 and rewards[-1] == 1.0 and sum(rewards) == 1.0
    episode = _json("real-golden-episode.json")
    assert [step["reward"] for step in episode["trace"]] == rewards
    subset = {row["name"]: row for row in episode["real_reward_timing_subset"]}
    assert subset["correct-fields-without-submit"]["reward"] == 0.0
    assert subset["correct-fields-without-submit"]["step"] == 111


def test_every_review_sequence_submits_what_the_fake_submits() -> None:
    results = _json("results.json")
    cases = results["cases"]
    assert len(cases) == 7
    for name, case in cases.items():
        assert case["match"] is True, name
        assert case["guest_submissions"] == case["fake_submissions"], name
        assert all(step["reward"] == 0.0 for step in case["steps"]), name
    assert results["provider_closed"] is True


def test_run_records_the_pinned_upstream_and_screen() -> None:
    metadata = _json("results.json")["golden"]["backend_metadata"]
    assert metadata["upstream_tag"] == "v2026.06.24"
    assert metadata["provider"] == "docker-local"
    assert metadata["screen_size"] == [1024, 768]
    assert metadata["input_acknowledgement_barrier"] is True


def test_provenance_names_an_ancestor_revision_and_the_recorded_bundle() -> None:
    provenance = _json("provenance.json")
    metadata = _json("results.json")["golden"]["backend_metadata"]
    assert provenance["guest_bundle_sha256_recorded"] == metadata["task_bundle_sha256"]
    assert (
        provenance["guest_bundle_sha256_rebuilt_at_git_revision"] == metadata["task_bundle_sha256"]
    )
    later = provenance["later_guest_changes"]
    assert later["guest_bundle_sha256_at_merge"] != metadata["task_bundle_sha256"]
    for revision in (provenance["git_revision"], later["commit"]):
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, "HEAD"], cwd=ROOT, check=False
        )
        assert ancestor.returncode == 0, revision
    results = (EVIDENCE / "results.json").read_bytes()
    assert hashlib.sha256(results).hexdigest() == provenance["results_sha256"]


def test_checked_in_files_hold_no_local_paths_and_match_the_image_manifest() -> None:
    for name in ("results.json", "real-golden-episode.json", "provenance.json"):
        text = (EVIDENCE / name).read_text(encoding="utf-8")
        assert "/private/" not in text and "/Users/" not in text, name
    manifest = _json("images.manifest.json")
    assert manifest["file_count"] == len(manifest["files"]) == 114
    sheet = next(f for f in manifest["files"] if f["path"] == "contact-sheet.png")
    assert (
        hashlib.sha256((EVIDENCE / "contact-sheet.png").read_bytes()).hexdigest() == sheet["sha256"]
    )
