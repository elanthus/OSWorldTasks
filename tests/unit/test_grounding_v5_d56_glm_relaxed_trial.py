from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import d56_glm_relaxed_trial
from pixelgym.grounding.v5.contracts import CallCaps
from pixelgym.grounding.v5.d56_bcd_calibration import _streaming_file_digest
from pixelgym.grounding.v5.d56_calibration import _file_digest
from pixelgym.grounding.v5.d56_glm_relaxed_trial import (
    FROZEN_STRICT_GLM_TERMINAL_IDENTITY,
    build_plan,
    execute_trial,
    plan_digest,
)
from pixelgym.grounding.v5.journal import V5AttemptJournal

ROOT = Path(__file__).parents[2]


def fake_frozen_strict_glm_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    output = tmp_path / "frozen-strict-glm"
    output.mkdir()
    journal_path = output / "attempts.sqlite"
    journal = V5AttemptJournal(journal_path)
    try:
        _event, created = journal.reserve_attempt_started(
            FROZEN_STRICT_GLM_TERMINAL_IDENTITY,
            provider_endpoint_identity="openrouter",
            request_digest="sha256:request",
            idempotency_key="strict-glm",
            model_attempt_reservation=1,
            control_request_reservation=0,
            pre_call_checkpoint=b"checkpoint",
            approved_caps=CallCaps(28, 56, 0, 56),
        )
        assert created
        journal.seal_attempt_terminal(
            FROZEN_STRICT_GLM_TERMINAL_IDENTITY,
            kind="unknown_outcome_infrastructure_failure",
            post_attempt_checkpoint=b"checkpoint",
            failure_code="provider_request_unknown",
        )
        integrity = journal.integrity_report()
    finally:
        journal.close()
    summary = {
        "schema_version": "pixelgym-agent-v5-d56-glm-normalized-trial-result-v1",
        "purpose": (
            "one complete development-only GLM candidate episode using the normalized "
            "coordinate adapter; comparative diagnostic evidence, not calibration evidence"
        ),
        "approved_plan_sha256": d56_glm_relaxed_trial.FROZEN_STRICT_GLM_PLAN_SHA256,
        "code_revision": d56_glm_relaxed_trial.FROZEN_STRICT_GLM_CODE_REVISION,
        "provider_calls_made": 1,
        "provider_wire_requests": 1,
        "model_attempt_reservations": 1,
        "provider_control_requests": 0,
        "prior_aggregate_spend_usd": "2.487339457",
        "actual_aggregate_spend_usd": "2.487339457",
        "trial_incremental_spend_usd": "0E-9",
        "remaining_aggregate_spend_usd": "7.512660543",
        "maximum_aggregate_spend_usd": "10.00",
        "assigned_policy_task_pairs": 1,
        "attempted_policy_task_pairs": 1,
        "successful_policy_task_pairs": 0,
        "episode_result": {
            "classification": "infrastructure_failure",
            "environment_actions": 0,
            "final_policy_checkpoint_digest": (
                "sha256:63e9da03831c87ca45e2c7b7bbc730fa262decdf49943ad3b6032b212e7348c1"
            ),
            "model_attempts": 1,
            "provider_control_requests": 0,
            "provider_wire_requests": 1,
            "slot": "C-glm-stateful-candidate",
            "success": False,
            "task_id": "v5-bf8d93604c1ba0da75b32ca8",
            "trial_id": FROZEN_STRICT_GLM_TERMINAL_IDENTITY.trial_id,
        },
        "semantic_progress": {
            "first_transition_completed": False,
            "maximum_stage_index_observed": 0,
            "diagnostic_event_counts": {},
        },
        "execution_error": None,
        "transport_records": [
            {
                "status": "unknown",
                "failure_code": "HTTPError",
                "http_status": 404,
                "provider_error_code": 404,
                "error_body_bytes_read": 201,
                "error_body_prefix_digest": (
                    "sha256:680fd4832f47938dd9f75c25939ba46a92474efe69b643b0bfafbe087c195042"
                ),
                "error_body_truncated": False,
            }
        ],
        "journal_integrity": integrity,
        "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        d56_glm_relaxed_trial,
        "FROZEN_STRICT_GLM_SUMMARY_SHA256",
        _file_digest(summary_path),
    )
    monkeypatch.setattr(
        d56_glm_relaxed_trial,
        "FROZEN_STRICT_GLM_JOURNAL_SHA256",
        _streaming_file_digest(journal_path),
    )
    monkeypatch.setattr(
        d56_glm_relaxed_trial,
        "FROZEN_STRICT_GLM_JOURNAL_INTEGRITY",
        integrity,
    )
    return output


def test_relaxed_glm_plan_binds_frozen_404_and_local_fail_closed_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_plan(
        ROOT,
        frozen_strict_glm_trial_output_directory=fake_frozen_strict_glm_output(
            tmp_path, monkeypatch
        ),
    )

    assert plan["provider_calls_made"] == 0
    assert plan["task"] == {
        "partition": "development",
        "seed": 5010,
        "task_id": "v5-bf8d93604c1ba0da75b32ca8",
        "family": "conditional_precedence",
        "variant": "base",
        "max_episode_steps": 28,
    }
    assert plan["policy"]["slot"] == "C-glm-stateful-relaxed-schema-candidate"
    assert plan["policy"]["policy_manifest"]["model"] == "z-ai/glm-5.3-flash"
    assert plan["policy"]["response_validation"] == {
        "upstream_json_schema_strict": False,
        "local_exact_action_parser": True,
        "invalid_or_unparseable_output_rule": "retain_and_fail_closed_without_retry",
    }
    inference_parameters = dict(plan["policy"]["policy_manifest"]["inference_parameters"])
    assert inference_parameters["response_schema_strict"] == "false"
    assert plan["caps"]["environment_action_cap"] == 28
    assert plan["caps"]["model_attempt_cap"] == 56
    assert plan["caps"]["prior_aggregate_spend_usd"] == "2.487339457"
    assert plan["caps"]["trial_theoretical_maximum_usd"] == "0.590643200"
    assert plan["caps"]["aggregate_theoretical_upper_bound_usd"] == "3.077982657"
    assert plan["frozen_strict_glm_trial_evidence"]["terminal"] == {
        "classification": "unknown_outcome_infrastructure_failure",
        "failure_code": "provider_request_unknown",
        "http_status": 404,
        "provider_error_code": 404,
        "trial_id": FROZEN_STRICT_GLM_TERMINAL_IDENTITY.trial_id,
        "step_index": 0,
        "attempt_index": 0,
        "request_outcome": "unknown",
        "retry_eligible": False,
    }
    assert plan_digest(plan).startswith("sha256:")


def test_relaxed_glm_trial_rejects_unapproved_digest_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen_output = fake_frozen_strict_glm_output(tmp_path, monkeypatch)
    plan = build_plan(ROOT, frozen_strict_glm_trial_output_directory=frozen_output)
    output = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="approved relaxed-schema GLM trial digest"):
        execute_trial(
            ROOT,
            plan=plan,
            approved_plan_sha256="sha256:not-approved",
            frozen_strict_glm_trial_output_directory=frozen_output,
            output_directory=output,
        )

    assert not output.exists()


def test_relaxed_glm_plan_rejects_tampered_strict_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen_output = fake_frozen_strict_glm_output(tmp_path, monkeypatch)
    summary_path = frozen_output / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["provider_calls_made"] = 2
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="summary digest mismatch"):
        build_plan(ROOT, frozen_strict_glm_trial_output_directory=frozen_output)
