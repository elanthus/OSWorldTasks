"""Pure v5 metric and paired-statistics calculations from sealed records."""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pixelgym.grounding.stats import exact_mcnemar_p_value, wilson_interval

__all__ = [
    "MetricSummary",
    "clustered_bootstrap_difference",
    "exact_mcnemar_pvalue",
    "paired_success_table",
    "summarize_sealed_episodes",
    "wilson_interval",
]


def paired_success_table(first: dict[str, bool], second: dict[str, bool]) -> dict[str, int]:
    if set(first) != set(second) or not first:
        raise ValueError("paired policies require the same nonempty task identities")
    counts = Counter((first[key], second[key]) for key in sorted(first))
    return {
        "both_success": counts[(True, True)],
        "first_only": counts[(True, False)],
        "second_only": counts[(False, True)],
        "both_failure": counts[(False, False)],
    }


def exact_mcnemar_pvalue(first_only: int, second_only: int) -> float:
    """Two-sided exact McNemar p-value; delegates to the integer implementation."""
    if min(first_only, second_only) < 0:
        raise ValueError("discordant counts must be non-negative")
    return exact_mcnemar_p_value(first_only, second_only)


def clustered_bootstrap_difference(
    rows: tuple[tuple[str, bool, bool], ...],
    *,
    seed: int,
    samples: int = 10_000,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Unstratified logical-cluster bootstrap of the paired mean difference.

    Clusters are resampled with replacement from one pool, ignoring any family
    or other stratum, and the interval uses nearest-rank (floor/ceil index)
    percentiles. For the pre-registered family-stratified estimator see
    :func:`pixelgym.grounding.v5.confirmatory_analysis.stratified_cluster_bootstrap`.
    """
    if not rows or samples <= 0 or not 0 < confidence < 1:
        raise ValueError("bootstrap inputs are invalid")
    clusters: dict[str, list[tuple[bool, bool]]] = {}
    for cluster_id, first, second in rows:
        clusters.setdefault(cluster_id, []).append((first, second))
    keys = sorted(clusters)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(samples):
        selected = [rng.choice(keys) for _ in keys]
        values = [pair for key in selected for pair in clusters[key]]
        estimates.append(sum(int(first) - int(second) for first, second in values) / len(values))
    estimates.sort()
    alpha = (1 - confidence) / 2
    lower_index = max(0, min(samples - 1, math.floor(alpha * samples)))
    upper_index = max(0, min(samples - 1, math.ceil((1 - alpha) * samples) - 1))
    return estimates[lower_index], estimates[upper_index]


@dataclass(frozen=True)
class MetricSummary:
    successful: int
    total: int
    estimate: float
    wilson_95: tuple[float, float]
    termination_profile: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "successful": self.successful,
            "total": self.total,
            "estimate": self.estimate,
            "wilson_95": list(self.wilson_95),
            "termination_profile": self.termination_profile,
        }


def summarize_sealed_episodes(rows: tuple[dict[str, Any], ...]) -> MetricSummary:
    if not rows:
        raise ValueError("sealed episode set must not be empty")
    required = {"success", "classification"}
    if any(not required <= set(row) for row in rows):
        raise ValueError("sealed episode record is missing required outcome fields")
    successful = sum(row["success"] is True for row in rows)
    total = len(rows)
    return MetricSummary(
        successful=successful,
        total=total,
        estimate=successful / total,
        wilson_95=wilson_interval(successful, total),
        termination_profile=dict(Counter(str(row["classification"]) for row in rows)),
    )
