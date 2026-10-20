"""Private LFS backup refuses unsafe targets and verifies bytes from an independent fetch."""

from __future__ import annotations

import io
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import retention_lfs as lfs
from pixelgym.grounding.v5.retention import _hash, create_archive
from pixelgym.serialization import canonical_json_bytes

REPOSITORY = "fixture/private-evidence"
COMMIT = "a" * 40


@pytest.fixture
def budget(tmp_path):
    path = tmp_path / "budget.json"
    value = {
        "schema_version": lfs.BUDGET_SCHEMA,
        "repository": REPOSITORY,
        "product": "git_lfs",
        "budget_usd": 0,
        "stop_usage": True,
        "scope": "account",
        "observed_via": "github-billing-ui",
        "observed_at_utc": datetime.now(UTC).isoformat(),
    }
    path.write_bytes(canonical_json_bytes(value))
    return path


@pytest.fixture
def archive(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "observations.json").write_text('{"stored":true}')
    with sqlite3.connect(run / "events.sqlite") as db:
        db.execute("CREATE TABLE events (value TEXT)")
        db.execute("INSERT INTO events VALUES ('committed evidence')")
    target = tmp_path / "archive"
    info = create_archive(run, target)
    return target, info["manifest_digest"]


@pytest.mark.parametrize(
    "patch",
    [
        {"budget_usd": 1},
        {"budget_usd": False},
        {"budget_usd": 0.0},
        {"stop_usage": False},
        {"repository": "other/repository"},
        {"product": "actions"},
        {"scope": "other"},
        {"observed_via": "assumed-free-tier"},
        {"observed_at_utc": "invalid"},
        {"observed_at_utc": "2020-01-01T00:00:00+00:00"},
        {"observed_at_utc": "2999-01-01T00:00:00+00:00"},
        {"observed_at_utc": "2026-01-01T00:00:00"},
    ],
)
def test_invalid_budget_refuses_transfer(budget, patch):
    value = json.loads(budget.read_bytes())
    value.update(patch)
    budget.write_bytes(canonical_json_bytes(value))
    with pytest.raises(ValueError):
        lfs.check_budget(budget, REPOSITORY)


def test_budget_expires_without_sleep(budget):
    observed = datetime.fromisoformat(json.loads(budget.read_bytes())["observed_at_utc"])
    lfs.check_budget(budget, REPOSITORY, now=observed + timedelta(hours=24))
    with pytest.raises(ValueError, match="24 hours"):
        lfs.check_budget(budget, REPOSITORY, now=observed + timedelta(hours=24, seconds=1))


def test_budget_record_inside_git_is_refused(budget):
    (budget.parent / ".git").mkdir()
    with pytest.raises(ValueError, match="outside Git"):
        lfs.check_budget(budget, REPOSITORY)


def test_relative_budget_record_inside_nested_checkout_is_refused(budget, monkeypatch):
    (budget.parent / ".git").mkdir()
    nested = budget.parent / "subdirectory"
    nested.mkdir()
    record = nested / "budget.json"
    shutil.copyfile(budget, record)
    monkeypatch.chdir(nested)
    with pytest.raises(ValueError, match="outside Git"):
        lfs.check_budget(Path("budget.json"), REPOSITORY)


def test_archive_packaging_is_deterministic_and_self_contained(archive, tmp_path):
    source, digest = archive
    first, second = tmp_path / "first.gz", tmp_path / "second.gz"
    a = lfs.pack_archive(source, first, expected_digest=digest)
    b = lfs.pack_archive(source, second, expected_digest=digest)
    assert a == b
    restored = tmp_path / "unpacked"
    result = lfs.unpack_archive(first, restored, expected_digest=digest)
    assert result["manifest_digest"] == digest
    with sqlite3.connect(restored / "files" / "events.sqlite") as db:
        assert db.execute("SELECT value FROM events").fetchall() == [("committed evidence",)]


@pytest.mark.parametrize(
    "kind", ["parent", "absolute", "link", "duplicate", "unlisted", "oversize"]
)
def test_unsafe_packed_members_refused_without_partial_restore(tmp_path, kind):
    packed = tmp_path / "unsafe.tar.gz"
    with tarfile.open(packed, "w:gz") as tar:
        entry = tarfile.TarInfo({"parent": "../escape", "absolute": "/escape"}.get(kind, "x"))
        if kind == "link":
            entry.type, entry.linkname = tarfile.SYMTYPE, "/escape"
            tar.addfile(entry)
        elif kind == "oversize":
            entry.size = lfs.MAX_ARCHIVE_BYTES + 1024**2 + 1
            # No body is needed: the reader must refuse the declared size before copying.
            tar.addfile(entry)
        else:
            entry.size = 1
            tar.addfile(entry, io.BytesIO(b"x"))
            if kind == "duplicate":
                tar.addfile(entry, io.BytesIO(b"x"))
    destination = tmp_path / "unpacked"
    with pytest.raises(ValueError):
        lfs.unpack_archive(packed, destination, expected_digest="sha256:" + "0" * 64)
    assert not destination.exists()
    assert not (tmp_path / "escape").exists()


def test_archive_size_guard_prevents_upload(archive, tmp_path, monkeypatch):
    monkeypatch.setattr(lfs, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(ValueError, match="512 MiB"):
        lfs.pack_archive(archive[0], tmp_path / "packed.gz", expected_digest=archive[1])


@pytest.fixture
def remote(monkeypatch):
    state = {
        "metadata": {
            "full_name": REPOSITORY,
            "private": True,
            "visibility": "private",
            "archived": False,
            "disabled": False,
            "fork": False,
        },
        "roots": [],
        "uploads": 0,
        "downloads": 0,
        "objects": {},
        "corrupt": False,
    }

    def api(arguments, **kwargs):
        assert arguments == ["gh", "api", "repos/" + REPOSITORY]
        return json.dumps(state["metadata"])

    class StoredObjectFixture:
        def __init__(self, url, root):
            assert url == "https://github.com/" + REPOSITORY + ".git"
            root.mkdir()
            self.root = root
            state["roots"].append(root)

        def publish(self, packed, digest, entry):
            state["uploads"] += 1
            state["objects"].setdefault(digest, (packed.read_bytes(), entry))
            return COMMIT

        def download(self, commit, digest):
            assert commit == COMMIT
            state["downloads"] += 1
            data, entry = state["objects"][digest]
            packed = self.root / "fetched.tar.gz"
            packed.write_bytes(b"corrupted" if state["corrupt"] else data)
            # The production adapter checks these bindings after LFS checkout.
            if _hash(packed) != entry["oid"] or packed.stat().st_size != entry["size"]:
                raise ValueError("downloaded LFS object digest or size mismatch")
            return packed, entry

    monkeypatch.setattr(lfs, "_run", api)
    monkeypatch.setattr(lfs, "_GitStore", StoredObjectFixture)
    return state


def test_remote_backup_uses_a_fresh_fetch_and_reproduces_stored_data(
    archive, budget, remote, tmp_path
):
    destination = tmp_path / "restored"

    def verify(restored, logs):
        assert (restored / "observations.json").read_text() == '{"stored":true}'
        return {"exit_status": 0, "output": {"real_provider_requests": 0}}

    receipt = lfs.backup_remote(
        archive[0],
        REPOSITORY,
        archive[1],
        budget,
        destination,
        verify_restored=verify,
        logs=tmp_path / "logs",
    )
    assert remote["uploads"] == remote["downloads"] == 1
    assert len(set(remote["roots"])) == 2
    assert receipt["fresh_git_and_lfs_cache"]
    assert receipt["verification_scope"] == "scripted-campaign"
    assert receipt["billing_setting_verified_by_api"] is False
    assert receipt["transport_source_digest"] == _hash(Path(lfs.__file__))
    assert receipt["restored_archive"]["manifest_digest"] == archive[1]
    # Remove every local source: the stored remote object still permits another restore.
    shutil.rmtree(archive[0])
    shutil.rmtree(destination)
    restored = tmp_path / "second-restore"
    receipt = lfs.restore_remote(REPOSITORY, COMMIT, archive[1], restored, budget)
    assert remote["downloads"] == 2
    assert (restored / "events.sqlite").is_file()
    assert receipt["verification_scope"] == "archive-bytes"


@pytest.mark.parametrize(
    "patch",
    [
        {"private": False},
        {"visibility": "public"},
        {"fork": True},
        {"archived": True},
        {"disabled": True},
        {"full_name": "other/repository"},
    ],
)
def test_private_target_required_before_any_git_transfer(archive, budget, remote, tmp_path, patch):
    remote["metadata"].update(patch)
    with pytest.raises(ValueError, match="private"):
        lfs.backup_remote(archive[0], REPOSITORY, archive[1], budget, tmp_path / "restore")
    assert remote["roots"] == []


def test_corrupt_remote_download_produces_no_restore(archive, budget, remote, tmp_path):
    remote["corrupt"] = True
    with pytest.raises(ValueError, match="digest"):
        lfs.backup_remote(archive[0], REPOSITORY, archive[1], budget, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()
    assert archive[0].exists()


def test_mutating_verifier_cannot_receive_success_receipt(archive, budget, remote, tmp_path):
    def mutate(restored, logs):
        (restored / "observations.json").write_text("changed")
        return {"exit_status": 0}

    with pytest.raises(ValueError, match="mutated"):
        lfs.backup_remote(
            archive[0],
            REPOSITORY,
            archive[1],
            budget,
            tmp_path / "restore",
            verify_restored=mutate,
            logs=tmp_path / "logs",
        )


def test_credentials_do_not_reach_non_github_children(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-fixture")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-fixture")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "lfs.url")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://untrusted.invalid")
    environment = lfs._environment()
    assert "OPENAI_API_KEY" not in environment
    assert "ANTHROPIC_API_KEY" not in environment
    assert "GIT_CONFIG_COUNT" not in environment
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull


def test_failed_cli_does_not_publish_credentials(monkeypatch):
    class FailedProcess:
        returncode = 1

        def communicate(self, *, timeout):
            assert timeout == 180
            return b"", b"private-fixture-token"

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    with pytest.raises(ValueError, match="source evidence") as error:
        lfs._run(["gh", "api", "fixture"])
    assert "private-fixture-token" not in str(error.value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group termination")
@pytest.mark.parametrize("interruption", ["timeout", "keyboard"])
def test_command_interruption_kills_and_reaps_the_entire_process_group(monkeypatch, interruption):
    calls = []

    class InterruptedProcess:
        pid = 12345

        def communicate(self, *, timeout):
            calls.append(("communicate", timeout))
            if len(calls) == 1:
                if interruption == "timeout":
                    raise subprocess.TimeoutExpired("fixture", timeout)
                raise KeyboardInterrupt
            return b"", b""

    def spawn(arguments, **kwargs):
        assert kwargs["start_new_session"] is True
        assert kwargs["env"] == lfs._environment()
        return InterruptedProcess()

    def kill_group(pid, signum):
        calls.append(("killpg", pid, signum))

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(lfs.os, "killpg", kill_group)
    expected = ValueError if interruption == "timeout" else KeyboardInterrupt
    with pytest.raises(expected):
        lfs._run(["git", "fixture"], timeout_seconds=900)
    assert calls == [("communicate", 900), ("killpg", 12345, signal.SIGKILL), ("communicate", 5)]


def test_missing_cli_maps_to_safe_error_without_creating_a_process(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("fixture")

    monkeypatch.setattr(subprocess, "Popen", missing)
    with pytest.raises(ValueError, match="unavailable or timed out"):
        lfs._run(["git", "fixture"])


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group termination")
def test_pipe_cleanup_remains_bounded_after_group_termination(monkeypatch):
    calls = []

    class PipeFixture:
        pid = 12345
        stdout = io.BytesIO()
        stderr = io.BytesIO()

        def communicate(self, *, timeout):
            assert timeout == 5
            raise subprocess.TimeoutExpired("fixture", timeout)

        def wait(self, *, timeout):
            calls.append(("wait", timeout))

    monkeypatch.setattr(lfs.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    process = PipeFixture()
    lfs._stop_command(process)
    assert process.stdout.closed and process.stderr.closed
    assert calls == [(12345, signal.SIGKILL), ("wait", 5)]


def test_lfs_fetch_has_a_separate_transfer_deadline(tmp_path):
    packed = tmp_path / "archive.tar.gz"
    packed.write_bytes(b"stored object")
    digest = "sha256:" + "a" * 64
    entry = {"path": packed.name, "oid": _hash(packed), "size": packed.stat().st_size}
    calls = []

    class DownloadFixture:
        root = tmp_path

        def checkout(self, commit):
            return {"archives": {digest: entry}}

        def git(self, *arguments, timeout_seconds=180):
            calls.append((arguments, timeout_seconds))

    fetched, _ = lfs._GitStore.download(DownloadFixture(), COMMIT, digest)
    assert fetched == packed
    assert calls[0][0][:2] == ("lfs", "fetch")
    assert calls[0][1] == lfs.TRANSFER_TIMEOUT_SECONDS > 180
    assert calls[1][1] == 180


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "README.md").write_bytes(lfs.README)
    (root / ".gitattributes").write_bytes(lfs.ATTRIBUTES)
    digest = "sha256:" + "a" * 64
    entry = {"path": "archives/" + "a" * 64 + ".tar.gz", "oid": "sha256:" + "b" * 64, "size": 99}
    (root / "archives").mkdir()
    (root / entry["path"]).write_text(
        "version https://git-lfs.github.com/spec/v1\n" + f"oid {entry['oid']}\nsize 99\n"
    )
    value = {"schema_version": lfs.CATALOG_SCHEMA, "archives": {digest: entry}}
    (root / "catalog.json").write_bytes(canonical_json_bytes(value))

    def tracked(arguments, *, cwd):
        assert arguments == ["git", "ls-files", "-z"] and cwd == root
        return "\0".join(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())

    monkeypatch.setattr(lfs, "_run", tracked)
    return root, value


def test_catalog_binds_exact_pointer_and_size(catalog):
    root, value = catalog
    assert lfs._catalog(root) == value


@pytest.mark.parametrize("change", ["oid", "size", "path", "negative", "oversize", "schema"])
def test_invalid_catalog_is_refused_before_lfs_fetch(catalog, change):
    root, value = catalog
    entry = next(iter(value["archives"].values()))
    if change == "schema":
        value["schema_version"] = "unknown"
    else:
        key, replacement = {
            "oid": ("oid", "sha256:" + "c" * 64),
            "size": ("size", 100),
            "path": ("path", "../../outside"),
            "negative": ("size", -1),
            "oversize": ("size", lfs.MAX_ARCHIVE_BYTES + 1),
        }[change]
        entry[key] = replacement
    (root / "catalog.json").write_bytes(canonical_json_bytes(value))
    with pytest.raises(ValueError):
        lfs._catalog(root)


@pytest.mark.parametrize("extra", [".lfsconfig", ".github/workflows/upload.yml", "unlisted.tar.gz"])
def test_unexpected_remote_files_are_refused(catalog, extra):
    root, _ = catalog
    path = root / extra
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text("untrusted")
    with pytest.raises(ValueError, match="unexpected files"):
        lfs._catalog(root)


def test_catalog_cannot_follow_a_symlink(catalog, tmp_path):
    root, _ = catalog
    outside = tmp_path / "outside"
    outside.write_bytes(lfs.README)
    (root / "README.md").unlink()
    (root / "README.md").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        lfs._catalog(root)


def test_catalog_storage_guard_is_not_a_billing_quota(catalog, monkeypatch):
    monkeypatch.setattr(lfs, "MAX_STORAGE_BYTES", 98)
    with pytest.raises(ValueError, match="5 GiB"):
        lfs._catalog(catalog[0])


def test_missing_verifier_logs_are_refused_before_upload(archive, budget, remote, tmp_path):
    with pytest.raises(ValueError, match="log directory"):
        lfs.backup_remote(
            archive[0],
            REPOSITORY,
            archive[1],
            budget,
            tmp_path / "restore",
            verify_restored=lambda *_: {},
        )
    assert remote["uploads"] == 0


def test_cli_failed_restore_cannot_write_receipt(archive, budget, remote, tmp_path, monkeypatch):
    from scripts.backup_evidence_lfs import main

    remote["corrupt"] = True
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "backup_evidence_lfs",
            "backup",
            "--archive",
            str(archive[0]),
            "--repository",
            REPOSITORY,
            "--digest",
            archive[1],
            "--budget-record",
            str(budget),
            "--destination",
            str(tmp_path / "restore"),
            "--receipt",
            str(receipt),
        ],
    )
    with pytest.raises(ValueError, match="digest"):
        main()
    assert not receipt.exists()


def test_public_receipt_report_reads_stored_evidence_only(monkeypatch):
    from scripts import backup_evidence_lfs as command

    def forbidden(*args, **kwargs):
        raise AssertionError("report rendering must not fetch or execute evidence")

    monkeypatch.setattr(lfs, "_run", forbidden)
    monkeypatch.setattr(command, "_restored_check", forbidden)
    root = Path(__file__).resolve().parents[2] / "artifacts"
    public = root / "evidence-retention-20261020-lfs"
    receipts = [
        public / name for name in ("v1-receipt.json", "v2-receipt.json", "v2-repeat-receipt.json")
    ]
    report = command.report_receipts(
        receipts, [root / "evidence-retention-20261020", root / "evidence-retention-20261020-v2"]
    )
    assert report == (public / "REPORT.md").read_text()


@pytest.mark.parametrize("field", ["case_count", "report_digest", "replay_digest"])
def test_public_receipt_disagreement_is_detected(tmp_path, field):
    from scripts.backup_evidence_lfs import report_receipts

    root = Path(__file__).resolve().parents[2] / "artifacts"
    value = json.loads((root / "evidence-retention-20261020-lfs/v2-receipt.json").read_bytes())
    value["restored_source_check"]["output"][field] = 999 if field == "case_count" else "changed"
    receipt = tmp_path / "tampered.json"
    receipt.write_bytes(canonical_json_bytes(value))
    with pytest.raises(ValueError, match="differs|disagrees"):
        report_receipts([receipt], [root / "evidence-retention-20261020-v2"])


def test_review_successor_remote_receipt_report_is_reproducible(monkeypatch):
    from scripts import backup_evidence_lfs as command

    def forbidden(*args, **kwargs):
        raise AssertionError("stored receipt report attempted a remote operation")

    monkeypatch.setattr(lfs, "_run", forbidden)
    monkeypatch.setattr(command, "_restored_check", forbidden)
    root = Path(__file__).resolve().parents[2] / "artifacts"
    public = root / "evidence-retention-20261020-v3-lfs"
    assert (
        command.report_receipts(
            [public / "receipt.json"], [root / "evidence-retention-20261020-v3"]
        )
        == (public / "REPORT.md").read_text()
    )


def test_transport_recovery_publication_binds_source_and_reuses_object(monkeypatch):
    from scripts import backup_evidence_lfs as command

    def forbidden(*args, **kwargs):
        raise AssertionError("stored receipt report attempted a remote operation")

    monkeypatch.setattr(lfs, "_run", forbidden)
    monkeypatch.setattr(command, "_restored_check", forbidden)
    root = Path(__file__).resolve().parents[2] / "artifacts"
    public = root / "evidence-retention-20261020-v3-lfs"
    receipt = json.loads((public / "transport-receipt.json").read_bytes())
    original = json.loads((public / "receipt.json").read_bytes())
    binding = json.loads((public / "transport-source-binding.json").read_bytes())
    assert binding["schema_version"] == "pixelgym-lfs-transport-source-binding-v1"
    assert binding["files"] == {
        "pixelgym/grounding/v5/retention_lfs.py": receipt["transport_source_digest"]
    }
    assert len(binding["git_revision"]) == 40
    assert receipt["commit"] == original["commit"]
    assert receipt["lfs_object"] == original["lfs_object"]
    assert (
        command.report_receipts(
            [public / "transport-receipt.json"], [root / "evidence-retention-20261020-v3"]
        )
        == (public / "TRANSPORT-REPORT.md").read_text()
    )
