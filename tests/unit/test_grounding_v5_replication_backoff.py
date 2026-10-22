"""Pure global-dispatch backoff checks; no waits or provider calls."""

import pytest

from pixelgym.grounding.v5.replication_stop_rules import RateLimitBackoff


def test_unscheduled_backoff_never_delays_dispatch():
    backoff = RateLimitBackoff()
    assert backoff.dispatch_not_before is None
    assert backoff.remaining(100) == 0


@pytest.mark.parametrize("seconds", [30, 60])
def test_four_simultaneous_rejections_share_one_delay(seconds):
    backoff = RateLimitBackoff()
    for _ in range(4):
        assert backoff.schedule(completed_at=100, seconds=seconds) == 100 + seconds
    assert backoff.remaining(100) == seconds
    assert backoff.remaining(110) == seconds - 10
    assert backoff.remaining(100 + seconds) == 0


def test_queued_rejection_uses_its_completion_time_and_may_already_be_expired():
    backoff = RateLimitBackoff()
    assert backoff.schedule(completed_at=10, seconds=30) == 40
    assert backoff.remaining(100) == 0
    # Reading it repeatedly does not turn the old rejection into a new wait.
    assert backoff.remaining(101) == 0


def test_later_rejection_extends_only_from_its_own_completion_time():
    backoff = RateLimitBackoff()
    assert backoff.schedule(completed_at=100, seconds=30) == 130
    assert backoff.schedule(completed_at=115, seconds=30) == 145
    # If the later result is processed at 140, only five seconds remain.
    assert backoff.remaining(140) == 5
    assert backoff.remaining(145) == 0


def test_out_of_order_or_shorter_rejection_cannot_shorten_the_shared_deadline():
    backoff = RateLimitBackoff()
    assert backoff.schedule(completed_at=100, seconds=60) == 160
    assert backoff.schedule(completed_at=110, seconds=30) == 160
    assert backoff.schedule(completed_at=90, seconds=30) == 160
    assert backoff.remaining(120) == 40


@pytest.mark.parametrize("completed_at", [float("nan"), float("inf"), -float("inf")])
def test_schedule_rejects_nonfinite_completion_without_mutating_deadline(completed_at):
    backoff = RateLimitBackoff()
    backoff.schedule(completed_at=10, seconds=30)
    with pytest.raises(ValueError, match="completion time"):
        backoff.schedule(completed_at=completed_at, seconds=30)
    assert backoff.dispatch_not_before == 40


@pytest.mark.parametrize("seconds", [0, -1, 61, float("nan"), float("inf"), -float("inf")])
def test_schedule_rejects_unbounded_delay_without_mutating_deadline(seconds):
    backoff = RateLimitBackoff()
    backoff.schedule(completed_at=10, seconds=30)
    with pytest.raises(ValueError, match="backoff"):
        backoff.schedule(completed_at=20, seconds=seconds)
    assert backoff.dispatch_not_before == 40


@pytest.mark.parametrize("now", [float("nan"), float("inf"), -float("inf")])
def test_remaining_rejects_nonfinite_current_time_even_when_unscheduled(now):
    with pytest.raises(ValueError, match="current time"):
        RateLimitBackoff().remaining(now)
