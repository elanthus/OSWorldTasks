"""Extensions preserve every started assignment and inherit the original clock."""

from copy import deepcopy

import pytest

from scripts.run_grounding_v5_reliable_continuation import PREVIOUS
from scripts.run_grounding_v5_reliable_extension import continuation_assignments, read


def test_extension_retains_success_failure_and_started_time_stop_without_replay():
    old = read(PREVIOUS / "execution-plan.json")
    summary = read(PREVIOUS / "summary.json")
    untouched = [r for r in summary["conditions"] if r["classification"] == "not_run"]
    for row, classification in zip(
        untouched[:3],
        ["success_termination", "step_limit_truncation", "phase_time_stop"],
        strict=True,
    ):
        row["classification"] = classification
    jobs, execution, preserved = continuation_assignments(old, summary)
    assert len(preserved) == 13 and len(execution) == 87 and len(jobs) == 100
    assert all(row in preserved for row in untouched[:3])
    assert {(j["seed"], j["mode"]) for j in execution}.isdisjoint(
        (r["seed"], r["mode"]) for r in preserved
    )
    assert [j["task_digest"] for j in jobs] == [j["task_digest"] for j in old["jobs"]]


@pytest.mark.parametrize("mutation", ["duplicates", "complete"])
def test_extension_refuses_duplicate_or_fully_recorded_assignments(mutation):
    old, summary = read(PREVIOUS / "execution-plan.json"), read(PREVIOUS / "summary.json")
    if mutation == "duplicates":
        old["jobs"][-1] = deepcopy(old["jobs"][-2])
        summary["conditions"][-1] = deepcopy(summary["conditions"][-2])
    else:
        for row in summary["conditions"]:
            row["classification"] = "infrastructure_failure"
    with pytest.raises(ValueError):
        continuation_assignments(old, summary)
