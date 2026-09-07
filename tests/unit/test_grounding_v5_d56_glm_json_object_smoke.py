from __future__ import annotations

import json
from pathlib import Path

import pytest

from legacy.grounding.scripts import run_grounding_v5_d56_glm_json_object_smoke
from legacy.grounding.v5 import d56_glm_json_object_smoke as smoke
from legacy.grounding.v5.d56_bcd_calibration import _streaming_file_digest
from legacy.grounding.v5.d56_calibration import _file_digest
from pixelgym.grounding.v5.contracts import CallCaps
from pixelgym.grounding.v5.journal import V5AttemptJournal

ROOT = Path(__file__).parents[2]


def fake_frozen_relaxed_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    output = tmp_path / "frozen-relaxed-glm"
    output.mkdir()
    journal_path = output / "attempts.sqlite"
    journal = V5AttemptJournal(journal_path)
    try:
        _event, created = journal.reserve_attempt_started(
            smoke.FROZEN_RELAXED_GLM_TERMINAL_IDENTITY,
            provider_endpoint_identity="openrouter",
            request_digest="sha256:request",
            idempotency_key="relaxed-glm",
            model_attempt_reservation=1,
            control_request_reservation=0,
            pre_call_checkpoint=b"checkpoint",
            approved_caps=CallCaps(28, 56, 0, 56),
        )
        assert created
        journal.seal_attempt_terminal(
            smoke.FROZEN_RELAXED_GLM_TERMINAL_IDENTITY,
            kind="unknown_outcome_infrastructure_failure",
            post_attempt_checkpoint=b"checkpoint",
            failure_code="provider_request_unknown",
        )
        integrity = journal.integrity_report()
    finally:
        journal.close()
    summary = {
        "schema_version": "pixelgym-agent-v5-d56-glm-relaxed-trial-result-v1",
        "approved_plan_sha256": smoke.FROZEN_RELAXED_GLM_PLAN_SHA256,
        "code_revision": smoke.FROZEN_RELAXED_GLM_CODE_REVISION,
        "provider_calls_made": 1,
        "provider_wire_requests": 1,
        "model_attempt_reservations": 1,
        "provider_control_requests": 0,
        "actual_aggregate_spend_usd": "2.487339457",
        "trial_incremental_spend_usd": "0E-9",
        "remaining_aggregate_spend_usd": "7.512660543",
        "successful_policy_task_pairs": 0,
        "maximum_aggregate_spend_usd": "10.00",
        "journal_integrity": integrity,
        "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
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
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(smoke, "FROZEN_RELAXED_GLM_SUMMARY_SHA256", _file_digest(summary_path))
    monkeypatch.setattr(
        smoke, "FROZEN_RELAXED_GLM_JOURNAL_SHA256", _streaming_file_digest(journal_path)
    )
    monkeypatch.setattr(smoke, "FROZEN_RELAXED_GLM_JOURNAL_INTEGRITY", integrity)
    return output


def test_plan_is_one_call_exact_novita_fp8_json_object_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen_output = fake_frozen_relaxed_output(tmp_path, monkeypatch)
    monkeypatch.setattr(smoke, "_git", lambda *_args: "revision-1")

    plan = smoke.build_plan(ROOT, frozen_relaxed_glm_trial_output_directory=frozen_output)

    assert plan["provider_calls_made"] == 0
    assert plan["task"]["partition"] == "development"
    assert plan["task"]["action_limit"] == 1
    assert plan["policy"]["provider"]["only"] == ["novita/fp8"]
    assert plan["policy"]["provider"]["quantizations"] == ["fp8"]
    assert plan["policy"]["provider"]["allow_fallbacks"] is False
    assert plan["policy"]["provider"]["router_metadata"] == "enabled"
    assert plan["policy"]["response_validation"] == {
        "upstream_response_format": "json_object",
        "upstream_json_schema": False,
        "local_exact_action_parser": True,
        "invalid_or_unparseable_output_rule": "retain_and_fail_closed_without_retry",
    }
    assert plan["caps"]["environment_action_cap"] == 1
    assert plan["caps"]["provider_wire_request_cap"] == 1
    assert plan["caps"]["prior_campaign_spend"]["known_spend_usd"] == "2.487339457"
    assert plan["caps"]["maximum_run_spend_usd"] == "10.00"
    assert plan["caps"]["remaining_run_spend_usd"] == "10.00"
    assert plan["caps"]["per_request_theoretical_maximum_usd"] == "0.010547200"
    assert plan["caps"]["run_theoretical_upper_bound_usd"] == "0.010547200"
    assert smoke.plan_digest(plan).startswith("sha256:")


def test_execute_rejects_unapproved_digest_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen_output = fake_frozen_relaxed_output(tmp_path, monkeypatch)
    monkeypatch.setattr(smoke, "_git", lambda *_args: "revision-1")
    plan = smoke.build_plan(ROOT, frozen_relaxed_glm_trial_output_directory=frozen_output)
    output = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="approved GLM JSON-object smoke digest"):
        smoke.execute_smoke(
            ROOT,
            plan=plan,
            approved_plan_sha256="sha256:not-approved",
            frozen_relaxed_glm_trial_output_directory=frozen_output,
            output_directory=output,
        )

    assert not output.exists()


def test_command_refuses_existing_output_before_loading_plan(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()

    with pytest.raises(FileExistsError, match="refusing to replace"):
        run_grounding_v5_d56_glm_json_object_smoke.main(
            [
                "--execute",
                "--plan",
                "missing-plan.json",
                "--approved-plan-sha256",
                "sha256:" + "0" * 64,
                "--frozen-relaxed-glm-trial-output",
                "missing-frozen-output",
                "--output",
                str(output),
            ]
        )
