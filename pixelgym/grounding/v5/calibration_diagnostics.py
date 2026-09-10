"""Exploratory offline diagnostics; no provider or environment execution."""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from pixelgym.grounding.v5.contracts import V5Task


def score_trace(
    task: V5Task, trace: Sequence[Mapping[str, Any]], classification: str, success: bool
) -> dict[str, Any]:
    """Score stored host diagnostics, disclosing exposure and both recovery readings."""
    by_stage: defaultdict[int, list[Mapping[str, Any]]] = defaultdict(list)
    before = 0
    recovery_entries = []
    previously_visible_error = False
    for row in trace:
        diagnostic = row["diagnostic"]
        after = diagnostic["stage_index"]
        if type(after) is not int or after not in (before, before + 1):
            raise ValueError("non-monotonic or skipped stage in stored evidence")
        by_stage[before].append(row)
        if diagnostic["visible_error"] and not previously_visible_error:
            recovery_entries.append(row["step_index"])
        previously_visible_error = diagnostic["visible_error"]
        before = after
    maximum = before
    critical = []
    consumers = []
    seen_dependencies = set()
    for index, stage in enumerate(task.stages):
        actions = by_stage[index]
        entered = maximum >= index
        if stage.critical:
            first = actions[0]["diagnostic"] if actions else None
            correct: bool | None = None
            if first is not None:
                correct = (
                    first["stage_index"] == index + 1
                    and first["event"] in ("correct_transition", "text_value_accepted")
                ) or (stage.recovery_stage and first["event"] == "entered_declared_recovery")
            elif entered and classification == "invalid_output":
                correct = False
            critical.append(
                {
                    "stage_index": index,
                    "entered": entered,
                    "first_decision_observed": correct is not None,
                    "first_decision_correct": correct,
                    "outcome": ("correct" if correct else "incorrect")
                    if correct is not None
                    else ("entered_without_decision" if entered else "not_entered"),
                }
            )
        if stage.dependency_id is None:
            continue
        consumer = stage.dependency_id in seen_dependencies
        seen_dependencies.add(stage.dependency_id)
        if not consumer:
            continue
        any_error = declared_error = resolved = False
        retained_any = retained_declared = False
        for row in actions:
            diagnostic = row["diagnostic"]
            any_error = any_error or diagnostic["visible_error"]
            declared_error = declared_error or diagnostic["event"] == "entered_declared_recovery"
            if diagnostic["stage_index"] == index + 1:
                resolved = True
                retained_any = not any_error
                retained_declared = not declared_error
                break
        consumers.append(
            {
                "stage_index": index,
                "entered": entered,
                "resolved": resolved,
                "resolved_before_any_visible_error": retained_any,
                "resolved_before_declared_repair": retained_declared,
            }
        )
    return {
        "maximum_stage_index": maximum,
        "critical_decisions": critical,
        "deferred_consumers": consumers,
        "visible_recovery_entries": len(recovery_entries),
        "recovery_entries_followed_by_terminal_success": len(recovery_entries) if success else 0,
        "wrong_irreversible_commit": any(r["diagnostic"]["irreversible_failure"] for r in trace),
        "path_overhead": len(trace) - task.optimal_low_level_actions,
        "diagnostic_event_counts": dict(Counter(r["diagnostic"]["event"] for r in trace)),
    }


def aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Keep all declared decisions, exposure, and conditional rates distinct."""
    observed = [r for r in rows if r["diagnostics"] is not None]
    critical = [d for r in observed for d in r["diagnostics"]["critical_decisions"]]
    consumers = [d for r in observed for d in r["diagnostics"]["deferred_consumers"]]
    attempted = sum(d["first_decision_observed"] for d in critical)
    correct = sum(d["first_decision_correct"] is True for d in critical)
    entered = sum(d["entered"] for d in consumers)

    def rate(n: int, d: int) -> float | None:
        return n / d if d else None

    return {
        "assigned_episodes": len(rows),
        "observed_episodes": len(observed),
        "missing_episodes": len(rows) - len(observed),
        "critical_declared_in_observed_episodes": len(critical),
        "critical_entered": sum(d["entered"] for d in critical),
        "critical_first_decisions_observed": attempted,
        "critical_first_decisions_correct": correct,
        "critical_correct_fraction_of_declared_in_observed_episodes": rate(correct, len(critical)),
        "critical_accuracy_conditional_on_observed_first_decision": rate(correct, attempted),
        "critical_exclusions": dict(
            Counter(d["outcome"] for d in critical if not d["first_decision_observed"])
        ),
        "consumers_declared_in_observed_episodes": len(consumers),
        "consumers_entered": entered,
        "consumers_resolved": sum(d["resolved"] for d in consumers),
        "retained_before_any_visible_error": sum(
            d["resolved_before_any_visible_error"] for d in consumers
        ),
        "retained_before_declared_repair": sum(
            d["resolved_before_declared_repair"] for d in consumers
        ),
        "retention_any_error_conditional_on_entry": rate(
            sum(d["resolved_before_any_visible_error"] for d in consumers), entered
        ),
        "retention_declared_repair_conditional_on_entry": rate(
            sum(d["resolved_before_declared_repair"] for d in consumers), entered
        ),
        "consumer_exclusions_not_entered": sum(not d["entered"] for d in consumers),
        "recovery_entries": sum(r["diagnostics"]["visible_recovery_entries"] for r in observed),
        "recovery_entries_followed_by_terminal_success": sum(
            r["diagnostics"]["recovery_entries_followed_by_terminal_success"] for r in observed
        ),
        "wrong_irreversible_commit_episodes": sum(
            r["diagnostics"]["wrong_irreversible_commit"] for r in observed
        ),
        "successful_path_overheads": [
            r["diagnostics"]["path_overhead"] for r in observed if r["success"]
        ],
        "unsuccessful_path_overheads": [
            r["diagnostics"]["path_overhead"] for r in observed if not r["success"]
        ],
    }
