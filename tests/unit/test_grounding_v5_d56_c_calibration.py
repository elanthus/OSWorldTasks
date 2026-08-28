from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import d56_c_calibration
from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps
from pixelgym.grounding.v5.d56_c_calibration import (
    FROZEN_BCD_TERMINAL_IDENTITY,
    build_plan,
    execute_calibration,
    plan_digest,
)
from pixelgym.grounding.v5.journal import V5AttemptJournal
from tests.unit.test_grounding_v5_d56_bcd_calibration import (
    fake_frozen_calibration_output,
)
from tests.unit.test_grounding_v5_d56_calibration import fake_smoke_output

ROOT = Path(__file__).parents[2]


def fake_frozen_bcd_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    output = tmp_path / "frozen-bcd"
    output.mkdir()
    journal_path = output / "attempts.sqlite"
    journal = V5AttemptJournal(journal_path)
    caps = CallCaps(25, 25, 0, 25)
    try:
        for index in range(24):
            _event, created = journal.reserve_attempt_started(
                AttemptIdentity(f"bcd-{index}", 0, 0),
                provider_endpoint_identity="openrouter",
                request_digest=f"sha256:request-{index}",
                idempotency_key=f"bcd-{index}",
                model_attempt_reservation=1,
                control_request_reservation=0,
                pre_call_checkpoint=b"checkpoint",
                approved_caps=caps,
            )
            assert created
        _event, created = journal.reserve_attempt_started(
            FROZEN_BCD_TERMINAL_IDENTITY,
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
            FROZEN_BCD_TERMINAL_IDENTITY,
            kind="unknown_outcome_infrastructure_failure",
            post_attempt_checkpoint=b"checkpoint",
            failure_code="provider_request_unknown",
        )
        integrity = journal.integrity_report()
    finally:
        journal.close()
    terminal_result = {
        "classification": "infrastructure_failure",
        "environment_actions": 24,
        "final_policy_checkpoint_digest": (
            "sha256:9ad51c920f5a512662a473ce425d591af2cb4f2ebf31d0f56fa9bb1b2bfbfb6a"
        ),
        "model_attempts": 25,
        "provider_control_requests": 0,
        "provider_wire_requests": 25,
        "slot": "B-qwen-stateful",
        "success": False,
        "task_id": "v5-bfe5707f6b44202a0e7f493e",
        "trial_id": FROZEN_BCD_TERMINAL_IDENTITY.trial_id,
    }
    summary = {
        "schema_version": "pixelgym-agent-v5-d56-bcd-calibration-result-v1",
        "approved_plan_sha256": d56_c_calibration.FROZEN_BCD_PLAN_SHA256,
        "code_revision": d56_c_calibration.FROZEN_BCD_CODE_REVISION,
        "provider_calls_made": 25,
        "provider_wire_requests": 25,
        "model_attempt_reservations": 25,
        "provider_control_requests": 0,
        "prior_aggregate_spend_usd": "2.032875185",
        "actual_aggregate_spend_usd": "2.040917557",
        "calibration_incremental_spend_usd": "0.008042372",
        "remaining_aggregate_spend_usd": "7.959082443",
        "assigned_policy_task_pairs": 150,
        "attempted_policy_task_pairs": 1,
        "successful_policy_task_pairs": 0,
        "classifications": {"infrastructure_failure": 1},
        "episode_results": [terminal_result],
        "transport_records": [{} for _index in range(24)]
        + [
            {
                "status": "unknown",
                "failure_code": "HTTPError",
                "http_status": 429,
                "provider_error_code": 429,
                "upstream_provider": "Alibaba",
            }
        ],
        "journal_integrity": integrity,
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        d56_c_calibration,
        "FROZEN_BCD_SUMMARY_SHA256",
        d56_c_calibration._file_digest(summary_path),
    )
    monkeypatch.setattr(
        d56_c_calibration,
        "FROZEN_BCD_JOURNAL_SHA256",
        d56_c_calibration._streaming_file_digest(journal_path),
    )
    monkeypatch.setattr(
        d56_c_calibration, "FROZEN_BCD_JOURNAL_INTEGRITY", integrity
    )
    return output


def test_c_plan_binds_only_slot_c_and_latest_remaining_shared_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_plan(
        ROOT,
        smoke_output_directory=fake_smoke_output(tmp_path),
        frozen_calibration_output_directory=fake_frozen_calibration_output(
            tmp_path, monkeypatch
        ),
        frozen_bcd_output_directory=fake_frozen_bcd_output(tmp_path, monkeypatch),
    )

    assert plan["provider_calls_made"] == 0
    assert plan["assigned_policy_task_pairs"] == 50
    assert plan["calibration_partition"]["episode_count"] == 50
    assert plan["calibration_partition"]["action_cap_per_policy"] == 1431
    assert len(plan["task_order"]) == 50
    assert [record["slot"] for record in plan["policies"]] == ["C-llama-stateful"]
    assert plan["policies"][0]["caps"]["model_attempt_cap"] == 2862
    assert plan["aggregate_caps"]["environment_action_cap"] == 1431
    assert plan["aggregate_caps"]["model_attempt_cap"] == 2862
    assert plan["aggregate_caps"]["provider_control_request_cap"] == 0
    assert plan["aggregate_caps"]["provider_wire_request_cap"] == 2862
    assert plan["aggregate_caps"]["maximum_aggregate_spend_usd"] == "10.00"
    assert plan["aggregate_caps"]["prior_aggregate_spend_usd"] == "2.040917557"
    assert plan["aggregate_caps"]["remaining_aggregate_spend_usd"] == "7.959082443"
    assert (
        plan["aggregate_caps"]["uncapped_theoretical_request_maximum_usd"]
        == "39.8573568"
    )
    assert plan["frozen_bcd_predecessor_evidence"]["terminal"] == {
        "classification": "unknown_outcome_infrastructure_failure",
        "failure_code": "provider_request_unknown",
        "http_status": 429,
        "provider_error_code": 429,
        "trial_id": FROZEN_BCD_TERMINAL_IDENTITY.trial_id,
        "step_index": 24,
        "attempt_index": 0,
        "request_outcome": "unknown",
        "retry_eligible": False,
    }
    assert plan_digest(plan).startswith("sha256:")


def test_c_predecessor_rejects_journal_digest_before_opening_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen_output = fake_frozen_bcd_output(tmp_path, monkeypatch)
    monkeypatch.setattr(
        d56_c_calibration,
        "_streaming_file_digest",
        lambda _path: "sha256:changed",
    )

    def fail_if_opened(_path: Path) -> V5AttemptJournal:
        raise AssertionError("unverified journal was opened")

    monkeypatch.setattr(d56_c_calibration, "V5AttemptJournal", fail_if_opened)

    with pytest.raises(ValueError, match="frozen B/C/D journal digest mismatch"):
        d56_c_calibration._validated_frozen_bcd_evidence(frozen_output)


def test_c_execution_rejects_unapproved_digest_before_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke_output = fake_smoke_output(tmp_path)
    frozen_output = fake_frozen_calibration_output(tmp_path, monkeypatch)
    frozen_bcd_output = fake_frozen_bcd_output(tmp_path, monkeypatch)
    plan = build_plan(
        ROOT,
        smoke_output_directory=smoke_output,
        frozen_calibration_output_directory=frozen_output,
        frozen_bcd_output_directory=frozen_bcd_output,
    )
    output = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="approved Slot C calibration plan digest"):
        execute_calibration(
            ROOT,
            plan=plan,
            approved_plan_sha256="sha256:not-approved",
            smoke_output_directory=smoke_output,
            frozen_calibration_output_directory=frozen_output,
            frozen_bcd_output_directory=frozen_bcd_output,
            output_directory=output,
        )

    assert not output.exists()


def test_consumed_native_c_execution_is_locked_after_adapter_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke_output = fake_smoke_output(tmp_path)
    frozen_output = fake_frozen_calibration_output(tmp_path, monkeypatch)
    frozen_bcd_output = fake_frozen_bcd_output(tmp_path, monkeypatch)
    plan = build_plan(
        ROOT,
        smoke_output_directory=smoke_output,
        frozen_calibration_output_directory=frozen_output,
        frozen_bcd_output_directory=frozen_bcd_output,
    )
    output = tmp_path / "must-not-exist"

    with pytest.raises(RuntimeError, match="native-coordinate Slot C calibration is frozen"):
        execute_calibration(
            ROOT,
            plan=plan,
            approved_plan_sha256=plan_digest(plan),
            smoke_output_directory=smoke_output,
            frozen_calibration_output_directory=frozen_output,
            frozen_bcd_output_directory=frozen_bcd_output,
            output_directory=output,
        )

    assert not output.exists()
