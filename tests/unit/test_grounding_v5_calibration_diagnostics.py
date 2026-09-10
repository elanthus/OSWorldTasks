from types import SimpleNamespace

import pytest

from pixelgym.grounding.v5 import calibration_diagnostics as module


def task(recovery=False):
    return SimpleNamespace(
        stages=[
            SimpleNamespace(critical=False, dependency_id="ref", recovery_stage=False),
            SimpleNamespace(critical=True, dependency_id="ref", recovery_stage=recovery),
            SimpleNamespace(critical=True, dependency_id=None, recovery_stage=False),
        ],
        optimal_low_level_actions=3,
    )


def trace(*events):
    return [
        {
            "step_index": i,
            "diagnostic": {
                "stage_index": stage,
                "event": event,
                "visible_error": error,
                "irreversible_failure": False,
            },
        }
        for i, (stage, event, error) in enumerate(events)
    ]


def test_clean_consumer_and_commit():
    result = module.score_trace(
        task(),
        trace(
            (1, "correct_transition", False),
            (2, "correct_transition", False),
            (3, "correct_transition", False),
        ),
        "success",
        True,
    )
    assert [d["first_decision_correct"] for d in result["critical_decisions"]] == [True, True]
    assert result["deferred_consumers"][0]["resolved_before_any_visible_error"] is True
    assert result["deferred_consumers"][0]["resolved_before_declared_repair"] is True
    assert result["path_overhead"] == 0


def test_ordinary_error_distinguishes_retention_readings():
    result = module.score_trace(
        task(),
        trace(
            (1, "correct_transition", False),
            (1, "incorrect_choice", True),
            (2, "correct_transition", False),
            (3, "correct_transition", False),
        ),
        "success",
        True,
    )
    assert [d["first_decision_correct"] for d in result["critical_decisions"]] == [False, True]
    assert result["deferred_consumers"][0]["resolved_before_any_visible_error"] is False
    assert result["deferred_consumers"][0]["resolved_before_declared_repair"] is True
    assert (
        result["visible_recovery_entries"]
        == result["recovery_entries_followed_by_terminal_success"]
        == 1
    )


def test_correct_action_can_enter_declared_repair():
    result = module.score_trace(
        task(True),
        trace(
            (1, "correct_transition", False),
            (1, "entered_declared_recovery", True),
            (2, "visible_error_repaired", False),
            (3, "correct_transition", False),
        ),
        "success",
        True,
    )
    assert [d["first_decision_correct"] for d in result["critical_decisions"]] == [True, True]
    assert result["deferred_consumers"][0]["resolved_before_any_visible_error"] is False
    assert result["deferred_consumers"][0]["resolved_before_declared_repair"] is False


def test_unreached_decisions_are_not_observed_incorrect_answers():
    result = module.score_trace(
        task(), trace((0, "non_progressing_noop", False)), "step_limit_truncation", False
    )
    assert all(d["outcome"] == "not_entered" for d in result["critical_decisions"])
    aggregate = module.aggregate([{"diagnostics": result, "success": False}])
    assert aggregate["critical_declared_in_observed_episodes"] == 2
    assert aggregate["critical_first_decisions_observed"] == 0
    assert aggregate["critical_accuracy_conditional_on_observed_first_decision"] is None
    assert aggregate["retention_any_error_conditional_on_entry"] is None


def test_invalid_output_at_entered_critical_stage_counts_incorrect():
    result = module.score_trace(
        task(), trace((1, "correct_transition", False)), "invalid_output", False
    )
    assert result["critical_decisions"][0]["first_decision_correct"] is False
    assert result["critical_decisions"][1]["first_decision_correct"] is None


def test_missing_episode_stays_missing():
    result = module.aggregate([{"diagnostics": None, "success": None}])
    assert result["missing_episodes"] == 1
    assert result["observed_episodes"] == 0
    assert result["critical_accuracy_conditional_on_observed_first_decision"] is None


def test_skipped_state_is_rejected():
    with pytest.raises(ValueError, match="skipped stage"):
        module.score_trace(task(), trace((2, "correct_transition", False)), "success", True)
