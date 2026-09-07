from __future__ import annotations

from pathlib import Path

import pytest

from pixelgym.grounding.v5.diagnostics import (
    maximum_stage_index,
    privileged_diagnostic_for_step,
)
from pixelgym.grounding.v5.journal import V5AttemptJournal


def test_maximum_stage_index_ignores_missing_and_non_integer_values() -> None:
    diagnostics = [
        {"event": "missing"},
        {"event": "string", "stage_index": "9"},
        {"event": "boolean", "stage_index": True},
        {"event": "lower", "stage_index": 1},
        {"event": "higher", "stage_index": 3},
    ]

    assert maximum_stage_index(diagnostics) == 3
    assert maximum_stage_index([{"event": "missing"}]) == 0


def test_privileged_diagnostic_for_step_reads_the_split_event(tmp_path: Path) -> None:
    journal = V5AttemptJournal(tmp_path / "split.sqlite")
    trial_id = "trial-split"
    diagnostic_event_key = f"{trial_id}/step-0000/privileged_dispatch_diagnostic"
    journal.append_event(
        event_key=diagnostic_event_key,
        kind="privileged_dispatch_diagnostic",
        trial_id=trial_id,
        step_index=0,
        payload={
            "diagnostic": {"stage_index": 2, "event": "committed"},
            "diagnostic_digest": "sha256:" + "1" * 64,
            "policy_visible_result_digest": "sha256:" + "2" * 64,
        },
    )
    journal.append_event(
        event_key=f"{trial_id}/step-0000/dispatch_committed",
        kind="dispatch_committed",
        trial_id=trial_id,
        step_index=0,
        payload={
            "commit_result_digest": "sha256:" + "2" * 64,
            "privileged_diagnostic_event_key": diagnostic_event_key,
        },
    )

    diagnostic = privileged_diagnostic_for_step(journal, trial_id=trial_id, step_index=0)

    assert diagnostic == {"stage_index": 2, "event": "committed"}
    journal.close()


def test_privileged_diagnostic_for_step_falls_back_to_legacy_embedded_field(
    tmp_path: Path,
) -> None:
    journal = V5AttemptJournal(tmp_path / "legacy.sqlite")
    trial_id = "trial-legacy"
    journal.append_event(
        event_key=f"{trial_id}/step-0000/dispatch_committed",
        kind="dispatch_committed",
        trial_id=trial_id,
        step_index=0,
        payload={
            "commit_result_digest": "sha256:" + "3" * 64,
            "diagnostic": {"stage_index": 1, "event": "legacy"},
        },
    )

    diagnostic = privileged_diagnostic_for_step(journal, trial_id=trial_id, step_index=0)

    assert diagnostic == {"stage_index": 1, "event": "legacy"}
    journal.close()


def test_privileged_diagnostic_for_step_raises_without_any_evidence(tmp_path: Path) -> None:
    journal = V5AttemptJournal(tmp_path / "missing.sqlite")

    with pytest.raises(RuntimeError, match="no committed dispatch evidence"):
        privileged_diagnostic_for_step(journal, trial_id="trial-missing", step_index=0)

    journal.close()
