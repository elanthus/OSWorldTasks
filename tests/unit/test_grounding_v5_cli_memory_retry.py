"""Bounded CLI retries and unfinished-only continuation selection."""

import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from test_grounding_v5_claude_code_transport import SuccessfulProcess
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
from pixelgym.grounding.v5.contracts import CallCaps
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from scripts.run_grounding_v5_cli_memory import unfinished_jobs

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
