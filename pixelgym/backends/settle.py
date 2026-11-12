"""Host side of the vendor form's input acknowledgement barrier.

A backend that drives a real browser cannot tell from pixels whether its last input has been
processed: two bitwise-equal frames look the same before the browser handles a click as after,
and a submission the click caused may still be in flight to the task server. The task page
therefore acknowledges every trusted agent input to its server only after the input's whole task
has run, any submission it started has been recorded, and the resulting DOM has been committed to
a frame (see the barrier comment in ``pixelgym/tasks/vendor_form/app/static/app.js``).

`await_input_acknowledgement` is the host half: after dispatching its Nth input since reset, a
backend waits until the server reports exactly N acknowledgements, and only then captures the
settled frame and reads submissions. Correctness rests on that count, not on how long anything
takes. The deadline exists only to fail closed if the page stops acknowledging; it never turns a
missing acknowledgement into success. A count above N means the page saw input the host did not
send, so the backend can no longer pair acknowledgements with its own actions and stops.

This module has no OSWorld or browser import, so the core install and the fast suite keep it.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class InputAcknowledgementError(RuntimeError):
    """The task page did not acknowledge the host's inputs exactly."""


def await_input_acknowledgement(
    read_count: Callable[[], int],
    expected: int,
    *,
    timeout: float,
    poll_interval: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Poll `read_count` until it equals `expected`; return how many reads that took.

    Raises `InputAcknowledgementError` if the count overshoots `expected` or is still short at the
    deadline. `monotonic` and `sleep` are injectable so tests drive the loop without a clock.
    """

    if type(expected) is not int or expected < 0:
        raise ValueError("expected acknowledgement count must be a non-negative int")
    if timeout <= 0 or poll_interval < 0:
        raise ValueError("timeout must be positive and poll_interval non-negative")
    deadline = monotonic() + timeout
    reads = 0
    while True:
        count = read_count()
        reads += 1
        if count == expected:
            return reads
        if count > expected:
            raise InputAcknowledgementError(
                f"task page acknowledged {count} inputs but the host dispatched {expected}"
            )
        if monotonic() > deadline:
            raise InputAcknowledgementError(
                f"task page acknowledged {count} of {expected} inputs within {timeout:.1f}s"
            )
        if poll_interval:
            sleep(poll_interval)
