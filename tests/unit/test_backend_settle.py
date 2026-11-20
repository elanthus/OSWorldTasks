"""Host half of the input acknowledgement barrier, driven without a clock."""

from __future__ import annotations

import pytest

from pixelgym.backends.settle import InputAcknowledgementError, await_input_acknowledgement


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        self.now += 1.0
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _await(counts: list[int], expected: int, *, timeout: float = 100.0) -> tuple[int, _Clock]:
    clock = _Clock()
    queue = list(counts)
    reads = await_input_acknowledgement(
        lambda: queue.pop(0) if len(queue) > 1 else queue[0],
        expected,
        timeout=timeout,
        poll_interval=0.25,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return reads, clock


def test_returns_after_the_read_that_reaches_the_expected_count() -> None:
    reads, clock = _await([0, 0, 1, 2], 2)

    assert reads == 4
    assert clock.sleeps == [0.25, 0.25, 0.25]


def test_already_acknowledged_needs_one_read_and_no_sleep() -> None:
    reads, clock = _await([3], 3)

    assert reads == 1
    assert clock.sleeps == []


def test_overshoot_is_an_error_not_success() -> None:
    with pytest.raises(InputAcknowledgementError, match="acknowledged 2 inputs but the host"):
        _await([0, 2], 1)


def test_missing_acknowledgement_fails_at_the_deadline() -> None:
    with pytest.raises(InputAcknowledgementError, match="acknowledged 0 of 1 inputs"):
        _await([0], 1, timeout=3.0)


@pytest.mark.parametrize(
    ("expected", "timeout", "poll"), [(-1, 1.0, 0.0), (True, 1.0, 0.0), (1, 0.0, 0.0), (1, 1.0, -1)]
)
def test_rejects_invalid_arguments(expected: object, timeout: float, poll: float) -> None:
    with pytest.raises(ValueError):
        await_input_acknowledgement(
            lambda: 0,
            expected,
            timeout=timeout,
            poll_interval=poll,  # type: ignore[arg-type]
        )
