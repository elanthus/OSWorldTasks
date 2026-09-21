"""The Haiku CLI replication publication is complete and reproducible."""

import copy
import json

import pytest

from scripts.publish_haiku_cli_replication import DIRECTORY, build, paired


def test_committed_derivative_reproduces_and_retains_failures():
    data, report = build()
    assert report == (DIRECTORY / "report.md").read_text()
    assert data["conditions"]["history"]["classifications"] == {
        "success_termination": 28,
        "invalid_output": 18,
        "step_limit_truncation": 4,
    }
    assert data["conditions"]["stateless"]["classifications"] == {
        "step_limit_truncation": 41,
        "success_termination": 5,
        "invalid_output": 4,
    }
    assert data["paired"] == {
        "pairs": 50,
        "outcomes": {"both": 0, "history_only": 28, "stateless_only": 5, "neither": 17},
    }
    assert data["accounting"]["unresolved_invocations"] == 0
    assert data["accounting"]["invocation_statuses"] == {"response": 2416}
    assert data["provider_calls_made"] == 0


def test_paired_rejects_missing_or_duplicate_arm():
    snapshot = json.loads((DIRECTORY / "snapshot.json").read_text())
    with pytest.raises(ValueError, match="unpaired"):
        paired(snapshot["results"][:-1])
    rows = copy.deepcopy(snapshot["results"])
    rows[-1] = copy.deepcopy(rows[0])
    with pytest.raises(ValueError, match="unpaired"):
        paired(rows)


def test_build_rejects_changed_snapshot_bytes(tmp_path):
    for name in ("snapshot.json", "sources.json"):
        (tmp_path / name).write_bytes((DIRECTORY / name).read_bytes())
    path = tmp_path / "snapshot.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="snapshot digest mismatch"):
        build(tmp_path)


def test_snapshot_is_response_free_and_records_exact_caps():
    text = (DIRECTORY / "snapshot.json").read_text()
    snapshot = json.loads(text)
    assert "/Users/" not in text
    assert "/private/" not in text
    assert "data:image/" not in text
    assert snapshot["fresh_cohort"] == {
        "assignments": 100,
        "conditions": {"history": 50, "stateless": 50},
        "confirmatory_tasks_exposed": 0,
        "prior_outcomes_reused": 0,
    }
    assert snapshot["approval_digest"].startswith("sha256:")
    assert snapshot["accounting"]["environment_actions"] == 2394
    assert snapshot["accounting"]["model_attempts"] == 2416
    assert snapshot["accounting"]["provider_wire_requests"] == 2416
    assert snapshot["caps"] == {
        "environment_action_cap": 2862,
        "model_attempt_cap": 5724,
        "provider_control_request_cap": 0,
        "provider_wire_request_cap": 5724,
    }
