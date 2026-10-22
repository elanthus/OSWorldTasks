"""Unapproved D5.9 stop-rule options; constructing them never authorizes execution."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

PROPOSED_STOP_RULE_VERSION = "pixelgym-agent-v5-d59-replication-stop-rule-v2"


@dataclass(frozen=True)
class ProposedStopRule:
    """The owner must select both scientific choices before a successor freeze.

    Request failures remain failed assignments, with a separate bounded streak.
    Policy violations can either stop explicitly or continue as failed assignments.
    Neither choice relaxes the policy's dispatch or credential boundary.
    """

    failure_streak_scope: Literal["per_worker", "non_overlapping"]
    policy_violation_action: Literal["stop", "continue"]
    consecutive_request_failure_limit: int = 3
    rate_limit_backoff_seconds: float = 30.0
    maximum_rate_limit_backoff_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.failure_streak_scope not in ("per_worker", "non_overlapping"):
            raise ValueError("choose per_worker or non_overlapping failure streaks")
        if self.policy_violation_action not in ("stop", "continue"):
            raise ValueError("choose stop or continue for policy violations")
        if (
            type(self.consecutive_request_failure_limit) is not int
            or self.consecutive_request_failure_limit < 1
        ):
            raise ValueError("request failure limit must be a positive integer")
        if not (
            math.isfinite(self.rate_limit_backoff_seconds)
            and math.isfinite(self.maximum_rate_limit_backoff_seconds)
            and 0 < self.rate_limit_backoff_seconds <= self.maximum_rate_limit_backoff_seconds <= 60
        ):
            raise ValueError("rate-limit backoff must be positive and bounded by 60 seconds")

    def backoff_seconds(self, retry_after: float | None) -> float:
        hinted = retry_after if retry_after is not None and math.isfinite(retry_after) else 0.0
        return min(
            self.maximum_rate_limit_backoff_seconds, max(self.rate_limit_backoff_seconds, hinted)
        )


@dataclass
class FailureStreak:
    """Count one class of failure under the selected, explicit overlap rule."""

    scope: Literal["per_worker", "non_overlapping"]
    workers: dict[int, int] = field(default_factory=dict)
    count: int = 0
    counted_end: float | None = None

    def record(self, matches: bool, *, worker: int, started: float, ended: float) -> int:
        if not math.isfinite(started) or not math.isfinite(ended) or ended < started:
            raise ValueError("assignment timing must be finite and ordered")
        if self.scope == "per_worker":
            self.workers[worker] = self.workers.get(worker, 0) + 1 if matches else 0
            return self.workers[worker]
        if not matches:
            self.count = 0
            self.counted_end = None
        elif self.counted_end is None or started >= self.counted_end:
            self.count += 1
            self.counted_end = ended
        # Overlapping completions are skipped. This selects pairwise-disjoint
        # completed intervals; it makes no claim about distinct underlying outages.
        return self.count
