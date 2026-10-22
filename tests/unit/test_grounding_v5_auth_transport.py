"""Offline OAuth-expiry envelopes, bounded retries, and durable accounting."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_grounding_v5_claude_code_transport import (
    ConnectionResetProcess,
    RawExitProcess,
    SuccessfulProcess,
    credential_shaped_value,
)

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.cli_memory_calibration import (
    CliMemoryPolicy,
    CliMemoryRunner,
    build_memory_manifest,
)
from pixelgym.grounding.v5.contracts import CallCaps, CliFaultKind, CostKnowledge
from pixelgym.grounding.v5.journal import CallCapExceededError, V5AttemptJournal
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.runner import BoundedCallResult, InjectedInterruption

ROOT = Path(__file__).parents[2]
EXPIRED_MESSAGE = (
    "OAuth token has expired. Please obtain a new token or refresh your existing token."
)


def identity() -> claude.ClaudeRuntimeIdentity:
    return claude.ClaudeRuntimeIdentity(
        cli_version=claude.REPLICATION_CLI_VERSION,
        auth_method="oauth_token",
        subscription_type="max",
        requested_model=claude.MODEL,
        reasoning_effort=claude.MODEL_REASONING_EFFORT,
        help_sha256="sha256:fixture-help",
    )


def expired_events() -> list[dict[str, Any]]:
    text = "API Error: 401 " + json.dumps(
        {
            "type": "error",
            "error": {"type": "authentication_error", "message": EXPIRED_MESSAGE},
        }
    )
    return [
        {"type": "system", "subtype": "init", "tools": [], "mcp_servers": []},
        {
            "type": "assistant",
            "error": "authentication_failed",
            "message": {"model": "<synthetic>", "content": [{"type": "text", "text": text}]},
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "num_turns": 1,
            "result": text,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "modelUsage": {},
            "total_cost_usd": 0,
        },
    ]


def rejection(events: list[dict[str, Any]] | None = None) -> RawExitProcess:
    return RawExitProcess("\n".join(map(json.dumps, events or expired_events())), returncode=1)


class FixtureRecoveryGate:
    """Lease fixture: owner renewal occurs only before a subsequent reservation."""

    def __init__(self) -> None:
        self.markers: list[str] = []
        self.on_begin: Callable[[], None] = lambda: None
        self.active = 0
        self.login_required = False

    def begin_attempt(self) -> None:
        self.on_begin()
        self.markers.append("renew" if self.login_required else "begin")
        self.login_required = False
        self.active += 1

    def end_attempt(self) -> None:
        assert self.active == 1
        self.active -= 1
        self.markers.append("end")

    def require_login(self) -> None:
        self.markers.append("require_login")
        self.login_required = True


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        processes: list[SuccessfulProcess],
        *,
        recovery: bool = True,
        attempts: int = 2,
    ) -> None:
        self.gate = FixtureRecoveryGate()
        self.journal = V5AttemptJournal(tmp_path / "attempts.sqlite")
        self.invocations = claude.ClaudeInvocationJournal(tmp_path / "invocations.sqlite")
        self.ledger = claude.SubscriptionExemptLedger(Decimal(1), Decimal(0))
        self.processes = processes
        self.calls: list[dict[str, Any]] = []
        self.inputs: list[str] = []

        def factory(_command: list[str], **kwargs: Any) -> SuccessfulProcess:
            assert self.gate.active == int(recovery)
            process = self.processes[len(self.calls)]
            self.calls.append(kwargs)
            communicate = process.communicate

            def capture(input: str | None = None, timeout: float | None = None) -> tuple[str, str]:
                if input is not None:
                    self.inputs.append(input)
                return communicate(input=input, timeout=timeout)

            process.communicate = capture
            return process

        self.transport = claude.ClaudeCodeTransport(
            ledger=self.ledger,
            invocation_journal=self.invocations,
            runtime_identity=identity(),
            expected_resolved_model=claude.MODEL,
            process_factory=factory,
            allow_timeout_retry=True,
            allow_connection_retry=True,
            api_retry_limit=0,
            authentication_recovery=self.gate if recovery else None,
        )
        base = claude.build_claude_policy_manifest(
            ROOT,
            code_revision="fixture",
            runtime_identity=identity(),
            resolved_model=claude.MODEL,
            api_retry_limit=0,
        )
        self.manifest = build_memory_manifest(
            ROOT,
            base,
            retain_screenshots=True,
            allow_connection_retry=True,
            allow_authentication_retry=recovery,
        )
        self.runner = CliMemoryRunner(
            journal=self.journal,
            manifest=self.manifest,
            policy=CliMemoryPolicy(
                claude.ClaudeCodePolicy(runtime_identity=identity()), retain_screenshots=True
            ),
            transport=self.transport,
            approved_caps=CallCaps(1, attempts, 0, attempts),
        )

    def run(self) -> Any:
        return self.runner.run(
            trial_id="auth-retry",
            task=generate_memory_task(5112),
            backend=FocusMemoryBackend(),
            action_limit=1,
        )

    def close(self) -> None:
        self.transport.close()
        self.invocations.close()
        self.journal.close()


@pytest.mark.parametrize("ui_preamble", [False, True])
def test_expired_credential_retries_same_state_once_with_both_attempts_retained(
    tmp_path: Path, ui_preamble: bool
):
    events = expired_events()
    if ui_preamble:
        events.insert(0, {"type": "system", "subtype": "ui_invalidate", "event": "ui.render"})
        for index, event in enumerate(events):
            event.update(session_id="auth-ui-session", uuid=f"auth-ui-{index}")
    harness = Harness(tmp_path, [rejection(events), SuccessfulProcess()])
    try:
        result = harness.run()
        assert result.classification == "pilot_action_limit"
        assert result.environment_actions == 1
        assert result.model_attempts == result.provider_wire_requests == 2
        assert result.provider_control_requests == 0
        assert harness.inputs[0] == harness.inputs[1]
        assert harness.journal.call_counts() == (2, 0)
        assert harness.gate.markers == ["begin", "require_login", "end", "renew", "end"]
        assert harness.transport.subprocesses_closed
        assert not harness.ledger.blocked
        assert not harness.ledger.unresolved
        events = harness.journal.events()
        retry = [event for event in events if event.kind == "retryable_authentication"]
        assert len(retry) == 1
        assert retry[0].payload["cli_fault"]["kind"] == "authentication_expired"
        assert retry[0].payload["next_attempt_permitted"] is True
        assert retry[0].payload["bounded_retry_budget"] == 1
        assert retry[0].payload["retry_rule"] == (
            "claude-one-stopped-timeout-connection-or-auth-retry-v1"
        )
        assert len([event for event in events if event.kind == "attempt_started"]) == 2
        assert len({record["idempotency_key_digest"] for record in harness.transport.records}) == 2
        assert not any(event.kind == "sealed_unsuccessful_result" for event in events)
    finally:
        harness.close()


@pytest.mark.parametrize("kind", ["authentication", "connection"])
@pytest.mark.parametrize("fault", [None, "extra_ui", "session", "usage", "model"])
def test_ui_preamble_does_not_weaken_synthetic_fault_recognition(kind, fault):
    events = expired_events()
    if kind == "connection":
        text = "API Error: Connection dropped (ECONNRESET)"
        events[1]["error"] = "server_error"
        events[1]["message"]["content"][0]["text"] = text
        events[2]["result"] = text
    events.insert(0, {"type": "system", "subtype": "ui_invalidate", "event": "ui.render"})
    for index, event in enumerate(events):
        event.update(session_id="fault-ui-session", uuid=f"fault-ui-{index}")
    if fault == "extra_ui":
        events[0]["tools"] = ["Read"]
    elif fault == "session":
        events[0]["session_id"] = "other-session"
    elif fault == "usage":
        events[-1]["usage"]["input_tokens"] = 1
    elif fault == "model":
        events[2]["message"]["model"] = claude.MODEL
    raw = "\n".join(map(json.dumps, events))
    parsed = claude._parse_stream(raw)
    detector = (
        claude._synthetic_authentication_fault
        if kind == "authentication"
        else claude._synthetic_connection_fault
    )
    detected = detector(raw, parsed, 1)
    assert (detected is not None) == (fault is None)


def test_repeated_expiry_exhausts_the_existing_one_retry_budget(tmp_path: Path):
    harness = Harness(tmp_path, [rejection(), rejection(), SuccessfulProcess()])
    try:
        result = harness.run()
        assert result.classification == "request_failure"
        assert result.environment_actions == 0
        assert result.model_attempts == result.provider_wire_requests == 2
        assert len(harness.calls) == 2
        events = harness.journal.events()
        retries = [event for event in events if event.kind == "retryable_authentication"]
        assert [event.payload["next_attempt_permitted"] for event in retries] == [True, False]
        sealed = next(event for event in events if event.kind == "sealed_unsuccessful_result")
        assert sealed.payload["failure_code"] == "authentication_retry_exhausted"
    finally:
        harness.close()


def test_authentication_retry_cannot_exceed_global_model_and_wire_caps(tmp_path: Path):
    harness = Harness(tmp_path, [rejection(), SuccessfulProcess()], attempts=1)
    try:
        with pytest.raises(CallCapExceededError, match="model-attempt cap"):
            harness.run()
        assert len(harness.calls) == 1
        assert harness.journal.call_counts() == (1, 0)
        assert harness.gate.active == 0
        assert not any(event.kind == "dispatch_started" for event in harness.journal.events())
    finally:
        harness.close()


@pytest.mark.parametrize(
    "case",
    [
        "not_expired",
        "generic_401",
        "model_output",
        "wrong_error",
        "positive_usage",
        "positive_cost",
        "model_usage",
        "tools",
        "mcp",
        "extra_event",
        "content_mismatch",
        "redacted_stdout",
        "redacted_stderr",
        "refused_egress",
        "not_stopped",
        "no_opt_in",
    ],
)
def test_ambiguous_or_unsafe_rejection_never_acquires_authentication_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
):
    events = deepcopy(expired_events())
    if case in {"not_expired", "generic_401"}:
        text = (
            "API Error: 401 Unauthorized"
            if case == "generic_401"
            else events[2]["result"].replace(EXPIRED_MESSAGE, "Invalid bearer credential.")
        )
        events[2]["result"] = text
        events[1]["message"]["content"][0]["text"] = text
    elif case == "model_output":
        events[1]["message"]["model"] = claude.MODEL
    elif case == "wrong_error":
        events[1]["error"] = "authentication_error"
    elif case == "positive_usage":
        events[2]["usage"]["input_tokens"] = 1
    elif case == "positive_cost":
        events[2]["total_cost_usd"] = 0.01
    elif case == "model_usage":
        events[2]["modelUsage"] = {claude.MODEL: {"inputTokens": 0, "outputTokens": 0}}
    elif case == "tools":
        events[0]["tools"] = ["Bash"]
    elif case == "mcp":
        events[0]["mcp_servers"] = [{"name": "unapproved"}]
    elif case == "extra_event":
        events.insert(1, {"type": "unapproved"})
    elif case == "content_mismatch":
        events[1]["message"]["content"][0]["text"] = "An unrelated response"
    elif case == "redacted_stdout":
        events[0]["diagnostic"] = credential_shaped_value()
    process = rejection(events)
    if case == "redacted_stderr":
        original = process.communicate
        process.communicate = lambda *args, **kwargs: (
            original(*args, **kwargs)[0],
            credential_shaped_value(),
        )
    harness = Harness(tmp_path, [process, SuccessfulProcess()], recovery=case != "no_opt_in")
    if case == "refused_egress":
        monkeypatch.setattr(
            harness.transport, "_egress_summary", lambda _launch: {"refused_count": 1}
        )
    if case == "not_stopped":
        # A live process cannot authorize a new request even with a valid error envelope.
        process.returncode = None
        process.poll = lambda: None
    try:
        result = harness.run()
        assert len(harness.calls) == 1
        assert result.environment_actions == 0
        assert "require_login" not in harness.gate.markers
        assert not any(
            event.kind == "retryable_authentication" for event in harness.journal.events()
        )
    finally:
        harness.close()


def test_unknown_post_send_fault_preserves_shared_ledger_block(tmp_path: Path):
    class UnknownProcess(SuccessfulProcess):
        def communicate(self, input=None, timeout=None):
            if input is not None:
                raise OSError("synthetic post-send failure")
            return "", "synthetic stopped process with unknown send outcome"

    harness = Harness(tmp_path, [UnknownProcess(), SuccessfulProcess()])
    try:
        result = harness.run()
        assert len(harness.calls) == 1
        assert result.environment_actions == 0
        assert harness.ledger.blocked
        assert len(harness.ledger.unresolved) == 1
        assert "require_login" not in harness.gate.markers
    finally:
        harness.close()


def test_reconciliation_retains_authentication_rejection_without_resending(tmp_path: Path):
    harness = Harness(tmp_path, [rejection(), SuccessfulProcess()])
    harness.runner.interrupt_after = "retryable_authentication"
    try:
        with pytest.raises(InjectedInterruption, match="retryable_authentication"):
            harness.run()
        assert len(harness.calls) == 1
        record = harness.transport.records[0]
        fault = record["cli_fault"]
        assert fault["cost_knowledge"] == CostKnowledge.ZERO.value
        assert fault["kind"] == CliFaultKind.AUTHENTICATION_EXPIRED.value
        # A second run's recovery is owned by the runner's durable recovery path;
        # the sealed retryable attempt is never silently issued by transport reconciliation.
        invocation = harness.invocations._connection.execute(
            "SELECT idempotency_key FROM invocations"
        ).fetchone()
        before = len(harness.calls)
        outcome = harness.transport.reconcile(idempotency_key=invocation[0], deadline_seconds=1)
        assert outcome.status == "authentication_required"
        assert len(harness.calls) == before
        harness.runner.interrupt_after = None
        recovered = harness.runner.recover_step(
            trial_id="auth-retry",
            step_index=0,
            task=generate_memory_task(5112),
            backend=FocusMemoryBackend(),
        )
        assert recovered["classification"] == "infrastructure_failure"
        assert recovered["reason"] == "retry_interrupted_before_next_attempt"
        assert len(harness.calls) == before
        assert harness.journal.call_counts() == (1, 0)
    finally:
        harness.close()


def test_owner_wait_precedes_reservation_and_request_deadline(tmp_path: Path):
    harness = Harness(tmp_path, [rejection(), SuccessfulProcess()])
    markers: list[tuple[str, int]] = []

    def owner_wait() -> None:
        markers.append(("owner_wait", harness.journal.call_counts()[0]))
        assert harness.gate.active == 0

    class ImmediateDeadline:
        def execute(
            self, call: Callable[[], object], *, timeout_seconds: float
        ) -> BoundedCallResult:
            markers.append(("deadline", harness.journal.call_counts()[0]))
            assert markers[-2][0] == "owner_wait"
            assert timeout_seconds == harness.manifest.request_deadline_seconds
            return BoundedCallResult(call(), False)

    harness.gate.on_begin = owner_wait
    harness.runner.deadline_executor = ImmediateDeadline()
    try:
        harness.run()
        assert markers == [("owner_wait", 0), ("deadline", 1), ("owner_wait", 1), ("deadline", 2)]
    finally:
        harness.close()


def test_connection_fault_and_expiry_share_one_bounded_retry(tmp_path: Path):
    harness = Harness(tmp_path, [ConnectionResetProcess(), rejection(), SuccessfulProcess()])
    try:
        result = harness.run()
        assert result.classification == "request_failure"
        assert result.environment_actions == 0
        assert result.model_attempts == result.provider_wire_requests == 2
        assert len(harness.calls) == 2
        retries = [
            event
            for event in harness.journal.events()
            if event.kind in {"retryable_transport_fault", "retryable_authentication"}
        ]
        assert [event.kind for event in retries] == [
            "retryable_transport_fault",
            "retryable_authentication",
        ]
        assert [event.payload["next_attempt_permitted"] for event in retries] == [True, False]
        assert not harness.ledger.blocked
        assert len(harness.ledger.unresolved) == 1
    finally:
        harness.close()
