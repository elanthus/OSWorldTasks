"""The fixed campaign is credential-free and independently restorable from both copies."""

from __future__ import annotations

import io
import json
import shutil
import socket
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import retention_validation as validation
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.retention import _hash, check_cleanup, verify_archive
from pixelgym.serialization import canonical_json_bytes
from scripts import validate_evidence_retention as command
from scripts.validate_evidence_retention import _environment, _restored_check, run_validation
from scripts.verify_retention_bundle import verify_bundle


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    temporary = tmp_path_factory.mktemp("retention-campaign").resolve()
    store, backup = temporary / "store", temporary / "backup"
    public = temporary / "public"
    # The workflow fixture is independent of an enclosing Git checkout. Production CLI
    # provenance is tested separately against explicit Git archive/status fixtures below.
    provenance = {
        "git_revision": "b" * 40,
        "files": {
            name: _hash(path) for name, path in validation.source_files(command.ROOT).items()
        },
    }
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(command, "_source_provenance", lambda _root: provenance)
        result = run_validation(store, backup, "unit-campaign", public)
    assert json.loads((public / "source-binding.json").read_bytes())["git_revision"] == "b" * 40
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
    assert verify_bundle(public)["checked_files"] == 6


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
        assert args[0][:3] == [sys.executable, "-I", "-B"]
        assert kwargs["env"] == _environment()
        assert kwargs["cwd"] == tmp_path / "restored"
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


@pytest.mark.parametrize(
    "filename",
    [
        "report.json",
        "observations.json",
        "source-binding.json",
        "archive-manifest.json",
        "retention-receipt.json",
        "REPORT.md",
    ],
)
def test_public_verifier_detects_changed_bundle_files(bundle, tmp_path, filename) -> None:
    _store, _backup, public, _result = bundle
    copy = tmp_path / "public"
    shutil.copytree(public, copy)
    path = copy / filename
    if filename == "REPORT.md":
        path.write_text("changed report")
    else:
        value = json.loads(path.read_bytes())
        if filename == "source-binding.json":
            value["git_revision"] = "changed"
        elif filename == "observations.json":
            value["cases"][0]["scripted_sends"] = 999
        elif filename == "archive-manifest.json":
            value["files"][0]["size"] = 999
        elif filename == "retention-receipt.json":
            value["restored_source_checks"][0]["exit_status"] = 1
        else:
            value["case_count"] = 999
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        verify_bundle(copy)


@pytest.fixture
def git_source(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "pixelgym").mkdir(parents=True)
    (root / "scripts").mkdir()
    contents = {
        "pixelgym/fixture.py": b"stored source\n",
        "scripts/validate_evidence_retention.py": b"entry point\n",
        "pyproject.toml": b"[project]\nname='fixture'\n",
    }
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for name, value in contents.items():
            (root / name).write_bytes(value)
            entry = tarfile.TarInfo(name)
            entry.size = len(value)
            archive.addfile(entry, io.BytesIO(value))
    state = {
        "status": b"",
        "revision": b"c" * 40,
        "toplevel": str(root).encode(),
        "archive": data.getvalue(),
        "arguments": [],
    }

    def run(arguments, **kwargs):
        assert arguments[0] == "/fixture/tools/git"
        assert kwargs["cwd"] == root
        assert kwargs["env"] == _environment()
        state["arguments"].append(arguments[1:])
        kind = arguments[1]
        if kind == "rev-parse":
            output = state["toplevel"] if arguments[2] == "--show-toplevel" else state["revision"]
        elif kind == "status":
            output = state["status"]
        elif kind == "archive":
            output = state["archive"]
        else:
            raise AssertionError(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout=output)

    monkeypatch.setattr(command.shutil, "which", lambda name: "/fixture/tools/git")
    monkeypatch.setattr(command.subprocess, "run", run)
    return root, state


def test_source_provenance_resolves_git_before_scrubbing_path(git_source, monkeypatch):
    root, state = git_source
    monkeypatch.setattr(command.os, "defpath", "/empty/system/path")
    result = command._source_provenance(root)
    assert result["git_revision"] == "c" * 40
    assert result["files"] == {
        name: _hash(path) for name, path in validation.source_files(root).items()
    }
    assert state["arguments"][2][:3] == ["status", "--porcelain", "--untracked-files=all"]


@pytest.mark.parametrize("problem", ["dirty", "untracked", "ignored", "wrong-root", "revision"])
def test_source_provenance_refuses_unbound_bytes_before_capture(git_source, tmp_path, problem):
    root, state = git_source
    if problem in {"dirty", "untracked"}:
        state["status"] = b" M pixelgym/fixture.py" if problem == "dirty" else b"?? pixelgym/new.py"
    elif problem == "ignored":
        (root / "pixelgym/ignored.py").write_text("absent from Git but selected for capture")
    elif problem == "wrong-root":
        state["toplevel"] = str(root.parent).encode()
    else:
        state["revision"] = b"invalid"
    with pytest.raises(ValueError, match="clean|checkout root|Git revision"):
        run_validation(
            tmp_path / "store", tmp_path / "backup", "refused", tmp_path / "public", root=root
        )
    assert not (tmp_path / "store").exists()


def test_missing_git_refuses_before_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(command.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="Git is required"):
        run_validation(tmp_path / "store", tmp_path / "backup", "no-git", tmp_path / "public")
    assert not (tmp_path / "store").exists()


def test_publication_scans_all_values_before_creating_output(tmp_path):
    public = tmp_path / "public"
    with pytest.raises(ValueError, match="runtime secret handle"):
        command._publish_bundle(
            public,
            {
                "observations.json": {"safe": "fixture"},
                "retention-receipt.json": {"unexpected": "vault://private-fixture"},
            },
        )
    assert not public.exists()


@pytest.mark.parametrize("stale", ["checks", "receipts"])
def test_stale_logs_and_receipts_fail_before_capture(tmp_path, monkeypatch, stale):
    store = tmp_path / "store"
    old = store / "checks" / "stale" if stale == "checks" else store / "receipts" / "stale.json"
    old.parent.mkdir(parents=True)
    old.write_text("preserve old evidence")

    def forbidden(*args, **kwargs):
        raise AssertionError("a stale destination reached source inspection or capture")

    monkeypatch.setattr(command, "_source_provenance", forbidden)
    monkeypatch.setattr(command, "capture_campaign", forbidden)
    with pytest.raises(FileExistsError, match="checks, or receipt"):
        run_validation(store, tmp_path / "backup", "stale", tmp_path / "public")
    assert old.read_text() == "preserve old evidence"
    assert not (store / "runs").exists()


def test_source_change_after_provenance_prevents_binding(git_source, tmp_path, monkeypatch):
    root, _ = git_source

    def capture(run):
        run.mkdir()
        (root / "pixelgym/fixture.py").write_text("changed during capture")

    monkeypatch.setattr(command, "capture_campaign", capture)
    with pytest.raises(ValueError, match="source changed after Git provenance"):
        run_validation(
            tmp_path / "store", tmp_path / "backup", "race", tmp_path / "public", root=root
        )
    assert not (tmp_path / "store/runs/race/source-binding.json").exists()
    assert not (tmp_path / "store/archives").exists()


def test_replay_and_report_cannot_open_network_sockets(bundle, monkeypatch):
    store, _, _, _ = bundle

    def forbidden(*args, **kwargs):
        raise AssertionError("replay/report attempted a network socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    run = store / "runs/unit-campaign"
    assert validation.replay_campaign(run)["case_count"] == 7
    assert validation.regenerate_report(run)["case_count"] == 7


@pytest.mark.parametrize("field", ["screenshot", "reward", "terminated", "truncated", "checkpoint"])
def test_dispatch_replay_detects_each_changed_result(bundle, monkeypatch, field):
    store, _, _, _ = bundle
    original = validation.PixelGuiEnv.step

    def corrupt(self, action):
        observation, reward, terminated, truncated, info = original(self, action)
        if field == "screenshot":
            observation = observation.copy()
            observation[0, 0, 0] ^= 1
        if field == "reward":
            reward += 0.25
        return (
            observation,
            reward,
            (not terminated if field == "terminated" else terminated),
            (not truncated if field == "truncated" else truncated),
            info,
        )

    monkeypatch.setattr(validation.PixelGuiEnv, "step", corrupt)
    if field == "checkpoint":
        monkeypatch.setattr(
            validation.V5FakeBackend, "checkpoint", lambda self: b"changed checkpoint"
        )
    with pytest.raises(ValueError, match="stored dispatch does not match replay"):
        validation.replay_campaign(store / "runs/unit-campaign")


@pytest.mark.parametrize(
    "field,message",
    [
        ("scripted_sends", "scripted request accounting mismatch"),
        ("trial_id", "validation identity changed"),
    ],
)
def test_replay_rejects_observation_accounting_and_identity(bundle, tmp_path, field, message):
    store, _, _, _ = bundle
    run = store / "runs/unit-campaign"
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "cases").symlink_to(run / "cases", target_is_directory=True)
    value = json.loads((run / "observations.json").read_bytes())
    value["cases"][0][field] = 999 if field == "scripted_sends" else "different-trial"
    (copy / "observations.json").write_bytes(canonical_json_bytes(value))
    with pytest.raises(ValueError, match=message):
        validation.replay_campaign(copy)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("deleted_source", True, "public campaign scope"),
        ("cleanup_precondition_satisfied", False, "public campaign scope"),
        ("real_provider_requests", 1, "public campaign scope"),
        ("restored_copies", [], "public campaign scope"),
    ],
)
def test_public_verifier_checks_receipt_scope(bundle, tmp_path, field, value, message):
    _, _, public, _ = bundle
    copy = tmp_path / "public"
    shutil.copytree(public, copy)
    path = copy / "retention-receipt.json"
    receipt = json.loads(path.read_bytes())
    receipt[field] = value
    path.write_bytes(canonical_json_bytes(receipt))
    with pytest.raises(ValueError, match=message):
        verify_bundle(copy)


def test_public_verifier_checks_restored_file_count(bundle, tmp_path):
    _, _, public, _ = bundle
    copy = tmp_path / "public"
    shutil.copytree(public, copy)
    path = copy / "retention-receipt.json"
    receipt = json.loads(path.read_bytes())
    receipt["restored_copies"][0]["restored_file_count"] = 0
    path.write_bytes(canonical_json_bytes(receipt))
    with pytest.raises(ValueError, match="restored copy does not bind"):
        verify_bundle(copy)


def test_public_verifier_rejects_duplicate_member_even_with_updated_digest(bundle, tmp_path):
    _, _, public, _ = bundle
    copy = tmp_path / "public"
    shutil.copytree(public, copy)
    path = copy / "archive-manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["files"].append(manifest["files"][0])
    path.write_bytes(canonical_json_bytes(manifest))
    path = copy / "retention-receipt.json"
    receipt = json.loads(path.read_bytes())
    receipt["manifest_digest"] = content_digest(manifest)
    path.write_bytes(canonical_json_bytes(receipt))
    with pytest.raises(ValueError, match="duplicate archive manifest member"):
        verify_bundle(copy)


@pytest.mark.parametrize(
    "name",
    [
        "evidence-retention-20261020",
        "evidence-retention-20261020-v2",
        "evidence-retention-20261020-v3",
    ],
)
def test_committed_retention_bundle_verifies(name):
    root = Path(__file__).resolve().parents[2]
    assert verify_bundle(root / "artifacts" / name)["checked_files"] == 6
