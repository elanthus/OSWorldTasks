"""Each statement in the 248097d guest check's off-host copy note holds against its stored files."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SET = "osworld-guest-check-2026-11-12-248097d"
EVIDENCE = ROOT / "artifacts" / SET
RECORD = EVIDENCE / "offhost-copy-2026-11-18"
COMMANDS = (
    "00-git-revision",
    "01-rclone-version",
    "02-object-lock-readback",
    "03-download-to-fresh-directory",
    "04-downloaded-vs-sha256-manifest",
)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _command(name: str) -> dict[str, Any]:
    return _json(RECORD / "commands" / f"{name}.json")


def test_every_recorded_command_exited_zero_and_holds_no_local_path() -> None:
    assert sorted(path.stem for path in (RECORD / "commands").glob("*.json")) == list(COMMANDS)
    for name in COMMANDS:
        record = _command(name)
        assert record["exit_status"] == 0, name
        assert record["environment"] == {}, name
        text = json.dumps(record)
        assert "/Users/" not in text and "/private/" not in text and "/home/" not in text, name


def test_observation_file_is_the_recorded_read_back_output() -> None:
    record = _command("02-object-lock-readback")
    assert record["argv"][-2:] == ["scripts/observe_b2_object_lock.py", SET]
    assert (RECORD / "object-lock-observation.json").read_text("utf-8") == record["output"]


def test_remote_listing_equals_the_manifest_by_path_and_size() -> None:
    manifest = _json(EVIDENCE / "images.manifest.json")
    observation = _json(RECORD / "object-lock-observation.json")
    listed = {f"{SET}/{entry['path']}": entry["size"] for entry in manifest["files"]}
    remote = {entry["name"]: entry["size"] for entry in observation["files"]}
    assert remote == listed
    assert observation["file_count"] == len(listed) == 114
    assert observation["bytes"] == sum(listed.values()) == 7_312_308
    assert observation["mutations"] == 0


def test_every_remote_file_is_governance_locked_to_one_time() -> None:
    observation = _json(RECORD / "object-lock-observation.json")
    assert observation["bucket_type"] == "allPrivate"
    assert observation["object_lock_enabled"] is True
    assert observation["all_governance"] is True
    assert {entry["retention_mode"] for entry in observation["files"]} == {"governance"}
    (retain_until_ms,) = {entry["retain_until_ms"] for entry in observation["files"]}
    retain_until = datetime.fromtimestamp(retain_until_ms / 1000, UTC)
    assert retain_until.strftime("%Y-%m-%dT%H:%M:%SZ") == "2027-11-18T04:15:08Z"


def test_fresh_download_matches_the_sha256_manifest() -> None:
    download = _command("03-download-to-fresh-directory")
    assert download["argv"][:3] == ["rclone", "copy", f"bb:OSWorld/{SET}"]
    verify = _command("04-downloaded-vs-sha256-manifest")
    assert verify["argv"][-2] == f"artifacts/{SET}/images.manifest.json"
    assert verify["argv"][-1] == download["argv"][3]
    assert json.loads(verify["output"]) == {
        "manifest_files": 114,
        "downloaded_files": 114,
        "size_and_sha256_match": 114,
        "missing": [],
        "not_matching": [],
        "not_in_manifest": [],
        "bytes": 7_312_308,
    }


def test_note_states_the_recorded_numbers_and_leaves_the_readme_unchanged() -> None:
    note = (RECORD / "NOTE.md").read_text(encoding="utf-8")
    for statement in (
        "114 files, 7,312,308 bytes",
        "2027-11-18T04:15:08Z",
        "114 downloaded, 114 match by size and SHA-256, 0 missing, 0 extra",
        _command("00-git-revision")["output"].strip()[:8],
    ):
        assert statement in note, statement
    assert "are not checked in" in (EVIDENCE / "README.md").read_text(encoding="utf-8")
