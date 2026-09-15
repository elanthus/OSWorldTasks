"""Owner-authorized zero budget holds without inventing provider charge receipts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger

OWNER_AUTHORIZATION_KEY = "d58-owner-zero-holds/authorized"
OWNER_BUDGET_RULE = "owner-authorized-zero-unpriced-holds-v1"


class OwnerZeroHoldLedger(ReboundedMemoryLedger):
    """Retain unknown outcomes while the owner assigns them zero budget weight.

    In-flight requests still reserve their full request bound. Known charges are
    unchanged, including a later priced receipt for an earlier unknown outcome.
    Existing unknown holds require append-only waivers after transport closure.
    """

    def __init__(
        self,
        maximum_spend_usd: Decimal,
        spent_usd: Decimal,
        *,
        journal: V5AttemptJournal,
        approval_digest: str,
    ) -> None:
        self.approval_digest = approval_digest
        super().__init__(maximum_spend_usd, spent_usd, journal=journal)

    def _replay_journal(self, journal: V5AttemptJournal) -> None:
        authorization = journal.event(OWNER_AUTHORIZATION_KEY)
        if (
            authorization is None
            or authorization.kind != "owner_zero_hold_budget_authorized"
            or authorization.payload.get("approval_digest") != self.approval_digest
            or authorization.payload.get("rule") != OWNER_BUDGET_RULE
        ):
            raise ValueError("the exact owner budget authorization is required")
        super()._replay_journal(journal)
        for event in journal.events():
            if (
                event.kind == "spend_reservation_unknown"
                and Decimal(event.payload["unknown_reservation_usd"]) == 0
                and event.sequence <= authorization.sequence
            ):
                raise ValueError("zero unknown budget hold predates owner authorization")
            if event.kind != "spend_unknown_budget_waived":
                continue
            payload = event.payload
            rid = payload["reservation_id"]
            before = Decimal(payload["previous_budget_hold_usd"])
            if (
                payload["approval_digest"] != self.approval_digest
                or event.sequence <= authorization.sequence
                or payload["rule"] != OWNER_BUDGET_RULE
                or payload["budget_hold_usd"] != "0"
                or not before.is_finite()
                or before <= 0
                or rid not in self._settlements
            ):
                raise ValueError("invalid owner budget waiver")
            prior = self._settlements[rid]
            # A later actual receipt supersedes the unknown outcome normally.
            if prior[0] == "unknown":
                if prior[1] != before:
                    raise ValueError("owner waiver differs from the prior unknown hold")
                self._settlements[rid] = ("unknown", Decimal(0))
                self.unknown_reservation_usd -= before

    def waive_existing_unknown_holds(self) -> list[dict[str, Any]]:
        """Zero existing unknown budget weights only when no request is in flight."""
        assert self.journal is not None
        changes = []
        with self._lock:
            if self._in_flight:
                raise ValueError("owner reconciliation requires an idle transport")
            for rid, (kind, amount) in sorted(self._settlements.items()):
                if kind != "unknown" or amount == 0:
                    continue
                payload = {
                    "reservation_id": rid,
                    "approval_digest": self.approval_digest,
                    "rule": OWNER_BUDGET_RULE,
                    "previous_budget_hold_usd": str(amount),
                    "budget_hold_usd": "0",
                    "basis": "owner activity check and explicit budget instruction; not a provider-reported zero charge",
                }
                self.journal.append_event(
                    event_key=f"spend/{rid}/owner-budget-waiver",
                    kind="spend_unknown_budget_waived",
                    trial_id="__spend_ledger__",
                    step_index=0,
                    payload=payload,
                )
                self._settlements[rid] = ("unknown", Decimal(0))
                self.unknown_reservation_usd -= amount
                changes.append(payload)
        return changes

    def reserve_wire(self, idempotency_key: str, request_maximum_usd: Decimal) -> bool:
        with self._lock:
            if self.unknown_reservation_usd != 0:
                raise ValueError("prior unknown holds must be reconciled before sending")
            return super().reserve_wire(idempotency_key, request_maximum_usd)

    def _unknown_charge_reservation(self, request_maximum_usd: Decimal) -> Decimal:
        del request_maximum_usd
        return Decimal(0)

    def to_dict(self) -> dict[str, str | int | bool]:
        value = super().to_dict()
        for key in (
            "spent_usd",
            "in_flight_reservation_usd",
            "unknown_reservation_usd",
            "budget_accounted_spend_usd",
        ):
            value[key] = format(Decimal(str(value[key])).normalize(), "f")
        return {
            **value,
            "unknown_budget_rule": OWNER_BUDGET_RULE,
            "owner_budget_approval_digest": self.approval_digest,
        }
