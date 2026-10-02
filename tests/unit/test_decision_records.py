"""WP3: normalized owner decision records under artifacts/decisions/."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
DECISIONS = ROOT / "artifacts" / "decisions"
EXPECTED = {"d1.8", "d2.11", "d3.11", "d4.12", "d3-pilot"}
REQUIRED = (
    "schema_version",
    "gate",
    "decision",
    "revision",
    "date",
    "decided_by",
    "tag",
    "source_record",
    "source_record_sha256",
)
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN = ("source_user_message", "source_user_messages", "declaration")


def validate(record: dict[str, Any], root: Path) -> None:
    """Raise ValueError when a decision record violates the schema."""
    for key in REQUIRED:
        if key not in record:
            raise ValueError(f"missing field: {key}")
    for key in FORBIDDEN:
        if key in record:
            raise ValueError(f"transcript field present: {key}")
    if record["decided_by"] != "Michael Swailes":
        raise ValueError("decided_by must be Michael Swailes")
    revision = record["revision"]
    if revision is None:
        if not record.get("revision_note"):
            raise ValueError("null revision requires revision_note")
        if record["tag"] is not None:
            raise ValueError("null revision requires null tag")
    else:
        if not isinstance(revision, str) or not SHA40.match(revision):
            raise ValueError("revision must be a 40-hex SHA or null")
        if not isinstance(record["tag"], str) or not record["tag"]:
            raise ValueError("revision requires a tag name")
    if not SHA256.match(str(record["source_record_sha256"])):
        raise ValueError("source_record_sha256 must be 64-hex")
    source = root / record["source_record"]
    if not source.is_file():
        raise ValueError(f"source_record missing: {record['source_record']}")
    if hashlib.sha256(source.read_bytes()).hexdigest() != record["source_record_sha256"]:
        raise ValueError("source_record_sha256 mismatch")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def tag_target(repo: Path, tag: str) -> str | None:
    """Return the commit a tag points at, or None when the tag is absent."""
    result = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else None


def check_tag(repo: Path, record: dict[str, Any]) -> bool:
    """Return False when the tag is absent; raise when it points elsewhere."""
    tag = record["tag"]
    if tag is None:
        return False
    target = tag_target(repo, tag)
    if target is None:
        return False
    if target != record["revision"]:
        raise ValueError(f"tag {tag} points at {target}, not {record['revision']}")
    return True


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((DECISIONS / f"{name}.json").read_text())
    return data


def test_expected_decision_files_exist() -> None:
    assert {p.stem for p in DECISIONS.glob("*.json")} == EXPECTED


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_decision_record_validates(name: str) -> None:
    validate(_load(name), ROOT)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_decision_fields_match_source_record(name: str) -> None:
    record = _load(name)
    raw = json.loads((ROOT / record["source_record"]).read_text())
    if "verdict" in raw:
        assert record["gate"] == raw["gate"]
        assert record["decision"] == raw["verdict"]
        assert record["date"] == raw["declared_at"]
        assert record["revision"] == raw.get("graded_revision")
    else:
        assert raw["pilot"]["approved"] is True
        assert record["date"] == raw["recorded_date"]
        assert record["revision"] is None


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_decision_revision_exists_in_repository(name: str) -> None:
    record = _load(name)
    if record["revision"] is None:
        pytest.skip("record names no reviewed revision (revision_note)")
    result = _git(ROOT, "cat-file", "-t", record["revision"])
    if result.returncode != 0:
        pytest.skip(f"revision {record['revision']} absent (shallow clone?)")
    assert result.stdout.strip() == "commit"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_decision_tag_points_at_revision(name: str) -> None:
    record = _load(name)
    if not check_tag(ROOT, record):
        pytest.skip(f"tag {record['tag']} not present in this clone")


def test_d211_names_superseded_historical_record() -> None:
    record = _load("d2.11")
    historical = ROOT / record["supersedes_record"]
    assert hashlib.sha256(historical.read_bytes()).hexdigest() == record["supersedes_record_sha256"]


# Negative tests on synthetic copies.


def _synthetic(tmp_path: Path, name: str = "d1.8") -> dict[str, Any]:
    record = _load(name)
    dest = tmp_path / record["source_record"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / record["source_record"], dest)
    return record


def test_rejects_wrong_sha256(tmp_path: Path) -> None:
    record = _synthetic(tmp_path)
    record["source_record_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="mismatch"):
        validate(record, tmp_path)


def test_rejects_modified_source_record(tmp_path: Path) -> None:
    record = _synthetic(tmp_path)
    (tmp_path / record["source_record"]).write_text("{}\n")
    with pytest.raises(ValueError, match="mismatch"):
        validate(record, tmp_path)


@pytest.mark.parametrize("field", REQUIRED)
def test_rejects_missing_field(tmp_path: Path, field: str) -> None:
    record = _synthetic(tmp_path)
    del record[field]
    with pytest.raises(ValueError, match="missing field"):
        validate(record, tmp_path)


def test_rejects_short_revision(tmp_path: Path) -> None:
    record = _synthetic(tmp_path)
    record["revision"] = "5b32952"
    with pytest.raises(ValueError, match="40-hex"):
        validate(record, tmp_path)


def test_rejects_null_revision_without_note(tmp_path: Path) -> None:
    record = _synthetic(tmp_path, "d3-pilot")
    del record["revision_note"]
    with pytest.raises(ValueError, match="revision_note"):
        validate(record, tmp_path)


def test_rejects_null_revision_with_tag(tmp_path: Path) -> None:
    record = _synthetic(tmp_path, "d2.11")
    record["tag"] = "d2.11-pass"
    with pytest.raises(ValueError, match="null tag"):
        validate(record, tmp_path)


def test_rejects_transcript_field(tmp_path: Path) -> None:
    record = _synthetic(tmp_path)
    record["source_user_message"] = "x"
    with pytest.raises(ValueError, match="transcript"):
        validate(record, tmp_path)


def _temp_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    env_args = (
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "-c",
        "commit.gpgsign=false",
    )

    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *env_args, "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    run("init", "-q")
    run("commit", "-q", "--allow-empty", "-m", "one")
    first = run("rev-parse", "HEAD")
    run("commit", "-q", "--allow-empty", "-m", "two")
    second = run("rev-parse", "HEAD")
    run("-c", "tag.gpgsign=false", "tag", "d1.8-pass", second)
    return repo, first, second


def test_tag_pointing_elsewhere_is_rejected(tmp_path: Path) -> None:
    repo, first, _ = _temp_repo(tmp_path)
    with pytest.raises(ValueError, match="points at"):
        check_tag(repo, {"tag": "d1.8-pass", "revision": first})


def test_tag_pointing_at_revision_passes(tmp_path: Path) -> None:
    repo, _, second = _temp_repo(tmp_path)
    assert check_tag(repo, {"tag": "d1.8-pass", "revision": second}) is True


def test_absent_tag_is_reported_absent(tmp_path: Path) -> None:
    repo, first, _ = _temp_repo(tmp_path)
    assert check_tag(repo, {"tag": "d9.9-pass", "revision": first}) is False
