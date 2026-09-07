"""Fast contract tests for `pixelgym.grounding.v5.claude_code_policy`.

Ported from `test_grounding_v5_d56_claude_subscription_campaign.py`, which imported
`legacy.grounding.v5.d56_claude_subscription_campaign` at module scope (issue #170).
Two groups of tests moved here:

- Six tests that never actually called into the legacy campaign module at all — they
  always exercised only the shipped `pixelgym.grounding.v5.claude_code_policy`
  transport, and are unchanged below.
- Eight tests that did exercise shipped `ClaudeCodeTransport`/`ClaudeInvocationJournal`
  behaviour, but only indirectly through the legacy campaign driver's
  `execute_smoke()`/`_summary_common()` wrappers. These are ported to call
  `ClaudeCodeTransport.send()` (and, where the legacy test asserted a downstream
  dispatch was blocked, `ClaudeCodePolicy.parse()`) directly, dropping only the
  legacy-only aggregation code around them.
"""

from __future__ import annotations

import hashlib
import json
import signal
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn

import pytest

from pixelgym.grounding.v5 import claude_code_policy as policy
from pixelgym.grounding.v5.contracts import (
    CliFaultKind,
    CostKnowledge,
    ModelAttemptConsumption,
)
from pixelgym.grounding.v5.runner import PolicyVisibleResult

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


def credential_shaped_value() -> str:
    """Build a detector fixture without retaining credential material in source."""

    return "".join(("s", "k", "-", "synthetic", "0" * 16))


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


# ── Ported from `d56_claude_subscription_campaign` (issue #170) ──
#
# The legacy tests below drove these same transport/journal behaviours through
# `campaign.execute_smoke()` and `campaign._summary_common()`. Both wrappers only
# assembled a request, called `ClaudeCodeTransport.send()` once, and read back
# `ClaudeInvocationJournal` state; neither added behaviour of its own. These call
# `send()` and the journal directly, and use `ClaudeCodePolicy.parse()` where the
# legacy test asserted that a policy violation blocked dispatch before an action
# could reach the environment.


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_claude_credential_shaped_raw_stdio_is_redacted_without_changing_outcome(
    tmp_path: Path, stream_name: str
) -> None:
    candidate = credential_shaped_value()
    process = SuccessfulProcess(
        diagnostic=candidate if stream_name == "stdout" else None,
        stderr=candidate if stream_name == "stderr" else "",
    )
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / f"{stream_name}.sqlite")
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
    idempotency_key = f"sha256:claude-credential-{stream_name}"
    try:
        outcome = transport.send(
            request,
            idempotency_key=idempotency_key,
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "response"
        original = process.communicate()[0 if stream_name == "stdout" else 1]
        record = invocation_journal.record(idempotency_key)
        assert record is not None
        stored = record[f"raw_{stream_name}"]
        assert candidate not in stored
        assert record[f"raw_{stream_name}_original_sha256"] == (
            "sha256:" + hashlib.sha256(original.encode()).hexdigest()
        )
        assert record["credential_redacted"] is True
    finally:
        transport.close()
        invocation_journal.close()


def test_claude_clean_raw_stdout_is_stored_unchanged_without_redaction_marker(
    tmp_path: Path,
) -> None:
    process = SuccessfulProcess()
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / "clean.sqlite")
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
    idempotency_key = "sha256:claude-clean-stdout"
    try:
        outcome = transport.send(
            request,
            idempotency_key=idempotency_key,
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "response"
        original_stdout = process.communicate()[0]
        record = invocation_journal.record(idempotency_key)
        assert record is not None
        assert record["raw_stdout"] == original_stdout
        assert record["credential_redacted"] is False
    finally:
        transport.close()
        invocation_journal.close()


def test_tool_content_is_fail_closed_before_environment_dispatch(tmp_path: Path) -> None:
    process = SuccessfulProcess(content_block_type="tool_use")
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / "tool-violation.sqlite")
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
            idempotency_key="sha256:claude-tool-content",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "policy_violation"
        assert outcome.fault is None
        assert outcome.response is not None
        assert "unauthorized_content_block:tool_use" in (
            outcome.response["usage"]["policy_violation"]
        )
        with pytest.raises(ValueError, match="policy boundary is invalid"):
            claude_policy.parse(policy.canonical_json_bytes(outcome.response), b"{}")
    finally:
        transport.close()
        invocation_journal.close()


@pytest.mark.parametrize(
    ("usage", "total_cost_usd"),
    [
        ({"input_tokens": True, "output_tokens": 1}, 0.01),
        ({"input_tokens": 1, "output_tokens": 1}, float("nan")),
        ({"input_tokens": 1, "output_tokens": 1}, float("inf")),
    ],
)
def test_malformed_telemetry_is_fail_closed_before_environment_dispatch(
    tmp_path: Path, usage: object, total_cost_usd: object
) -> None:
    process = SuccessfulProcess(usage=usage, total_cost_usd=total_cost_usd)
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / "malformed.sqlite")
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
            idempotency_key="sha256:claude-malformed-telemetry",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "policy_violation"
        assert outcome.fault is None
        assert outcome.response is not None
        assert outcome.response["usage"]["policy_violation"] != "none"
        with pytest.raises(ValueError, match="policy boundary is invalid"):
            claude_policy.parse(policy.canonical_json_bytes(outcome.response), b"{}")
    finally:
        transport.close()
        invocation_journal.close()


@pytest.mark.parametrize(
    ("usage", "total_cost_usd", "expected_attempt_consumption", "expected_cost_knowledge"),
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
    usage: object,
    total_cost_usd: object,
    expected_attempt_consumption: ModelAttemptConsumption,
    expected_cost_knowledge: CostKnowledge,
) -> None:
    process = SuccessfulProcess(
        usage=usage,
        total_cost_usd=total_cost_usd,
        stderr="synthetic CLI failure",
        returncode=2,
    )
    invocation_journal = policy.ClaudeInvocationJournal(
        tmp_path / f"nonzero-{expected_cost_knowledge.value}.sqlite"
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
            idempotency_key=f"sha256:claude-nonzero-{expected_cost_knowledge.value}",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "transport_fault"
        assert outcome.fault is not None
        assert outcome.fault.kind is CliFaultKind.NONZERO_EXIT
        assert outcome.fault.classification == "infrastructure_failure"
        assert outcome.fault.model_attempt_consumption is expected_attempt_consumption
        assert outcome.fault.cost_knowledge is expected_cost_knowledge
    finally:
        transport.close()
        invocation_journal.close()


@pytest.mark.parametrize(
    ("process", "expected_status", "expected_violation"),
    [
        (SuccessfulProcess(result_text="not-json"), "response", None),
        (
            SuccessfulProcess(resolved_model="claude-opus-unapproved"),
            "policy_violation",
            "resolved_model_mismatch",
        ),
        (
            SuccessfulProcess(result_text=credential_shaped_value()),
            "policy_violation",
            "credential_shaped_output",
        ),
    ],
)
def test_claude_exit_zero_output_and_identity_failures_remain_distinct(
    tmp_path: Path,
    process: SuccessfulProcess,
    expected_status: str,
    expected_violation: str | None,
) -> None:
    invocation_journal = policy.ClaudeInvocationJournal(
        tmp_path / f"{expected_status}-{expected_violation}.sqlite"
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
            idempotency_key=f"sha256:claude-{expected_status}-{expected_violation}",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == expected_status
        assert outcome.fault is None
        assert outcome.response is not None
        if expected_violation is None:
            assert outcome.response["usage"]["policy_violation"] == "none"
        else:
            assert expected_violation in outcome.response["usage"]["policy_violation"]
        with pytest.raises(ValueError):
            claude_policy.parse(policy.canonical_json_bytes(outcome.response), b"{}")
    finally:
        transport.close()
        invocation_journal.close()


def test_claude_fault_record_and_summary_keep_model_mismatch_visible(
    tmp_path: Path,
) -> None:
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / "combined.sqlite")
    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
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

        assert outcome.status == "rate_limited"
        assert record["cli_fault"]["classification"] == "request_failure"
        assert "resolved_model_differs_from_smoke" in record["policy_violation"]
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


def test_cleanup_does_not_claim_a_stubborn_process_was_closed(tmp_path: Path) -> None:
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / "stubborn.sqlite")
    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
        invocation_journal=invocation_journal,
        runtime_identity=runtime_identity(),
        process_factory=lambda _command, **_kwargs: StubbornProcess(),
    )
    claude_policy = policy.ClaudeCodePolicy()
    screenshot = bytes(policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)
    request = claude_policy.build_request(
        claude_policy.reset("Complete the visible task."), screenshot
    )
    try:
        outcome = transport.send(
            request,
            idempotency_key="sha256:claude-stubborn",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "deadline"
        assert outcome.fault is not None
        assert outcome.fault.kind is CliFaultKind.PROCESS_TIMEOUT
        assert outcome.fault.cost_knowledge is CostKnowledge.UNKNOWN
    finally:
        transport.close()
        invocation_journal.close()
    assert transport.subprocesses_closed is False


# ── Coverage recovery after issue #170 (round 2) ──
#
# The tests above exercise `ClaudeCodeTransport`/`ClaudeInvocationJournal` and only
# ever call `ClaudeCodePolicy.parse()` inside `pytest.raises` blocks (failure paths).
# The deleted legacy campaign's `execute_smoke()` also drove a full successful round
# trip — `parse()` returning a candidate, plus every no-op `Policy` protocol hook the
# runner calls around it, and `ClaudeInvocationJournal.integrity_report()` — with no
# replacement. These restore that coverage directly.


def test_claude_policy_hooks_are_pure_pass_throughs_between_attempts() -> None:
    claude_policy = policy.ClaudeCodePolicy()
    state = claude_policy.reset("Complete the visible task.")

    assert claude_policy.reduce_state(state, b'{"ignored": true}') == state
    assert claude_policy.failure_state(state, "some_failure_code") == state
    assert claude_policy.retryable_response_code(b'{"ignored": true}') is None
    assert claude_policy.post_parse_state(state, {"action_type": 0, "x": 0, "y": 0, "key": 0}) == (
        state
    )
    result = PolicyVisibleResult(
        screenshot_digest="sha256:" + "a" * 64,
        reward=0.0,
        terminated=False,
        truncated=False,
        step_index=0,
    )
    assert (
        claude_policy.post_dispatch_state(state, {"action_type": 0, "x": 0, "y": 0, "key": 0}, result)
        == state
    )
    assert claude_policy.close() is None


def test_claude_policy_parse_accepts_one_successful_response_and_returns_the_action(
    tmp_path: Path,
) -> None:
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / "successful-parse.sqlite")
    action = {"action_type": 1, "x": 200, "y": 150, "key": 3}
    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
        invocation_journal=invocation_journal,
        runtime_identity=runtime_identity(),
        process_factory=lambda _command, **_kwargs: SuccessfulProcess(action),
    )
    claude_policy = policy.ClaudeCodePolicy()
    screenshot = bytes(policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)
    request = claude_policy.build_request(
        claude_policy.reset("Complete the visible task."), screenshot
    )
    try:
        outcome = transport.send(
            request,
            idempotency_key="sha256:claude-successful-parse",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "response"
        candidate = claude_policy.parse(policy.canonical_json_bytes(outcome.response), b"{}")
        assert candidate == action
        assert all(type(value) is int for value in candidate.values())
    finally:
        transport.close()
        invocation_journal.close()


def test_claude_invocation_journal_integrity_report_summarizes_every_record(
    tmp_path: Path,
) -> None:
    invocation_journal = policy.ClaudeInvocationJournal(tmp_path / "integrity-report.sqlite")
    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
        invocation_journal=invocation_journal,
        runtime_identity=runtime_identity(),
        process_factory=lambda _command, **_kwargs: SuccessfulProcess(),
    )
    claude_policy = policy.ClaudeCodePolicy()
    screenshot = bytes(policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)
    request = claude_policy.build_request(
        claude_policy.reset("Complete the visible task."), screenshot
    )
    try:
        transport.send(
            request,
            idempotency_key="sha256:claude-integrity-report",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )
    finally:
        transport.close()

    report = invocation_journal.integrity_report()
    assert report["schema_version"] == policy.INVOCATION_JOURNAL_SCHEMA_VERSION
    assert report["invocation_count"] == 1
    (record,) = report["records"]
    assert record["idempotency_key_digest"] == policy.content_digest(
        "sha256:claude-integrity-report"
    )
    assert record["status"] == "response"
    assert record["raw_stdout_sha256"].startswith("sha256:")
    assert record["raw_stdout_original_sha256"] == record["raw_stdout_sha256"]
    assert record["credential_redacted"] is False
    invocation_journal.close()
