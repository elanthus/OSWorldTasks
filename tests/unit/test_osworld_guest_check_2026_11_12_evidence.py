"""The checked-in 2026-11-12 OSWorld guest check is self-consistent; its redaction is disclosed."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "artifacts/osworld-guest-check-2026-11-12"
CHECKED_IN = (
    "results.json",
    "raw/real-golden-episode.json",
    "park-output-supplement.json",
    "redaction.json",
    "images.manifest.json",
    "README.md",
)


def _json(name: str) -> Any:
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def _pointer(document: Any, pointer: str) -> Any:
    for part in pointer.lstrip("/").split("/"):
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


def test_checked_in_files_hold_no_local_paths() -> None:
    for name in CHECKED_IN:
        text = (EVIDENCE / name).read_text(encoding="utf-8")
        assert "/Users/" not in text and "/private/" not in text and "/home/" not in text, name


def test_redaction_record_matches_the_checked_in_files() -> None:
    record = _json("redaction.json")
    assert [entry["path"] for entry in record["files"]] == [
        "results.json",
        "raw/real-golden-episode.json",
    ]
    for entry in record["files"]:
        data = (EVIDENCE / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["redacted_sha256"], entry["path"]
        assert entry["original_sha256"] != entry["redacted_sha256"]
        document = json.loads(data)
        assert entry["changed_fields"], entry["path"]
        for field in entry["changed_fields"]:
            assert _pointer(document, field["json_pointer"]) == field["redacted_value"]
            assert not field["redacted_value"].startswith("/")


def test_provenance_names_an_ancestor_revision_with_a_clean_worktree() -> None:
    provenance = _json("results.json")["provenance"]
    assert provenance["worktree_clean_at_start"] is True
    assert provenance["git_status_porcelain"] == ""
    assert provenance["input_contract"] == "pixelgym-vendor-form-input-v3"
    assert provenance["setup_acknowledged_inputs"] == 0
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", provenance["git_revision"], "HEAD"],
        cwd=ROOT,
        check=False,
    )
    assert ancestor.returncode == 0


def test_provider_closed_is_the_conjunction_of_the_recorded_closes() -> None:
    results = _json("results.json")
    closes = [results[part]["provider_close"] for part in ("resets", "golden", "cases", "oracle")]
    assert results["provider_closed"] is all(close["returned"] is True for close in closes)


def test_run_records_the_pinned_upstream_and_screen() -> None:
    metadata = _json("results.json")["golden"]["backend_metadata"]
    assert metadata["upstream_tag"] == "v2026.06.24"
    assert metadata["provider"] == "docker-local"
    assert metadata["screen_size"] == [1024, 768]
    assert metadata["input_contract"] == "pixelgym-vendor-form-input-v3"


def test_golden_rewards_match_the_episode_trace() -> None:
    golden = _json("results.json")["golden"]
    episode = _json("raw/real-golden-episode.json")
    assert [step["reward"] for step in episode["trace"]] == golden["rewards"]
    assert episode["contact_sheet_path"] == (
        "artifacts/osworld-guest-check-2026-11-12/real-golden/contact-sheet.png"
    )
    assert (ROOT / episode["contact_sheet_path"]).is_file()


def test_park_supplement_covers_the_reset_seeds_without_absolute_paths() -> None:
    supplement = _json("park-output-supplement.json")
    reset_seeds = {record["seed"] for record in _json("results.json")["resets"]["records"]}
    assert {record["seed"] for record in supplement["records"]} == reset_seeds
    for record in supplement["records"]:
        for name, file in record["files"].items():
            assert not file["path"].startswith("/"), name
            content = file["content"].encode("utf-8")
            assert hashlib.sha256(content).hexdigest() == file["sha256"], name
            assert len(content) == file["bytes"], name


def test_contact_sheet_matches_the_image_manifest() -> None:
    manifest = _json("images.manifest.json")
    assert manifest["file_count"] == len(manifest["files"])
    sheet = next(f for f in manifest["files"] if f["path"] == "real-golden/contact-sheet.png")
    data = (EVIDENCE / "real-golden/contact-sheet.png").read_bytes()
    assert hashlib.sha256(data).hexdigest() == sheet["sha256"]
    assert len(data) == sheet["size"]
