"""Completed budget and time stops survive a publication interruption."""

from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest

from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import SpendLedger
from pixelgym.grounding.v5.runner import InjectedInterruption
from tests.support.grounding_v5 import GoldenTransport
from tests.support.grounding_v5 import reliable_execute as execute

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("stop", ["budget", "time"])
def test_completed_stop_survives_publication_interruption(tmp_path, stop):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        transport = GoldenTransport(
            SpendLedger(Decimal(28), Decimal(28 if stop == "budget" else 0), journal=journal)
        )

        def interrupt(name):
            if name == "full_result_recorded":
                raise InjectedInterruption(name)

        with pytest.raises(InjectedInterruption):
            execute(journal, transport, boundary=interrupt, time_exhausted=lambda: stop == "time")
        saved = journal.event("history/full_completed").payload
        row = execute(journal, transport)
        assert row == saved
        assert row["classification"] == ("budget_stop" if stop == "budget" else "phase_time_stop")
        assert not transport.model_requests
