"""Bounded CLI retries and unfinished-only continuation selection."""

import json
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from test_grounding_v5_claude_code_transport import (
    ConnectionResetProcess,
    RawExitProcess,
    SuccessfulProcess,
    credential_shaped_value,
)
from test_grounding_v5_claude_code_transport import runtime_identity as claude_identity
from test_grounding_v5_codex_cli_policy import FakeProcess, cli_stream
from test_grounding_v5_codex_cli_policy import runtime_identity as codex_identity

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5 import codex_cli_policy as codex
from pixelgym.grounding.v5.cli_memory_calibration import (
    CliMemoryPolicy,
    CliMemoryRunner,
    build_memory_manifest,
)
from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from scripts.run_grounding_v5_cli_memory import (
    fresh_jobs,
    unfinished_jobs,
    validate_fresh_approval,
    validate_fresh_runtime_surface,
)

ROOT = Path(__file__).parents[2]


class UnstoppableProcess(FakeProcess):
    def communicate(self, *args, **kwargs):
        raise subprocess.TimeoutExpired("cli", 90)

    def terminate(self):
        pass

    def kill(self):
        pass


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize(
    "case,expected_calls,expected_actions",
    [
        ("timeout_success", 2, 1),
        ("timeout_timeout", 2, 0),
        ("nonzero_exit", 1, 0),
        ("unstoppable", 1, 0),
    ],
)
def test_only_one_retry_after_stopped_timeout(
    tmp_path, provider, case, expected_calls, expected_actions
):
    def timeout():
        return FakeProcess("", failure=subprocess.TimeoutExpired("cli", 90))

    good = FakeProcess(cli_stream()) if provider == "codex" else SuccessfulProcess()
    first = timeout()
    if case == "nonzero_exit":
        first = FakeProcess("", returncode=1)
    if case == "unstoppable":
        first = UnstoppableProcess("", failure=subprocess.TimeoutExpired("cli", 90))
    processes = [first, timeout() if case == "timeout_timeout" else good]
    calls = []

    def factory(*args, **kwargs):
        calls.append(1)
        return processes[len(calls) - 1]

    ledger = codex.SubscriptionExemptLedger(Decimal(1), Decimal(0))
    if provider == "codex":
        invocations = codex.CodexCliInvocationJournal(tmp_path / "invocations.sqlite")
        base = codex.build_codex_cli_policy_manifest(
            ROOT, code_revision="test", runtime_identity=codex_identity()
        )
        transport = codex.CodexCliTransport(
            ledger=ledger,
            invocation_journal=invocations,
            runtime_identity=codex_identity(),
            process_factory=factory,
            allow_timeout_retry=True,
        )
        policy = codex.CodexCliPolicy()
    else:
        invocations = claude.ClaudeInvocationJournal(tmp_path / "invocations.sqlite")
        base = claude.build_claude_policy_manifest(
            ROOT,
            code_revision="test",
            runtime_identity=claude_identity(),
            resolved_model=claude.MODEL,
        )
        transport = claude.ClaudeCodeTransport(
            ledger=ledger,
            invocation_journal=invocations,
            runtime_identity=claude_identity(),
            process_factory=factory,
            allow_timeout_retry=True,
        )
        policy = claude.ClaudeCodePolicy()
    journal = V5AttemptJournal(tmp_path / "attempts.sqlite")
    try:
        result = CliMemoryRunner(
            journal=journal,
            manifest=build_memory_manifest(ROOT, base, retain_screenshots=True),
            policy=CliMemoryPolicy(policy, retain_screenshots=True),
            transport=transport,
            approved_caps=CallCaps(1, 2, 0, 2),
        ).run(
            trial_id="retry-test",
            task=generate_memory_task(5112),
            backend=FocusMemoryBackend(),
            action_limit=1,
        )
        assert len(calls) == expected_calls, (
            result,
            ledger.blocked,
            transport.records,
            [e.payload for e in journal.events() if e.kind == "retryable_transport_fault"],
        )
        assert result.environment_actions == expected_actions
        assert result.model_attempts == expected_calls
        if case.startswith("timeout"):
            assert len(ledger.unresolved) == (1 if case == "timeout_success" else 2)
            assert not ledger.blocked
            events = [e for e in journal.events() if e.kind == "retryable_transport_fault"]
            assert events[0].payload["next_attempt_permitted"] is True
            if case == "timeout_timeout":
                assert events[1].payload["next_attempt_permitted"] is False
        if case == "unstoppable":
            assert ledger.blocked
    finally:
        transport.close()
        invocations.close()
        journal.close()


def test_continuation_preserves_terminal_failures_and_retries_only_unfinished():
    jobs = [
        {"seed": i, "mode": "history", "task_id": str(i), "task_digest": str(i), "action_limit": 28}
        for i in range(6)
    ]
    rows = [
        {**job, "classification": kind}
        for job, kind in zip(
            jobs,
            [
                "success_termination",
                "step_limit_truncation",
                "invalid_output",
                "phase_time_stop",
                "infrastructure_failure",
            ],
        )
    ]
    assert unfinished_jobs(jobs, {"results": rows}) == jobs[3:]
    with pytest.raises(ValueError, match="duplicate"):
        unfinished_jobs(jobs, {"results": rows + [rows[0]]})
    rows[0]["task_digest"] = "changed"
    with pytest.raises(ValueError, match="differs"):
        unfinished_jobs(jobs, {"results": rows})


def test_fresh_cohort_keeps_every_assignment_and_rejects_duplicates():
    jobs = [
        {"seed": 5112, "mode": "history"},
        {"seed": 5112, "mode": "stateless"},
    ]
    assert fresh_jobs(jobs) == jobs
    assert fresh_jobs(jobs) is not jobs
    with pytest.raises(ValueError, match="duplicate"):
        fresh_jobs(jobs + [jobs[0]])


def test_fresh_runtime_surface_rejects_changed_measurement_code(monkeypatch):
    calls = []

    def fake_check_output(command, cwd):
        calls.append((command, cwd))
        if command[2] == "HEAD:pixelgym/grounding/v5/memory_calibration.py":
            return b"changed"
        return b"frozen"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    with pytest.raises(ValueError, match="runtime source differs"):
        validate_fresh_runtime_surface(ROOT, "HEAD")
    assert any(
        command[-1] == "HEAD:pixelgym/grounding/v5/memory_calibration.py" for command, _cwd in calls
    )


def test_fresh_approval_must_match_every_exact_cap(tmp_path):
    caps = CallCaps(2862, 5724, 0, 5724)
    plan = {"maximum_elapsed_seconds": 43200, "jobs": ["bound"]}
    approval = {
        "schema_version": "pixelgym-pr196-haiku-cli-replication-approval-v1",
        "execution_plan_digest": content_digest(plan),
        "approved_environment_action_cap": 2862,
        "approved_model_attempt_cap": 5724,
        "approved_provider_control_request_cap": 0,
        "approved_provider_wire_request_cap": 5724,
        "approved_runtime_seconds": 43200,
        "incremental_experiment_charge_cap_usd": "0.00",
        "owner_approved": True,
    }
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval))
    assert validate_fresh_approval(path, plan, caps) == approval
    approval["approved_model_attempt_cap"] -= 1
    path.write_text(json.dumps(approval))
    with pytest.raises(ValueError, match="differs"):
        validate_fresh_approval(path, plan, caps)


def synthetic_connection_error(
    *, error="server_error", text="API Error: Connection dropped (ECONNRESET)"
):
    return [
        {"type": "system", "subtype": "init", "tools": [], "mcp_servers": []},
        {
            "type": "assistant",
            "error": error,
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


@pytest.mark.parametrize(
    "case,expected_calls,expected_actions",
    [
        ("reset_success", 2, 1),
        ("reset_reset", 2, 0),
        ("process_reset", 2, 1),
        ("auth_error", 1, 0),
        ("tool_error", 1, 0),
        ("no_opt_in", 1, 0),
        ("model_error_text", 1, 0),
        ("diagnostic_only", 1, 0),
        ("redacted_diagnostic", 1, 0),
    ],
)
def test_counted_connection_retry_preserves_request_and_only_fails_if_unrecovered(
    tmp_path, case, expected_calls, expected_actions
):
    events = synthetic_connection_error()
    if case == "auth_error":
        events = synthetic_connection_error(
            error="authentication_error", text="API Error: 401 Unauthorized"
        )
    if case == "tool_error":
        events[0]["tools"] = ["Bash"]
    if case == "model_error_text":
        events[1]["message"]["model"] = claude.MODEL
    first = RawExitProcess("\n".join(map(json.dumps, events)), returncode=1)
    if case == "process_reset":
        first = ConnectionResetProcess()
    if case in {"diagnostic_only", "redacted_diagnostic"}:
        first = SuccessfulProcess(
            stderr="ECONNRESET",
            diagnostic=credential_shaped_value() if case == "redacted_diagnostic" else None,
        )
    processes = [first, first if case == "reset_reset" else SuccessfulProcess()]
    calls = []
    inputs = []

    def factory(*args, **kwargs):
        assert kwargs["env"]["CLAUDE_CODE_MAX_RETRIES"] == "0"
        process = processes[len(calls)]
        calls.append(kwargs)
        original = process.communicate

        def communicate(input=None, timeout=None):
            if input is not None:
                inputs.append(input)
            return original(input=input, timeout=timeout)

        process.communicate = communicate
        return process

    enabled = case != "no_opt_in"
    ledger = codex.SubscriptionExemptLedger(Decimal(1), Decimal(0))
    invocations = claude.ClaudeInvocationJournal(tmp_path / "invocations.sqlite")
    transport = claude.ClaudeCodeTransport(
        ledger=ledger,
        invocation_journal=invocations,
        runtime_identity=claude_identity(),
        expected_resolved_model=claude.MODEL,
        process_factory=factory,
        allow_timeout_retry=True,
        allow_connection_retry=enabled,
        api_retry_limit=0,
    )
    base = claude.build_claude_policy_manifest(
        ROOT,
        code_revision="test",
        runtime_identity=claude_identity(),
        resolved_model=claude.MODEL,
        api_retry_limit=0,
    )
    journal = V5AttemptJournal(tmp_path / "attempts.sqlite")
    try:
        result = CliMemoryRunner(
            journal=journal,
            manifest=build_memory_manifest(
                ROOT, base, retain_screenshots=True, allow_connection_retry=enabled
            ),
            policy=CliMemoryPolicy(claude.ClaudeCodePolicy(), retain_screenshots=True),
            transport=transport,
            approved_caps=CallCaps(1, 2, 0, 2),
        ).run(
            trial_id="connection-retry",
            task=generate_memory_task(5112),
            backend=FocusMemoryBackend(),
            action_limit=1,
        )
        assert len(calls) == expected_calls
        assert result.model_attempts == expected_calls
        assert result.provider_wire_requests == expected_calls
        assert result.environment_actions == expected_actions
        assert transport.subprocesses_closed
        if expected_actions:
            assert result.classification == "pilot_action_limit"
            assert inputs[0] == inputs[1]
            assert not any(e.kind == "sealed_unsuccessful_result" for e in journal.events())
        if case == "reset_reset":
            assert result.classification == "infrastructure_failure"
        if case.startswith("reset") or case == "process_reset":
            faults = [e for e in journal.events() if e.kind == "retryable_transport_fault"]
            assert faults[0].payload["next_attempt_permitted"] is True
            assert faults[0].payload["cli_fault"]["kind"] == "connection_reset"
            assert len({r["idempotency_key_digest"] for r in transport.records}) == expected_calls
    finally:
        transport.close()
        invocations.close()
        journal.close()


def test_connection_retry_requires_internal_retries_disabled(tmp_path):
    with pytest.raises(ValueError, match="zero internal CLI retries"):
        claude.ClaudeCodeTransport(
            ledger=codex.SubscriptionExemptLedger(Decimal(1), Decimal(0)),
            invocation_journal=claude.ClaudeInvocationJournal(tmp_path / "invocations.sqlite"),
            runtime_identity=claude_identity(),
            allow_connection_retry=True,
        )
