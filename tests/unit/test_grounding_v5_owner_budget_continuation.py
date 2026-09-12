"""Closed-boundary owner reconciliation is durable and never makes model calls."""

from contextlib import closing
from decimal import Decimal

import pytest

from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger
from scripts import run_grounding_v5_owner_budget_continuation as driver


@pytest.fixture
def reconciliation(tmp_path, monkeypatch):
    previous = tmp_path / "previous"
    previous.mkdir()
    approval = driver.read(driver.OWNER_APPROVAL)
    driver.write(tmp_path / "approval.json", approval)
    journal_path = tmp_path / "journal.sqlite"
    with closing(V5AttemptJournal(journal_path)) as journal:
        ledger = ReboundedMemoryLedger(Decimal(28), Decimal(0), journal=journal)
        assert ledger.reserve_wire("priced", Decimal(1))
        assert ledger.record_cost("priced", Decimal("0.25"), Decimal(1))
        for key in ("unpriced-1", "unpriced-2"):
            assert ledger.reserve_wire(key, Decimal("0.08"))
            ledger.reserve_unknown_charge(key, Decimal("0.08"))
        journal.append_event(
            event_key="previous-phase/closed",
            kind="reliable_continuation_closed",
            trial_id="previous-phase",
            step_index=0,
            payload={"stop_reason": "aggregate_budget_or_wire_stop", "transport_idle": True},
        )
        summary = {
            "execution_plan_digest": "test-plan",
            "phase_id": "previous-phase",
            "transport_idle_at_close": True,
            "aggregate_spend": ledger.to_dict(),
            "journal_integrity": journal.integrity_report(),
        }
        prefix = journal.events()
        driver.write(previous / "summary.json", summary)
        driver.write(
            previous / "execution-plan.json",
            {"execution_plan_digest": "test-plan", "source_digests": {}},
        )
    monkeypatch.setattr(driver, "PREVIOUS", previous)
    monkeypatch.setattr(driver, "PRIVATE", tmp_path)
    monkeypatch.setattr(driver, "JOURNAL", journal_path)
    monkeypatch.setattr(driver, "OWNER_APPROVAL", tmp_path / "approval.json")
    monkeypatch.setattr(driver, "RECONCILIATION", tmp_path / "reconciliation.json")
    monkeypatch.setattr(driver, "git", lambda *args: "a" * 40 if args[0] == "log" else "")
    return journal_path, prefix


def test_reconciliation_preserves_history_and_repeats_without_new_events(reconciliation):
    journal_path, prefix = reconciliation
    receipt = driver.reconcile_owner_budget()
    assert receipt["provider_calls"] == 0 and len(receipt["waivers"]) == 2
    assert Decimal(receipt["waived_budget_holds_usd"]) == Decimal("0.16")
    assert receipt["after_aggregate_spend"]["spent_usd"] == "0.25"
    assert receipt["after_aggregate_spend"]["unknown_reservation_usd"] == "0"
    with closing(V5AttemptJournal(journal_path)) as journal:
        assert journal.events()[: len(prefix)] == prefix
        after = journal.events()
    assert driver.reconcile_owner_budget() == receipt
    with closing(V5AttemptJournal(journal_path)) as journal:
        assert journal.events() == after


@pytest.mark.parametrize("tamper", [False, True])
def test_read_only_reconciliation_audit_checks_journal_and_rejects_changed_charge(
    reconciliation, monkeypatch, tamper
):
    import importlib.util

    path = driver.ROOT / "artifacts/grounding-v5-d58-owner-budget-continuation/analyze.py"
    spec = importlib.util.spec_from_file_location("owner_budget_audit", path)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    for name in ("OWNER_APPROVAL", "RECONCILIATION", "PREVIOUS"):
        monkeypatch.setattr(audit, name, getattr(driver, name))
    journal_path, _ = reconciliation
    receipt = driver.reconcile_owner_budget()
    if tamper:
        receipt["after_aggregate_spend"]["spent_usd"] = "0.26"
        driver.write(driver.RECONCILIATION, receipt)
        with pytest.raises(AssertionError):
            audit.verify_reconciliation(journal_path)
    else:
        verified = audit.verify_reconciliation(journal_path)
        assert verified["private_journal_verified"] and verified["prior_prefix_preserved"]
        assert verified["waivers_verified"] == 2 and verified["provider_calls"] == 0


def test_reconciliation_recovers_after_one_durable_waiver(reconciliation, monkeypatch):
    journal_path, prefix = reconciliation
    append = V5AttemptJournal.append_event

    class Interrupted(BaseException):
        pass

    def interrupt(self, **kwargs):
        result = append(self, **kwargs)
        if kwargs["kind"] == "spend_unknown_budget_waived":
            raise Interrupted
        return result

    monkeypatch.setattr(V5AttemptJournal, "append_event", interrupt)
    with pytest.raises(Interrupted):
        driver.reconcile_owner_budget()
    monkeypatch.setattr(V5AttemptJournal, "append_event", append)
    receipt = driver.reconcile_owner_budget()
    assert len(receipt["waivers"]) == 2
    assert receipt["after_aggregate_spend"]["budget_accounted_spend_usd"] == "0.25"
    with closing(V5AttemptJournal(journal_path)) as journal:
        assert journal.events()[: len(prefix)] == prefix
        assert len([e for e in journal.events() if e.kind == "spend_unknown_budget_waived"]) == 2


@pytest.mark.parametrize("changed", ["active", "missing_closure", "approval"])
def test_reconciliation_refuses_active_or_changed_state(reconciliation, changed):
    journal_path, prefix = reconciliation
    if changed == "approval":
        approval = driver.read(driver.OWNER_APPROVAL)
        approval["aggregate_ceiling_usd"] = "35.00"
        driver.write(driver.OWNER_APPROVAL, approval)
    elif changed == "missing_closure":
        summary = driver.read(driver.PREVIOUS / "summary.json")
        summary["phase_id"] = "not-closed"
        driver.write(driver.PREVIOUS / "summary.json", summary)
    else:
        with closing(V5AttemptJournal(journal_path)) as journal:
            ledger = ReboundedMemoryLedger(Decimal(28), Decimal(0), journal=journal)
            assert ledger.reserve_wire("still-active", Decimal(1))
            prefix = journal.events()
    with pytest.raises(ValueError):
        driver.reconcile_owner_budget()
    with closing(V5AttemptJournal(journal_path)) as journal:
        assert journal.events() == prefix
        assert journal.event(driver.OWNER_AUTHORIZATION_KEY) is None
