"""Independent exact-probability checks for the prospective D5.8 calculation."""

from __future__ import annotations

import importlib
import itertools
import math
from fractions import Fraction

import pytest

planning = importlib.import_module("artifacts.grounding-v5-d58-final-design.power")


def test_power_matches_enumerated_outcome_sequences() -> None:
    # Independently enumerate every ordered outcome sequence, using rational
    # probabilities and the exact null binomial tail rather than its cutoff.
    probabilities = (Fraction(9, 20), Fraction(1, 4), Fraction(3, 10))
    power = Fraction(0)
    for outcomes in itertools.product(range(3), repeat=8):
        positive, negative = outcomes.count(0), outcomes.count(1)
        discordant = positive + negative
        tail = sum(math.comb(discordant, k) for k in range(min(positive, negative) + 1))
        if Fraction(2 * tail, 2**discordant) <= Fraction(1, 20):
            power += math.prod(probabilities[value] for value in outcomes)
    assert planning.POWER.exact_power(8, 0.20, 0.70) == pytest.approx(float(power), abs=1e-14)


def test_power_controls_null_and_is_symmetric() -> None:
    assert planning.POWER.exact_power(120, 0, 0.7) <= 0.05
    assert planning.POWER.exact_power(120, 0.2, 0.7) == pytest.approx(
        planning.POWER.exact_power(120, -0.2, 0.7), abs=1e-14
    )


@pytest.mark.parametrize("damage", ["duplicate", "missing", "incomplete", "negative"])
def test_representative_counts_reject_invalid_denominators(damage: str) -> None:
    evidence = {
        "complete": True,
        "logical_clusters": 2,
        "representative_seeds": [1, 2],
        "representative_terminal_outcomes": {
            "both_success": 1,
            "history_only": 1,
            "stateless_only": 0,
            "neither_success": 0,
            "incomplete": 0,
            "unpaired_in_subset": 0,
        },
    }
    assert planning.representative_counts(evidence) == (2, 1)
    if damage == "duplicate":
        evidence["representative_seeds"] = [1, 1]
    elif damage == "missing":
        evidence["representative_seeds"] = [1]
    elif damage == "incomplete":
        evidence["complete"] = False
    else:
        evidence["representative_terminal_outcomes"]["history_only"] = -1
    with pytest.raises(ValueError):
        planning.representative_counts(evidence)


def test_wilson_endpoint_symmetry_and_invalid_count() -> None:
    low, high = planning.wilson_limits(31, 44)
    complement_low, complement_high = planning.wilson_limits(13, 44)
    assert low == pytest.approx(1 - complement_high)
    assert high == pytest.approx(1 - complement_low)
    assert low < 31 / 44 < high
    with pytest.raises(ValueError):
        planning.wilson_limits(45, 44)
