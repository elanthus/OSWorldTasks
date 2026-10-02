"""WP6 estimator tests: shared integer McNemar and the stratified cluster bootstrap."""

from __future__ import annotations

import math
import random
from fractions import Fraction

import pytest

from pixelgym.grounding import analysis, stats
from pixelgym.grounding.v5 import metrics
from pixelgym.grounding.v5.confirmatory_analysis import (
    PREREGISTERED_LEVEL,
    PREREGISTERED_RESAMPLES,
    PREREGISTERED_SEED,
    stratified_cluster_bootstrap,
    stratified_cluster_resample_estimates,
)


def _v1_integer_mcnemar(first_only: int, second_only: int) -> float:
    """The v1 integer formula, restated with exact fractions."""
    discordant = first_only + second_only
    if discordant == 0:
        return 1.0
    tail = Fraction(
        sum(math.comb(discordant, k) for k in range(min(first_only, second_only) + 1)),
        2**discordant,
    )
    return float(min(Fraction(1), 2 * tail))


@pytest.mark.parametrize("counts", [(0, 0), (1, 0), (5, 5), (540, 540), (0, 5), (3, 40)])
def test_mcnemar_matches_v1_integer_values_everywhere(counts: tuple[int, int]) -> None:
    expected = _v1_integer_mcnemar(*counts)
    assert stats.exact_mcnemar_p_value(*counts) == expected
    assert analysis.mcnemar_exact(*counts) == expected
    assert metrics.exact_mcnemar_pvalue(*counts) == expected


def test_mcnemar_hand_values_and_v5_no_longer_overflows() -> None:
    assert metrics.exact_mcnemar_pvalue(0, 0) == 1.0
    assert metrics.exact_mcnemar_pvalue(1, 0) == 1.0  # 2 * 1/2
    assert metrics.exact_mcnemar_pvalue(5, 5) == 1.0
    assert metrics.exact_mcnemar_pvalue(540, 540) == 1.0  # previously OverflowError
    assert metrics.exact_mcnemar_pvalue(0, 5) == 0.0625  # 2 * 1/32


@pytest.mark.parametrize("fn", [stats.exact_mcnemar_p_value, metrics.exact_mcnemar_pvalue])
def test_mcnemar_rejects_negative_counts(fn) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        fn(-1, 2)


def test_reexported_names_are_the_shared_implementations() -> None:
    assert metrics.wilson_interval is stats.wilson_interval
    assert analysis._percentile is stats.interpolated_percentile
    lower, upper = metrics.wilson_interval(8, 10)
    assert 0.49 < lower < 0.50 and 0.94 < upper < 0.95
    with pytest.raises(ValueError):
        stats.wilson_interval(1, 0)


# Three strata (families), six logical clusters. Cluster c2 is a robustness pair (two rows).
# Per-cluster (sum of first-minus-second differences, row count):
#   fam-a: c1 -> (+1, 1)   c2 -> (+1, 2)   [rows (T,F) and (T,T)]
#   fam-b: c3 -> ( 0, 1)   c4 -> (-1, 1)
#   fam-c: c5 -> (+1, 1)   c6 -> ( 0, 1)
ROWS = (
    {"family": "fam-a", "cluster": "c1", "first": True, "second": False},
    {"family": "fam-a", "cluster": "c2", "first": True, "second": False},
    {"family": "fam-a", "cluster": "c2", "first": True, "second": True},
    {"family": "fam-b", "cluster": "c3", "first": False, "second": False},
    {"family": "fam-b", "cluster": "c4", "first": False, "second": True},
    {"family": "fam-c", "cluster": "c5", "first": True, "second": False},
    {"family": "fam-c", "cluster": "c6", "first": True, "second": True},
)
CLUSTER_TABLE = {
    "c1": (1, 1),
    "c2": (1, 2),
    "c3": (0, 1),
    "c4": (-1, 1),
    "c5": (1, 1),
    "c6": (0, 1),
}
STRATA_ORDER = (("c1", "c2"), ("c3", "c4"), ("c5", "c6"))


def test_hand_computed_three_strata_six_clusters() -> None:
    """Expected values are derived without calling the implementation.

    The point estimate is by hand: differences sum to 1+1+0+0-1+1+0 = 2 over 7 rows.
    For the resamples, a separately constructed ``random.Random(7)`` replays the documented
    draw order (strata ascending, then two ``choice`` calls over the ascending cluster pair)
    and the resample statistic is computed from the hand-written ``CLUSTER_TABLE`` above as
    (sum of drawn cluster sums) / (sum of drawn cluster sizes).
    The first resample is also spelled out explicitly from the replayed draws.
    """
    rng = random.Random(7)
    draws = [[rng.choice(pair) for pair in STRATA_ORDER for _ in pair] for _ in range(5)]
    expected = []
    for drawn in draws:
        total = sum(CLUSTER_TABLE[c][0] for c in drawn)
        size = sum(CLUSTER_TABLE[c][1] for c in drawn)
        expected.append(total / size)

    got = stratified_cluster_resample_estimates(
        ROWS, strata_key="family", cluster_key="cluster", seed=7, resamples=5
    )
    assert got == expected
    # Spell out resample 1 so the arithmetic is visible in the failure output.
    first = draws[0]
    assert len(first) == 6 and first[0:2] and set(first[0:2]) <= {"c1", "c2"}
    assert set(first[2:4]) <= {"c3", "c4"} and set(first[4:6]) <= {"c5", "c6"}

    result = stratified_cluster_bootstrap(
        ROWS, strata_key="family", cluster_key="cluster", seed=7, resamples=5, level=0.5
    )
    assert result.estimate == 2 / 7
    ordered = sorted(expected)
    # (n - 1) * 0.25 = 1.0 and (n - 1) * 0.75 = 3.0: integer positions, no interpolation.
    assert result.interval == (ordered[1], ordered[3])
    assert (result.strata, result.clusters, result.rows) == (3, 6, 7)


def test_interpolated_percentile_convention() -> None:
    result = stratified_cluster_bootstrap(
        ROWS, strata_key="family", cluster_key="cluster", seed=7, resamples=4, level=0.5
    )
    ordered = sorted(
        stratified_cluster_resample_estimates(
            ROWS, strata_key="family", cluster_key="cluster", seed=7, resamples=4
        )
    )
    # positions 0.75 and 2.25 interpolate between neighbours.
    assert result.interval[0] == pytest.approx(ordered[0] * 0.25 + ordered[1] * 0.75)
    assert result.interval[1] == pytest.approx(ordered[2] * 0.75 + ordered[3] * 0.25)


def test_single_stratum_equals_unstratified_bootstrap() -> None:
    """With one stratum the draw order equals ``clustered_bootstrap_difference``.

    41 resamples puts both conventions on the same ranks: interpolated positions
    40 * 0.025 = 1 and 40 * 0.975 = 39; nearest-rank floor(1.025) = 1 and ceil(39.975) - 1 = 39.
    """
    tuples = (
        ("p1", True, False),
        ("p1", True, True),
        ("s2", False, True),
        ("s3", True, False),
        ("s4", False, False),
        ("s5", True, True),
    )
    rows = [{"f": "only", "c": c, "first": a, "second": b} for c, a, b in tuples]
    stratified = stratified_cluster_bootstrap(
        rows, strata_key="f", cluster_key="c", seed=42, resamples=41
    )
    # 40 * 0.025 evaluates to 1.0000000000000002 in floating point, so the interpolated
    # bound carries a one-ulp-scale weight on the neighbouring rank; compare within 1e-12.
    assert stratified.interval == pytest.approx(
        metrics.clustered_bootstrap_difference(tuples, seed=42, samples=41), abs=1e-12
    )


def test_deterministic_across_calls_and_preregistered_defaults() -> None:
    a = stratified_cluster_bootstrap(ROWS, strata_key="family", cluster_key="cluster")
    b = stratified_cluster_bootstrap(
        list(reversed(ROWS)), strata_key=lambda r: r["family"], cluster_key="cluster"
    )
    assert a == b
    assert (
        (a.seed, a.resamples, a.level)
        == (
            PREREGISTERED_SEED,
            PREREGISTERED_RESAMPLES,
            PREREGISTERED_LEVEL,
        )
        == (20260911, 10_000, 0.95)
    )
    assert a.to_dict()["interval"] == list(a.interval)


@pytest.mark.parametrize(
    ("rows", "kwargs", "message"),
    [
        ((), {}, "at least one row"),
        (ROWS, {"resamples": 0}, "resamples"),
        (ROWS, {"resamples": True}, "resamples"),
        (ROWS, {"level": 1.0}, "level"),
        (ROWS, {"level": 0}, "level"),
        (({"family": "a", "cluster": "x", "first": True},), {}, "second"),
        (({"family": "a", "cluster": "x", "first": 1, "second": False},), {}, "boolean"),
        (({"cluster": "x", "first": True, "second": False},), {}, "strata_key"),
        (({"family": "a", "cluster": 3, "first": True, "second": False},), {}, "strings"),
        (
            (
                {"family": "a", "cluster": "x", "first": True, "second": False},
                {"family": "b", "cluster": "x", "first": True, "second": False},
            ),
            {},
            "spans strata",
        ),
    ],
)
def test_invalid_inputs_are_rejected(rows, kwargs, message) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises((ValueError, TypeError), match=message):
        stratified_cluster_bootstrap(rows, strata_key="family", cluster_key="cluster", **kwargs)
