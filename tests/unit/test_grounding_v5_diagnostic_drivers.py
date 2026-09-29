"""Focus and reliable diagnostic drivers record measurements and honour stop rules."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest

from pixelgym.grounding.v5.journal import V5AttemptJournal

ROOT = Path(__file__).resolve().parents[2]


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
