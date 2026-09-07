from __future__ import annotations

import hashlib
import json
import signal
import sqlite3
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn

import pytest

from legacy.grounding.v5 import d56_claude_subscription_campaign as campaign
from pixelgym.grounding.v5 import claude_code_policy as policy
from pixelgym.grounding.v5.contracts import (
    CliFaultKind,
    CostKnowledge,
    ModelAttemptConsumption,
)

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
        usage: object = None,
        total_cost_usd: object = 0.01,
        diagnostic: str | None = None,
        stderr: str = "",
        returncode: int = 0,
        resolved_model: str = RESOLVED_MODEL,
        rate_limit_status: str = "allowed",
        result_text: str | None = None,
    ) -> None:
        self.action = action or {"action_type": 1, "x": 100, "y": 100, "key": 0}
        self.content_block_type = content_block_type
        self.usage = (
            {"input_tokens": 1000, "output_tokens": 50}
            if usage is None
            else usage
        )
        self.total_cost_usd = total_cost_usd
        self.diagnostic = diagnostic
        self.stderr = stderr
        self.returncode = returncode
        self.resolved_model = resolved_model
        self.rate_limit_status = rate_limit_status
        self.result_text = result_text
        self.input_event: dict[str, Any] | None = None

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        del timeout
        if input is not None:
            self.input_event = json.loads(input)
        system_event: dict[str, Any] = {
            "type": "system",
            "subtype": "init",
            "tools": [],
            "mcp_servers": [],
        }
        if self.diagnostic is not None:
            system_event["diagnostic"] = self.diagnostic
        events = [
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": self.rate_limit_status,
                    "isUsingOverage": False,
                    "overageStatus": "rejected",
                },
            },
            system_event,
            {
                "type": "assistant",
                "message": {
                    "model": self.resolved_model,
                    "content": [{"type": self.content_block_type, "text": "action"}],
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 1,
                "result": (
                    json.dumps(self.action, separators=(",", ":"))
                    if self.result_text is None
                    else self.result_text
                ),
                "usage": self.usage,
                "modelUsage": {
                    self.resolved_model: {"inputTokens": 1000, "outputTokens": 50}
                },
                "total_cost_usd": self.total_cost_usd,
            },
        ]
        return "\n".join(json.dumps(event) for event in events) + "\n", self.stderr

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
    assert command[command.index("--mcp-config") + 1] == '{"mcpServers":{}}'
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
    assert summary["outcome_denominators"] == {
        "attempted": 1,
        "invalid_output": 0,
        "infrastructure_failure": 0,
    }
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


def credential_shaped_value() -> str:
    """Build a detector fixture without retaining credential material in source."""

    return "".join(("s", "k", "-", "synthetic", "0" * 16))


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_claude_credential_shaped_raw_stdio_is_redacted_without_changing_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream_name: str,
) -> None:
    stub_git(monkeypatch)
    runtime = runtime_identity()
    plan = campaign.build_smoke_plan(ROOT, runtime_identity=runtime)
    output = tmp_path / stream_name
    candidate = credential_shaped_value()
    process = SuccessfulProcess(
        diagnostic=candidate if stream_name == "stdout" else None,
        stderr=candidate if stream_name == "stderr" else "",
    )

    summary = campaign.execute_smoke(
        ROOT,
        plan=plan,
        approved_plan_sha256=campaign.plan_digest(plan),
        output_directory=output,
        runtime_identity=runtime,
        process_factory=lambda _command, **_kwargs: process,
    )

    assert summary["episode_result"]["classification"] == "pilot_action_limit"
    original = process.communicate()[0 if stream_name == "stdout" else 1]
    with sqlite3.connect(output / "claude-code-invocations.sqlite") as connection:
        row = connection.execute(
            f"SELECT raw_{stream_name}, raw_{stream_name}_original_sha256, "
            "credential_redacted FROM invocations"
        ).fetchone()
    assert row is not None
    stored = bytes(row[0]).decode("utf-8")
    assert candidate not in stored
    assert row[1] == "sha256:" + hashlib.sha256(original.encode()).hexdigest()
    assert row[2] == 1


def test_claude_clean_raw_stdout_is_stored_unchanged_without_redaction_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_git(monkeypatch)
    runtime = runtime_identity()
    plan = campaign.build_smoke_plan(ROOT, runtime_identity=runtime)
    output = tmp_path / "clean"
    process = SuccessfulProcess()

    summary = campaign.execute_smoke(
        ROOT,
        plan=plan,
        approved_plan_sha256=campaign.plan_digest(plan),
        output_directory=output,
        runtime_identity=runtime,
        process_factory=lambda _command, **_kwargs: process,
    )

    assert summary["episode_result"]["classification"] == "pilot_action_limit"
    original_stdout = process.communicate()[0]
    with sqlite3.connect(output / "claude-code-invocations.sqlite") as connection:
        row = connection.execute(
            "SELECT raw_stdout, credential_redacted FROM invocations"
        ).fetchone()
    assert row is not None
    assert bytes(row[0]).decode("utf-8") == original_stdout
    assert row[1] == 0


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
    assert summary["episode_result"]["classification"] == "policy_violation"
    assert "unauthorized_content_block:tool_use" in summary["policy_violation"]
    with pytest.raises(ValueError, match="successful bounded policy action"):
        campaign.successful_smoke_evidence_from_files(tmp_path / "tool-violation")


@pytest.mark.parametrize(
    ("usage", "total_cost_usd"),
    [
        ({"input_tokens": True, "output_tokens": 1}, 0.01),
        ({"input_tokens": 1, "output_tokens": 1}, float("nan")),
        ({"input_tokens": 1, "output_tokens": 1}, float("inf")),
    ],
)
def test_malformed_telemetry_is_fail_closed_before_environment_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    usage: object,
    total_cost_usd: object,
) -> None:
    stub_git(monkeypatch)
    runtime = runtime_identity()
    plan = campaign.build_smoke_plan(ROOT, runtime_identity=runtime)

    summary = campaign.execute_smoke(
        ROOT,
        plan=plan,
        approved_plan_sha256=campaign.plan_digest(plan),
        output_directory=tmp_path / f"malformed-{len(list(tmp_path.iterdir()))}",
        runtime_identity=runtime,
        process_factory=lambda _command, **_kwargs: SuccessfulProcess(
            usage=usage,
            total_cost_usd=total_cost_usd,
        ),
    )

    assert summary["provider_calls_made"] == 1
    assert summary["episode_result"]["environment_actions"] == 0
    assert summary["policy_violation"] != "none"


@pytest.mark.parametrize(
    ("usage", "total_cost", "attempt_consumption", "cost_knowledge"),
    [
        (
            {"input_tokens": 1000, "output_tokens": 50},
            0.01,
            ModelAttemptConsumption.CONSUMED,
            CostKnowledge.KNOWN,
        ),
        (
            "unavailable",
            None,
            ModelAttemptConsumption.UNKNOWN,
            CostKnowledge.UNKNOWN,
        ),
    ],
)
def test_claude_nonzero_exit_is_reported_as_infrastructure_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    usage: object,
    total_cost: object,
    attempt_consumption: ModelAttemptConsumption,
    cost_knowledge: CostKnowledge,
) -> None:
    stub_git(monkeypatch)
    runtime = runtime_identity()
    plan = campaign.build_smoke_plan(ROOT, runtime_identity=runtime)
    process = SuccessfulProcess(
        usage=usage,
        total_cost_usd=total_cost,
        stderr="synthetic CLI failure",
        returncode=2,
    )

    summary = campaign.execute_smoke(
        ROOT,
        plan=plan,
        approved_plan_sha256=campaign.plan_digest(plan),
        output_directory=tmp_path / f"nonzero-{cost_knowledge.value}",
        runtime_identity=runtime,
        process_factory=lambda _command, **_kwargs: process,
    )

    assert summary["episode_result"]["classification"] == "infrastructure_failure"
    assert summary["outcome_denominators"] == {
        "attempted": 1,
        "invalid_output": 0,
        "infrastructure_failure": 1,
    }
    fault = summary["transport_records"][0]["cli_fault"]
    assert fault["kind"] == CliFaultKind.NONZERO_EXIT
    assert fault["model_attempt_consumption"] == attempt_consumption
    assert fault["cost_knowledge"] == cost_knowledge
    assert "parse_failure" not in json.dumps(summary)
    if usage == "unavailable":
        assert summary["unresolved_invocation_count"] == 0
        assert summary["usage_telemetry_unavailable_count"] == 1


class ConnectionResetProcess(SuccessfulProcess):
    def __init__(self) -> None:
        super().__init__()
        self.returncode = None
        self.calls = 0

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        del input, timeout
        self.calls += 1
        if self.calls == 1:
            raise ConnectionResetError("synthetic reset")
        return "", "connection reset by peer"


class RawExitProcess(SuccessfulProcess):
    def __init__(self, stdout: str, *, returncode: int) -> None:
        super().__init__(returncode=returncode)
        self.stdout = stdout

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        del input, timeout
        return self.stdout, "synthetic CLI failure"


def test_claude_connection_reset_is_stable_and_recoverable(tmp_path: Path) -> None:
    process = ConnectionResetProcess()
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / "reset.sqlite")
    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
        invocation_journal=invocation_journal,
        runtime_identity=runtime_identity(),
        process_factory=lambda _command, **_kwargs: process,
    )
    claude_policy = policy.ClaudeCodePolicy()
    screenshot = bytes(policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)
    request = claude_policy.build_request(
        claude_policy.reset("Complete the visible task."), screenshot
    )
    try:
        outcome = transport.send(
            request,
            idempotency_key="sha256:claude-reset",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "transport_fault"
        assert outcome.failure_code == "cli_connection_reset"
        assert outcome.fault is not None
        assert outcome.fault.kind is CliFaultKind.CONNECTION_RESET
        assert transport.reconcile(
            idempotency_key="sha256:claude-reset", deadline_seconds=1
        ) == outcome
    finally:
        transport.close()
        invocation_journal.close()


def test_claude_process_start_failure_is_pre_send_and_costs_zero(
    tmp_path: Path,
) -> None:
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / "start-failure.sqlite")
    process_starts = 0

    def fail_to_start(_command: list[str], **_kwargs: object) -> NoReturn:
        nonlocal process_starts
        process_starts += 1
        raise OSError("synthetic process start failure")

    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
        invocation_journal=invocation_journal,
        runtime_identity=runtime_identity(),
        process_factory=fail_to_start,
    )
    claude_policy = policy.ClaudeCodePolicy()
    screenshot = bytes(policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)
    request = claude_policy.build_request(
        claude_policy.reset("Complete the visible task."), screenshot
    )
    try:
        outcome = transport.send(
            request,
            idempotency_key="sha256:claude-start-failure",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert process_starts == 1
        assert outcome.status == "pre_send_failure"
        assert outcome.fault is not None
        assert outcome.fault.kind is CliFaultKind.PROCESS_START
        assert outcome.fault.model_attempt_consumption is ModelAttemptConsumption.NOT_CONSUMED
        assert outcome.fault.cost_knowledge is CostKnowledge.ZERO
        assert transport.records[-1]["type"] == "OSError"
        assert transport.reconcile(
            idempotency_key="sha256:claude-start-failure", deadline_seconds=1
        ) == outcome
    finally:
        transport.close()
        invocation_journal.close()


@pytest.mark.parametrize(
    ("process", "expected_kind", "expected_code"),
    [
        (
            RawExitProcess("partial-event\n", returncode=1),
            CliFaultKind.MALFORMED_EVENT_STREAM,
            "cli_malformed_event_stream",
        ),
        (
            RawExitProcess("partial-event\n", returncode=0),
            CliFaultKind.MALFORMED_EVENT_STREAM,
            "cli_malformed_event_stream",
        ),
        (
            SuccessfulProcess(returncode=-signal.SIGTERM),
            CliFaultKind.PROCESS_SIGNAL,
            "cli_process_signal",
        ),
    ],
)
def test_claude_process_failures_use_shared_fault_taxonomy(
    tmp_path: Path,
    process: SuccessfulProcess,
    expected_kind: CliFaultKind,
    expected_code: str,
) -> None:
    invocation_journal = policy.ClaudeInvocationJournal(
        tmp_path / f"{expected_kind.value}.sqlite"
    )
    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
        invocation_journal=invocation_journal,
        runtime_identity=runtime_identity(),
        process_factory=lambda _command, **_kwargs: process,
    )
    claude_policy = policy.ClaudeCodePolicy()
    screenshot = bytes(policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)
    request = claude_policy.build_request(
        claude_policy.reset("Complete the visible task."), screenshot
    )
    try:
        outcome = transport.send(
            request,
            idempotency_key=f"sha256:claude-{expected_kind.value}",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "transport_fault"
        assert outcome.failure_code == expected_code
        assert outcome.fault is not None and outcome.fault.kind is expected_kind
    finally:
        transport.close()
        invocation_journal.close()


@pytest.mark.parametrize(
    ("process", "expected_classification"),
    [
        (SuccessfulProcess(result_text="not-json"), "invalid_output"),
        (
            SuccessfulProcess(resolved_model="claude-opus-unapproved"),
            "policy_violation",
        ),
        (
            SuccessfulProcess(result_text=credential_shaped_value()),
            "policy_violation",
        ),
    ],
)
def test_claude_exit_zero_output_and_identity_failures_remain_distinct(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process: SuccessfulProcess,
    expected_classification: str,
) -> None:
    stub_git(monkeypatch)
    runtime = runtime_identity()
    plan = campaign.build_smoke_plan(ROOT, runtime_identity=runtime)

    summary = campaign.execute_smoke(
        ROOT,
        plan=plan,
        approved_plan_sha256=campaign.plan_digest(plan),
        output_directory=tmp_path / expected_classification,
        runtime_identity=runtime,
        process_factory=lambda _command, **_kwargs: process,
    )

    assert summary["episode_result"]["classification"] == expected_classification
    assert summary["episode_result"]["environment_actions"] == 0


def test_claude_resolved_model_differs_from_smoke_is_policy_violation(
    tmp_path: Path,
) -> None:
    process = SuccessfulProcess(resolved_model="claude-sonnet-5-20260901")
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / "model.sqlite")
    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
        invocation_journal=invocation_journal,
        runtime_identity=runtime_identity(),
        expected_resolved_model=RESOLVED_MODEL,
        process_factory=lambda _command, **_kwargs: process,
    )
    claude_policy = policy.ClaudeCodePolicy()
    screenshot = bytes(policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)
    request = claude_policy.build_request(
        claude_policy.reset("Complete the visible task."), screenshot
    )
    try:
        outcome = transport.send(
            request,
            idempotency_key="sha256:claude-model-mismatch",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "policy_violation"
        assert outcome.response is not None
        assert "resolved_model_differs_from_smoke" in (
            outcome.response["usage"]["policy_violation"]
        )
        assert outcome.fault is None
    finally:
        transport.close()
        invocation_journal.close()


def test_claude_subscription_rejection_is_request_failure(tmp_path: Path) -> None:
    process = SuccessfulProcess(rate_limit_status="rejected")
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / "rate-limit.sqlite")
    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
        invocation_journal=invocation_journal,
        runtime_identity=runtime_identity(),
        process_factory=lambda _command, **_kwargs: process,
    )
    claude_policy = policy.ClaudeCodePolicy()
    screenshot = bytes(policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)
    request = claude_policy.build_request(
        claude_policy.reset("Complete the visible task."), screenshot
    )
    try:
        outcome = transport.send(
            request,
            idempotency_key="sha256:claude-rate-limit",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "rate_limited"
        assert outcome.fault is not None
        assert outcome.fault.kind is CliFaultKind.SUBSCRIPTION_RATE_LIMIT
        assert outcome.fault.classification == "request_failure"
        assert outcome.fault.model_attempt_consumption is ModelAttemptConsumption.NOT_CONSUMED
        assert outcome.fault.cost_knowledge is CostKnowledge.ZERO
    finally:
        transport.close()
        invocation_journal.close()


def test_claude_fault_record_and_summary_keep_model_mismatch_visible(
    tmp_path: Path,
) -> None:
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / "combined.sqlite")
    ledger = policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00"))
    transport = policy.ClaudeCodeTransport(
        ledger=ledger,
        invocation_journal=invocation_journal,
        runtime_identity=runtime_identity(),
        expected_resolved_model=RESOLVED_MODEL,
        process_factory=lambda _command, **_kwargs: SuccessfulProcess(
            rate_limit_status="rejected",
            resolved_model="claude-sonnet-5-20260901",
        ),
    )
    claude_policy = policy.ClaudeCodePolicy()
    screenshot = bytes(policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)
    request = claude_policy.build_request(
        claude_policy.reset("Complete the visible task."), screenshot
    )
    try:
        outcome = transport.send(
            request,
            idempotency_key="sha256:rate-limit-model-mismatch",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )
        record = transport.records[0]
        summary = campaign._summary_common(
            plan={"code_revision": "revision", "human_approval_scope": {}},
            digest="sha256:" + "a" * 64,
            ledger=ledger,
            attempt_integrity={},
            invocation_integrity=invocation_journal.integrity_report(),
            call_counts=(1, 0),
            transport_records=list(transport.records),
            execution_error=None,
            subprocesses_closed=True,
        )

        assert outcome.status == "rate_limited"
        assert record["cli_fault"]["classification"] == "request_failure"
        assert "resolved_model_differs_from_smoke" in record["policy_violation"]
        assert "resolved_model_differs_from_smoke" in summary["policy_violation"]
    finally:
        transport.close()
        invocation_journal.close()


class StubbornProcess:
    pid = 930_002
    returncode: int | None = None

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        del input
        raise subprocess.TimeoutExpired("claude", timeout)

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


def test_cleanup_does_not_claim_a_stubborn_process_was_closed(
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
        output_directory=tmp_path / "stubborn-process",
        runtime_identity=runtime,
        process_factory=lambda _command, **_kwargs: StubbornProcess(),
    )

    assert summary["episode_result"]["environment_actions"] == 0
    assert summary["policy_violation"] == "none"
    assert summary["incremental_experiment_charge_usd"] == "0.00"
    assert summary["usage_telemetry_status"] == "unavailable"
    assert summary["transport_records"][0]["cli_fault"]["kind"] == (
        CliFaultKind.PROCESS_TIMEOUT
    )
    assert summary["transport_records"][0]["cli_fault"]["cost_knowledge"] == (
        CostKnowledge.UNKNOWN
    )
    assert summary["cleanup"]["subprocesses_closed"] is False


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
