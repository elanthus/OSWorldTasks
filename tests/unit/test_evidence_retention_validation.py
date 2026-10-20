"""The fixed campaign is credential-free and independently restorable from both copies."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import retention_validation as validation
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.retention import check_cleanup, verify_archive
from scripts.validate_evidence_retention import _environment, _restored_check, run_validation


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    temporary = tmp_path_factory.mktemp("retention-campaign").resolve()
    store, backup = temporary / "store", temporary / "backup"
    public = temporary / "public"
    result = run_validation(store, backup, "unit-campaign", public)
    return store, backup, public, result


def test_complete_campaign_restores_and_replays_both_copies(bundle) -> None:
    store, backup, public, result = bundle
    assert result["case_count"] == 7
    assert result["real_provider_requests"] == 0
    for check in result["restored_source_checks"]:
        assert check["exit_status"] == 0
        assert check["output"]["report_digest"] == result["report_digest"]
        assert check["output"]["real_provider_requests"] == 0
    run = store / "runs" / "unit-campaign"
    receipt = check_cleanup(
        run,
        store / "archives" / "unit-campaign",
        backup / "unit-campaign",
        expected_digest=result["manifest_digest"],
    )
    assert len(receipt["restored_copies"]) == 2
    report = json.loads((public / "report.json").read_bytes())
    assert report["milestone_verdict"] is None
    cases = {row["case"]: row for row in report["cases"]}
    assert cases["golden"]["reward_one_count"] == 1
    assert cases["premature_commit"]["reward_sum"] == 0
    assert cases["step_limit"]["classification"] == "step_limit_truncation"
    assert cases["call_cap"]["scripted_sends"] == 1
    assert cases["resume"]["replayed_dispatches"] == 1
    assert not list(public.rglob("*.sqlite"))
    assert content_digest(report) == result["report_digest"]
    assert "not D5.9" in (public / "REPORT.md").read_text()


def test_report_renderer_never_replays_or_calls_a_policy(bundle, monkeypatch) -> None:
    store, _backup, public, _result = bundle

    def forbidden(*args, **kwargs):
        raise AssertionError("report rendering must only read stored observations")

    monkeypatch.setattr(validation, "replay_campaign", forbidden)
    monkeypatch.setattr(validation, "capture_campaign", forbidden)
    monkeypatch.setattr(validation, "V5FakeBackend", forbidden)
    report = validation.regenerate_report(store / "runs" / "unit-campaign")
    assert report == json.loads((public / "report.json").read_bytes())


def test_replay_detects_changed_screenshot_bytes(bundle, monkeypatch) -> None:
    store, _backup, _public, _result = bundle
    original = validation.V5AttemptJournal.get_object

    def corrupt_screenshot(self, digest, *, expected_kind=None):
        data = original(self, digest, expected_kind=expected_kind)
        return b"changed" if len(data) == 1024 * 768 * 3 else data

    monkeypatch.setattr(validation.V5AttemptJournal, "get_object", corrupt_screenshot)
    with pytest.raises(ValueError, match="screenshot replay mismatch"):
        validation.replay_campaign(store / "runs" / "unit-campaign")


def test_child_environment_excludes_credentials_and_python_injection(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "private-fixture")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-fixture")
    monkeypatch.setenv("PYTHONPATH", "untrusted-code")
    monkeypatch.setenv("GIT_DIR", "other-checkout")
    assert set(_environment()) == {"PATH", "LANG", "PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE"}


def test_capture_campaign_cannot_open_network_sockets(tmp_path: Path, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("the credential-free campaign opened a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    run = tmp_path / "run"
    validation.capture_campaign(run)
    assert len(json.loads((run / "observations.json").read_bytes())["cases"]) == 7


@pytest.mark.parametrize("run_id", ["../outside", "name/child", "UPPER", "", "a" * 81])
def test_invalid_run_identity_cannot_create_evidence(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ValueError, match="run ID"):
        run_validation(tmp_path / "store", tmp_path / "backup", run_id, tmp_path / "public")
    assert not (tmp_path / "store").exists()


def test_retained_source_tampering_is_detected(bundle, tmp_path: Path) -> None:
    store, _backup, _public, result = bundle
    archive = store / "archives" / "unit-campaign"
    assert verify_archive(archive, expected_digest=result["manifest_digest"])
    # A changed source file is detected before report generation, even with a valid
    # observation projection. Keep the shared fixture intact by using a minimal copy.
    run = tmp_path / "run"
    (run / "source").mkdir(parents=True)
    (run / "source" / "code.py").write_text("changed")
    (run / "source-binding.json").write_text('{"files": {"code.py": "sha256:old"}}')
    with pytest.raises(ValueError, match="source snapshot"):
        validation.verify_source(run)


def test_output_collision_preserves_existing_evidence(bundle) -> None:
    store, backup, public, _result = bundle
    before = (public / "report.json").read_bytes()
    with pytest.raises(FileExistsError):
        run_validation(store, backup, "unit-campaign", public)
    assert (public / "report.json").read_bytes() == before


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_failed_restore_checks_preserve_private_output(tmp_path, monkeypatch, failure) -> None:
    def fail(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("fixture", 120, output=b"partial", stderr=b"diagnostic")
        return subprocess.CompletedProcess("fixture", 1, stdout=b"partial", stderr=b"diagnostic")

    monkeypatch.setattr(subprocess, "run", fail)
    logs = tmp_path / "private-logs"
    with pytest.raises(ValueError, match="verification"):
        _restored_check(tmp_path / "restored", logs)
    assert (logs / "stdout.txt").read_bytes() == b"partial"
    assert (logs / "stderr.txt").read_bytes() == b"diagnostic"
    assert json.loads((logs / "process.json").read_bytes())["timeout"] is (failure == "timeout")
