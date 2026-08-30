from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5 import codex_cli_policy as policy
from pixelgym.grounding.v5 import d56_codex_cli_calibration as calibration
from scripts import run_grounding_v5_d56_codex_cli_calibration

ROOT = Path(__file__).parents[2]


def runtime_identity() -> policy.CodexRuntimeIdentity:
    return policy.CodexRuntimeIdentity(
        cli_version=policy.CODEX_CLI_VERSION,
        authentication_mode=policy.AUTH_MODE,
        model=policy.MODEL,
        model_catalog_comp_hash=policy.MODEL_CATALOG_COMP_HASH,
        supported_reasoning_efforts=("low", "medium", "high"),
        input_modalities=("text", "image"),
        context_window_tokens=policy.MODEL_CONTEXT_WINDOW_TOKENS,
        exec_help_sha256="sha256:help",
        feature_inventory_sha256="sha256:features",
        configuration_preflight_validated=True,
    )


def predecessor_evidence() -> dict[str, Any]:
    return {
        "status": "consumed_immutable_incomplete_negative_evidence",
        "approved_plan_content_sha256": calibration.PREDECESSOR_PLAN_CONTENT_SHA256,
        "budget_accounted_aggregate_spend_usd": "4.778164718",
        "maximum_remaining_incremental_exposure_usd": "5.221835282",
        "attempted_tasks": 13,
        "successful_tasks": 0,
        "reuse_rule": "do_not_resume_overwrite_reuse_or_reinterpret",
    }


def failed_luna_smoke_evidence() -> dict[str, Any]:
    return {
        "status": "consumed_immutable_infrastructure_failure",
        "approved_plan_content_sha256": calibration.FAILED_LUNA_SMOKE_PLAN_CONTENT_SHA256,
        "classification": "infrastructure_failure",
        "provider_calls_made": 1,
        "environment_actions": 0,
        "root_cause": "strict_config_rejected_unknown_tools_view_image_key_before_inference",
        "reuse_rule": "do_not_resume_overwrite_or_reuse_plan_or_run_directory",
    }


def retry_predecessor_evidence() -> dict[str, Any]:
    return {
        "status": "consumed_immutable_invalid_output",
        "approved_plan_content_sha256": calibration.RETRY_PREDECESSOR_PLAN_CONTENT_SHA256,
        "classification": "invalid_output",
        "provider_calls_made": 1,
        "environment_actions": 0,
        "policy_violation": "unauthorized_item:error",
        "experiment_charge_usd": "0.00",
        "reuse_rule": "do_not_resume_overwrite_or_reuse_plan_or_run_directory",
    }


def third_smoke_evidence() -> dict[str, Any]:
    return {
        "status": "consumed_immutable_invalid_output",
        "approved_plan_content_sha256": calibration.THIRD_SMOKE_PLAN_CONTENT_SHA256,
        "classification": "invalid_output",
        "provider_calls_made": 1,
        "environment_actions": 0,
        "policy_violation": "unauthorized_item:error",
        "schema_valid_action_present_in_restricted_raw_stream": True,
        "error_message_sha256": policy.ALLOWED_DISABLED_CODE_MODE_DIAGNOSTIC_SHA256,
        "experiment_charge_usd": "0.00",
        "historical_result_reinterpreted": False,
        "reuse_rule": "do_not_resume_overwrite_or_reuse_plan_or_run_directory",
    }


def stub_plan_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        calibration,
        "validated_predecessor_evidence",
        lambda _root: predecessor_evidence(),
    )
    monkeypatch.setattr(
        calibration,
        "validated_failed_luna_smoke_evidence",
        lambda _root: failed_luna_smoke_evidence(),
    )
    monkeypatch.setattr(
        calibration,
        "validated_retry_predecessor_evidence",
        lambda _root: retry_predecessor_evidence(),
    )
    monkeypatch.setattr(
        calibration,
        "validated_third_smoke_evidence",
        lambda _root: third_smoke_evidence(),
    )

    def git(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "revision-codex-cli"
        if args == ("status", "--porcelain", "--untracked-files=no"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(calibration, "_git", git)


def successful_smoke() -> dict[str, Any]:
    return {
        "approved_smoke_plan_sha256": "sha256:smoke",
        "summary_file_sha256": "sha256:summary",
        "attempt_journal_file_sha256": "sha256:attempts",
        "invocation_journal_file_sha256": "sha256:invocations",
        "budget_accounted_aggregate_spend_usd": "4.778164718",
        "incremental_luna_experiment_charge_usd": "0.00",
        "usage_telemetry_status": "available",
        "task_id": "v5-64ba7d452b3c8d3e43d1d30e",
        "classification": "pilot_action_limit",
        "provider_calls_made": 1,
        "environment_actions": 1,
        "model": policy.MODEL,
        "model_reasoning_effort": policy.MODEL_REASONING_EFFORT,
        "cli_version": policy.CODEX_CLI_VERSION,
        "policy_violation": "none",
    }


class SuccessfulProcess:
    pid = 910_001
    returncode: int | None = 0

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        del input, timeout
        action = json.dumps({"action_type": 1, "x": 100, "y": 100, "key": 0})
        events = [
            {"type": "thread.started", "thread_id": "restricted-private-id"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "message", "type": "agent_message", "text": action},
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 500,
                    "output_tokens": 100,
                    "reasoning_output_tokens": 20,
                },
            },
        ]
        return "\n".join(json.dumps(event) for event in events) + "\n", ""

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def test_real_predecessor_evidence_is_bound_and_frozen() -> None:
    evidence = calibration.validated_predecessor_evidence(ROOT)

    assert evidence["approved_plan_content_sha256"] == (
        "sha256:fc1f41d00df8c847d55765ea6af6b4c688463a893281f995f3eee3b770c43f7c"
    )
    assert evidence["restricted_journal_file_sha256"] == (
        "sha256:81e9137cb5c5074c3a0fd02f8f20ef7c2368ede9dbf95f1a92a1805a61818671"
    )
    assert evidence["attempted_tasks"] == 13
    assert evidence["successful_tasks"] == 0
    assert evidence["budget_accounted_aggregate_spend_usd"] == "4.778164718"
    assert evidence["reuse_rule"] == "do_not_resume_overwrite_reuse_or_reinterpret"


def test_failed_luna_smoke_is_bound_and_preserved_as_immutable_evidence() -> None:
    evidence = calibration.validated_failed_luna_smoke_evidence(ROOT)

    assert evidence["approved_plan_content_sha256"] == (
        "sha256:1bea6849d3dd619ab481ee21b957975fedac9302119806aaadc90b21ce6be433"
    )
    assert evidence["summary_file_sha256"] == (
        "sha256:6bff9bfb09f36556133dc1e33ff2c69ce846ad3856b7af89edc2ef5e5f045ebb"
    )
    assert evidence["provider_calls_made"] == 1
    assert evidence["environment_actions"] == 0
    assert evidence["classification"] == "infrastructure_failure"
    assert evidence["root_cause"] == (
        "strict_config_rejected_unknown_tools_view_image_key_before_inference"
    )
    assert evidence["historical_result_accounting"] == {
        "accounting_method": "superseded_conservative_list_price_reservation_v1",
        "incremental_cost_equivalent_usd": "0.97920000",
        "budget_accounted_aggregate_spend_usd": "5.757364718",
        "unresolved_reservation_count": 1,
    }


def test_retry_predecessor_is_bound_without_publishing_error_content() -> None:
    evidence = calibration.validated_retry_predecessor_evidence(ROOT)

    assert evidence["approved_plan_content_sha256"] == (
        "sha256:ec88a91aa1573d9b33aeb840c9deb56147c94c47456169546a9c7c834a6bfc7b"
    )
    assert evidence["summary_file_sha256"] == (
        "sha256:fd7c3c8ab525b1a5e924e6aa7fa1fddd77e203fb805a82d3992515766ecee401"
    )
    assert evidence["error_message_sha256"] == (
        "sha256:098e801ebc95c9c7312a945849442846324dcf639365a297313248993822711b"
    )
    assert evidence["event_sequence"] == [
        "thread.started",
        "item.completed:error",
        "turn.started",
        "item.completed:agent_message",
        "turn.completed",
    ]
    assert evidence["classification"] == "invalid_output"
    assert evidence["policy_violation"] == "unauthorized_item:error"
    assert "message" not in evidence


def test_third_smoke_is_bound_without_reinterpreting_historical_result() -> None:
    evidence = calibration.validated_third_smoke_evidence(ROOT)

    assert evidence["approved_plan_content_sha256"] == (
        "sha256:822306dac3987dad1fa744cf773b2f543b4e2624208beeb0ac7491106d87acc4"
    )
    assert evidence["summary_file_sha256"] == (
        "sha256:4973c4ab6ab28842aa442ca0afdbdb3f588f0b40455461e5e9a73a82bbe33c04"
    )
    assert evidence["error_message_sha256"] == (
        "sha256:098e801ebc95c9c7312a945849442846324dcf639365a297313248993822711b"
    )
    assert evidence["classification"] == "invalid_output"
    assert evidence["schema_valid_action_present_in_restricted_raw_stream"] is True
    assert evidence["historical_result_reinterpreted"] is False
    assert "message" not in evidence


def test_smoke_plan_binds_exact_cli_isolation_cost_and_one_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_plan_inputs(monkeypatch)

    plan = calibration.build_smoke_plan(ROOT, runtime_identity=runtime_identity())

    assert plan["provider_calls_made_while_planning"] == 0
    assert plan["execution_state"] == "awaiting_exact_human_approval"
    assert plan["policy"]["evaluated_provider"] == "Codex CLI"
    assert plan["policy"]["model"] == "gpt-5.6-luna"
    assert plan["policy"]["model_reasoning_effort"] == "low"
    assert plan["policy"]["orchestrator_model_excluded"] == "gpt-5.6-sol"
    assert plan["policy"]["isolation"] == {
        "working_directory": "isolated_empty_temporary_directory",
        "session_persistence": "ephemeral",
        "sandbox": "read-only",
        "shell_tool": "disabled",
        "web_search": "disabled",
        "user_config": "ignored_except_authentication",
        "user_and_project_rules": "ignored",
        "plugins": "disabled",
        "mcp": "unconfigured_and_unavailable",
        "skills": "disabled",
        "connectors": "disabled",
        "filesystem_and_repository_tools": "disabled",
        "environment_inheritance_for_tools": "none",
    }
    assert plan["task"] == {
        "partition": "development",
        "seed": 5002,
        "task_id": "v5-64ba7d452b3c8d3e43d1d30e",
        "family": "evidence_aggregation",
        "variant": "base",
        "action_limit": 1,
    }
    assert plan["caps"] == {
        "assigned_task_cap": 1,
        "environment_action_cap": 1,
        "codex_process_invocation_cap": 1,
        "model_completed_turn_cap": 1,
        "model_attempt_cap": 1,
        "provider_control_request_cap": 0,
        "provider_wire_request_cap": 1,
        "provider_wire_request_cap_semantics": (
            "one logical adapter transport send; packet-level ChatGPT authentication "
            "exchanges are not observable"
        ),
        "prior_non_luna_budget_accounted_spend_usd": "4.778164718",
        "maximum_remaining_non_luna_incremental_exposure_usd": "5.221835282",
        "luna_experiment_charge_per_call_usd": "0.00",
        "maximum_luna_incremental_experiment_charge_usd": "0.00",
        "per_invocation_maximum_informational_list_price_equivalent_usd": "0.97920000",
        "aggregate_theoretical_upper_bound_usd": "4.778164718",
        "maximum_aggregate_spend_usd": "10.00",
    }
    assert plan["price_and_cost_accounting"]["billing_classification"] == (
        "ChatGPT_subscription_billed"
    )
    assert plan["price_and_cost_accounting"]["luna_experiment_charge_per_call_usd"] == "0.00"
    assert plan["failed_luna_smoke_evidence"] == failed_luna_smoke_evidence()
    assert plan["retry_predecessor_evidence"] == retry_predecessor_evidence()
    assert plan["third_smoke_evidence"] == third_smoke_evidence()
    assert plan["policy"]["jsonl_diagnostic_policy"] == {
        "allowed_item_type": "error",
        "allowed_message_sha256": policy.ALLOWED_DISABLED_CODE_MODE_DIAGNOSTIC_SHA256,
        "allowed_occurrence_cap": 1,
        "required_event_type": "item.completed",
        "all_other_error_items": "fail_closed_policy_violation",
        "historical_smoke_results_reinterpreted": False,
    }
    assert plan["retry_context"] == {
        "human_requested_fresh_attempt": True,
        "transport_retry": False,
        "luna_smoke_process_ordinal": 4,
        "fresh_process_invocation_cap": 1,
        "parser_policy_changed": True,
        "parser_change": (
            "accept exactly one item.completed:error only when its restricted message "
            "matches the bound disabled-code-mode diagnostic SHA-256"
        ),
        "all_unmatched_error_items_remain_fail_closed": True,
        "historical_smoke_results_reinterpreted": False,
    }
    assert plan["human_accounting_override"] == {
        "approved_scope": "gpt-5.6-luna_calls_in_this_experiment",
        "effective_experiment_charge_usd": "0.00",
        "rationale": "subscription_billed",
        "historical_raw_result_rewritten": False,
        "superseded_failed_smoke_reservation_excluded_from_current_aggregate": True,
        "service_subscription_limits_unchanged": True,
    }
    assert "HTTP 401" in plan["policy"]["retry_and_backoff"]["codex_401_auth_recovery"]
    assert plan["approval_required"]["earlier_qwen_or_openrouter_approvals_apply"] is False
    assert plan["approval_required"]["earlier_luna_smoke_approval_applies"] is False
    assert plan["approval_required"]["v2_luna_smoke_approval_applies"] is False
    assert plan["approval_required"]["v3_luna_smoke_approval_applies"] is False
    assert calibration.plan_digest(plan).startswith("sha256:")


def test_smoke_plan_rebuilds_to_identical_bytes_and_contains_no_private_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_plan_inputs(monkeypatch)

    first = calibration.build_smoke_plan(ROOT, runtime_identity=runtime_identity())
    second = calibration.build_smoke_plan(ROOT, runtime_identity=runtime_identity())
    first_bytes = calibration.plan_file_bytes(first)
    second_bytes = calibration.plan_file_bytes(second)

    assert first_bytes == second_bytes
    assert calibration.plan_digest(first) == calibration.plan_digest(second)
    text = first_bytes.decode("utf-8")
    assert str(ROOT) not in text
    assert "private-thread-id" not in text
    assert "raw_stdout" not in text
    assert '"raw_response":' not in text
    assert "image_png_base64" not in text
    assert "screenshot_path" not in text
    assert "attempts.sqlite" not in text
    assert re.search(r"\bsk-[A-Za-z0-9_-]{12,}\b", text) is None


def test_successor_generator_binds_frozen_fifty_task_order_and_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_plan_inputs(monkeypatch)

    plan = calibration.build_successor_calibration_plan(
        ROOT,
        successful_smoke_evidence=successful_smoke(),
        runtime_identity=runtime_identity(),
    )

    assert plan["execution_state"] == "awaiting_separate_exact_human_approval"
    assert plan["calibration_partition"]["assigned_task_count"] == 50
    assert len(plan["task_order"]) == 50
    assert [record["ordinal"] for record in plan["task_order"]] == list(range(50))
    assert len({record["task_id"] for record in plan["task_order"]}) == 50
    assert plan["caps"]["environment_action_cap"] == 1431
    assert plan["caps"]["codex_process_invocation_cap"] == 1431
    assert plan["caps"]["model_attempt_cap"] == 1431
    assert plan["caps"]["provider_wire_request_cap"] == 1431
    assert "packet-level" in plan["caps"]["provider_wire_request_cap_semantics"]
    assert plan["caps"]["provider_control_request_cap"] == 0
    assert plan["caps"]["maximum_aggregate_spend_usd"] == "10.00"
    assert plan["caps"]["luna_experiment_charge_per_call_usd"] == "0.00"
    assert plan["caps"]["maximum_luna_incremental_experiment_charge_usd"] == "0.00"
    assert plan["approval_required"]["smoke_approval_applies"] is False
    assert plan["third_smoke_evidence"] == third_smoke_evidence()
    assert plan["successful_smoke_evidence"] == successful_smoke()


def test_successor_generator_rejects_unsuccessful_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_plan_inputs(monkeypatch)
    evidence = successful_smoke()
    evidence["classification"] = "invalid_output"

    with pytest.raises(ValueError, match="successful bounded action"):
        calibration.build_successor_calibration_plan(
            ROOT,
            successful_smoke_evidence=evidence,
            runtime_identity=runtime_identity(),
        )


def test_probe_runtime_checks_local_flags_auth_model_and_reasoning() -> None:
    help_text = (
        "--model --config --disable --strict-config --image --sandbox --cd --ephemeral "
        "--ignore-user-config --ignore-rules --output-schema --json --skip-git-repo-check"
    )
    features = "\n".join(policy._DISABLED_FEATURES)
    catalog = {
        "models": [
            {
                "slug": policy.MODEL,
                "comp_hash": policy.MODEL_CATALOG_COMP_HASH,
                "context_window": policy.MODEL_CONTEXT_WINDOW_TOKENS,
                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}],
                "input_modalities": ["text", "image"],
            }
        ]
    }

    def run(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        if command == policy._configuration_preflight_command():
            return subprocess.CompletedProcess(command, 0, "[]", "")
        outputs = {
            ("codex", "--version"): policy.CODEX_CLI_VERSION,
            ("codex", "login", "status"): "Logged in using ChatGPT",
            ("codex", "exec", "--help"): help_text,
            ("codex", "features", "list"): features,
            ("codex", "debug", "models", "--bundled"): json.dumps(catalog),
        }
        return subprocess.CompletedProcess(command, 0, outputs[command], "")

    identity = policy.probe_codex_runtime(run=run)

    assert identity.cli_version == "codex-cli 0.150.1"
    assert identity.authentication_mode == "chatgpt_subscription"
    assert identity.model == "gpt-5.6-luna"
    assert "low" in identity.supported_reasoning_efforts
    assert identity.input_modalities == ("text", "image")
    assert identity.configuration_preflight_validated is True


def test_execute_rejects_unapproved_digest_before_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_plan_inputs(monkeypatch)
    plan = calibration.build_smoke_plan(ROOT, runtime_identity=runtime_identity())
    output = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="approved Codex CLI smoke digest"):
        calibration.execute_smoke(
            ROOT,
            plan=plan,
            approved_plan_sha256="sha256:not-approved",
            output_directory=output,
            runtime_identity=runtime_identity(),
        )

    assert not output.exists()


def test_execute_smoke_with_fake_cli_writes_restricted_journals_and_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_plan_inputs(monkeypatch)
    plan = calibration.build_smoke_plan(ROOT, runtime_identity=runtime_identity())
    output = tmp_path / "codex-smoke"
    process = SuccessfulProcess()

    def factory(command: Sequence[str], **kwargs: Any) -> SuccessfulProcess:
        assert command[:4] == ["codex", "exec", "--model", "gpt-5.6-luna"]
        assert not any(Path(kwargs["cwd"]).iterdir())
        return process

    summary = calibration.execute_smoke(
        ROOT,
        plan=plan,
        approved_plan_sha256=calibration.plan_digest(plan),
        output_directory=output,
        runtime_identity=runtime_identity(),
        process_factory=factory,
    )

    assert summary["provider_calls_made"] == 1
    assert summary["provider_wire_requests"] == 1
    assert summary["model_attempt_reservations"] == 1
    assert summary["provider_control_requests"] == 0
    assert summary["episode_result"]["classification"] == "pilot_action_limit"
    assert summary["episode_result"]["environment_actions"] == 1
    assert summary["policy_violation"] == "none"
    assert summary["incremental_luna_experiment_charge_usd"] == "0.00"
    assert summary["budget_accounted_aggregate_spend_usd"] == "4.778164718"
    assert summary["informational_list_price_equivalent_usd"] == "0.00071600"
    assert summary["usage_telemetry_status"] == "available"
    assert summary["unresolved_invocation_count"] == 0
    assert summary["cleanup"] == {
        "attempt_journal_closed": True,
        "invocation_journal_closed": True,
        "policy_and_environment_closed": True,
        "subprocesses_closed": True,
        "temporary_inputs_removed": True,
    }
    assert (output / "attempts.sqlite").exists()
    assert (output / "codex-cli-invocations.sqlite").exists()
    summary_text = (output / "summary.json").read_text(encoding="utf-8")
    assert "restricted-private-id" not in summary_text
    assert '"raw_stdout":' not in summary_text


def test_command_refuses_existing_output_before_loading_plan(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()

    with pytest.raises(FileExistsError, match="refusing to replace"):
        run_grounding_v5_d56_codex_cli_calibration.main(
            [
                "--execute-smoke",
                "--plan",
                "missing-plan.json",
                "--approved-plan-sha256",
                "sha256:" + "0" * 64,
                "--output",
                str(output),
            ]
        )
