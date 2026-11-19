"""Each statement in the 2026-11-18 frame regeneration note holds against its stored files."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pixelgym.evidence_redaction import redact_evidence_text

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
RERUN_FILES_RETAIN_UNTIL_MS = 1_826_566_570_000
# Committer times of 7338ca2c and a0fa54d6, from `git log --format=%cI`, in UTC.
COMMITTED_7338CA2C = datetime(2026, 11, 18, 7, 59, 14, tzinfo=UTC)
COMMITTED_A0FA54D6 = datetime(2026, 11, 18, 8, 9, 40, tzinfo=UTC)
WORK = ".cache/guest-check-regen-2026-11-18"
PREFIX = "osworld-guest-check-frames-regeneration-2026-11-18"
DOWNLOAD_MATCH = {
    "manifest_files": 114,
    "downloaded_files": 114,
    "size_and_sha256_match": 114,
    "missing": [],
    "not_matching": [],
    "not_in_manifest": [],
    "bytes": 7_285_563,
}
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
    assert [path.name[:2] for path in records] == [f"{index:02d}" for index in range(45)]
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
def test_copies_at_the_manifest_paths_and_first_downloads_match_the_manifest(
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


@pytest.mark.parametrize(("tag", "record"), [("2026-11-11", "17"), ("2026-11-12-attempt-3", "18")])
def test_rerun_output_itself_matches_the_manifest(tag: str, record: str) -> None:
    folder, revision, _ = SETS[tag]
    document = _json(RECORD / "runs" / f"{revision}.json")
    (placeholder,) = document["path_replacement"]
    assert WORK in document["path_replacement"][placeholder]
    compared = _command(record)
    assert compared["argv"][-2] == f"artifacts/{folder}/images.manifest.json"
    assert compared["argv"][-1] == document["run"]["argv"][-1].replace(placeholder, WORK)
    output = json.loads(compared["output"])
    earliest, latest = output.pop("earliest_modified_utc"), output.pop("latest_modified_utc")
    assert output.pop("path_rule").startswith("manifest path with a leading 'real-golden/' removed")
    assert output == MATCH
    run = document["run"]
    assert run["started_utc"] <= earliest <= latest <= run["ended_utc"]


@pytest.mark.parametrize(
    ("tag", "download", "check"),
    [("2026-11-11", "19", "20"), ("2026-11-12-attempt-3", "21", "22")],
)
def test_two_command_download_and_check_report_a_full_match(
    tag: str, download: str, check: str
) -> None:
    # Each record redacts its own working directory, so this pair does not prove one directory.
    # The one-command records below do.
    folder = SETS[tag][0]
    directory = f"{WORK}/review-download/{folder}"
    downloaded = _command(download)
    assert downloaded["argv"][:4] == ["rclone", "copy", f"bb:OSWorld/{folder}", directory]
    assert downloaded["output"].count("Copied (new)") == 114
    checked = _command(check)
    assert checked["argv"][-2:] == [f"artifacts/{folder}/images.manifest.json", directory]
    assert json.loads(checked["output"]) == DOWNLOAD_MATCH


@pytest.mark.parametrize(
    ("record", "remote", "manifest", "files", "size"),
    [
        ("32", SETS["2026-11-11"][0], "images.manifest.json", 114, 7_285_563),
        ("33", SETS["2026-11-12-attempt-3"][0], "images.manifest.json", 114, 7_285_563),
        ("34", PREFIX, "rerun-output.manifest.json", 11, 1_675_324),
    ],
)
def test_one_command_downloads_into_a_new_directory_and_checks_that_directory(
    record: str, remote: str, manifest: str, files: int, size: int
) -> None:
    command = _command(record)
    script, _, source, directory, _, checked_manifest = command["argv"][2:]
    assert command["argv"][:2] == ["sh", "-c"]
    # One shell command: "$2" must not exist, is the download target, and is what is checked.
    assert script.startswith('set -e; test ! -e "$2"; rclone copy "$1" "$2" ')
    assert script.endswith('.venv/bin/python -I -c "$3" "$4" "$2"')
    assert source == f"bb:OSWorld/{remote}"
    assert checked_manifest == f"artifacts/{remote}/{manifest}"
    assert directory == f"{WORK}/review-download-one-command/{remote}"
    output = command["output"]
    assert output.count("Copied (new)") == files
    assert json.loads(output[output.rindex("\n{") + 1 :]) == {
        "manifest_files": files,
        "downloaded_files": files,
        "size_and_sha256_match": files,
        "missing": [],
        "not_matching": [],
        "not_in_manifest": [],
        "bytes": size,
    }


def test_rerun_output_and_bundle_archives_were_copied_and_read_back() -> None:
    manifest = _json(RECORD / "rerun-output.manifest.json")
    assert (RECORD / "rerun-output.manifest.json").read_text("utf-8") == _command("23")["output"]
    files = {entry["path"]: entry for entry in manifest["files"]}
    assert len(files) == manifest["file_count"] == 11 and manifest["bytes"] == 1_675_324
    for folder, revision, bundle in SETS.values():
        document = _json(RECORD / "runs" / f"{revision}.json")
        record = files[f"rerun-output/{revision}/run-record.json"]
        assert record["sha256"] == document["run_record_sha256_before_path_replacement"]
        assert f"rerun-output/{revision}/raw/real-golden-episode.json" in files
        (archive,) = document["guest_bundle_archives_built_in_the_worktree_cache"]
        provenance = _json(ARTIFACTS / folder.split("/")[0] / "provenance.json")
        assert (
            files[f"guest-bundles/{archive}"]["sha256"]
            == (provenance["guest_bundle_sha256_recorded"])
        )
        assert provenance["guest_bundle_sha256_recorded"].startswith(bundle)
    for upload, check, remote, count in (
        ("24", "26", "rerun-output", 9),
        ("25", "27", "guest-bundles", 2),
    ):
        uploaded = _command(upload)
        assert uploaded["argv"][3] == f"bb:OSWorld/{PREFIX}/{remote}"
        assert "--immutable" in uploaded["argv"] and "--checksum" in uploaded["argv"]
        assert uploaded["output"].count("Copied (new)") == count
        checked = _command(check)["output"]
        assert "0 differences found" in checked and f"{count} matching files" in checked
    directory = f"{WORK}/review-download/{PREFIX}"
    assert _command("28")["argv"][:4] == ["rclone", "copy", f"bb:OSWorld/{PREFIX}", directory]
    verified = _command("29")
    assert verified["argv"][-2:] == [f"artifacts/{PREFIX}/rerun-output.manifest.json", directory]
    assert json.loads(verified["output"]) == {
        "manifest_files": 11,
        "downloaded_files": 11,
        "size_and_sha256_match": 11,
        "missing": [],
        "not_matching": [],
        "not_in_manifest": [],
        "bytes": 1_675_324,
    }
    listing = json.loads(_command("30")["output"])
    assert {entry["name"]: entry["size"] for entry in listing["files"]} == {
        f"{PREFIX}/{path}": entry["size"] for path, entry in files.items()
    }
    assert {entry["retention_mode"] for entry in listing["files"]} == {None}


def test_rerun_output_and_bundle_archives_were_locked_once_and_read_back() -> None:
    before = json.loads(_command("30")["output"])
    names = [entry["name"] for entry in before["files"]]
    applied = _command("36")
    assert applied["argv"][2:] == [
        f"artifacts/{PREFIX}/apply_governance_retention.py",
        PREFIX,
        "--retain-until-ms",
        str(RERUN_FILES_RETAIN_UNTIL_MS),
        "--expect-files",
        "11",
    ]
    lines = applied["output"].strip().splitlines()
    assert lines[2:5] == [
        "files listed: 11 (expected 11)",
        "files whose retention this key cannot read: 0",
        "files already carrying a different retention: 0",
    ]
    assert json.loads(lines[-1]) == {
        "already_set": 0,
        "files_listed": 11,
        "mode": "governance",
        "retain_until_ms": RERUN_FILES_RETAIN_UNTIL_MS,
        "retention_applied": 11,
    }
    assert lines[5:-1] == [f"governance {RERUN_FILES_RETAIN_UNTIL_MS}  {name}" for name in names]

    after = json.loads(_command("37")["output"])
    unlocked = [
        entry | {"retention_mode": None, "retain_until_ms": None} for entry in after["files"]
    ]
    assert unlocked == before["files"]
    assert {(entry["retention_mode"], entry["retain_until_ms"]) for entry in after["files"]} == {
        ("governance", RERUN_FILES_RETAIN_UNTIL_MS)
    }
    assert after["file_count"] == 11 and after["bytes"] == 1_675_324
    assert after["all_governance"] is True and after["mutations"] == 0
    assert after["min_retain_until_ms"] == RERUN_FILES_RETAIN_UNTIL_MS
    assert after["object_lock_enabled"] is True and after["bucket_type"] == "allPrivate"
    retain_until = datetime.fromtimestamp(RERUN_FILES_RETAIN_UNTIL_MS / 1000, UTC)
    assert retain_until.isoformat() == "2027-11-18T19:36:10+00:00"
    started = datetime.fromisoformat(applied["started_at_utc"])
    assert timedelta(days=364, hours=23) < retain_until - started <= timedelta(days=365)
    frames_until = datetime.fromtimestamp(RETAIN_UNTIL_MS / 1000, UTC)
    assert timedelta(hours=13) < retain_until - frames_until < timedelta(hours=14)


def test_review_records_were_written_at_the_revisions_the_note_names() -> None:
    assert _command("16")["output"].strip().startswith("6b1c75f2")
    for record in ("32", "33", "34"):
        started = datetime.fromisoformat(_command(record)["started_at_utc"])
        assert COMMITTED_7338CA2C < started < COMMITTED_A0FA54D6, record
    assert _command("35")["output"].strip().startswith("a0fa54d6")
    assert _command("35")["started_at_utc"] < _command("36")["started_at_utc"]
    assert _command("38")["output"].strip().startswith("c6eb00cb")
    assert _command("40")["output"].strip().startswith("a293538b")
    assert _command("40")["started_at_utc"] < _command("41")["started_at_utc"]
    assert _command("42")["output"].strip().startswith("fa51317f")
    assert _command("42")["started_at_utc"] < _command("43")["started_at_utc"]
    assert _command("43")["started_at_utc"] < _command("44")["started_at_utc"]


def test_repeats_store_the_program_text_that_redaction_changed_in_records_39_and_41() -> None:
    # The recorder was given ``--cwd .`` and redacts its working directory, so it replaced each
    # standalone "." in the stored program text. The repeats were given an absolute path.
    working_directory = [(Path("."), "<path-0>")]
    for earlier, repeat in (("41", "43"), ("39", "44")):
        old, new = _command(earlier), _command(repeat)
        assert "<path-0>" in old["argv"][3], earlier
        assert "<path-" not in json.dumps(new["argv"]) and new["cwd"] == "<path-0>", repeat
        assert old["argv"] == [
            redact_evidence_text(argument, working_directory) for argument in new["argv"]
        ], earlier
    assert "at + '.' + key" in _command("43")["argv"][3]
    assert _command("44")["argv"][3].count("(?:\\.[0-9]+)?") == 2
    assert _command("43")["output"] == _command("41")["output"]
    first, second = json.loads(_command("39")["output"]), json.loads(_command("44")["output"])
    assert second["read_utc"].startswith("2026-11-19T")
    assert {**second, "read_utc": first["read_utc"]} == first


@pytest.mark.parametrize(
    ("tag", "folder"),
    [
        ("627e09b", "osworld-guest-check-2026-11-11"),
        ("d4e711e", "osworld-guest-check-2026-11-12/attempt-3"),
    ],
)
def test_rerun_episode_equals_the_original_run_apart_from_ids_paths_and_python_version(
    tag: str, folder: str
) -> None:
    record = _command("41")
    run = json.loads(record["output"])["runs"][tag]
    original = f"artifacts/{folder}/real-golden-episode.json"
    results = f"artifacts/{folder}/results.json"
    rerun = f"{WORK}/{tag}/raw/real-golden-episode.json"
    entry = f"rerun-output/{tag}/raw/real-golden-episode.json"
    assert record["argv"][4] == f"artifacts/{PREFIX}/rerun-output.manifest.json"
    start = record["argv"].index(tag)
    assert record["argv"][start : start + 5] == [tag, original, results, rerun, entry]
    # The rerun file compared is the file listed in the off-host manifest.
    (listed,) = (
        item
        for item in _json(RECORD / "rerun-output.manifest.json")["files"]
        if item["path"] == entry
    )
    assert (run["rerun_episode_bytes"], run["rerun_episode_sha256"]) == (
        listed["size"],
        listed["sha256"],
    )
    assert run["rerun_episode_equals_offhost_manifest_entry"] is True
    # The original run's side of the comparison is checked in; the record must agree with it.
    episode = _json(ROOT / original)
    rewards = [step["reward"] for step in episode["trace"]]
    assert rewards == _json(ROOT / results)["golden"]["rewards"]
    assert (len(rewards), sum(rewards)) == (112, 1.0)
    assert episode["backend_metadata"]["python_version"] == run["python_version"]["original"]
    assert episode["summary"] == run["summary"]

    assert run["leaf_values"] == {"original": 2418, "rerun": 2418}
    assert (run["keys_only_in_original"], run["keys_only_in_rerun"]) == (0, 0)
    assert run["differing_leaf_values"] == 226
    assert run["differing_leaf_values_by_field"] == {
        ".backend_metadata.python_version": 1,
        ".contact_sheet_path": 1,
        ".trace[*].info.episode_id": 112,
        ".trace[*].screenshot_path": 112,
    }
    assert run["steps"] == {"original": 112, "rerun": 112}
    assert run["steps_equal_apart_from_screenshot_path_and_episode_id"] == 112
    assert run["frame_file_names_equal"] == 112 and run["contact_sheet_file_name_equal"] is True
    assert run["absolute_screenshot_paths"] == {"original": 0, "rerun": 112}
    assert run["distinct_episode_ids"] == {"original": 1, "rerun": 1, "shared": 0}
    assert run["python_version"] == {"original": "3.12.15", "rerun": "3.12.16"}
    assert run["rewards"] == {
        "original_episode": 112,
        "rerun_episode": 112,
        "original_results": 112,
        "rerun_sum": 1.0,
        "rerun_equals_original_episode": True,
        "rerun_equals_original_results": True,
    }
    assert run["summary"]["first_positive_reward_step"] == run["summary"]["terminal_step"] == 112
    assert run["summary"]["positive_reward_count"] == 1
    assert run["summary_equals_original_episode"] is True
    assert run["summary_equals_original_results"] is True
    assert run["reward_timing_subset_equal"] is True


def test_storage_estimate_is_the_recorded_size_times_the_rate_read_from_the_pricing_page() -> None:
    record = _command("39")
    size_record = f"artifacts/{PREFIX}/commands/31-bucket-size-all-versions.json"
    assert record["argv"][-2:] == ["https://www.backblaze.com/cloud-storage/pricing", size_record]
    estimate = json.loads(record["output"])
    size = json.loads(_command("31")["output"].strip().splitlines()[-1])
    assert estimate["http_status"] == 200 and estimate["read_utc"].startswith("2026-11-18T")
    assert (estimate["bucket_files"], estimate["bucket_bytes"]) == (size["count"], size["bytes"])
    assert estimate["bucket_size_record"] == size_record
    # One rate in the billing sentence and one free allowance; other figures are listed, unused.
    assert estimate["billing_sentence_usd_per_tb_per_30_days"] == ["6.95"]
    assert estimate["free_storage_gb"] == ["10"]
    assert estimate["every_usd_per_tb_figure_on_the_page"] == ["5", "6.95", "15"]
    assert estimate["bytes_per_tb_assumed"] == 10**12
    full = size["bytes"] / 10**12 * 6.95
    after_allowance = (size["bytes"] - 10 * 10**9) / 10**12 * 6.95
    assert estimate["usd_per_30_days_with_no_free_allowance"] == round(full, 4)
    assert estimate["usd_per_30_days_after_free_allowance"] == round(after_allowance, 4)
    assert (f"{full:.2f}", f"{after_allowance:.2f}") == ("0.18", "0.11")


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


def _lock_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "apply_governance_retention", RECORD / "apply_governance_retention.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("retention", "exit_status", "updates"),
    [
        ({"isClientAuthorizedToRead": True, "value": {"mode": None}}, 0, 2),
        ({"isClientAuthorizedToRead": False, "value": None}, 1, 0),
        ({"isClientAuthorizedToRead": True, "value": {"mode": "governance"}}, 1, 0),
    ],
    ids=["readable-and-unset", "unreadable", "different-retention"],
)
def test_lock_script_changes_nothing_unless_every_retention_is_readable_and_unset(
    retention: dict[str, Any],
    exit_status: int,
    updates: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unset = {"isClientAuthorizedToRead": True, "value": {"mode": None}}
    listed = [
        {"fileName": "p/a.png", "fileId": "id-a", "fileRetention": unset},
        {"fileName": "p/b.png", "fileId": "id-b", "fileRetention": retention},
    ]
    update_calls: list[dict[str, Any]] = []

    def fake_call(url: str, body: dict[str, Any] | None, authorization: str) -> dict[str, Any]:
        operation = url.rsplit("/", 1)[-1]
        if operation == "b2_authorize_account":
            api = {"storageApi": {"apiUrl": "https://api.invalid"}}
            return {"apiInfo": api, "authorizationToken": "token", "accountId": "account"}
        if operation == "b2_list_buckets":
            return {"buckets": [{"bucketName": "bucket", "bucketId": "bucket-id"}]}
        if operation == "b2_list_file_names":
            return {"files": listed, "nextFileName": None}
        assert operation == "b2_update_file_retention" and body is not None
        update_calls.append(body)
        return {"fileRetention": body["fileRetention"]}

    module = _lock_script()
    monkeypatch.setattr(module, "call", fake_call)
    for name in ("BACKBLAZE_KEY_ID", "BACKBLAZE_APPLICATION_KEY"):
        monkeypatch.setenv(name, "not-a-credential")
    monkeypatch.setenv("BACKBLAZE_BUCKET_NAME", "bucket")
    arguments = ["lock", "p", "--retain-until-ms", "1", "--expect-files", "2"]
    monkeypatch.setattr(sys, "argv", arguments)
    assert module.main() == exit_status
    assert [call["fileName"] for call in update_calls] == ["p/a.png", "p/b.png"][:updates]


def test_note_states_the_recorded_numbers() -> None:
    note = " ".join((RECORD / "NOTE.md").read_text(encoding="utf-8").split())
    size = json.loads(_command("13")["output"].strip().splitlines()[-1])
    assert size == {"count": 433, "bytes": 26_107_365_320, "sizeless": 0, "fs": "bb:OSWorld"}
    later = json.loads(_command("31")["output"].strip().splitlines()[-1])
    assert later == {"count": 444, "bytes": 26_109_040_644, "sizeless": 0, "fs": "bb:OSWorld"}
    for statement in (
        "433 files and 26,107,365,320 bytes",
        "444 files and 26,109,040,644 bytes",
        "228 files and 14,571,126 bytes",
        "11 files, 1,675,324 bytes",
        "holds the 112-step trace",
        _command("16")["output"].strip()[:8],
        "228 files listed, 228 changed, 0 already set, exit 0",
        "one retain-until time, 2027-11-18T06:13:22Z",
        "11 files listed, 0 with unreadable retention, 0 with a different retention,",
        "retention, 11 changed, 0 already set, exit 0",
        "one retain-until time, 2027-11-18T19:36:10Z",
        "Records 16 to 31 were added",
        "Records 32 to 34 were added later",
        "Records 35 to 37 were written with the checkout at `a0fa54d6`",
        "Records 38 and 39 were written with the checkout at `c6eb00cb`",
        "about $0.11 per 30 days after the free 10 GB, about $0.18 with no free allowance,",
        "at $6.95 per TB per 30 days, read from Backblaze's pricing page on 2026-11-18",
        "Taking 1 TB as 10^12 bytes is the record's own assumption",
        "Record 31 is the last size recorded",
        "shows the figures $5 and $15 per TB in other sentences",
        "Records 40 and 41 were written with the checkout at `a293538b`",
        "| Leaf values in each episode file | 2,418 and 2,418, same keys | 2,418 and 2,418,",
        "| Leaf values that differ | 226 | 226 |",
        "the episode id | 112 of 112 | 112 of 112 |",
        "| equal, 112 values, sum 1.0 | equal, 112 values, sum 1.0 |",
        "The 226 differing values are the same four fields in both pairs",
        "no id is shared between a rerun and its original run",
        "The frame file names are equal on all 112 steps",
        "recorded Python 3.12.15 and the reruns recorded 3.12.16",
        "the only positive reward is at step 112, which is also the terminal step",
        "Record 41 compares it with the original run's files",
        "Records 42 to 44 were written with the checkout at `fa51317f`",
        "replaced each standalone `.` in the stored program text with `<path-0>`",
        "Neither earlier record was edited",
        "| the same output as record 41 |",
        "every value equal to record 39's except the time of the read, including the page's",
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
