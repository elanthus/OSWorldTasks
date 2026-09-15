"""Retain actual closed evidence and select only its untouched assignments."""

from collections import Counter

import pytest

from scripts.run_grounding_v5_focus_continuation import PREVIOUS, continuation_assignments, read


def test_continuation_preserves_five_failures_and_exact_untouched_task_order():
    old, summary = read(PREVIOUS / "execution-plan.json"), read(PREVIOUS / "summary.json")
    jobs, execution, preserved = continuation_assignments(old, summary)
    assert len(jobs) == 100 and len(execution) == 95 and len(preserved) == 5
    assert Counter(j["mode"] for j in execution) == {"history": 47, "stateless": 48}
    assert preserved == [r for r in summary["conditions"] if r["classification"] != "not_run"]
    assert {(j["seed"], j["mode"]) for j in execution}.isdisjoint(
        (r["seed"], r["mode"]) for r in preserved
    )
    assert [{k: v for k, v in j.items() if k != "trial_id"} for j in jobs] == [
        {k: v for k, v in j.items() if k != "trial_id"} for j in old["jobs"]
    ]


@pytest.mark.parametrize("mutation", ["omit_failure", "changed_task", "changed_result"])
def test_invalid_carry_forward_cannot_authorize_calls(mutation):
    old, summary = read(PREVIOUS / "execution-plan.json"), read(PREVIOUS / "summary.json")
    if mutation == "omit_failure":
        summary["conditions"][0]["classification"] = "not_run"
    elif mutation == "changed_task":
        summary["conditions"][0]["task_id"] = "different"
    else:
        summary["conditions"][0]["classification"] = "success_termination"
    with pytest.raises(ValueError):
        continuation_assignments(old, summary)
