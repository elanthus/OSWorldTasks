from __future__ import annotations

from pathlib import Path

import pytest

from pixelgym.grounding.v5 import d56_qwen_full_calibration as calibration
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
        lambda _path: {
            "approved_plan_sha256": "sha256:smoke",
            "actual_aggregate_spend_usd": "0.372661310",
        },
    )
    monkeypatch.setattr(
        calibration,
        "_validated_frozen_bcd_evidence",
        lambda _path: {
            "approved_plan_sha256": "sha256:qwen-429",
            "actual_aggregate_spend_usd": "2.040917557",
            "terminal": {"http_status": 429, "request_outcome": "unknown"},
        },
    )
    monkeypatch.setattr(
        calibration,
        "_validated_latest_spend_evidence",
        lambda _root, *, frozen_gemini_output_directory: {
            "approved_plan_sha256": calibration.FROZEN_GEMINI_PLAN_SHA256,
            "actual_aggregate_spend_usd": "4.552765957",
            "conservative_aggregate_spend_usd": "4.652298757",
            "remaining_aggregate_spend_usd": "5.447234043",
            "journal_path": str(frozen_gemini_output_directory / "attempts.sqlite"),
        },
    )


def test_qwen_retry_successor_is_distinct_but_keeps_route_and_adapter() -> None:
    successor = QWEN_STATEFUL_RETRY_SUCCESSOR

    assert successor not in PANEL
    assert successor.slot == "B-qwen-stateful-v2"
    assert successor.model == QWEN_STATEFUL.model == "qwen/qwen3-vl-8b-instruct"
    assert successor.provider_route == QWEN_STATEFUL.provider_route == "alibaba"
    assert successor.adapter == QWEN_STATEFUL.adapter
    assert successor.max_model_attempts_per_action == 2
    assert successor.max_rate_limit_retries_per_action == 1


def test_plan_binds_all_tasks_latest_spend_and_bounded_429_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_evidence(monkeypatch)

    plan = calibration.build_plan(
        ROOT,
        smoke_output_directory=tmp_path / "smoke",
        frozen_bcd_output_directory=tmp_path / "bcd",
        frozen_gemini_output_directory=tmp_path / "gemini",
    )

    manifest = plan["policy"]["policy_manifest"]
    inference = dict(manifest["inference_parameters"])
    assert plan["provider_calls_made"] == 0
    assert plan["assigned_policy_task_pairs"] == 50
    assert len(plan["task_order"]) == 50
    assert len({record["task_id"] for record in plan["task_order"]}) == 50
    assert plan["policy"]["slot"] == "B-qwen-stateful-v2"
    assert manifest["model"] == "qwen/qwen3-vl-8b-instruct"
    assert manifest["provider"] == "openrouter/alibaba"
    assert manifest["max_model_attempts_per_action"] == 2
    assert inference["max_rate_limit_retries_per_action"] == "1"
    assert inference["rate_limit_backoff_base_seconds"] == "2.0"
    assert inference["rate_limit_backoff_max_seconds"] == "60.0"
    assert plan["caps"]["environment_action_cap"] == 1431
    assert plan["caps"]["model_attempt_cap"] == 2862
    assert plan["caps"]["provider_wire_request_cap"] == 2862
    assert plan["caps"]["known_prior_aggregate_spend_usd"] == "4.552765957"
    assert plan["caps"]["unknown_prior_charge_reservation_usd"] == "0.099532800"
    assert plan["caps"]["prior_aggregate_spend_usd"] == "4.652298757"
    assert plan["caps"]["remaining_aggregate_spend_usd"] == "5.347701243"
    assert plan["caps"]["maximum_aggregate_spend_usd"] == "10.00"
    price = plan["policy"]["price_record"]
    assert price["observed_at_utc"] == "2026-08-28T13:34:08Z"
    assert price["endpoint_context_length"] == 131_072
    assert price["endpoint_tag"] == "alibaba"
    assert price["endpoint_status"] == 0
    assert price["supports_response_format"] is True
    assert price["supports_structured_outputs"] is True
    assert any("confirmed HTTP 429" in rule for rule in plan["stop_rules"])
    assert any("do not resume or replay" in rule for rule in plan["stop_rules"])
    assert calibration.plan_digest(plan).startswith("sha256:")


def test_plan_rejects_spend_lineage_that_moves_backwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_evidence(monkeypatch)
    monkeypatch.setattr(
        calibration,
        "_validated_frozen_bcd_evidence",
        lambda _path: {"actual_aggregate_spend_usd": "5.00"},
    )

    with pytest.raises(ValueError, match="predecessor spend exceeds"):
        calibration.build_plan(
            ROOT,
            smoke_output_directory=tmp_path / "smoke",
            frozen_bcd_output_directory=tmp_path / "bcd",
            frozen_gemini_output_directory=tmp_path / "gemini",
        )


def test_execute_rejects_unapproved_digest_before_output(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="approved Qwen full calibration digest"):
        calibration.execute_calibration(
            ROOT,
            plan={"provider_calls_made": 0},
            approved_plan_sha256="sha256:not-approved",
            smoke_output_directory=tmp_path / "smoke",
            frozen_bcd_output_directory=tmp_path / "bcd",
            frozen_gemini_output_directory=tmp_path / "gemini",
            output_directory=output,
        )

    assert not output.exists()


def test_latest_spend_validation_checks_audit_before_raw_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calibration, "_file_digest", lambda _path: "sha256:wrong")
    raw_digest_called = False

    def raw_digest(_path: Path) -> str:
        nonlocal raw_digest_called
        raw_digest_called = True
        return calibration.FROZEN_GEMINI_JOURNAL_SHA256

    monkeypatch.setattr(calibration, "_streaming_file_digest", raw_digest)

    with pytest.raises(ValueError, match="integrity-audit digest mismatch"):
        calibration._validated_latest_spend_evidence(
            ROOT,
            frozen_gemini_output_directory=tmp_path / "gemini",
        )

    assert not raw_digest_called
