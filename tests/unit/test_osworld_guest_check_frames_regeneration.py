"""Each statement in the 2026-11-18 frame regeneration note holds against its stored files."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
RECORD = ARTIFACTS / "osworld-guest-check-frames-regeneration-2026-11-18"
SETS = {
    "2026-11-11": ("osworld-guest-check-2026-11-11", "627e09b", "4067dc638dbaaa8e"),
    "2026-11-12-attempt-3": (
        "osworld-guest-check-2026-11-12/attempt-3",
        "d4e711e",
        "505a15bf40bac294",
    ),
}
RETAIN_UNTIL_MS = 1_826_518_402_000
MATCH = {
    "manifest_files": 114,
    "png_files_present": 114,
    "size_and_sha256_match": 114,
    "missing": [],
    "not_matching": [],
    "png_not_in_manifest": [],
    "bytes": 7_285_563,
}


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _command(prefix: str) -> dict[str, Any]:
    (path,) = (RECORD / "commands").glob(f"{prefix}-*.json")
    return _json(path)


def _manifest(folder: str) -> dict[str, int]:
    manifest = _json(ARTIFACTS / folder / "images.manifest.json")
    return {entry["path"]: entry["size"] for entry in manifest["files"]}


def test_every_recorded_command_exited_zero_and_holds_no_local_path() -> None:
    records = sorted((RECORD / "commands").glob("*.json"))
    assert [path.name[:2] for path in records] == [f"{index:02d}" for index in range(16)]
    for path in records:
        record = _json(path)
        assert record["exit_status"] == 0, path.name
        assert record["environment"] == {}, path.name
        text = json.dumps(record)
        assert "/Users/" not in text and "/private/" not in text and "/home/" not in text, path.name


@pytest.mark.parametrize("tag", sorted(SETS))
def test_rerun_was_clean_at_the_original_revision_and_built_the_recorded_bundle(tag: str) -> None:
    folder, revision, bundle = SETS[tag]
    document = _json(RECORD / "runs" / f"{revision}.json")
    run = document["run"]
    assert run["revision"].startswith(revision) and run["worktree_head"] == run["revision"]
    assert run["worktree_status_porcelain"] == ""
    assert run["exit_status"] == 0 and run["timed_out"] is False
    assert run["containers_before"] == [] and run["containers_after"] == []
    assert run["argv"][:2] == ["scripts/osworld_golden_trajectory.py", "record"]
    assert document["stdout_summary"]["provider_closed"] is True
    assert document["frames_written"] == 113 and document["frame_size"] == [1024, 768]
    (archive,) = document["guest_bundle_archives_built_in_the_worktree_cache"]
    assert archive.endswith(f"-{bundle}.zip")
    provenance = ARTIFACTS / folder.split("/")[0] / "provenance.json"
    assert _json(provenance)["guest_bundle_sha256_recorded"].startswith(bundle)
    assert document["integration"]["upstream_tag"] == "v2026.06.24"
    assert "/Users/" not in json.dumps(document)


@pytest.mark.parametrize(
    ("tag", "local", "downloaded"),
    [
        ("2026-11-11", "02", "06"),
        ("2026-11-12-attempt-3", "07", "11"),
    ],
)
def test_regenerated_and_downloaded_files_match_the_manifest(
    tag: str, local: str, downloaded: str
) -> None:
    folder = SETS[tag][0]
    for prefix in (local, downloaded):
        record = _command(prefix)
        assert record["argv"][-2] == f"artifacts/{folder}/images.manifest.json"
        assert json.loads(record["output"]) == MATCH
    assert _command(local)["argv"][-1] == f"artifacts/{folder}"
    assert sum(_manifest(folder).values()) == MATCH["bytes"]


@pytest.mark.parametrize(
    ("tag", "upload", "check"),
    [
        ("2026-11-11", "03", "04"),
        ("2026-11-12-attempt-3", "08", "09"),
    ],
)
def test_upload_copied_every_file_and_the_check_found_no_difference(
    tag: str, upload: str, check: str
) -> None:
    folder = SETS[tag][0]
    uploaded = _command(upload)
    assert uploaded["argv"][:4] == ["rclone", "copy", f"artifacts/{folder}", f"bb:OSWorld/{folder}"]
    assert "--immutable" in uploaded["argv"] and "--checksum" in uploaded["argv"]
    assert uploaded["output"].count("Copied (new)") == 114
    checked = _command(check)["output"]
    assert "0 differences found" in checked and "114 matching files" in checked


def _listed_in_both_manifests() -> dict[str, int]:
    return {
        f"{folder}/{path}": size
        for folder, _, _ in SETS.values()
        for path, size in _manifest(folder).items()
    }


def test_remote_listing_before_the_lock_equals_both_manifests_with_no_retention() -> None:
    before = json.loads(_command("12")["output"])
    assert {
        entry["name"]: entry["size"] for entry in before["files"]
    } == _listed_in_both_manifests()
    assert before["file_count"] == 228 and before["bytes"] == 14_571_126
    assert {entry["retention_mode"] for entry in before["files"]} == {None}
    assert before["bucket_type"] == "allPrivate" and before["mutations"] == 0


def test_retention_was_applied_once_to_every_listed_file() -> None:
    applied = _command("14")
    assert applied["argv"][2:] == [
        "artifacts/osworld-guest-check-frames-regeneration-2026-11-18/apply_governance_retention.py",
        "osworld-guest-check-2026-11-11",
        "osworld-guest-check-2026-11-12/attempt-3",
        "--retain-until-ms",
        str(RETAIN_UNTIL_MS),
        "--expect-files",
        "228",
    ]
    lines = applied["output"].strip().splitlines()
    assert json.loads(lines[-1]) == {
        "already_set": 0,
        "files_listed": 228,
        "mode": "governance",
        "retain_until_ms": RETAIN_UNTIL_MS,
        "retention_applied": 228,
    }
    changed = {
        line.split("  ", 1)[1]
        for line in lines
        if line.startswith(f"governance {RETAIN_UNTIL_MS}  ")
    }
    assert changed == set(_listed_in_both_manifests())


def test_every_file_is_governance_locked_to_one_time_after_the_lock() -> None:
    observation = _json(RECORD / "object-lock-observation.json")
    assert (RECORD / "object-lock-observation.json").read_text("utf-8") == _command("15")["output"]
    before = json.loads(_command("12")["output"])
    unlocked = [
        entry | {"retention_mode": None, "retain_until_ms": None} for entry in observation["files"]
    ]
    assert unlocked == before["files"]
    assert {
        (entry["retention_mode"], entry["retain_until_ms"]) for entry in observation["files"]
    } == {("governance", RETAIN_UNTIL_MS)}
    assert observation["all_governance"] is True
    assert observation["min_retain_until_ms"] == RETAIN_UNTIL_MS
    assert observation["object_lock_enabled"] is True and observation["bucket_type"] == "allPrivate"
    retain_until = datetime.fromtimestamp(RETAIN_UNTIL_MS / 1000, UTC)
    assert retain_until.isoformat() == "2027-11-18T06:13:22+00:00"
    started = datetime.fromisoformat(_command("14")["started_at_utc"])
    assert timedelta(days=364, hours=23) < retain_until - started <= timedelta(days=365)


def test_the_two_manifests_list_the_same_digests() -> None:
    first, second = (
        {
            entry["path"]: entry["sha256"]
            for entry in _json(ARTIFACTS / folder / "images.manifest.json")["files"]
        }
        for folder, _, _ in SETS.values()
    )
    assert first == second and len(first) == 114


def test_note_states_the_recorded_numbers() -> None:
    note = (RECORD / "NOTE.md").read_text(encoding="utf-8")
    size = json.loads(_command("13")["output"].strip().splitlines()[-1])
    assert size == {"count": 433, "bytes": 26_107_365_320, "sizeless": 0, "fs": "bb:OSWorld"}
    for statement in (
        "433 files and 26,107,365,320 bytes",
        "228 files and\n14,571,126 bytes",
        "228 files listed, 228 changed, 0 already set, exit 0",
        "one retain-until time, 2027-11-18T06:13:22Z",
        "| `627e09b` | 2026-11-18T04:22:21Z | 625.3 | 0 |",
        "| `d4e711e` | 2026-11-18T04:32:47Z | 570.9 | 0 |",
        _command("00")["output"].strip()[:8],
    ):
        assert statement in note, statement
    for revision, started, seconds in (
        ("627e09b", "04:22:21Z", 625.3),
        ("d4e711e", "04:32:47Z", 570.9),
    ):
        run = _json(RECORD / "runs" / f"{revision}.json")["run"]
        assert run["started_utc"].endswith(started) and run["seconds"] == seconds
