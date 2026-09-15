"""An owner budget waiver preserves uncertainty, real charges and active holds."""

from contextlib import closing
from decimal import Decimal

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.owner_budget import (
    OWNER_AUTHORIZATION_KEY,
    OWNER_BUDGET_RULE,
    OwnerZeroHoldLedger,
)
from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger

APPROVAL = content_digest({"owner": "zero unresolved budget holds"})


def authorize(journal):
    journal.append_event(
        event_key=OWNER_AUTHORIZATION_KEY,
        kind="owner_zero_hold_budget_authorized",
        trial_id="__spend_ledger__",
        step_index=0,
        payload={"approval_digest": APPROVAL, "rule": OWNER_BUDGET_RULE},
    )


def test_exact_recorded_authorization_required(tmp_path):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        with pytest.raises(ValueError, match="exact owner"):
            OwnerZeroHoldLedger(Decimal(28), Decimal(0), journal=journal, approval_digest=APPROVAL)
        authorize(journal)
        with pytest.raises(ValueError, match="exact owner"):
            OwnerZeroHoldLedger(Decimal(28), Decimal(0), journal=journal, approval_digest="other")


def test_waiver_is_append_only_replayable_and_preserves_known_cost(tmp_path):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        original = ReboundedMemoryLedger(Decimal(28), Decimal(0), journal=journal)
        assert original.reserve_wire("known", Decimal(2))
        assert original.record_cost("known", Decimal("1.25"), Decimal(2))
        assert original.reserve_wire("unknown", Decimal(3))
        original.reserve_unknown_charge("unknown", Decimal(3))
        original.revise_unknown_bound(
            {
                "reservation_id": content_digest("unknown"),
                "old_bound_usd": "3",
                "new_bound_usd": "0.08",
            },
            plan_digest="earlier-budget-plan",
        )
        prefix = journal.events()
        authorize(journal)
        ledger = OwnerZeroHoldLedger(
            Decimal(28), Decimal(0), journal=journal, approval_digest=APPROVAL
        )
        with pytest.raises(ValueError, match="reconciled"):
            ledger.reserve_wire("premature", Decimal(1))
        waivers = ledger.waive_existing_unknown_holds()
        assert len(waivers) == 1 and waivers[0]["previous_budget_hold_usd"] == "0.08"
        assert ledger.spent_usd == ledger.budget_accounted_spend_usd == Decimal("1.25")
        assert ledger.unknown_reservation_usd == 0 and ledger.unknown_charge_outcomes == 1
        assert ledger._settlements[content_digest("unknown")] == ("unknown", Decimal(0))
        assert journal.events()[: len(prefix)] == prefix
        assert ledger.waive_existing_unknown_holds() == []
        replay = OwnerZeroHoldLedger(
            Decimal(28), Decimal(0), journal=journal, approval_digest=APPROVAL
        )
        assert replay.to_dict() == ledger.to_dict()
        assert replay.record_cost("unknown", Decimal("0.04"), Decimal(3))
        assert replay.spent_usd == Decimal("1.29")
        again = OwnerZeroHoldLedger(
            Decimal(28), Decimal(0), journal=journal, approval_digest=APPROVAL
        )
        assert again.to_dict() == replay.to_dict()


def test_future_unknowns_have_zero_weight_but_active_requests_reserve_full_bound(tmp_path):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        authorize(journal)
        ledger = OwnerZeroHoldLedger(
            Decimal(1), Decimal(0), journal=journal, approval_digest=APPROVAL
        )
        assert ledger.reserve_wire("first", Decimal("0.8"))
        assert ledger.budget_accounted_spend_usd == Decimal("0.8")
        assert not ledger.reserve_wire("second", Decimal("0.3"))
        with pytest.raises(ValueError, match="idle"):
            ledger.waive_existing_unknown_holds()
        assert ledger.reserve_unknown_charge("first", Decimal("0.8")) == 0
        assert ledger.budget_accounted_spend_usd == 0
        assert ledger.reserve_wire("second", Decimal("0.8"))
        assert ledger.record_cost("second", Decimal("0.7"), Decimal("0.8"))
        assert not ledger.reserve_wire("third", Decimal("0.4"))
        assert ledger.record_cost("first", Decimal("0.2"), Decimal("0.8"))
        assert ledger.spent_usd == Decimal("0.9")
        replay = OwnerZeroHoldLedger(
            Decimal(1), Decimal(0), journal=journal, approval_digest=APPROVAL
        )
        assert replay.to_dict() == ledger.to_dict()


def test_later_charge_still_blocks_if_it_breaks_the_ceiling(tmp_path):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        authorize(journal)
        ledger = OwnerZeroHoldLedger(
            Decimal(1), Decimal(0), journal=journal, approval_digest=APPROVAL
        )
        assert ledger.reserve_wire("unknown", Decimal("0.8"))
        ledger.reserve_unknown_charge("unknown", Decimal("0.8"))
        assert ledger.reserve_wire("known", Decimal("0.8"))
        assert ledger.record_cost("known", Decimal("0.7"), Decimal("0.8"))
        assert not ledger.record_cost("unknown", Decimal("0.4"), Decimal("0.8"))
        assert ledger.blocked and ledger.spent_usd == Decimal("1.1")
        assert not ledger.reserve_wire("stop", Decimal("0.01"))
