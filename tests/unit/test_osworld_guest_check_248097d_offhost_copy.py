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
    "05-git-revision-at-review",
    "06-download-to-named-directory",
    "07-named-directory-vs-sha256-manifest",
    "08-download-and-check-in-one-command",
)
MANIFEST = f"artifacts/{SET}/images.manifest.json"
FULL_MATCH = {
    "manifest_files": 114,
    "downloaded_files": 114,
    "size_and_sha256_match": 114,
    "missing": [],
    "not_matching": [],
    "not_in_manifest": [],
    "bytes": 7_312_308,
}


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


def test_earlier_download_and_check_report_a_full_match() -> None:
    # The directory in these two records is a per-record redaction placeholder, so they are not
    # asserted to name the same directory. Record 08 carries that link.
    download = _command("03-download-to-fresh-directory")
    assert download["argv"][:3] == ["rclone", "copy", f"bb:OSWorld/{SET}"]
    verify = _command("04-downloaded-vs-sha256-manifest")
    assert verify["argv"][-2] == MANIFEST
    assert json.loads(verify["output"]) == FULL_MATCH


def test_one_command_downloads_into_a_new_directory_and_checks_that_directory() -> None:
    record = _command("08-download-and-check-in-one-command")
    script, _, remote, directory, _, manifest = record["argv"][2:]
    assert record["argv"][:2] == ["sh", "-c"]
    # One shell command: "$2" must not exist, is the download target, and is what is checked.
    assert script.startswith('set -e; test ! -e "$2"; rclone copy "$1" "$2" ')
    assert script.endswith('.venv/bin/python -I -c "$3" "$4" "$2"')
    assert (remote, manifest) == (f"bb:OSWorld/{SET}", MANIFEST)
    assert directory.endswith(f"/review-download-one-command/{SET}")
    output = record["output"]
    assert output.count("Copied (new)") == 114
    assert json.loads(output[output.rindex("\n{") + 1 :]) == FULL_MATCH


def test_two_command_repeat_reports_a_full_match() -> None:
    # Same relative path in both records, but each redacts its own working directory, so this
    # pair is not asserted to prove one directory either.
    directory = f".cache/guest-check-regen-2026-11-18/review-download/{SET}"
    download = _command("06-download-to-named-directory")
    assert download["argv"][:4] == ["rclone", "copy", f"bb:OSWorld/{SET}", directory]
    assert download["output"].count("Copied (new)") == 114
    verify = _command("07-named-directory-vs-sha256-manifest")
    assert verify["argv"][-2:] == [MANIFEST, directory]
    assert json.loads(verify["output"]) == FULL_MATCH


def test_note_states_the_recorded_numbers_and_leaves_the_readme_unchanged() -> None:
    note = " ".join((RECORD / "NOTE.md").read_text(encoding="utf-8").split())
    for statement in (
        "114 files, 7,312,308 bytes",
        "2027-11-18T04:15:08Z",
        "114 downloaded, 114 match by size and SHA-256, 0 missing, 0 extra",
        _command("00-git-revision")["output"].strip()[:8],
        _command("05-git-revision-at-review")["output"].strip()[:8],
        "as one command",
    ):
        assert statement in note, statement
    assert "are not checked in" in (EVIDENCE / "README.md").read_text(encoding="utf-8")
