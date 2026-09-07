from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from legacy.grounding.v5.d56_spend import (
    LEGACY_SPEND_SCHEMA_VERSION,
    SPEND_SCHEMA_VERSION,
    UNKNOWN_SPEND,
    chained_spend_bytes,
    combine_spend_disclosures,
    ledger_spend_disclosure,
    legacy_summary_spend_disclosure,
    validate_spend_disclosure,
)

ROOT = Path(__file__).parents[2]


@pytest.fixture
def phase_spend_v1() -> dict[str, str]:
    return {
        "schema_version": LEGACY_SPEND_SCHEMA_VERSION,
        "known_spend_usd": "1.25",
        "unknown_reservation_usd": UNKNOWN_SPEND,
        "in_flight_reservation_usd": UNKNOWN_SPEND,
        "budget_accounted_spend_usd": UNKNOWN_SPEND,
    }


@pytest.fixture
def phase_spend_v2() -> dict[str, str]:
    return {
        "schema_version": SPEND_SCHEMA_VERSION,
        "known_spend_usd": "1.25",
        "unknown_reservation_usd": "0.50",
        "in_flight_reservation_usd": "0.25",
        "budget_accounted_spend_usd": "2.00",
    }


def test_each_phase_spend_schema_version_remains_loadable(
    phase_spend_v1: dict[str, str], phase_spend_v2: dict[str, str]
) -> None:
    assert validate_spend_disclosure(phase_spend_v1) == phase_spend_v1
    assert validate_spend_disclosure(phase_spend_v2) == phase_spend_v2


@pytest.mark.parametrize(
    ("schema_name", "fixture_name"),
    [
        ("d56-phase-spend-v1.schema.json", "phase_spend_v1"),
        ("d56-phase-spend-v2.schema.json", "phase_spend_v2"),
    ],
)
def test_each_version_fixture_validates_against_its_packaged_schema(
    schema_name: str, fixture_name: str, request: pytest.FixtureRequest
) -> None:
    schema = json.loads(
        (ROOT / "pixelgym/grounding/v5/schemas" / schema_name).read_text(
            encoding="utf-8"
        )
    )

    Draft202012Validator(schema).validate(request.getfixturevalue(fixture_name))


def test_old_summary_without_reservation_reports_literal_unknown() -> None:
    loaded = legacy_summary_spend_disclosure(
        {
            "schema_version": "pixelgym-agent-v5-d56-calibration-result-v2",
            "calibration_incremental_spend_usd": "1.660213875",
            "actual_aggregate_spend_usd": "2.032875185",
        }
    )

    assert loaded["known_spend_usd"] == "1.660213875"
    assert loaded["unknown_reservation_usd"] == "unknown"
    assert loaded["budget_accounted_spend_usd"] == "unknown"


@pytest.mark.parametrize("unknown", [Decimal(0), Decimal("0.25"), Decimal("0.50")])
def test_ledger_spend_reconciles_zero_one_and_multiple_unknown_outcomes(
    unknown: Decimal,
) -> None:
    class Ledger:
        spent_usd = Decimal("1.75")
        unknown_reservation_usd = unknown
        in_flight_reservation_usd = Decimal("0.10")

    disclosure = ledger_spend_disclosure(Ledger())

    assert disclosure["known_spend_usd"] == "1.75"
    assert disclosure["unknown_reservation_usd"] == str(unknown)
    assert Decimal(disclosure["budget_accounted_spend_usd"]) == (
        Decimal("1.85") + unknown
    )


def test_pre_issue_103_ledger_defaults_missing_in_flight_hold_to_zero() -> None:
    class Ledger:
        spent_usd = Decimal("0.40")
        unknown_reservation_usd = Decimal("0.20")

    assert ledger_spend_disclosure(Ledger())["in_flight_reservation_usd"] == "0"


def test_ledger_spend_uses_fixed_point_amounts_accepted_by_packaged_schema() -> None:
    class Ledger:
        spent_usd = Decimal("0.0000005")
        unknown_reservation_usd = Decimal("0.0000000004")

    disclosure = ledger_spend_disclosure(Ledger())
    schema = json.loads(
        (
            ROOT
            / "pixelgym/grounding/v5/schemas/d56-phase-spend-v2.schema.json"
        ).read_text(encoding="utf-8")
    )

    Draft202012Validator(schema).validate(disclosure)
    assert disclosure["known_spend_usd"] == "0.0000005"
    assert disclosure["unknown_reservation_usd"] == "0.0000000004"
    existing_precision = validate_spend_disclosure(
        {
            "schema_version": SPEND_SCHEMA_VERSION,
            "known_spend_usd": "10.00",
            "unknown_reservation_usd": "2.032875185",
            "in_flight_reservation_usd": "0",
            "budget_accounted_spend_usd": "12.032875185",
        }
    )
    assert existing_precision["known_spend_usd"] == "10.00"
    assert existing_precision["unknown_reservation_usd"] == "2.032875185"


def test_known_cost_and_reservations_must_reconcile_to_accounted_spend() -> None:
    with pytest.raises(ValueError, match="does not reconcile"):
        validate_spend_disclosure(
            {
                "schema_version": SPEND_SCHEMA_VERSION,
                "known_spend_usd": "1.00",
                "unknown_reservation_usd": "0.25",
                "in_flight_reservation_usd": "0.50",
                "budget_accounted_spend_usd": "1.50",
            }
        )


def test_campaign_rollup_sums_per_run_accounted_spend_once(
    phase_spend_v2: dict[str, str],
) -> None:
    other = {
        "schema_version": SPEND_SCHEMA_VERSION,
        "known_spend_usd": "0.75",
        "unknown_reservation_usd": "0.25",
        "in_flight_reservation_usd": "0",
        "budget_accounted_spend_usd": "1.00",
    }

    total = combine_spend_disclosures((phase_spend_v2, other))

    assert total == {
        "schema_version": SPEND_SCHEMA_VERSION,
        "known_spend_usd": "2.00",
        "unknown_reservation_usd": "0.75",
        "in_flight_reservation_usd": "0.25",
        "budget_accounted_spend_usd": "3.00",
    }


def test_resume_chaining_is_byte_stable_and_does_not_duplicate_reservations(
    phase_spend_v2: dict[str, str],
) -> None:
    prior: dict[str, Any] = {
        "schema_version": SPEND_SCHEMA_VERSION,
        "known_spend_usd": "4.00",
        "unknown_reservation_usd": "0.50",
        "in_flight_reservation_usd": "0",
        "budget_accounted_spend_usd": "4.50",
    }

    first = chained_spend_bytes(prior, phase_spend_v2)
    second = chained_spend_bytes(prior, phase_spend_v2)
    chained = json.loads(first)

    assert first == second
    assert chained["campaign_spend"]["known_spend_usd"] == "5.25"
    assert chained["campaign_spend"]["unknown_reservation_usd"] == "1.00"
    assert chained["campaign_spend"]["budget_accounted_spend_usd"] == "6.50"
