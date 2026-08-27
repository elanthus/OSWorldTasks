from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import d56_bcd_calibration
from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps
from pixelgym.grounding.v5.d56_bcd_calibration import (
    FROZEN_TERMINAL_IDENTITY,
    build_plan,
    execute_calibration,
    plan_digest,
)
from pixelgym.grounding.v5.journal import V5AttemptJournal
from tests.unit.test_grounding_v5_d56_calibration import fake_smoke_output

ROOT = Path(__file__).parents[2]


def fake_frozen_calibration_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    output = tmp_path / "frozen-calibration"
    output.mkdir()
    journal_path = output / "attempts.sqlite"
    journal = V5AttemptJournal(journal_path)
    caps = CallCaps(864, 864, 0, 864)
    try:
        for index in range(863):
            _event, created = journal.reserve_attempt_started(
                AttemptIdentity(f"prior-{index}", 0, 0),
                provider_endpoint_identity="openrouter",
                request_digest=f"sha256:request-{index}",
                idempotency_key=f"prior-{index}",
                model_attempt_reservation=1,
                control_request_reservation=0,
                pre_call_checkpoint=b"checkpoint",
                approved_caps=caps,
            )
            assert created
        _event, created = journal.reserve_attempt_started(
            FROZEN_TERMINAL_IDENTITY,
            provider_endpoint_identity="openrouter",
            request_digest="sha256:terminal-request",
            idempotency_key="terminal",
            model_attempt_reservation=1,
            control_request_reservation=0,
            pre_call_checkpoint=b"checkpoint",
            approved_caps=caps,
        )
        assert created
        journal.seal_attempt_terminal(
            FROZEN_TERMINAL_IDENTITY,
            kind="unknown_outcome_infrastructure_failure",
            post_attempt_checkpoint=b"checkpoint",
            failure_code="runner_request_deadline",
        )
        integrity = journal.integrity_report()
    finally:
        journal.close()
    terminal_result = {
        "classification": "infrastructure_failure",
        "environment_actions": 14,
        "final_policy_checkpoint_digest": (
            "sha256:677ece2bb2f88aa36136e3bd472ec6c3622f5ccbd3815b271888d1f4e137c188"
        ),
        "model_attempts": 15,
        "provider_control_requests": 0,
        "provider_wire_requests": 15,
        "slot": "A-gemini-stateful",
        "success": False,
        "task_id": "v5-48860ad9b285908aa000a26b",
        "trial_id": FROZEN_TERMINAL_IDENTITY.trial_id,
    }
    summary = {
        "schema_version": "pixelgym-agent-v5-d56-calibration-result-v2",
        "approved_plan_sha256": d56_bcd_calibration.FROZEN_PLAN_SHA256,
        "code_revision": d56_bcd_calibration.FROZEN_CODE_REVISION,
        "provider_calls_made": 864,
        "provider_wire_requests": 864,
        "model_attempt_reservations": 864,
        "provider_control_requests": 0,
        "prior_aggregate_spend_usd": "0.372661310",
        "actual_aggregate_spend_usd": "2.032875185",
        "calibration_incremental_spend_usd": "1.660213875",
        "remaining_aggregate_spend_usd": "7.967124815",
        "assigned_policy_task_pairs": 200,
        "attempted_policy_task_pairs": 33,
        "successful_policy_task_pairs": 20,
        "classifications": {
            "infrastructure_failure": 1,
            "step_limit_truncation": 12,
            "success_termination": 20,
        },
        "episode_results": [
            {"slot": "A-gemini-stateful"} for _index in range(32)
        ]
        + [terminal_result],
        "journal_integrity": integrity,
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        d56_bcd_calibration,
        "FROZEN_SUMMARY_SHA256",
        d56_bcd_calibration._file_digest(summary_path),
    )
    monkeypatch.setattr(
        d56_bcd_calibration,
        "FROZEN_JOURNAL_SHA256",
        d56_bcd_calibration._streaming_file_digest(journal_path),
    )
    monkeypatch.setattr(d56_bcd_calibration, "FROZEN_JOURNAL_INTEGRITY", integrity)
    return output


def test_bcd_plan_binds_only_unattempted_slots_and_remaining_shared_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_plan(
        ROOT,
        smoke_output_directory=fake_smoke_output(tmp_path),
        frozen_calibration_output_directory=fake_frozen_calibration_output(
            tmp_path, monkeypatch
        ),
    )

    assert plan["provider_calls_made"] == 0
    assert plan["assigned_policy_task_pairs"] == 150
    assert plan["calibration_partition"]["episode_count"] == 50
    assert plan["calibration_partition"]["action_cap_per_policy"] == 1431
    assert len(plan["task_order"]) == 50
    assert [record["slot"] for record in plan["policies"]] == [
        "B-qwen-stateful",
        "C-llama-stateful",
        "D-qwen-stateless",
    ]
    assert all(record["caps"]["model_attempt_cap"] == 2862 for record in plan["policies"])
    assert plan["aggregate_caps"]["environment_action_cap"] == 4293
    assert plan["aggregate_caps"]["model_attempt_cap"] == 8586
    assert plan["aggregate_caps"]["provider_control_request_cap"] == 0
    assert plan["aggregate_caps"]["provider_wire_request_cap"] == 8586
    assert plan["aggregate_caps"]["maximum_aggregate_spend_usd"] == "10.00"
    assert plan["aggregate_caps"]["prior_aggregate_spend_usd"] == "2.032875185"
    assert plan["aggregate_caps"]["remaining_aggregate_spend_usd"] == "7.967124815"
    assert (
        plan["aggregate_caps"]["uncapped_theoretical_request_maximum_usd"]
        == "135.561904128"
    )
    assert plan["frozen_predecessor_evidence"]["terminal"] == {
        "classification": "unknown_outcome_infrastructure_failure",
        "failure_code": "runner_request_deadline",
        "trial_id": FROZEN_TERMINAL_IDENTITY.trial_id,
        "step_index": 14,
        "attempt_index": 0,
        "request_outcome": "unknown",
        "retry_eligible": False,
    }
    assert plan_digest(plan).startswith("sha256:")


def test_bcd_execution_rejects_unapproved_digest_before_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke_output = fake_smoke_output(tmp_path)
    frozen_output = fake_frozen_calibration_output(tmp_path, monkeypatch)
    plan = build_plan(
        ROOT,
        smoke_output_directory=smoke_output,
        frozen_calibration_output_directory=frozen_output,
    )
    output = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="approved B/C/D calibration plan digest"):
        execute_calibration(
            ROOT,
            plan=plan,
            approved_plan_sha256="sha256:not-approved",
            smoke_output_directory=smoke_output,
            frozen_calibration_output_directory=frozen_output,
            output_directory=output,
        )

    assert not output.exists()


def test_consumed_native_bcd_execution_is_locked_after_adapter_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke_output = fake_smoke_output(tmp_path)
    frozen_output = fake_frozen_calibration_output(tmp_path, monkeypatch)
    plan = build_plan(
        ROOT,
        smoke_output_directory=smoke_output,
        frozen_calibration_output_directory=frozen_output,
    )
    output = tmp_path / "must-not-exist"

    with pytest.raises(RuntimeError, match="native-coordinate B/C/D calibration is frozen"):
        execute_calibration(
            ROOT,
            plan=plan,
            approved_plan_sha256=plan_digest(plan),
            smoke_output_directory=smoke_output,
            frozen_calibration_output_directory=frozen_output,
            output_directory=output,
        )

    assert not output.exists()
