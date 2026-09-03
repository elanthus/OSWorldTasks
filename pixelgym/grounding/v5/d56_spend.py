"""Versioned, deterministic spend disclosure for D5.6 phase chains."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from pixelgym.serialization import canonical_json_bytes

UNKNOWN_SPEND = "unknown"
LEGACY_SPEND_SCHEMA_VERSION = "pixelgym-agent-v5-d56-phase-spend-v1"
SPEND_SCHEMA_VERSION = "pixelgym-agent-v5-d56-phase-spend-v2"


class _PanelSpendLedger(Protocol):
    spent_usd: Decimal
    unknown_reservation_usd: Decimal


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a decimal amount") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return amount


def _amount_or_unknown(value: object, *, field: str) -> str:
    if value == UNKNOWN_SPEND:
        return UNKNOWN_SPEND
    return str(_decimal(value, field=field))


def validate_spend_disclosure(value: Mapping[str, Any]) -> dict[str, str]:
    """Validate and return one canonical phase/campaign spend disclosure."""

    version = value.get("schema_version")
    if version not in {LEGACY_SPEND_SCHEMA_VERSION, SPEND_SCHEMA_VERSION}:
        raise ValueError("D5.6 spend disclosure schema mismatch")
    result = {"schema_version": str(version)}
    for field in (
        "known_spend_usd",
        "unknown_reservation_usd",
        "in_flight_reservation_usd",
        "budget_accounted_spend_usd",
    ):
        if field not in value:
            raise ValueError(f"D5.6 spend disclosure is missing {field}")
        result[field] = _amount_or_unknown(value[field], field=field)
    known = result["known_spend_usd"]
    unknown = result["unknown_reservation_usd"]
    in_flight = result["in_flight_reservation_usd"]
    accounted = result["budget_accounted_spend_usd"]
    if version == SPEND_SCHEMA_VERSION and UNKNOWN_SPEND in result.values():
        raise ValueError("current D5.6 spend disclosures cannot contain unknown amounts")
    if all(value != UNKNOWN_SPEND for value in (known, unknown, in_flight, accounted)):
        expected = Decimal(known) + Decimal(unknown) + Decimal(in_flight)
        if Decimal(accounted) != expected:
            raise ValueError("budget-accounted spend does not reconcile")
    elif accounted != UNKNOWN_SPEND:
        raise ValueError("budget-accounted spend must be unknown when a component is unknown")
    return result


def ledger_spend_disclosure(ledger: _PanelSpendLedger) -> dict[str, str]:
    """Snapshot a ledger without requiring issue #103's in-flight API.

    ``SpendLedger.in_flight_reservation_usd`` is introduced by issue #103. Until
    that change is present, the pre-#103 ledger cannot retain an in-flight hold,
    so the documented compatibility default is zero.
    """

    known = _decimal(ledger.spent_usd, field="known_spend_usd")
    unknown = _decimal(
        ledger.unknown_reservation_usd, field="unknown_reservation_usd"
    )
    in_flight = _decimal(
        getattr(ledger, "in_flight_reservation_usd", Decimal(0)),
        field="in_flight_reservation_usd",
    )
    return validate_spend_disclosure(
        {
            "schema_version": SPEND_SCHEMA_VERSION,
            "known_spend_usd": str(known),
            "unknown_reservation_usd": str(unknown),
            "in_flight_reservation_usd": str(in_flight),
            "budget_accounted_spend_usd": str(known + unknown + in_flight),
        }
    )


def legacy_summary_spend_disclosure(summary: Mapping[str, Any]) -> dict[str, str]:
    """Load a pre-v2 phase summary without treating an absent hold as zero."""

    nested = summary.get("phase_spend")
    if isinstance(nested, Mapping):
        return validate_spend_disclosure(nested)

    known: object = UNKNOWN_SPEND
    for field in (
        "known_spend_usd",
        "run_spend_usd",
        "calibration_incremental_spend_usd",
        "trial_incremental_spend_usd",
        "smoke_incremental_spend_usd",
        "incremental_luna_experiment_charge_usd",
        "actual_aggregate_spend_usd",
    ):
        if field in summary:
            known = summary[field]
            break

    unknown: object = UNKNOWN_SPEND
    for field in ("unknown_reservation_usd", "unknown_charge_reservation_usd"):
        if field in summary:
            unknown = summary[field]
            break

    in_flight: object = summary.get("in_flight_reservation_usd", UNKNOWN_SPEND)
    accounted: object = summary.get(
        "budget_accounted_spend_usd",
        summary.get("budget_accounted_run_spend_usd", UNKNOWN_SPEND),
    )
    if (
        in_flight == UNKNOWN_SPEND
        and accounted != UNKNOWN_SPEND
        and known != UNKNOWN_SPEND
        and unknown != UNKNOWN_SPEND
    ):
        in_flight = Decimal(str(accounted)) - Decimal(str(known)) - Decimal(str(unknown))
    if UNKNOWN_SPEND in (known, unknown, in_flight):
        accounted = UNKNOWN_SPEND
    if accounted == UNKNOWN_SPEND and all(
        value != UNKNOWN_SPEND for value in (known, unknown, in_flight)
    ):
        accounted = Decimal(str(known)) + Decimal(str(unknown)) + Decimal(str(in_flight))
    return validate_spend_disclosure(
        {
            "schema_version": LEGACY_SPEND_SCHEMA_VERSION,
            "known_spend_usd": known,
            "unknown_reservation_usd": unknown,
            "in_flight_reservation_usd": in_flight,
            "budget_accounted_spend_usd": accounted,
        }
    )


def legacy_campaign_spend_disclosure(summary: Mapping[str, Any]) -> dict[str, str]:
    """Load campaign totals from a summary while preserving legacy uncertainty."""

    nested = summary.get("campaign_spend")
    if isinstance(nested, Mapping):
        return validate_spend_disclosure(nested)
    known = summary.get(
        "campaign_known_spend_usd",
        summary.get(
            "actual_aggregate_spend_usd",
            summary.get("known_spend_usd", UNKNOWN_SPEND),
        ),
    )
    unknown = summary.get(
        "campaign_unknown_reservation_usd",
        summary.get(
            "unknown_reservation_usd",
            summary.get("unknown_prior_charge_reservation_usd", UNKNOWN_SPEND),
        ),
    )
    in_flight = summary.get(
        "campaign_in_flight_reservation_usd",
        summary.get("in_flight_reservation_usd", UNKNOWN_SPEND),
    )
    accounted = summary.get(
        "campaign_budget_accounted_spend_usd",
        summary.get("budget_accounted_aggregate_spend_usd", UNKNOWN_SPEND),
    )
    if (
        in_flight == UNKNOWN_SPEND
        and accounted != UNKNOWN_SPEND
        and known != UNKNOWN_SPEND
        and unknown != UNKNOWN_SPEND
    ):
        in_flight = Decimal(str(accounted)) - Decimal(str(known)) - Decimal(str(unknown))
    if UNKNOWN_SPEND in (known, unknown, in_flight):
        accounted = UNKNOWN_SPEND
    return validate_spend_disclosure(
        {
            "schema_version": LEGACY_SPEND_SCHEMA_VERSION,
            "known_spend_usd": known,
            "unknown_reservation_usd": unknown,
            "in_flight_reservation_usd": in_flight,
            "budget_accounted_spend_usd": accounted,
        }
    )


def combine_spend_disclosures(
    disclosures: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    """Sum per-run disclosures once, propagating unknown components fail-closed."""

    normalized = [validate_spend_disclosure(value) for value in disclosures]

    def total(field: str) -> str:
        values = [value[field] for value in normalized]
        if UNKNOWN_SPEND in values:
            return UNKNOWN_SPEND
        return str(sum((Decimal(value) for value in values), Decimal(0)))

    known = total("known_spend_usd")
    unknown = total("unknown_reservation_usd")
    in_flight = total("in_flight_reservation_usd")
    accounted = (
        UNKNOWN_SPEND
        if UNKNOWN_SPEND in (known, unknown, in_flight)
        else str(Decimal(known) + Decimal(unknown) + Decimal(in_flight))
    )
    version = (
        LEGACY_SPEND_SCHEMA_VERSION
        if UNKNOWN_SPEND in (known, unknown, in_flight)
        else SPEND_SCHEMA_VERSION
    )
    return validate_spend_disclosure(
        {
            "schema_version": version,
            "known_spend_usd": known,
            "unknown_reservation_usd": unknown,
            "in_flight_reservation_usd": in_flight,
            "budget_accounted_spend_usd": accounted,
        }
    )


def phase_spend_fields(disclosure: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten the required phase quantities beside the versioned object."""

    normalized = validate_spend_disclosure(disclosure)
    return {
        "phase_spend": normalized,
        "known_spend_usd": normalized["known_spend_usd"],
        "unknown_reservation_usd": normalized["unknown_reservation_usd"],
        "in_flight_reservation_usd": normalized["in_flight_reservation_usd"],
        "budget_accounted_spend_usd": normalized["budget_accounted_spend_usd"],
    }


def campaign_spend_fields(disclosure: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten campaign totals without changing the phase quantities' meaning."""

    normalized = validate_spend_disclosure(disclosure)
    return {
        "campaign_spend": normalized,
        "campaign_known_spend_usd": normalized["known_spend_usd"],
        "campaign_unknown_reservation_usd": normalized["unknown_reservation_usd"],
        "campaign_in_flight_reservation_usd": normalized[
            "in_flight_reservation_usd"
        ],
        "campaign_budget_accounted_spend_usd": normalized[
            "budget_accounted_spend_usd"
        ],
    }


def chained_spend_bytes(
    prior_campaign: Mapping[str, Any], phase: Mapping[str, Any]
) -> bytes:
    """Return canonical bytes for deterministic resume/chaining comparisons."""

    value = {
        "prior_campaign_spend": validate_spend_disclosure(prior_campaign),
        "phase_spend": validate_spend_disclosure(phase),
        "campaign_spend": combine_spend_disclosures((prior_campaign, phase)),
    }
    return canonical_json_bytes(value)
