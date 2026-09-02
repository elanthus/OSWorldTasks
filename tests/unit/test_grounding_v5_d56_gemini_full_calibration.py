from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import d56_gemini_full_calibration as calibration
from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps
from pixelgym.grounding.v5.d56_bcd_calibration import _streaming_file_digest
from pixelgym.grounding.v5.d56_calibration import CONSECUTIVE_FAILURE_LIMIT, _file_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from scripts import run_grounding_v5_d56_gemini_full_calibration

ROOT = Path(__file__).parents[2]


def fake_successful_smoke_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    output = tmp_path / "successful-smoke"
    output.mkdir()
    journal_path = output / "attempts.sqlite"
    identity = AttemptIdentity(
        "d56-gemini-one-call-smoke-v5-64ba7d452b3c8d3e43d1d30e", 0, 0
    )
    journal = V5AttemptJournal(journal_path)
    try:
        _event, created = journal.reserve_attempt_started(
            identity,
            provider_endpoint_identity="openrouter",
            request_digest="sha256:request",
            idempotency_key="gemini-smoke",
            model_attempt_reservation=1,
            control_request_reservation=0,
            pre_call_checkpoint=b"checkpoint",
            approved_caps=CallCaps(1, 1, 0, 1),
        )
        assert created
        journal.seal_attempt_terminal(
            identity,
            kind="attempt_completed",
            post_attempt_checkpoint=b"checkpoint",
            response_digest="sha256:response",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )
        integrity = journal.integrity_report()
    finally:
        journal.close()
    summary = {
        "schema_version": "pixelgym-agent-v5-d56-gemini-one-call-smoke-result-v1",
        "approved_plan_sha256": calibration.FROZEN_SMOKE_PLAN_SHA256,
        "code_revision": calibration.FROZEN_SMOKE_CODE_REVISION,
        "provider_calls_made": 1,
        "provider_wire_requests": 1,
        "model_attempt_reservations": 1,
        "provider_control_requests": 0,
        "actual_aggregate_spend_usd": "2.488646332",
        "smoke_incremental_spend_usd": "0.001306875",
        "remaining_aggregate_spend_usd": "7.511353668",
        "reached_model_response": True,
        "journal_integrity": integrity,
        "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
        "episode_result": {
            "classification": "pilot_action_limit",
            "environment_actions": 1,
            "model_attempts": 1,
            "provider_wire_requests": 1,
            "provider_control_requests": 0,
            "slot": "A-gemini-stateful-one-call-smoke",
            "task_id": "v5-64ba7d452b3c8d3e43d1d30e",
        },
        "semantic_progress": {
            "first_transition_completed": True,
            "maximum_stage_index_observed": 1,
            "diagnostic_event_counts": {"correct_transition": 1},
        },
        "transport_records": [
            {
                "status": "response",
                "response_model": "google/gemini-3.7-flash",
                "upstream_provider": "Google",
                "cost_usd": "0.001306875",
            }
        ],
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        calibration, "FROZEN_SMOKE_SUMMARY_SHA256", _file_digest(summary_path)
    )
    monkeypatch.setattr(
        calibration,
        "FROZEN_SMOKE_JOURNAL_SHA256",
        _streaming_file_digest(journal_path),
    )
    monkeypatch.setattr(calibration, "FROZEN_SMOKE_JOURNAL_INTEGRITY", integrity)
    return output


def test_plan_binds_all_fifty_tasks_successful_smoke_and_shared_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke_output = fake_successful_smoke_output(tmp_path, monkeypatch)
    monkeypatch.setattr(calibration, "_git", lambda *_args: "revision-1")

    plan = calibration.build_plan(
        ROOT, smoke_output_directory=smoke_output, maximum_spend_usd=Decimal("3.00")
    )

    assert plan["provider_calls_made"] == 0
    assert plan["assigned_policy_task_pairs"] == 50
    assert plan["calibration_partition"]["episode_count"] == 50
    assert plan["calibration_partition"]["action_cap"] == 1431
    assert len(plan["task_order"]) == 50
    assert len({record["task_id"] for record in plan["task_order"]}) == 50
    assert plan["policy"]["policy_manifest"]["model"] == "google/gemini-3.7-flash"
    assert plan["policy"]["policy_manifest"]["max_model_attempts_per_action"] == 4
    assert dict(
        plan["policy"]["policy_manifest"]["inference_parameters"]
    )["max_rate_limit_retries_per_action"] == "3"
    assert plan["policy"]["policy_manifest"]["request_deadline_seconds"] == 210.0
    assert plan["policy"]["provider"]["only"] == ["google-vertex/global"]
    assert plan["caps"]["environment_action_cap"] == 1431
    assert plan["caps"]["model_attempt_cap"] == 5724
    assert plan["caps"]["provider_control_request_cap"] == 0
    assert plan["caps"]["provider_wire_request_cap"] == 5724
    # The budget is exactly the approved value; no prior run contributes to it.
    assert plan["caps"]["maximum_run_spend_usd"] == "3.00"
    assert "no prior run's spend is carried in" in plan["caps"]["spend_lineage"]
    assert not [key for key in plan["caps"] if "prior" in key or "aggregate" in key]
    assert plan["caps"]["per_request_theoretical_maximum_usd"] == "0.099532800"
    assert plan["caps"]["uncapped_run_theoretical_maximum_usd"] == "569.725747200"
    assert any("confirmed HTTP 429" in rule for rule in plan["stop_rules"])
    assert calibration.plan_digest(plan).startswith("sha256:")


def test_execute_rejects_unapproved_digest_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke_output = fake_successful_smoke_output(tmp_path, monkeypatch)
    monkeypatch.setattr(calibration, "_git", lambda *_args: "revision-1")
    plan = calibration.build_plan(
        ROOT, smoke_output_directory=smoke_output, maximum_spend_usd=Decimal("3.00")
    )
    output = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="approved Gemini full calibration digest"):
        calibration.execute_calibration(
            ROOT,
            plan=plan,
            approved_plan_sha256="sha256:not-approved",
            smoke_output_directory=smoke_output,
            output_directory=output,
        )

    assert not output.exists()


def test_command_refuses_existing_output_before_loading_plan(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()

    with pytest.raises(FileExistsError, match="refusing to replace"):
        run_grounding_v5_d56_gemini_full_calibration.main(
            [
                "--execute",
                "--plan",
                "missing-plan.json",
                "--approved-plan-sha256",
                "sha256:" + "0" * 64,
                "--smoke-output",
                "missing-smoke-output",
                "--output",
                str(output),
            ]
        )


def test_smoke_evidence_records_repository_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: this module recorded str(output_directory / ...), so an absolute
    # invocation embedded the operator's home directory in the stored plan and summary.
    smoke_output = fake_successful_smoke_output(tmp_path, monkeypatch)

    evidence = calibration._validated_smoke_evidence(tmp_path, smoke_output)

    assert evidence["summary_path"] == "successful-smoke/summary.json"
    assert evidence["journal_path"] == "successful-smoke/attempts.sqlite"
    assert str(tmp_path) not in evidence["summary_path"]
    assert str(tmp_path) not in evidence["journal_path"]


def test_plan_declares_run_continuation_and_unobservable_charge_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke_output = fake_successful_smoke_output(tmp_path, monkeypatch)
    monkeypatch.setattr(calibration, "_git", lambda *_args: "revision-1")

    plan = calibration.build_plan(
        ROOT, smoke_output_directory=smoke_output, maximum_spend_usd=Decimal("3.00")
    )
    continuation = plan["run_continuation"]

    assert continuation["consecutive_failure_limit"] == CONSECUTIVE_FAILURE_LIMIT
    assert "reserve the per-request theoretical maximum" in (
        continuation["unobservable_charge_rule"]
    )
    assert "http_429_rate_limit" in continuation["retryable_send_outcomes"]
    assert any(
        "transient transport fault" in outcome
        for outcome in continuation["retryable_send_outcomes"]
    )
    assert "run spend ledger blocked" in continuation["hard_stop_conditions"]
    # A malformed model output must fail the assignment, not the run.
    assert plan["policy"]["response_validation"][
        "invalid_or_unparseable_output_rule"
    ] == "retain_fail_the_assignment_and_continue_without_retry"
    assert any(
        "continue to the next task" in rule for rule in plan["stop_rules"]
    )
    assert any(
        f"stop after {CONSECUTIVE_FAILURE_LIMIT} consecutive" in rule
        for rule in plan["stop_rules"]
    )
