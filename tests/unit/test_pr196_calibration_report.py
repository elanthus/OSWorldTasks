"""Publication must retain failed outcomes and reject corrupted continuation evidence."""

import copy
import json

import pytest

from pixelgym.grounding.v5.journal import V5AttemptJournal
from scripts.publish_pr196_calibration import (
    CHAINS,
    COHORTS,
    DIRECTORY,
    ReadOnlyJournal,
    build,
    metrics,
    paired,
    select_chain,
)


def snapshots():
    return {name: json.loads((DIRECTORY / (name + ".json")).read_text()) for name in COHORTS}


def test_committed_derivative_reproduces_and_retains_invalid_and_interrupted():
    data, report = build()
    assert report == (DIRECTORY / "report.md").read_text()
    haiku = data["models"]["haiku"]
    assert sum(r["classification"] == "invalid_output" for r in haiku["rows"]) == 6
    assert len(haiku["interrupted_attempts"]) == 2
    assert haiku["paired"]["outcomes"] == {
        "both": 2,
        "history_only": 29,
        "stateless_only": 3,
        "neither": 16,
    }
    assert len(haiku["rows"]) == 100
    assert haiku["representative_pairs"]["pairs"] == 44


@pytest.mark.parametrize("mutation", ["duplicate", "drop", "rerun_terminal", "task_change"])
def test_rejects_invalid_continuation_partition(mutation):
    data = snapshots()
    successor = data["haiku-cli-continuation"]
    if mutation == "duplicate":
        successor["results"].append(copy.deepcopy(successor["results"][0]))
    elif mutation == "drop":
        successor["jobs"].pop()
    elif mutation == "rerun_terminal":
        successor["jobs"].append(data["haiku-v4"]["jobs"][0])
    else:
        successor["results"][0]["task_digest"] = "sha256:changed"
    with pytest.raises(ValueError):
        select_chain(data, CHAINS["haiku"])


def test_paired_rejects_missing_arm():
    row = snapshots()["haiku-v4"]["results"][0]
    with pytest.raises(ValueError, match="unpaired"):
        paired([row])


def test_invalid_first_choice_stays_in_attempt_denominator():
    row = copy.deepcopy(snapshots()["haiku-v4"]["results"][0])
    row["first_attempts"] = [{"correct": False, "valid_choice": False}]
    result = metrics([row])
    assert result["first_choices_attempted"] == 1
    assert result["first_choices_valid"] == result["first_choices_correct"] == 0


def test_readonly_audit_cannot_write_and_detects_corrupt_object(tmp_path):
    path = tmp_path / "journal.sqlite"
    journal = V5AttemptJournal(path)
    journal.put_object("fixture", b"evidence")
    expected = journal.integrity_report()
    journal.close()
    reader = ReadOnlyJournal(path)
    try:
        assert reader.integrity_report() == expected
        with pytest.raises(Exception, match="readonly"):
            reader._connection.execute("DELETE FROM objects")
    finally:
        reader.close()
    import sqlite3

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE objects SET data=?", (b"changed",))
    reader = ReadOnlyJournal(path)
    try:
        with pytest.raises(ValueError, match="object digest mismatch"):
            reader.integrity_report()
    finally:
        reader.close()


def test_rejects_changed_snapshot_bytes(tmp_path):
    import shutil

    for p in DIRECTORY.glob("*.json"):
        shutil.copyfile(p, tmp_path / p.name)
    path = tmp_path / "haiku-v4.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="snapshot digest mismatch"):
        build(tmp_path)
