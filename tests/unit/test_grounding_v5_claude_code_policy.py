"""Launch-path coverage for the v5 Claude Code CLI transport."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5 import claude_code_policy as policy
from pixelgym.grounding.v5.sandbox import environment_allowlist_digest

ROOT = Path(__file__).parents[2]


class SuccessfulProcess:
    pid = 940_001
    returncode: int | None = 0

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        del input, timeout
        events = (
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
                    "model": "claude-sonnet-5-20260801",
                    "content": [{"type": "text", "text": "action"}],
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 1,
                "result": '{"action_type":1,"x":100,"y":100,"key":0}',
                "usage": {"input_tokens": 1000, "output_tokens": 50},
                "modelUsage": {
                    "claude-sonnet-5-20260801": {
                        "inputTokens": 1000,
                        "outputTokens": 50,
                    }
                },
                "total_cost_usd": 0.01,
            },
        )
        return "\n".join(json.dumps(event) for event in events) + "\n", ""

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def runtime_identity() -> policy.ClaudeRuntimeIdentity:
    return policy.ClaudeRuntimeIdentity(
        cli_version=policy.CLAUDE_CLI_VERSION,
        auth_method=policy.AUTH_METHOD,
        subscription_type=policy.SUBSCRIPTION_TYPE,
        requested_model=policy.MODEL,
        reasoning_effort=policy.MODEL_REASONING_EFFORT,
        help_sha256="sha256:help",
    )


def request() -> dict[str, Any]:
    screenshot = bytes(policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)
    claude_policy = policy.ClaudeCodePolicy()
    return claude_policy.build_request(
        claude_policy.reset("Complete the visible task."),
        screenshot,
    )


def make_transport(
    tmp_path: Path,
    process_factory: Any,
) -> tuple[policy.ClaudeCodeTransport, policy.ClaudeInvocationJournal]:
    journal = policy.ClaudeInvocationJournal(tmp_path / "claude-invocations.sqlite")
    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
        invocation_journal=journal,
        runtime_identity=runtime_identity(),
        environment={"PATH": "/bin", "HOME": "/private/auth-home", "SECRET": "blocked"},
        process_factory=process_factory,
    )
    return transport, journal


def test_claude_policy_manifest_emits_declared_v3_sandbox_contract() -> None:
    manifest = policy.build_claude_policy_manifest(
        ROOT,
        code_revision="revision-test",
        runtime_identity=runtime_identity(),
        resolved_model="claude-sonnet-5-20260801",
    )
    sandbox = manifest.to_dict()["sandbox"]

    assert sandbox["schema_version"] == "pixelgym-agent-v5-sandbox-v3"
    assert sandbox["probe_result"]["status"] == "not_run"
    assert sandbox["runtime_enforcement"]["mechanism_name"] == (
        "cli_flags_and_environment_allowlist"
    )
    assert sandbox["runtime_enforcement"]["os_sandbox_applied"] is False
    assert "declared_unavailable_capabilities" in sandbox["policy_claim"]
    assert "denied_capabilities" not in sandbox


def test_claude_child_launch_metadata_matches_exact_argv_and_environment(
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def factory(command: list[str], **kwargs: Any) -> SuccessfulProcess:
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return SuccessfulProcess()

    transport, journal = make_transport(tmp_path, factory)
    try:
        outcome = transport.send(
            request(),
            idempotency_key="sha256:claude-launch",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "response"
        record = journal.record("sha256:claude-launch")
        assert record is not None
        enforcement = record["outcome"]["runtime_enforcement"]
        assert enforcement["mechanism_name"] == "cli_flags_and_environment_allowlist"
        assert enforcement["argv_digest"] == policy.content_digest(captured["command"])
        assert enforcement["environment_allowlist_digest"] == (
            environment_allowlist_digest(captured["environment"])
        )
        assert enforcement["environment_variable_names"] == sorted(captured["environment"])
        assert enforcement["os_sandbox_applied"] is False
        assert "SECRET" not in captured["environment"]
    finally:
        transport.close()
        journal.close()


def test_claude_missing_required_launch_flag_fails_before_process_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete = policy._runtime_command()
    altered = list(complete)
    altered.remove("--safe-mode")
    monkeypatch.setattr(policy, "_runtime_command", lambda: altered)
    process_started = False

    def factory(_command: list[str], **_kwargs: Any) -> SuccessfulProcess:
        nonlocal process_started
        process_started = True
        return SuccessfulProcess()

    transport, journal = make_transport(tmp_path, factory)
    try:
        outcome = transport.send(
            request(),
            idempotency_key="sha256:claude-missing-restriction",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "pre_send_failure"
        assert outcome.failure_code == "runtime_enforcement_mismatch"
        assert process_started is False
        record = journal.record("sha256:claude-missing-restriction")
        assert record is not None
        enforcement = record["outcome"]["runtime_enforcement"]
        assert enforcement["argv_digest"] == policy.content_digest(altered)
        assert enforcement["argv_digest"] != policy.content_digest(complete)
        assert enforcement["cli_restrictions_applied"] is False
        assert enforcement["environment_allowlist_applied"] is True
        assert len(transport.records) == 1
        assert transport.records[0]["runtime_enforcement"] == enforcement
    finally:
        transport.close()
        journal.close()
