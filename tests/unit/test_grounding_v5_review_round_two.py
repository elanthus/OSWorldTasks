"""Regression fixtures for the second CodeRabbit review of PR 196."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import threading
from contextlib import closing
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import AttemptIdentity
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import SpendLedger
from pixelgym.grounding.v5.runner import InjectedInterruption
from scripts import prepare_grounding_v5_power_report as power
from scripts import verify_d58_gemini38_calibration as gemini
from scripts import verify_d58_reliable_continuation as reliable
from tests.unit.test_grounding_v5_memory_calibration import GoldenTransport
from tests.unit.test_grounding_v5_reliable_continuation import execute

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "reader", ["events", "terminal_attempt", "integrity_report", "_digest_version"]
)
def test_all_journal_readers_wait_for_transaction_rollback(tmp_path, monkeypatch, reader):
    class Rollback(Exception):
        pass

    reached = threading.Event()
    owner = threading.get_ident()
    results, errors = [], []
    with closing(V5AttemptJournal(tmp_path / "isolation.sqlite")) as journal:
        lock = journal._lock

        class ObservedLock:
            def __enter__(self):
                if threading.get_ident() != owner:
                    reached.set()
                lock.acquire()

            def __exit__(self, *args):
                lock.release()

        monkeypatch.setattr(journal, "_lock", ObservedLock())
        identity = AttemptIdentity("trial", 0, 0)
        query = lambda: getattr(journal, reader)(
            *([identity] if reader == "terminal_attempt" else [])
        )
        baseline = query()

        def read():
            try:
                results.append(query())
            except RuntimeError as error:
                errors.append(error)
            finally:
                reached.set()

        worker = threading.Thread(target=read)
        try:
            with pytest.raises(Rollback), journal._write_transaction():
                journal._connection.execute(
                    "INSERT INTO events(event_key, kind, trial_id, step_index, attempt_index, payload) VALUES (?, ?, ?, ?, ?, ?)",
                    ("uncommitted", "attempt_completed", "trial", 0, 0, b"{}"),
                )
                journal._connection.execute(
                    "INSERT INTO objects VALUES (?, ?, ?)", ("bad-digest", "fixture", b"bad")
                )
                journal._connection.execute("UPDATE journal_metadata SET value='invalid'")
                worker.start()
                assert reached.wait(5)
                raise Rollback
        finally:
            worker.join(5)
        assert not worker.is_alive()
        assert not errors
        assert results == [baseline]


@pytest.mark.parametrize("stop", ["budget", "time"])
def test_completed_stop_survives_publication_interruption(tmp_path, stop):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        transport = GoldenTransport(
            SpendLedger(Decimal(28), Decimal(28 if stop == "budget" else 0), journal=journal)
        )

        def interrupt(name):
            if name == "full_result_recorded":
                raise InjectedInterruption(name)

        with pytest.raises(InjectedInterruption):
            execute(journal, transport, boundary=interrupt, time_exhausted=lambda: stop == "time")
        saved = journal.event("history/full_completed").payload
        row = execute(journal, transport)
        assert row == saved
        assert row["classification"] == ("budget_stop" if stop == "budget" else "phase_time_stop")
        assert not transport.model_requests


def test_power_report_follows_changed_structured_inputs():
    data = json.loads(power.SOURCE.read_text())
    changed = deepcopy(data)
    changed.update(alpha=0.01, minimum_relevant_absolute_difference=0.15, power_target=0.70)
    changed["options"] = [deepcopy(data["options"][0])]
    row = changed["options"][0]
    row.update(independent_pairs=99, episodes_per_arm=103, additional_reliability_episodes=7)
    row["power"].update(
        observed_discordance=0.76, upper_sensitivity=0.72, all_discordant_stress=0.61
    )
    report = power.render_report(changed)
    for expected in (
        "15-point",
        "alpha 0.01",
        "99-pair",
        "70% power target",
        "76.0%",
        "72.0%",
        "61.0%",
        "| 7 |",
    ):
        assert expected in report
    for stale in ("168-pair", "85.9%", "80.3%", "48 additional", "95%"):
        assert stale not in report
    row["power"]["upper_sensitivity"] = 0.69
    assert "No listed option meets" in power.render_report(changed)


@pytest.mark.parametrize("existing", ["analysis.json", "verification.json", "files.json"])
def test_partial_recording_is_never_overwritten(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(gemini, "DIRECTORY", tmp_path)
    monkeypatch.setattr(gemini, "read", lambda name: {})
    monkeypatch.setattr(gemini, "render_report", lambda summary: "report")
    monkeypatch.setattr(gemini, "analyze", lambda *args: {})
    monkeypatch.setattr(
        gemini, "verify_journal", lambda *args: pytest.fail("must reject before journal audit")
    )
    monkeypatch.setattr(sys, "argv", ["verify", "--record"])
    (tmp_path / "report.md").write_text("report")
    (tmp_path / existing).write_bytes(b"original evidence")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(FileExistsError):
        gemini.main()
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("damage", ["none", "extra", "missing", "hash"])
def test_readonly_manifest_requires_exact_file_set(tmp_path, monkeypatch, damage):
    monkeypatch.setattr(reliable, "PUBLIC", tmp_path)
    monkeypatch.setattr(reliable, "verify", lambda journal: ({}, {}))
    monkeypatch.setattr(sys, "argv", ["verify"])
    (tmp_path / "analysis.json").write_text("{}")
    digest = "sha256:" + reliable.sha256_bytes(b"{}")
    (tmp_path / "files.json").write_text(
        json.dumps({"analysis.json": digest, "fixture.txt": digest})
    )
    (tmp_path / "fixture.txt").write_text("{}")
    if damage == "extra":
        (tmp_path / "unlisted.txt").write_text("extra")
    elif damage == "missing":
        (tmp_path / "fixture.txt").unlink()
    elif damage == "hash":
        (tmp_path / "fixture.txt").write_text("changed")
    if damage == "none":
        reliable.main()
    else:
        with pytest.raises(ValueError):
            reliable.main()


@pytest.mark.parametrize("function", ["verify", "verify_reconciliation"])
def test_owner_verification_rejects_corruption_under_optimization(tmp_path, function):
    code = """
from scripts import verify_d58_owner_budget_continuation as v
original = v.read
def corrupt(path):
    value = original(path)
    if path == v.RECONCILIATION:
        value['approval_digest'] = 'corrupt'
    if path == v.PUBLIC / 'execution-plan.json':
        value['execution_plan_digest'] = 'corrupt'
    return value
v.read = corrupt
"""
    code += f"v.{function}()\n"
    result = subprocess.run(
        [sys.executable, "-O", "-c", code], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "ValueError: evidence check failed" in result.stderr


def test_successor_checks_have_no_optimization_removable_assertions():
    for name in ("owner_budget_continuation", "reliable_continuation", "gemini38_calibration"):
        tree = ast.parse((ROOT / f"scripts/verify_d58_{name}.py").read_text())
        assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))


@pytest.mark.parametrize("damage", ["none", "pixels", "checkpoint"])
def test_focus_admission_records_measurements_under_optimization(tmp_path, damage):
    code = f"""
from pathlib import Path
from scripts import run_grounding_v5_focus_diagnostic as d
d.PUBLIC = Path({str(tmp_path)!r})
d.range = lambda *args: [5000]
d.COUNTERFACTUAL_SEEDS = ()
base = d.FocusMemoryBackend
class Changed(base):
    def screenshot(self):
        value = super().screenshot()
        if {damage!r} == 'pixels':
            value[0, 0, 0] ^= 1
        return value
    def checkpoint(self):
        value = super().checkpoint()
        return value + b' ' if {damage!r} == 'checkpoint' else value
d.FocusMemoryBackend = Changed
d.admission()
"""
    result = subprocess.run(
        [sys.executable, "-O", "-c", code], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if damage == "none":
        assert result.returncode == 0, result.stderr
        row = json.loads((tmp_path / "admission.json").read_text())["tasks"][0]
        assert row["changed_input_states"] > 0
        assert row["summed_differing_pixels_before_confinement_check"] > 0
        assert row["differing_pixels_outside_focused_input"] == 0
        assert (
            row["state_checkpoints_equal"]
            and row["consumer_pixels_equal"]
            and row["submissions_equal"]
        )
    else:
        assert result.returncode != 0 and "ValueError" in result.stderr
        assert not (tmp_path / "admission.json").exists()


def configure_diagnostic_fixture(tmp_path, monkeypatch, driver):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    public = tmp_path / "artifacts"
    public.mkdir()
    original = driver.PUBLIC
    plan = driver.read(original / "execution-plan.json")
    snapshot = driver.read(original / "price-recheck.json")
    snapshot["observed_at"] = datetime.now(UTC).isoformat()
    driver.write(public / "price-recheck.json", snapshot)
    journal_path = tmp_path / "journal.sqlite"
    with closing(V5AttemptJournal(journal_path)) as journal:
        ledger = driver.ReboundedMemoryLedger(
            Decimal(plan.get("aggregate_cap_usd", "20")), Decimal(0), journal=journal
        )
        plan["prior_spend"] = ledger.to_dict()
        plan["prior_integrity"] = journal.integrity_report()
        plan["prior_event_count"] = len(journal.events())
    monkeypatch.setattr(driver, "PUBLIC", public)
    monkeypatch.setattr(driver, "PRIVATE", tmp_path)
    monkeypatch.setattr(driver, "JOURNAL", journal_path)
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(driver, "NEW_SOURCES", ())
    monkeypatch.setattr(driver, "git", lambda *args: "")
    monkeypatch.setattr(driver, "canonical_plan", lambda: plan)
    if "curl_identity" in plan:
        binary = tmp_path / "fixture-curl"
        binary.write_bytes(b"fixture")
        plan["curl_identity"] = {
            "version_output": "fixture curl",
            "executable_digest": "sha256:" + driver.sha256_bytes(binary.read_bytes()),
        }
        monkeypatch.setattr(
            driver, "Path", lambda path: binary if path == "/usr/bin/curl" else Path(path)
        )
        monkeypatch.setattr(
            driver.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="fixture curl")
        )
    driver.write(public / "execution-plan.json", plan)
    return plan, journal_path


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_focus_driver_reports_escaping_exception_as_interrupted(tmp_path, monkeypatch, error):
    from scripts import run_grounding_v5_focus_diagnostic as driver

    plan, journal_path = configure_diagnostic_fixture(tmp_path, monkeypatch, driver)
    published = []

    class Transport:
        retired = False

        def __init__(self, *args, **kwargs):
            pass

        def wait_until_idle(self, *args):
            return True

    def interrupt(*args, **kwargs):
        raise error("fixture interruption")

    monkeypatch.setattr(driver, "IsolatedRequestBoundTransport", Transport)
    monkeypatch.setattr(driver, "build_screenshot_policy_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver, "run_condition", interrupt)
    monkeypatch.setattr(driver, "publish", published.append)
    with pytest.raises(error, match="fixture interruption"):
        driver.execute(plan["execution_plan_digest"])
    assert published[-1]["stop_reason"] == "interrupted"
    with closing(V5AttemptJournal(journal_path)) as journal:
        assert journal.event(f"{driver.PHASE}/closed") is None


def test_reliable_diagnostic_uses_declared_phase_cap_and_stops(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts import run_grounding_v5_reliable_diagnostic as driver

    plan, _ = configure_diagnostic_fixture(tmp_path, monkeypatch, driver)
    plan["phase_cap_usd"] = "0.125"
    driver.write(driver.PUBLIC / "execution-plan.json", plan)
    constructed, called, published = [], [], []

    class Transport:
        retired = False
        wire = SimpleNamespace(idle=True)

        def __init__(self, config, **kwargs):
            self.ledger = kwargs["ledger"]
            self.phase_start_accounted = self.ledger.budget_accounted_spend_usd
            self.phase_spend_limit = kwargs["phase_spend_limit"]
            constructed.append(self)

        def wait_until_idle(self, *args):
            return True

    def run(journal, **kwargs):
        transport = kwargs["transport"]
        called.append(kwargs["job"])
        transport.ledger.spent_usd += Decimal("0.125")
        return {}

    monkeypatch.setattr(driver, "ReliableTransport", Transport)
    monkeypatch.setattr(driver, "run_condition", run)
    monkeypatch.setattr(driver, "publish", published.append)
    driver.execute(plan["execution_plan_digest"])
    assert constructed[0].phase_spend_limit == Decimal("0.125")
    assert len(called) == 1
    assert published[-1]["stop_reason"] == "phase_cap_stop"


@pytest.mark.parametrize("accounting", [False, True])
def test_revision_wrapper_copies_predecessors_and_clears_optimization(
    tmp_path, monkeypatch, accounting
):
    import io
    import tarfile
    from types import SimpleNamespace

    from scripts import verify_d58_at_revision as wrapper

    name = "grounding-v5-d58-owner-budget" if accounting else "grounding-v5-d58-reliable-diagnostic"
    directory = tmp_path / "artifacts" / name
    directory.mkdir(parents=True)
    filename = "reconciliation.json" if accounting else "execution-plan.json"
    (directory / filename).write_text(json.dumps({"driver_code_revision": "a" * 40}))
    predecessor = tmp_path / "artifacts/grounding-v5-d58-gemini38-calibration"
    predecessor.mkdir()
    (predecessor / "summary.json").write_text("{}")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w"):
        pass
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["git", "archive"]:
            return SimpleNamespace(stdout=archive.getvalue())
        environment = kwargs["env"]
        assert "PYTHONOPTIMIZE" not in environment
        assert "OPENROUTER_API_KEY" not in environment
        assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
        tree = Path(environment["PYTHONPATH"])
        assert (tree / "artifacts" / predecessor.name / "summary.json").read_text() == "{}"
        assert ("--verify-reconciliation" in command) is accounting
        return SimpleNamespace(returncode=0, stdout='{"verified": true}', stderr="")

    monkeypatch.setattr(wrapper, "ROOT", tmp_path)
    monkeypatch.setattr(wrapper.subprocess, "run", run)
    monkeypatch.setenv("PYTHONOPTIMIZE", "2")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-never-sent")
    assert wrapper.verify(name, None)["verification"] == {"verified": True}
    assert len(calls) == 2
