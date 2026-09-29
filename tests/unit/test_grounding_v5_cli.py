"""Direct tests for `pixelgym.grounding.v5.cli`'s shared argparse validator.

The validator has no shipped caller but is public module surface, so it is tested directly.
"""

from __future__ import annotations

import argparse
from decimal import Decimal

import pytest

from pixelgym.grounding.v5.cli import positive_finite_decimal


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", Decimal(1)),
        ("0.01", Decimal("0.01")),
        ("1000000", Decimal(1000000)),
    ],
)
def test_positive_finite_decimal_parses_valid_values(value: str, expected: Decimal) -> None:
    assert positive_finite_decimal(value) == expected


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "-0.01", "not-a-number", "inf", "nan", ""],
)
def test_positive_finite_decimal_rejects_non_positive_or_non_finite_values(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="finite positive decimal"):
        positive_finite_decimal(value)
