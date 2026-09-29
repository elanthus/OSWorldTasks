"""Shared, dependency-free statistics for grounding analyses.

The v1 grounding analysis (:mod:`pixelgym.grounding.analysis`) and the v5 metrics
(:mod:`pixelgym.grounding.v5.metrics`) re-export these functions under their
historical names, so existing callers keep their behaviour.
"""

from __future__ import annotations

import math


def exact_mcnemar_p_value(first_only: int, second_only: int) -> float:
    """Two-sided exact McNemar p-value, conditional on the discordant pairs.

    The binomial tail is summed with integer arithmetic and divided once by
    ``2**discordant``. Python's integer true division is correctly rounded and
    never overflows, so large discordant counts such as (540, 540) are exact.
    """
    if first_only < 0 or second_only < 0:
        raise ValueError("discordant counts must be nonnegative")
    discordant = first_only + second_only
    if discordant == 0:
        return 1.0
    smaller = min(first_only, second_only)
    lower_tail = sum(math.comb(discordant, k) for k in range(smaller + 1)) / (2**discordant)
    return float(min(1.0, 2 * lower_tail))


def wilson_interval(
    successes: int, total: int, *, z: float = 1.959963984540054
) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if total <= 0 or not 0 <= successes <= total:
        raise ValueError("Wilson interval requires 0 <= successes <= positive total")
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return max(0.0, center - half), min(1.0, center + half)


def interpolated_percentile(sorted_values: list[float], percentile: float) -> float:
    """Linear-interpolation percentile used by the frozen v1 bootstrap.

    The position is ``(n - 1) * percentile`` over the ascending values; a
    fractional position interpolates linearly between its two neighbours.
    """
    if not sorted_values:
        raise ValueError("cannot calculate a percentile of an empty sequence")
    position = (len(sorted_values) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction
