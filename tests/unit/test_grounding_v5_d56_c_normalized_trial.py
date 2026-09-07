from __future__ import annotations

import json
from pathlib import Path

import pytest

from legacy.grounding.v5 import d56_c_normalized_trial
from legacy.grounding.v5.d56_bcd_calibration import _streaming_file_digest
from legacy.grounding.v5.d56_c_normalized_trial import (
    FROZEN_C_TERMINAL_IDENTITY,
    build_plan,
    execute_trial,
    plan_digest,
)
from legacy.grounding.v5.d56_calibration import _file_digest
from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps
from pixelgym.grounding.v5.journal import V5AttemptJournal

ROOT = Path(__file__).parents[2]


def fake_frozen_c_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    output = tmp_path / "frozen-c"
    output.mkdir()
    journal_path = output / "attempts.sqlite"
    journal = V5AttemptJournal(journal_path)
    caps = CallCaps(1180, 1180, 0, 1180)
    try:
        for index in range(1179):
            _event, created = journal.reserve_attempt_started(
                AttemptIdentity(f"c-{index}", 0, 0),
                provider_endpoint_identity="openrouter",
                request_digest=f"sha256:request-{index}",
                idempotency_key=f"c-{index}",
                model_attempt_reservation=1,
                control_request_reservation=0,
                pre_call_checkpoint=b"checkpoint",
                approved_caps=caps,
            )
            assert created
        _event, created = journal.reserve_attempt_started(
            FROZEN_C_TERMINAL_IDENTITY,
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
            FROZEN_C_TERMINAL_IDENTITY,
            kind="unknown_outcome_infrastructure_failure",
            post_attempt_checkpoint=b"checkpoint",
            failure_code="provider_request_unknown",
        )
        integrity = journal.integrity_report()
    finally:
        journal.close()
    terminal_result = {
        "classification": "infrastructure_failure",
        "environment_actions": 6,
        "final_policy_checkpoint_digest": (
            "sha256:8a2f1fdd61d5e37c44b4c7141c4063e1bfbb9cf1f1d2bfa1018d9c8e7640eef6"
        ),
        "model_attempts": 7,
        "provider_control_requests": 0,
        "provider_wire_requests": 7,
        "slot": "C-llama-stateful",
        "success": False,
        "task_id": "v5-c1e3ad39ecbaa0beb626a8ec",
        "trial_id": FROZEN_C_TERMINAL_IDENTITY.trial_id,
    }
    summary = {
        "schema_version": "pixelgym-agent-v5-d56-c-calibration-result-v1",
        "approved_plan_sha256": d56_c_normalized_trial.FROZEN_C_PLAN_SHA256,
        "code_revision": d56_c_normalized_trial.FROZEN_C_CODE_REVISION,
        "provider_calls_made": 1180,
        "provider_wire_requests": 1180,
        "model_attempt_reservations": 1180,
        "provider_control_requests": 0,
        "prior_aggregate_spend_usd": "2.040917557",
        "actual_aggregate_spend_usd": "2.481019457",
        "calibration_incremental_spend_usd": "0.440101900",
        "remaining_aggregate_spend_usd": "7.518980543",
        "assigned_policy_task_pairs": 50,
        "attempted_policy_task_pairs": 42,
        "successful_policy_task_pairs": 0,
        "classifications": {
            "infrastructure_failure": 1,
            "step_limit_truncation": 41,
        },
        "episode_results": [
            {"slot": "C-llama-stateful", "classification": "step_limit_truncation"}
            for _index in range(41)
        ]
        + [terminal_result],
        "transport_records": [
            {"status": "response", "upstream_provider": "DeepInfra"} for _index in range(1179)
        ]
        + [
            {
                "status": "unknown",
                "failure_code": "HTTPError",
                "http_status": 429,
                "provider_error_code": 429,
                "upstream_provider": "DeepInfra",
            }
        ],
        "journal_integrity": integrity,
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        d56_c_normalized_trial,
        "FROZEN_C_SUMMARY_SHA256",
        _file_digest(summary_path),
    )
    monkeypatch.setattr(
        d56_c_normalized_trial,
        "FROZEN_C_JOURNAL_SHA256",
        _streaming_file_digest(journal_path),
    )
    monkeypatch.setattr(d56_c_normalized_trial, "FROZEN_C_JOURNAL_INTEGRITY", integrity)
    return output


def test_normalized_trial_plan_binds_one_development_episode_and_latest_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_plan(
        ROOT,
        frozen_c_output_directory=fake_frozen_c_output(tmp_path, monkeypatch),
    )

    assert plan["provider_calls_made"] == 0
    assert plan["assigned_policy_task_pairs"] == 1
    assert plan["task"] == {
        "partition": "development",
        "seed": 5010,
        "task_id": "v5-bf8d93604c1ba0da75b32ca8",
        "family": "conditional_precedence",
        "variant": "base",
        "max_episode_steps": 28,
    }
    manifest = plan["policy"]["policy_manifest"]
    assert manifest["model"] == "meta-llama/llama-4-scout"
    assert manifest["coordinate_adapter"] == "normalized-1000x1000"
    assert manifest["coordinate_input_convention"] == ("integer-normalized-square/0..999-inclusive")
    assert plan["policy"]["provider"]["only"] == ["deepinfra"]
    assert plan["policy"]["provider"]["quantizations"] == ["fp8"]
    assert plan["caps"]["environment_action_cap"] == 28
    assert plan["caps"]["model_attempt_cap"] == 56
    assert plan["caps"]["provider_control_request_cap"] == 0
    assert plan["caps"]["provider_wire_request_cap"] == 56
    assert plan["caps"]["prior_campaign_spend"]["known_spend_usd"] == "2.481019457"
    assert plan["caps"]["remaining_run_spend_usd"] == "10.00"
    assert plan["caps"]["trial_theoretical_maximum_usd"] == "0.7798784"
    assert plan["caps"]["run_theoretical_upper_bound_usd"] == "0.7798784"
    assert plan["frozen_native_c_evidence"]["terminal"] == {
        "classification": "unknown_outcome_infrastructure_failure",
        "failure_code": "provider_request_unknown",
        "http_status": 429,
        "provider_error_code": 429,
        "trial_id": FROZEN_C_TERMINAL_IDENTITY.trial_id,
        "step_index": 6,
        "attempt_index": 0,
        "request_outcome": "unknown",
        "retry_eligible": False,
    }
    assert plan_digest(plan).startswith("sha256:")


def test_normalized_trial_rejects_unapproved_digest_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen_output = fake_frozen_c_output(tmp_path, monkeypatch)
    plan = build_plan(ROOT, frozen_c_output_directory=frozen_output)
    output = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="approved normalized Slot C trial digest"):
        execute_trial(
            ROOT,
            plan=plan,
            approved_plan_sha256="sha256:not-approved",
            frozen_c_output_directory=frozen_output,
            output_directory=output,
        )

    assert not output.exists()


def test_consumed_normalized_llama_trial_execution_is_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen_output = fake_frozen_c_output(tmp_path, monkeypatch)
    plan = build_plan(ROOT, frozen_c_output_directory=frozen_output)
    output = tmp_path / "must-not-exist"

    with pytest.raises(RuntimeError, match="normalized Llama trial is frozen"):
        execute_trial(
            ROOT,
            plan=plan,
            approved_plan_sha256=plan_digest(plan),
            frozen_c_output_directory=frozen_output,
            output_directory=output,
        )

    assert not output.exists()
