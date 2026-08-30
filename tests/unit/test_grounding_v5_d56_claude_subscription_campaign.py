from __future__ import annotations

import json
import signal
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5 import claude_code_policy as policy
from pixelgym.grounding.v5 import d56_claude_subscription_campaign as campaign

ROOT = Path(__file__).parents[2]
RESOLVED_MODEL = "claude-sonnet-5-20260801"


def runtime_identity() -> policy.ClaudeRuntimeIdentity:
    return policy.ClaudeRuntimeIdentity(
        cli_version=policy.CLAUDE_CLI_VERSION,
        auth_method=policy.AUTH_METHOD,
        subscription_type=policy.SUBSCRIPTION_TYPE,
        requested_model=policy.MODEL,
        reasoning_effort=policy.MODEL_REASONING_EFFORT,
        help_sha256="sha256:help",
    )


def stub_git(monkeypatch: pytest.MonkeyPatch) -> None:
    def git(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "campaign-revision"
        if args == ("status", "--porcelain", "--untracked-files=no"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(campaign, "_git", git)


class SuccessfulProcess:
    pid = 930_001
    returncode: int | None = 0

    def __init__(
        self,
        action: dict[str, int] | None = None,
        *,
        content_block_type: str = "text",
    ) -> None:
        self.action = action or {"action_type": 1, "x": 100, "y": 100, "key": 0}
        self.content_block_type = content_block_type
        self.input_event: dict[str, Any] | None = None

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        del timeout
        if input is not None:
            self.input_event = json.loads(input)
        events = [
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "allowed",
                    "isUsingOverage": False,
                    "overageStatus": "rejected",
                },
            },
            {"type": "system", "subtype": "init", "tools": [], "mcp_servers": []},
            {
                "type": "assistant",
                "message": {
                    "model": RESOLVED_MODEL,
                    "content": [{"type": self.content_block_type, "text": "action"}],
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 1,
                "result": json.dumps(self.action, separators=(",", ":")),
                "usage": {"input_tokens": 1000, "output_tokens": 50},
                "modelUsage": {RESOLVED_MODEL: {"inputTokens": 1000, "outputTokens": 50}},
                "total_cost_usd": 0.01,
            },
        ]
        return "\n".join(json.dumps(event) for event in events) + "\n", ""

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -signal.SIGTERM

    def kill(self) -> None:
        self.returncode = -signal.SIGKILL


def test_smoke_plan_is_deterministic_bounded_and_zero_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_git(monkeypatch)
    runtime = runtime_identity()

    first = campaign.build_smoke_plan(ROOT, runtime_identity=runtime)
    second = campaign.build_smoke_plan(ROOT, runtime_identity=runtime)

    assert campaign.plan_file_bytes(first) == campaign.plan_file_bytes(second)
    assert first["policy"]["model"] == "claude-sonnet-5"
    assert first["policy"]["model_reasoning_effort"] == "medium"
    assert first["policy"]["resolved_model"] is None
    assert first["caps"]["claude_process_invocation_cap"] == 1
    assert first["caps"]["experiment_charge_per_call_usd"] == "0.00"
    assert first["human_approval_scope"]["aggregate_smoke_process_cap"] == 3
    command = first["policy"]["command_contract"]
    assert command[command.index("--tools") + 1] == ""
    assert "--json-schema" not in command
    assert "--strict-mcp-config" in command
    assert "--safe-mode" in command
    text = campaign.plan_file_bytes(first).decode("utf-8")
    assert str(ROOT) not in text
    assert "raw_stdout" not in text


def test_successful_smoke_uses_inline_image_and_unlocks_resolved_full_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_git(monkeypatch)
    runtime = runtime_identity()
    plan = campaign.build_smoke_plan(ROOT, runtime_identity=runtime)
    output = tmp_path / "smoke"
    process = SuccessfulProcess()

    summary = campaign.execute_smoke(
        ROOT,
        plan=plan,
        approved_plan_sha256=campaign.plan_digest(plan),
        output_directory=output,
        runtime_identity=runtime,
        process_factory=lambda _command, **_kwargs: process,
    )

    assert summary["provider_calls_made"] == 1
    assert summary["episode_result"]["classification"] == "pilot_action_limit"
    assert summary["episode_result"]["environment_actions"] == 1
    assert summary["policy_violation"] == "none"
    assert summary["resolved_model"] == RESOLVED_MODEL
    assert summary["incremental_experiment_charge_usd"] == "0.00"
    assert summary["informational_cost_telemetry_usd"] == "0.01"
    assert process.input_event is not None
    blocks = process.input_event["message"]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["type"] == "base64"
    assert blocks[1]["type"] == "text"
    smoke = campaign.successful_smoke_evidence_from_files(output)
    full = campaign.build_full_plan(
        ROOT,
        successful_smoke_evidence=smoke,
        runtime_identity=runtime,
    )
    assert full["execution_state"] == "advance_human_authorized_after_successful_smoke"
    assert full["policy"]["resolved_model"] == RESOLVED_MODEL
    assert full["policy"]["policy_manifest"]["exact_snapshot"] is True
    assert full["calibration_partition"]["assigned_task_count"] == 50
    assert len(full["task_order"]) == 50
    assert full["caps"]["environment_action_cap"] == 1431


def test_tool_content_is_fail_closed_before_environment_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_git(monkeypatch)
    runtime = runtime_identity()
    plan = campaign.build_smoke_plan(ROOT, runtime_identity=runtime)

    summary = campaign.execute_smoke(
        ROOT,
        plan=plan,
        approved_plan_sha256=campaign.plan_digest(plan),
        output_directory=tmp_path / "tool-violation",
        runtime_identity=runtime,
        process_factory=lambda _command, **_kwargs: SuccessfulProcess(
            content_block_type="tool_use"
        ),
    )

    assert summary["provider_calls_made"] == 1
    assert summary["episode_result"]["environment_actions"] == 0
    assert "unauthorized_content_block:tool_use" in summary["policy_violation"]
    with pytest.raises(ValueError, match="successful bounded policy action"):
        campaign.successful_smoke_evidence_from_files(tmp_path / "tool-violation")


def test_runtime_probe_sanitizes_authenticated_subscription_identity() -> None:
    outputs = {
        ("claude", "--version"): policy.CLAUDE_CLI_VERSION + "\n",
        ("claude", "auth", "status", "--json"): json.dumps(
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "subscriptionType": "max",
                "email": "must-not-be-retained@example.invalid",
                "orgId": "must-not-be-retained",
            }
        ),
        ("claude", "--help"): " ".join(
            value for value in policy.sanitized_command_contract() if value.startswith("--")
        ),
    }

    class Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    identity = policy.probe_claude_runtime(
        run=lambda command: Completed(outputs[tuple(command)])  # type: ignore[arg-type]
    )

    record = identity.to_dict()
    assert record["auth_method"] == "claude.ai"
    assert record["subscription_type"] == "max"
    assert "email" not in record
    assert "orgId" not in record
