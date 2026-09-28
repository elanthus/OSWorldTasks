"""Public D5.9 counts reproduce from stored evidence without restricted journals."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from scripts import report_grounding_v5_d59_haiku as report

PUBLIC = Path(__file__).resolve().parents[2] / "artifacts/grounding-v5-d59-haiku-results"


def test_public_report_reproduces_without_private_journals():
    data = json.loads((PUBLIC / "report.json").read_text())
    assert len(data["results"]) == data["completed"] == data["assigned"] == 432
    assert len({r["trial_id"] for r in data["results"]}) == 432
    assert report.counts(data["results"]) == data["counts"]
    assert report.render(data) == (PUBLIC / "report.md").read_text()
    assert all(set(row) == set(report.ROW_FIELDS) for row in data["results"])


def test_report_rejects_changed_counts():
    data = json.loads((PUBLIC / "report.json").read_text())
    data["counts"]["primary"]["history"]["success_termination"] += 1
    with pytest.raises(ValueError, match="counts differ"):
        report.render(data)


def test_projection_omits_raw_response_and_local_path(tmp_path):
    data = json.loads((PUBLIC / "report.json").read_text())
    binding = {"runtime_identity": data["runtime_identity"]}
    amendment = {
        k: data[k]
        for k in [
            "continuation_source_sha256",
            "continuation_rule",
            "original_started_at",
            "deadline",
        ]
    }
    summary = {
        **deepcopy(data),
        "error": None,
        "execution_binding_digest": content_digest(binding),
        "continuation_digest": content_digest(amendment),
        "invocation_integrity": {"records": []},
        "raw_response": "private text",
        "local_path": "/private/operator",
    }
    summary["results"][0]["private_checkpoint"] = "private text"
    for name, value in [
        ("summary.json", summary),
        ("execution-binding.json", binding),
        ("continuation-approval.json", amendment),
        ("execution-plan.json", {}),
        ("owner-approval.json", {}),
    ]:
        (tmp_path / name).write_text(json.dumps(value))
    projected = report.project(tmp_path)
    assert "private text" not in json.dumps(projected)
    assert "/private/operator" not in json.dumps(projected)
    assert projected["counts"] == data["counts"]
    summary["results"].append(summary["results"][0])
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="consistent completed"):
        report.project(tmp_path)
