"""The S7 retention record renders only from stored JSON and fails closed on disagreement."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.render_s7_retention_record import check_record, render

PUBLIC = Path(__file__).resolve().parents[2] / "artifacts/evidence-retention-s7-20261026-successor"


def fixture_record() -> dict[str, Any]:
    digest = "sha256:" + "a" * 64
    output = {
        "activation_performed": False,
        "cumulative_qualification_sends": 227,
        "fake_sends": {"qualification": 77},
        "inspection_unchanged": True,
        "policy_or_transport_executed": False,
        "publication_performed": False,
        "real_provider_requests": 0,
        "report_generated_from_stored_results_only": True,
        "report_sha256": "sha256:" + "b" * 64,
        "source_binding_sha256": "sha256:" + "c" * 64,
        "source_revision": "3" * 40,
        "stored_action_replay_executed": True,
        "verification_sha256": "sha256:" + "d" * 64,
    }
    copy_receipt = {
        "bytes": 10,
        "file_count": 2,
        "manifest_digest": digest,
        "restored_file_count": 2,
        "source_fingerprint": "sha256:" + "e" * 64,
    }
    return {
        "schema_version": "pixelgym-s7-successor-retention-record-v1",
        "run_id": "fixture",
        "qualified_source_revision": "3" * 40,
        "archive": {k: v for k, v in copy_receipt.items() if k != "restored_file_count"},
        "local_restore": dict(copy_receipt),
        "remote_copy": {
            "budget_observation": {"budget_usd": 0, "stop_usage": True, "observed_at_utc": "t"},
            "commit": "0" * 40,
            "lfs_object": {"size": 5},
            "manifest_digest": digest,
            "repository": "owner/private",
            "restored_archive": dict(copy_receipt),
        },
        "checks": [
            {"label": label, "exit_status": 0, "command": "fixture", "output": dict(output)}
            for label in ("original", "local-restore", "remote-restore")
        ],
        "digests_identical_across_checks": True,
        "activation_performed": False,
        "publication_of_qualification_content": False,
        "milestone_verdict": None,
        "limitations": ["fixture limitation"],
    }


def test_render_is_deterministic_and_scopes_the_fake_send_claim() -> None:
    record = fixture_record()
    rendered = render(record)
    assert rendered == render(json.loads(json.dumps(record)))
    assert "without making any additional fake send" in rendered
    assert '| original | 0 | {"qualification": 77} | 227 |' in rendered
    assert "no fake send was made" not in rendered


@pytest.mark.parametrize(
    ("damage", "match"),
    [
        (lambda r: r["local_restore"].update(manifest_digest="sha256:" + "f" * 64), "differs"),
        (lambda r: r["checks"][1].update(exit_status=1), "incomplete or failed"),
        (lambda r: r["checks"][2]["output"].update(real_provider_requests=1), "boundary"),
        (lambda r: r["checks"][2]["output"].update(verification_sha256="x"), "identical"),
        (lambda r: r["remote_copy"]["budget_observation"].update(stop_usage=False), "stop-usage"),
        (lambda r: r.update(milestone_verdict="PASS"), "verdict"),
        (lambda r: r["checks"].pop(), "three ordered"),
        (lambda r: r["checks"][0]["output"].update(source_revision="4" * 40), "revision"),
    ],
)
def test_check_record_fails_closed(damage: Any, match: str) -> None:
    record = copy.deepcopy(fixture_record())
    damage(record)
    with pytest.raises(ValueError, match=match):
        check_record(record)


@pytest.mark.skipif(not PUBLIC.exists(), reason="public record not checked in")
def test_checked_in_report_matches_its_record() -> None:
    record = json.loads((PUBLIC / "recovery.json").read_bytes())
    assert render(record) == (PUBLIC / "REPORT.md").read_text()
