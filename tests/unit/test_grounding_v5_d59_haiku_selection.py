"""The D5.9 Haiku owner selection is exact, response-free, and non-executable."""

from __future__ import annotations

import copy
import json

import pytest

from scripts.prepare_grounding_v5_d59_haiku_selection import (
    ANALYSIS,
    SELECTION,
    build_selection,
)


def test_checked_in_haiku_selection_reproduces_without_provider_access() -> None:
    selection = build_selection()
    assert json.loads(SELECTION.read_text(encoding="utf-8")) == selection
    assert selection["owner_statement"] == "yes, select haiku"
    assert selection["selected_pair"] == {
        "model": "claude-haiku-4-5-20251001",
        "provider": "claude-code-cli/claude-ai-max-subscription",
        "cli_version": "2.1.267 (Claude Code)",
        "history_policy_id": "policy-79441db33362e00a1ac6",
        "stateless_policy_id": "policy-e1d746cd6a83ffb12a20",
    }


def test_selection_carries_168_pair_design_but_no_execution_authority() -> None:
    selection = build_selection()
    assert selection["selected_design"]["independent_representatives"] == 168
    assert selection["selected_design"]["episodes_per_arm"] == 192
    assert selection["selected_design"]["robustness_twins_per_arm"] == 24
    assert selection["selected_design"]["power_at_observed_discordance"] == pytest.approx(
        0.8702198361382038
    )
    assert selection["selected_design"]["power_at_upper_sensitivity"] == pytest.approx(
        0.8120345910241988
    )
    boundary = selection["execution_boundary"]
    assert boundary["execution_enabled"] is False
    assert boundary["paid_or_subscription_execution_authorized"] is False
    assert boundary["approved_model_attempt_cap"] == 0
    assert boundary["approved_provider_wire_request_cap"] == 0
    assert boundary["aggregate_planning_cap_usd"] is None
    assert boundary["provider_calls_made"] == 0
    assert boundary["confirmatory_tasks_generated"] == 0
    assert boundary["os_sandbox_applied"] == {"history": False, "stateless": False}


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("provider_calls_made",), 1, "response-free"),
        (("carry_forward_candidate", "independent_pairs"), 167, "independent-pair"),
        (
            ("carry_forward_candidate", "meets_power_at_upper_sensitivity"),
            False,
            "upper-sensitivity",
        ),
        (("calibration", "history_policy_id"), "policy-replaced", "identity drift"),
        (("execution_readiness", "os_sandbox_applied", "history"), True, "unsandboxed"),
    ],
)
def test_selection_rejects_changed_source_analysis(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    changed = copy.deepcopy(json.loads(ANALYSIS.read_text(encoding="utf-8")))
    target = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    damaged = tmp_path / "analysis.json"
    damaged.write_text(json.dumps(changed), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.prepare_grounding_v5_d59_haiku_selection.ANALYSIS", damaged
    )
    with pytest.raises(ValueError, match=message):
        build_selection()
