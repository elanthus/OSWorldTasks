from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from legacy.grounding.scripts import run_grounding_v5_d56_qwen_full_calibration
from legacy.grounding.v5 import d56_qwen_full_calibration as calibration
from pixelgym.grounding.v5.panel_policy import (
    PANEL,
    QWEN_STATEFUL,
    QWEN_STATEFUL_RETRY_SUCCESSOR,
)

ROOT = Path(__file__).parents[2]


def _stub_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calibration, "_git", lambda *_args: "revision-qwen-v2")
    monkeypatch.setattr(
        calibration,
        "_validated_smoke_evidence",
        lambda _root, _path: {
            "approved_plan_sha256": "sha256:smoke",
            "actual_aggregate_spend_usd": "0.372661310",
        },
    )
    monkeypatch.setattr(
        calibration,
        "_validated_frozen_bcd_evidence",
        lambda _root, _path: {
            "approved_plan_sha256": "sha256:qwen-429",
            "actual_aggregate_spend_usd": "2.040917557",
            "terminal": {"http_status": 429, "request_outcome": "unknown"},
        },
    )


def test_qwen_retry_successor_is_distinct_but_keeps_route_and_adapter() -> None:
    successor = QWEN_STATEFUL_RETRY_SUCCESSOR

    assert successor not in PANEL
    assert successor.slot == "B-qwen-stateful-v3"
    assert successor.model == QWEN_STATEFUL.model == "qwen/qwen3-vl-8b-instruct"
    assert successor.provider_route == QWEN_STATEFUL.provider_route == "alibaba"
    assert successor.adapter == QWEN_STATEFUL.adapter
    assert successor.max_model_attempts_per_action == 4
    assert successor.max_rate_limit_retries_per_action == 3
    assert successor.bounded_retry_budget == 3


def test_plan_binds_all_tasks_latest_spend_and_bounded_429_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_evidence(monkeypatch)

    plan = calibration.build_plan(
        ROOT,
        smoke_output_directory=tmp_path / "smoke",
        frozen_bcd_output_directory=tmp_path / "bcd",
        maximum_spend_usd=Decimal("1.50"),
    )

    manifest = plan["policy"]["policy_manifest"]
    inference = dict(manifest["inference_parameters"])
    assert plan["provider_calls_made"] == 0
    assert plan["assigned_policy_task_pairs"] == 50
    assert len(plan["task_order"]) == 50
    assert len({record["task_id"] for record in plan["task_order"]}) == 50
    assert plan["policy"]["slot"] == "B-qwen-stateful-v3"
    assert manifest["model"] == "qwen/qwen3-vl-8b-instruct"
    assert manifest["provider"] == "openrouter/alibaba"
    assert manifest["max_model_attempts_per_action"] == 4
    assert inference["max_rate_limit_retries_per_action"] == "3"
    assert inference["max_bounded_retries_per_action"] == "3"
    assert inference["rate_limit_backoff_base_seconds"] == "2.0"
    assert inference["rate_limit_backoff_max_seconds"] == "60.0"
    assert plan["caps"]["environment_action_cap"] == 1431
    assert plan["caps"]["model_attempt_cap"] == 5724
    assert plan["caps"]["provider_wire_request_cap"] == 5724
    # The budget is exactly the approved value; no prior run contributes to it.
    assert plan["caps"]["maximum_run_spend_usd"] == "1.50"
    assert "no prior run's spend is carried in" in plan["caps"]["spend_lineage"]
    assert plan["caps"]["remaining_run_spend_usd"] == "1.50"
    assert plan["caps"]["prior_campaign_spend"]["known_spend_usd"] == "2.040917557"
    assert plan["caps"]["prior_campaign_spend"]["unknown_reservation_usd"] == (
        "unknown"
    )
    price = plan["policy"]["price_record"]
    assert price["observed_at_utc"] == "2026-08-28T13:34:08Z"
    assert price["endpoint_context_length"] == 131_072
    assert price["endpoint_tag"] == "alibaba"
    assert price["endpoint_status"] == 0
    assert price["supports_response_format"] is True
    assert price["supports_structured_outputs"] is True
    assert any("confirmed HTTP 429" in rule for rule in plan["stop_rules"])
    assert any(
        "against this run's approved 1.50 USD ledger" in rule
        for rule in plan["stop_rules"]
    )
    assert any(
        "approved maximum_run_spend_usd cap of 1.50 USD" in rule
        for rule in plan["stop_rules"]
    )
    assert all("shared ten-dollar ledger" not in rule for rule in plan["stop_rules"])
    assert any("do not resume or replay" in rule for rule in plan["stop_rules"])
    assert calibration.plan_digest(plan).startswith("sha256:")


@pytest.mark.parametrize(
    "budget",
    [
        Decimal(0),
        Decimal(-1),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_plan_rejects_a_non_finite_or_non_positive_run_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: Decimal
) -> None:
    _stub_evidence(monkeypatch)

    with pytest.raises(ValueError, match="maximum run spend must be finite and positive"):
        calibration.build_plan(
            ROOT,
            smoke_output_directory=tmp_path / "smoke",
            frozen_bcd_output_directory=tmp_path / "bcd",
            maximum_spend_usd=budget,
        )


@pytest.mark.parametrize(
    "value", ["abc", "NaN", "sNaN", "Infinity", "-Infinity", "0", "-1"]
)
def test_command_reports_invalid_run_budgets_as_usage_errors(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_grounding_v5_d56_qwen_full_calibration.parse_args(
            [
                "--plan-only",
                "--output",
                "plan.json",
                "--smoke-output",
                "smoke",
                "--frozen-bcd-output",
                "bcd",
                f"--maximum-spend-usd={value}",
            ]
        )

    assert exc_info.value.code == 2
    assert "must be a finite positive decimal" in capsys.readouterr().err


def test_plan_budget_is_the_only_spend_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_evidence(monkeypatch)

    def plan_for(budget: str) -> dict[str, object]:
        return calibration.build_plan(
            ROOT,
            smoke_output_directory=tmp_path / "smoke",
            frozen_bcd_output_directory=tmp_path / "bcd",
            maximum_spend_usd=Decimal(budget),
        )

    # Changing only the budget must change the plan, and therefore its digest,
    # so a budget can never be swapped in under an already-approved plan.
    cheap, rich = plan_for("0.25"), plan_for("9.75")
    assert cheap["caps"]["maximum_run_spend_usd"] == "0.25"  # type: ignore[index]
    assert rich["caps"]["maximum_run_spend_usd"] == "9.75"  # type: ignore[index]
    assert calibration.plan_digest(cheap) != calibration.plan_digest(rich)


def test_execute_rejects_unapproved_digest_before_output(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="approved Qwen full calibration digest"):
        calibration.execute_calibration(
            ROOT,
            plan={"provider_calls_made": 0},
            approved_plan_sha256="sha256:not-approved",
            smoke_output_directory=tmp_path / "smoke",
            frozen_bcd_output_directory=tmp_path / "bcd",
            output_directory=output,
        )

    assert not output.exists()
