"""End-to-end focus backend, authoritative scores, and interruption boundaries."""

import json
from contextlib import closing
from decimal import Decimal

import pytest

from pixelgym.grounding.v5.contracts import CallCaps
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_focus_calibration import run_episode
from pixelgym.grounding.v5.panel_policy import SpendLedger
from pixelgym.grounding.v5.runner import InjectedInterruption
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    build_screenshot_policy_manifest,
)
from tests.unit.test_grounding_v5_memory_calibration import CONFIG, PLAN, ROOT, GoldenTransport, job


def execute(journal, transport, *, mode="history", **kwargs):
    return run_episode(
        journal,
        job=job(mode),
        manifest=build_screenshot_policy_manifest(
            ROOT, config=CONFIG, code_revision="test", retain_screenshots=mode == "history"
        ),
        policy=ScreenshotMemoryPolicy(CONFIG, retain_screenshots=mode == "history"),
        transport=transport,
        ledger=transport.ledger,
        caps=CallCaps(100, 100, 0, 100),
        plan_digest=PLAN,
        **kwargs,
    )


@pytest.mark.parametrize("mode,wrong", [("history", False), ("stateless", True)])
def test_fresh_focus_episode_scores_delayed_choices_without_prefix(tmp_path, mode, wrong):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        transport = GoldenTransport(
            SpendLedger(Decimal(20), Decimal(0), journal=journal), wrong_memory=wrong
        )
        row = execute(journal, transport, mode=mode)
        assert row["success"] is not wrong
        assert row["reached_consumers"] == [5, 7]
        assert [c["correct"] for c in row["first_attempts"]] == [not wrong, True]
        assert row["model_attempts"] == row["environment_actions_dispatched"]
        assert not any("prefix" in e.kind for e in journal.events())
        initial = journal.event(f"{mode}/initial_screenshot")
        checkpoint = json.loads(
            journal.get_object(initial.payload["environment_checkpoint_digest"])
        )
        assert checkpoint["stage_index"] == 0 and not checkpoint["deferred_choices"]
        assert (
            journal.event(f"{mode}/full_started").payload["backend_identity"]
            == FocusMemoryBackend.backend_identity
        )
        resume = journal.get_object(initial.payload["environment_resume_digest"])
        assert FocusMemoryBackend.backend_identity in resume.decode()
        count = len(transport.model_requests)
        assert execute(journal, transport, mode=mode) == row
        assert len(transport.model_requests) == count


@pytest.mark.parametrize(
    "boundary,success",
    [
        ("full_started", False),
        ("provider_receipt", False),
        ("dispatch_committed", False),
        ("before_full_result", True),
    ],
)
def test_interrupted_focus_episode_is_recorded_without_resending(tmp_path, boundary, success):
    path = tmp_path / "journal.sqlite"

    def interrupt(name):
        if name == boundary:
            raise InjectedInterruption(name)

    with closing(V5AttemptJournal(path)) as journal:
        transport = GoldenTransport(SpendLedger(Decimal(20), Decimal(0), journal=journal))
        with pytest.raises(InjectedInterruption):
            execute(journal, transport, boundary=interrupt)
        counts = journal.call_counts()
    with closing(V5AttemptJournal(path)) as journal:
        transport = GoldenTransport(SpendLedger(Decimal(20), Decimal(0), journal=journal))
        row = execute(journal, transport)
        assert row["success"] is success
        assert journal.call_counts() == counts and not transport.model_requests


def test_time_stop_prevents_next_model_action(tmp_path):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        transport = GoldenTransport(SpendLedger(Decimal(20), Decimal(0), journal=journal))
        row = execute(journal, transport, time_exhausted=lambda: len(transport.model_requests) >= 1)
        assert row["classification"] == "phase_time_stop"
        assert row["model_attempts"] == row["environment_actions_dispatched"] == 1
        assert not row["success"]


def test_budget_stop_prevents_next_model_action(tmp_path):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        transport = GoldenTransport(
            SpendLedger(Decimal("1.50"), Decimal(0), journal=journal), cost="0.10"
        )
        row = execute(journal, transport)
        assert row["classification"] == "budget_stop"
        assert row["model_attempts"] == 1
