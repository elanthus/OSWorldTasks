"""Shared command-line argument validation for v5 grounding runs."""

from __future__ import annotations

import argparse
from decimal import Decimal, DecimalException


def positive_finite_decimal(value: str) -> Decimal:
    """Parse one positive finite decimal as an argparse argument value."""

    try:
        parsed = Decimal(value)
    except (DecimalException, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be a finite positive decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive decimal")
    return parsed
