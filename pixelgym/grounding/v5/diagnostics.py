"""Safe aggregation helpers for privileged v5 diagnostic evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pixelgym.grounding.v5.journal import V5AttemptJournal


def maximum_stage_index(diagnostics: Sequence[Mapping[str, Any]]) -> int:
    """Return the largest plain-integer stage index without coercing malformed evidence."""

    return max(
        (
            diagnostic["stage_index"]
            for diagnostic in diagnostics
            if type(diagnostic.get("stage_index")) is int
        ),
        default=0,
    )


def privileged_diagnostic_for_step(
    journal: V5AttemptJournal, *, trial_id: str, step_index: int
) -> Mapping[str, Any]:
    """Return the host-only privileged diagnostic recorded for one dispatch step.

    This is the one sanctioned path for reading this evidence: it reads the
    ``privileged_dispatch_diagnostic`` event a committed dispatch references, and
    falls back to the diagnostic embedded directly in a pre-split
    ``dispatch_committed`` payload for evidence sealed before that event existed.
    The result is host-only audit/resume evidence; it must never be forwarded to
    a policy hook or policy checkpoint.
    """

    event_key = f"{trial_id}/step-{step_index:04d}/dispatch_committed"
    event = journal.event(event_key)
    if event is None or event.kind != "dispatch_committed":
        raise RuntimeError(f"no committed dispatch evidence for {event_key}")
    diagnostic_event_key = event.payload.get("privileged_diagnostic_event_key")
    if diagnostic_event_key is not None:
        diagnostic_event = journal.event(diagnostic_event_key)
        if diagnostic_event is None or diagnostic_event.kind != "privileged_dispatch_diagnostic":
            raise RuntimeError(f"missing privileged diagnostic event for {event_key}")
        diagnostic = diagnostic_event.payload.get("diagnostic")
    else:
        diagnostic = event.payload.get("diagnostic")
    if isinstance(diagnostic, Mapping):
        return diagnostic
    raise RuntimeError(f"committed dispatch is missing privileged evidence for {event_key}")
