"""ScreenshotMemoryRunner rejects an oversized horizon before any state change."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import CallCaps
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.panel_policy import GEMINI_STATEFUL
from pixelgym.grounding.v5.runner import ScriptedTransport
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    ScreenshotMemoryRunner,
    build_screenshot_policy_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("retain", [True, False])
def test_oversized_horizon_rejected_before_reset_or_requests(tmp_path: Path, retain: bool) -> None:
    class MustNotReset(MemoryBackend):
        def reset(self, seed):
            raise AssertionError("preflight must reject before backend reset")

    task = replace(
        generate_memory_task(5000),
        optimal_low_level_actions=40,
        correction_slack=10,
        max_episode_steps=50,
    )
    manifest = build_screenshot_policy_manifest(
        ROOT, config=GEMINI_STATEFUL, code_revision="test", retain_screenshots=retain
    )
    transport = ScriptedTransport()
    with closing(V5AttemptJournal(tmp_path / "horizon.sqlite")) as journal:
        runner = ScreenshotMemoryRunner(
            manifest=manifest,
            journal=journal,
            policy=ScreenshotMemoryPolicy(GEMINI_STATEFUL, retain_screenshots=retain),
            transport=transport,
            approved_caps=CallCaps(50, 50, 0, 50),
        )
        with pytest.raises(ValueError, match="frozen screenshot capacity"):
            runner.run(trial_id="too-long", task=task, backend=MustNotReset())
        assert transport.model_requests == []
        assert journal.events() == ()
        runner._preflight(task, MustNotReset(), required_action_limit=32)
