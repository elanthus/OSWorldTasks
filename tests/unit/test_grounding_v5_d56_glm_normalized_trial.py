from __future__ import annotations

import json
from pathlib import Path

import pytest

from legacy.grounding.v5 import d56_glm_normalized_trial
from legacy.grounding.v5.d56_bcd_calibration import _streaming_file_digest
from legacy.grounding.v5.d56_calibration import _file_digest
from legacy.grounding.v5.d56_glm_normalized_trial import (
    FROZEN_LLAMA_TRIAL_TERMINAL_IDENTITY,
    build_plan,
    execute_trial,
    plan_digest,
)
from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps
from pixelgym.grounding.v5.journal import V5AttemptJournal

ROOT = Path(__file__).parents[2]


def fake_frozen_llama_trial_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    output = tmp_path / "frozen-llama-trial"
    output.mkdir()
    journal_path = output / "attempts.sqlite"
    journal = V5AttemptJournal(journal_path)
    caps = CallCaps(20, 20, 0, 20)
    try:
        for index in range(19):
            _event, created = journal.reserve_attempt_started(
                AttemptIdentity(
                    FROZEN_LLAMA_TRIAL_TERMINAL_IDENTITY.trial_id,
                    index,
                    0,
                ),
                provider_endpoint_identity="openrouter",
                request_digest=f"sha256:request-{index}",
                idempotency_key=f"llama-{index}",
                model_attempt_reservation=1,
                control_request_reservation=0,
                pre_call_checkpoint=b"checkpoint",
                approved_caps=caps,
            )
            assert created
        _event, created = journal.reserve_attempt_started(
            FROZEN_LLAMA_TRIAL_TERMINAL_IDENTITY,
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
            FROZEN_LLAMA_TRIAL_TERMINAL_IDENTITY,
            kind="unknown_outcome_infrastructure_failure",
            post_attempt_checkpoint=b"checkpoint",
            failure_code="provider_request_unknown",
        )
        integrity = journal.integrity_report()
    finally:
        journal.close()
    terminal_result = {
        "classification": "infrastructure_failure",
        "environment_actions": 19,
        "final_policy_checkpoint_digest": (
            "sha256:d16e535fe2ad504b0227fb97128a33ff533765806de449ed5626762d37ed9fb6"
        ),
        "model_attempts": 20,
        "provider_control_requests": 0,
        "provider_wire_requests": 20,
        "slot": "C-llama-stateful",
        "success": False,
        "task_id": "v5-bf8d93604c1ba0da75b32ca8",
        "trial_id": FROZEN_LLAMA_TRIAL_TERMINAL_IDENTITY.trial_id,
    }
    summary = {
        "schema_version": "pixelgym-agent-v5-d56-c-normalized-trial-result-v1",
        "purpose": (
            "one complete development-only Slot C episode to test the normalized coordinate "
            "adapter; not calibration evidence"
        ),
        "approved_plan_sha256": d56_glm_normalized_trial.FROZEN_LLAMA_TRIAL_PLAN_SHA256,
        "code_revision": d56_glm_normalized_trial.FROZEN_LLAMA_TRIAL_CODE_REVISION,
        "provider_calls_made": 20,
        "provider_wire_requests": 20,
        "model_attempt_reservations": 20,
        "provider_control_requests": 0,
        "prior_aggregate_spend_usd": "2.481019457",
        "actual_aggregate_spend_usd": "2.487339457",
        "trial_incremental_spend_usd": "0.006320000",
        "remaining_aggregate_spend_usd": "7.512660543",
        "maximum_aggregate_spend_usd": "10.00",
        "assigned_policy_task_pairs": 1,
        "attempted_policy_task_pairs": 1,
        "successful_policy_task_pairs": 0,
        "episode_result": terminal_result,
        "semantic_progress": {
            "first_transition_completed": True,
            "maximum_stage_index_observed": 1,
            "diagnostic_event_counts": {
                "correct_transition": 1,
                "text_input_focused": 18,
            },
        },
        "execution_error": None,
        "transport_records": [
            {
                "status": "response",
                "upstream_provider": "DeepInfra",
                "response_model": "meta-llama/llama-4-scout",
            }
            for _index in range(19)
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
        "frozen_native_c_evidence": {
            "actual_aggregate_spend_usd": "2.481019457",
            "summary_sha256": (
                "sha256:73cfd90ae6594a4d826520be3aa971ba049554c5d69196111edb672a78f0da0f"
            ),
            "journal_sha256": (
                "sha256:d67a8c9197e3d2d54f62fd28002e7c9e054c0e5cdb3872b1042ff76537272cf6"
            ),
        },
        "journal_integrity": integrity,
        "publication_status": "restricted_raw_responses_in_local_journal",
        "cleanup": {
            "journal_closed": True,
            "policy_and_environments_closed": True,
        },
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        d56_glm_normalized_trial,
        "FROZEN_LLAMA_TRIAL_SUMMARY_SHA256",
        _file_digest(summary_path),
    )
    monkeypatch.setattr(
        d56_glm_normalized_trial,
        "FROZEN_LLAMA_TRIAL_JOURNAL_SHA256",
        _streaming_file_digest(journal_path),
    )
    monkeypatch.setattr(
        d56_glm_normalized_trial,
        "FROZEN_LLAMA_TRIAL_JOURNAL_INTEGRITY",
        integrity,
    )
    return output


def test_glm_trial_plan_binds_candidate_route_task_and_latest_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_plan(
        ROOT,
        frozen_llama_trial_output_directory=fake_frozen_llama_trial_output(tmp_path, monkeypatch),
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
    assert plan["policy"]["slot"] == "C-glm-stateful-candidate"
    assert manifest["model"] == "z-ai/glm-5.3-flash"
    assert manifest["coordinate_adapter"] == "normalized-1000x1000"
    assert manifest["memory_policy_version"] == ("pixelgym-agent-v5-visible-action-history-v1")
    assert plan["policy"]["provider"] == {
        "name": "openrouter",
        "upstream_provider": "Novita",
        "only": ["novita"],
        "quantizations": ["fp8"],
        "allow_fallbacks": False,
        "automatic_retries": False,
        "data_collection": "deny",
        "require_parameters": True,
    }
    assert plan["policy"]["model_capabilities"]["input_modalities"] == [
        "text",
        "image",
        "video",
    ]
    assert plan["caps"]["environment_action_cap"] == 28
    assert plan["caps"]["model_attempt_cap"] == 56
    assert plan["caps"]["provider_control_request_cap"] == 0
    assert plan["caps"]["provider_wire_request_cap"] == 56
    assert plan["caps"]["prior_campaign_spend"]["known_spend_usd"] == "2.487339457"
    assert plan["caps"]["remaining_run_spend_usd"] == "10.00"
    assert plan["caps"]["trial_theoretical_maximum_usd"] == "0.590643200"
    assert plan["caps"]["run_theoretical_upper_bound_usd"] == "0.590643200"
    assert plan["frozen_normalized_llama_trial_evidence"]["terminal"] == {
        "classification": "unknown_outcome_infrastructure_failure",
        "failure_code": "provider_request_unknown",
        "http_status": 429,
        "provider_error_code": 429,
        "trial_id": FROZEN_LLAMA_TRIAL_TERMINAL_IDENTITY.trial_id,
        "step_index": 19,
        "attempt_index": 0,
        "request_outcome": "unknown",
        "retry_eligible": False,
    }
    assert plan_digest(plan).startswith("sha256:")


def test_glm_trial_rejects_unapproved_digest_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen_output = fake_frozen_llama_trial_output(tmp_path, monkeypatch)
    plan = build_plan(
        ROOT,
        frozen_llama_trial_output_directory=frozen_output,
    )
    output = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="approved GLM candidate trial digest"):
        execute_trial(
            ROOT,
            plan=plan,
            approved_plan_sha256="sha256:not-approved",
            frozen_llama_trial_output_directory=frozen_output,
            output_directory=output,
        )

    assert not output.exists()


def test_consumed_strict_glm_trial_executor_is_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {"historical": "consumed strict GLM plan"}
    output = tmp_path / "must-not-exist"

    def must_not_rebuild(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("frozen executor must not rebuild a historical plan")

    monkeypatch.setattr(d56_glm_normalized_trial, "build_plan", must_not_rebuild)

    with pytest.raises(ValueError, match="strict-schema GLM trial is frozen"):
        execute_trial(
            ROOT,
            plan=plan,
            approved_plan_sha256=plan_digest(plan),
            frozen_llama_trial_output_directory=tmp_path / "not-read",
            output_directory=output,
        )

    assert not output.exists()
