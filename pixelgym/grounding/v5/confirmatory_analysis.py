"""Pre-registered D5.9/D5.10 confirmatory estimators.

Pre-registered definition
-------------------------
``plans/grounding-v5-d59-confirmatory-freeze.md`` lines 23-24 (revision
f0bcbd6f3e379f880baeca233d4b7e05528bcfc5), carried unchanged into the Haiku
successor ``plans/grounding-v5-d59-haiku-freeze.md`` ("preserves the admitted
confirmatory tasks and statistical design")::

    - The family-stratified logical-cluster bootstrap keeps seed 20260911, 10,000 resamples, and 95%
      intervals.

The originating design, ``plans/grounding-v5-d58-final-design.md`` lines 541-542,
names the estimand::

    Use a family-stratified logical-cluster bootstrap for the all-episode absolute difference and
    intervals.

Interpretation choices (the pre-registered text does not define these; each is
the most literal reading and is listed for owner review)
-----------------------------------------------------------------------------
1. **Stratum**: the value returned by ``strata_key`` for a row (for D5.9, the
   workflow family). Strata are fixed; they are never resampled.
2. **Logical cluster**: the value returned by ``cluster_key`` (for D5.9, a
   singleton task or a robustness pair ``twin_a``/``twin_b``). A cluster must lie
   entirely within one stratum; otherwise the call is rejected.
3. **Resampling within strata**: in each resample, each stratum independently
   draws as many clusters, with replacement, as it originally contains. All
   rows of a drawn cluster enter the resample.
4. **Combining strata / estimate**: the resample statistic is the *pooled*
   all-episode absolute difference ``mean(first) - mean(second)`` over every
   row in the resample (not an average of per-stratum differences). Cluster
   sizes may differ, so the resample size can vary.
5. **Percentile convention**: the frozen v1 linear-interpolation percentile
   (:func:`pixelgym.grounding.stats.interpolated_percentile`, position
   ``(n - 1) * q``) at ``q = (1 - level) / 2`` and ``1 - q``.
6. **Draw order** (determinism contract): one ``random.Random(seed)``; for each
   resample, strata in ascending key order; within a stratum, ``rng.choice``
   over the ascending cluster keys, once per original cluster. Keys must be
   strings so the order is total and platform independent.
7. **Row shape**: rows are mappings; ``strata_key`` and ``cluster_key`` name a
   string field or are callables; paired outcomes are the booleans at
   ``first_field`` and ``second_field``.

With a single stratum this draw order is identical to
:func:`pixelgym.grounding.v5.metrics.clustered_bootstrap_difference`, which is
unstratified and uses nearest-rank percentiles instead.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pixelgym.grounding.stats import interpolated_percentile

PREREGISTERED_SEED = 20260911
PREREGISTERED_RESAMPLES = 10_000
PREREGISTERED_LEVEL = 0.95

Row = Mapping[str, Any]
KeySpec = str | Callable[[Row], str]


@dataclass(frozen=True)
class StratifiedBootstrapResult:
    estimate: float
    interval: tuple[float, float]
    level: float
    seed: int
    resamples: int
    strata: int
    clusters: int
    rows: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimate": self.estimate,
            "interval": list(self.interval),
            "level": self.level,
            "seed": self.seed,
            "resamples": self.resamples,
            "strata": self.strata,
            "clusters": self.clusters,
            "rows": self.rows,
            "method": "family-stratified-logical-cluster-bootstrap-pooled-difference-v1",
            "percentile": "linear-interpolation-(n-1)q-v1",
        }


def _key(spec: KeySpec, row: Row, name: str) -> str:
    if callable(spec):
        value = spec(row)
    else:
        if spec not in row:
            raise ValueError(f"row is missing {name} field {spec!r}")
        value = row[spec]
    if not isinstance(value, str):
        raise TypeError(f"{name} values must be strings for a deterministic order")
    return value


def _outcome(row: Row, field: str) -> int:
    if field not in row:
        raise ValueError(f"row is missing outcome field {field!r}")
    value = row[field]
    if not isinstance(value, bool):
        raise TypeError(f"outcome field {field!r} must be a boolean")
    return int(value)


def _group(
    rows: Sequence[Row],
    strata_key: KeySpec,
    cluster_key: KeySpec,
    first_field: str,
    second_field: str,
) -> dict[str, dict[str, list[int]]]:
    """Return ``{stratum: {cluster: [per-row differences]}}``."""
    strata: dict[str, dict[str, list[int]]] = {}
    cluster_stratum: dict[str, str] = {}
    for row in rows:
        stratum = _key(strata_key, row, "strata_key")
        cluster = _key(cluster_key, row, "cluster_key")
        previous = cluster_stratum.setdefault(cluster, stratum)
        if previous != stratum:
            raise ValueError(
                f"logical cluster {cluster!r} spans strata {previous!r} and {stratum!r}"
            )
        difference = _outcome(row, first_field) - _outcome(row, second_field)
        strata.setdefault(stratum, {}).setdefault(cluster, []).append(difference)
    return strata


def stratified_cluster_resample_estimates(
    rows: Sequence[Row],
    *,
    strata_key: KeySpec,
    cluster_key: KeySpec,
    seed: int = PREREGISTERED_SEED,
    resamples: int = PREREGISTERED_RESAMPLES,
    first_field: str = "first",
    second_field: str = "second",
) -> list[float]:
    """Return the unsorted resample statistics in draw order."""
    if not rows:
        raise ValueError("stratified bootstrap requires at least one row")
    if isinstance(resamples, bool) or not isinstance(resamples, int) or resamples <= 0:
        raise ValueError("resamples must be a positive integer")
    grouped = _group(rows, strata_key, cluster_key, first_field, second_field)
    ordered = [(sorted(clusters), clusters) for _, clusters in sorted(grouped.items())]
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(resamples):
        total = 0
        count = 0
        for keys, clusters in ordered:
            for _ in keys:
                values = clusters[rng.choice(keys)]
                total += sum(values)
                count += len(values)
        estimates.append(total / count)
    return estimates


def stratified_cluster_bootstrap(
    rows: Sequence[Row],
    *,
    strata_key: KeySpec,
    cluster_key: KeySpec,
    seed: int = PREREGISTERED_SEED,
    resamples: int = PREREGISTERED_RESAMPLES,
    level: float = PREREGISTERED_LEVEL,
    first_field: str = "first",
    second_field: str = "second",
) -> StratifiedBootstrapResult:
    """Family-stratified logical-cluster bootstrap of the pooled paired difference.

    See the module docstring for the pre-registered text and every
    interpretation choice.
    """
    if isinstance(level, bool) or not isinstance(level, int | float) or not 0 < level < 1:
        raise ValueError("level must lie strictly between 0 and 1")
    estimates = stratified_cluster_resample_estimates(
        rows,
        strata_key=strata_key,
        cluster_key=cluster_key,
        seed=seed,
        resamples=resamples,
        first_field=first_field,
        second_field=second_field,
    )
    grouped = _group(rows, strata_key, cluster_key, first_field, second_field)
    differences = [
        value for clusters in grouped.values() for values in clusters.values() for value in values
    ]
    estimates.sort()
    alpha = (1 - level) / 2
    return StratifiedBootstrapResult(
        estimate=sum(differences) / len(differences),
        interval=(
            interpolated_percentile(estimates, alpha),
            interpolated_percentile(estimates, 1 - alpha),
        ),
        level=float(level),
        seed=seed,
        resamples=resamples,
        strata=len(grouped),
        clusters=sum(len(clusters) for clusters in grouped.values()),
        rows=len(differences),
    )
