"""Opt-in real-OSWorld evidence for the v5 live-reconnect contract."""

from __future__ import annotations

import math
import os
import signal
import time
from pathlib import Path

import pytest

from pixelgym.backends.osworld import (
    OSWorldBackend,
    OSWorldBackendConfig,
    OSWorldBackendError,
)

GUEST_IMAGE = Path(".cache/osworld/osworld-v2-ubuntu-x86.qcow2")

pytestmark = [
    pytest.mark.osworld_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_OSWORLD_TESTS") != "1" or not GUEST_IMAGE.is_file(),
        reason=(
            "set PIXELGYM_RUN_OSWORLD_TESTS=1 after preparing the pinned local OSWorld image"
        ),
    ),
]


def test_v5_osworld_live_reconnect_verifies_exact_state_and_rejects_stale_state() -> None:
    """Exercise live reconnect against the real local Docker-backed OSWorld session."""

    def stop_loss(_signum: int, _frame: object) -> None:
        raise TimeoutError("v5 OSWorld live-reconnect test exceeded the 90-minute stop-loss")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_alarm_seconds = signal.alarm(0)
    alarm_started_at = time.monotonic()
    backend: OSWorldBackend | None = None
    try:
        signal.signal(signal.SIGALRM, stop_loss)
        signal.alarm(90 * 60)
        backend = OSWorldBackend(OSWorldBackendConfig(guest_image_path=GUEST_IMAGE))
        backend.reset(7)
        initial_checkpoint = backend.checkpoint()
        initial_resume = backend.environment_resume_record(step_count=0)

        backend.restore(initial_checkpoint)
        backend.verify_resume_record(initial_resume, step_count=0)

        backend.noop()
        assert backend.structured_action_count == 1
        with pytest.raises(OSWorldBackendError, match="cannot prove"):
            backend.restore(initial_checkpoint)
        with pytest.raises(OSWorldBackendError, match="binding mismatch"):
            backend.verify_resume_record(initial_resume, step_count=0)

        committed_checkpoint = backend.checkpoint()
        committed_resume = backend.environment_resume_record(step_count=1)
        backend.restore(committed_checkpoint)
        backend.verify_resume_record(committed_resume, step_count=1)
        assert committed_resume.mechanism == "live_reconnect"
    finally:
        signal.alarm(120)
        try:
            if backend is not None:
                backend.close()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_alarm_seconds > 0:
                elapsed_seconds = math.ceil(time.monotonic() - alarm_started_at)
                signal.alarm(max(1, previous_alarm_seconds - elapsed_seconds))
