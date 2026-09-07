from __future__ import annotations

import json
import signal
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from legacy.grounding.v5 import d56_codex_subscription_campaign as campaign
from pixelgym.grounding.v5 import codex_cli_policy as policy
from pixelgym.grounding.v5.generator import generate_task

ROOT = Path(__file__).parents[2]


def runtime_identity(config: policy.CodexPolicyConfig) -> policy.CodexRuntimeIdentity:
    return policy.CodexRuntimeIdentity(
        cli_version=policy.CODEX_CLI_VERSION,
        authentication_mode=policy.AUTH_MODE,
        model=config.model,
        model_catalog_comp_hash=config.model_catalog_comp_hash,
        supported_reasoning_efforts=("low", "medium", "high"),
        input_modalities=("text", "image"),
        context_window_tokens=config.model_context_window_tokens,
        exec_help_sha256="sha256:help",
        feature_inventory_sha256="sha256:features",
        configuration_preflight_validated=True,
    )


def stub_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        campaign,
        "_historical_evidence",
        lambda _root: {
            "qwen_predecessor": {"status": "frozen"},
            "luna_low_smoke_v1": {"status": "frozen"},
            "luna_low_smoke_v2": {"status": "frozen"},
            "luna_low_smoke_v3": {"status": "frozen"},
            "luna_low_smoke_v4": {"status": "superseded_unconsumed_no_provider_calls"},
        },
    )

    def git(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "campaign-revision"
        if args == ("status", "--porcelain", "--untracked-files=no"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(campaign, "_git", git)


class SuccessfulProcess:
    pid = 920_001
    returncode: int | None = 0

    def __init__(self, action: dict[str, int] | None = None) -> None:
        self.action = action or {"action_type": 1, "x": 100, "y": 100, "key": 0}

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        del input, timeout
        events = [
            {"type": "thread.started", "thread_id": "restricted-id"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {
                    "id": "message",
                    "type": "agent_message",
                    "text": json.dumps(self.action, separators=(",", ":")),
                },
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
        self.returncode = -signal.SIGTERM

    def kill(self) -> None:
        self.returncode = -signal.SIGKILL


def test_policy_slots_are_exact_and_distinct() -> None:
    luna = campaign.policy_for_slot("luna-medium")
    terra = campaign.policy_for_slot("terra-medium")

    assert (luna.model, luna.model_reasoning_effort) == ("gpt-5.6-luna", "medium")
    assert (terra.model, terra.model_reasoning_effort) == ("gpt-5.6-terra", "medium")
    assert policy.command_contract_digest(luna) != policy.command_contract_digest(terra)
    with pytest.raises(ValueError, match="outside"):
        campaign.policy_for_slot("luna-low")


@pytest.mark.parametrize("config", [policy.LUNA_MEDIUM, policy.TERRA_MEDIUM])
def test_smoke_plans_are_deterministic_bounded_and_publishable(
    monkeypatch: pytest.MonkeyPatch,
    config: policy.CodexPolicyConfig,
) -> None:
    stub_inputs(monkeypatch)
    runtime = runtime_identity(config)

    first = campaign.build_smoke_plan(ROOT, config=config, runtime_identity=runtime)
    second = campaign.build_smoke_plan(ROOT, config=config, runtime_identity=runtime)

    assert campaign.plan_file_bytes(first) == campaign.plan_file_bytes(second)
    assert first["policy"]["slot"] == config.slot
    assert first["policy"]["model"] == config.model
    assert first["policy"]["model_reasoning_effort"] == "medium"
    assert first["caps"]["codex_process_invocation_cap"] == 1
    assert first["caps"]["experiment_charge_per_call_usd"] == "0.00"
    assert first["human_approval_scope"]["aggregate_smoke_process_cap"] == 3
    text = campaign.plan_file_bytes(first).decode("utf-8")
    assert str(ROOT) not in text
    assert "restricted-id" not in text
    assert "raw_stdout" not in text


def test_successful_smoke_executes_one_action_and_unlocks_full_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_inputs(monkeypatch)
    config = policy.LUNA_MEDIUM
    runtime = runtime_identity(config)
    plan = campaign.build_smoke_plan(ROOT, config=config, runtime_identity=runtime)
    output = tmp_path / "smoke"

    summary = campaign.execute_smoke(
        ROOT,
        config=config,
        plan=plan,
        approved_plan_sha256=campaign.plan_digest(plan),
        output_directory=output,
        runtime_identity=runtime,
        process_factory=lambda _command, **_kwargs: SuccessfulProcess(),
    )

    assert summary["provider_calls_made"] == 1
    assert summary["episode_result"]["classification"] == "pilot_action_limit"
    assert summary["episode_result"]["environment_actions"] == 1
    assert summary["policy_violation"] == "none"
    assert summary["incremental_experiment_charge_usd"] == "0.00"
    smoke = campaign.successful_smoke_evidence_from_files(output, config=config)
    full = campaign.build_full_plan(
        ROOT,
        config=config,
        successful_smoke_evidence=smoke,
        runtime_identity=runtime,
    )
    assert full["execution_state"] == "advance_human_authorized_after_successful_smoke"
    assert full["calibration_partition"]["assigned_task_count"] == 50
    assert len(full["task_order"]) == 50
    assert full["caps"]["environment_action_cap"] == 1431
    assert full["successful_smoke_evidence"] == smoke


def test_full_execution_uses_frozen_order_and_stops_only_after_normal_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_inputs(monkeypatch)
    config = policy.TERRA_MEDIUM
    runtime = runtime_identity(config)
    task = generate_task(5002)
    task_order = [
        {
            "ordinal": 0,
            "seed": task.seed,
            "task_id": task.task_id,
            "family": task.seed_record.family.value,
            "variant": task.seed_record.variant,
            "max_episode_steps": task.max_episode_steps,
        }
    ]
    partition = {"manifest_digest": "sha256:partition"}
    monkeypatch.setattr(campaign, "EXPECTED_TASK_COUNT", 1)
    monkeypatch.setattr(campaign, "_task_order", lambda _root: (task_order, partition))
    smoke = {
        "approved_smoke_plan_sha256": "sha256:smoke",
        "summary_file_sha256": "sha256:summary",
        "attempt_journal_file_sha256": "sha256:attempts",
        "invocation_journal_file_sha256": "sha256:invocations",
        "task_id": task.task_id,
        "classification": "pilot_action_limit",
        "provider_calls_made": 1,
        "environment_actions": 1,
        "model": config.model,
        "model_reasoning_effort": "medium",
        "cli_version": policy.CODEX_CLI_VERSION,
        "policy_violation": "none",
        "incremental_experiment_charge_usd": "0.00",
        "budget_accounted_aggregate_spend_usd": "4.778164718",
        "usage_telemetry_status": "available",
    }
    plan = campaign.build_full_plan(
        ROOT,
        config=config,
        successful_smoke_evidence=smoke,
        runtime_identity=runtime,
    )
    started: list[Sequence[str]] = []

    def factory(command: Sequence[str], **_kwargs: Any) -> SuccessfulProcess:
        started.append(command)
        return SuccessfulProcess({"action_type": 0, "x": 0, "y": 0, "key": 0})

    summary = campaign.execute_full(
        ROOT,
        config=config,
        plan=plan,
        approved_plan_sha256=campaign.plan_digest(plan),
        successful_smoke_evidence=smoke,
        output_directory=tmp_path / "full",
        runtime_identity=runtime,
        process_factory=factory,
    )

    assert summary["attempted_policy_task_pairs"] == 1
    assert summary["completed_all_assigned_pairs"] is True
    assert summary["classifications"] == {"step_limit_truncation": 1}
    assert summary["provider_calls_made"] == task.max_episode_steps
    assert len(started) == task.max_episode_steps
    assert all(command[3] == "gpt-5.6-terra" for command in started)
    assert summary["incremental_experiment_charge_usd"] == "0.00"
