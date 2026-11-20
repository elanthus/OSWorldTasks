"""The off-host Object Lock observation must report unlocked files, never assume a lock."""

from scripts.observe_b2_object_lock import SCHEMA_VERSION, _file_entry, summarize


def _file(name: str, size: int, mode: str | None, until: int | None) -> dict:
    retention = (
        {"value": {"mode": mode, "retainUntilTimestamp": until}} if mode else {"value": None}
    )
    return {
        "fileName": name,
        "contentLength": size,
        "contentSha1": "none" if size > 5 * 1024**3 else "abc",
        "fileInfo": {"large_file_sha1": "def"} if size > 5 * 1024**3 else {},
        "fileRetention": retention,
    }


def test_summary_reports_locked_files_and_totals() -> None:
    files = [
        _file_entry(
            _file("full/files/attempts.sqlite", 15 * 1024**3, "governance", 1_824_504_266_976)
        ),
        _file_entry(_file("full/manifest.json", 1200, "governance", 1_824_504_270_000)),
    ]
    summary = summarize("OSWorld", "allPrivate", {"isFileLockEnabled": True}, [], files)
    assert summary["schema_version"] == SCHEMA_VERSION
    assert summary["file_count"] == 2
    assert summary["bytes"] == 15 * 1024**3 + 1200
    assert summary["all_governance"] is True
    assert summary["min_retain_until_ms"] == 1_824_504_266_976
    assert summary["files"][0]["large_file_sha1"] == "def"
    assert summary["mutations"] == 0 and summary["model_calls"] == 0


def test_summary_flags_any_unlocked_or_missing_file() -> None:
    locked = _file_entry(_file("a.json", 10, "governance", 1))
    unlocked = _file_entry(_file("b.json", 10, None, None))
    partial = summarize(
        "OSWorld", "allPrivate", {"isFileLockEnabled": True}, [], [locked, unlocked]
    )
    assert partial["all_governance"] is False
    assert partial["min_retain_until_ms"] == 0
    assert partial["files"][1]["retention_mode"] is None
    empty = summarize("OSWorld", "allPrivate", {"isFileLockEnabled": False}, [], [])
    assert empty["all_governance"] is False
    assert empty["min_retain_until_ms"] is None
    assert empty["object_lock_enabled"] is False
