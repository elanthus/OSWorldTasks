"""Opt-in, no-cost check of the real Claude Code CLI under the v5 kernel sandbox.

Requires Darwin, ``sandbox-exec``, an installed ``claude`` binary, and
``PIXELGYM_RUN_V5_CLI_SANDBOX_TESTS=1``. Uses a placeholder token and a proxy that
refuses every target, so no request can reach a provider and nothing is charged.
"""

from __future__ import annotations

import os
import platform
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import claude_code_policy as policy
from pixelgym.grounding.v5.cli_sandbox_probe import run_cli_sandbox_probe

pytestmark = [
    pytest.mark.v5_cli_sandbox_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_V5_CLI_SANDBOX_TESTS") != "1"
        or platform.system() != "Darwin"
        or shutil.which("claude") is None,
        reason="set PIXELGYM_RUN_V5_CLI_SANDBOX_TESTS=1 on Darwin with claude installed",
    ),
]


def _placeholder_token() -> str:
    return "".join(("s", "k", "-ant-oat01-placeholder-", "0" * 24))


def _executable() -> Path:
    located = shutil.which("claude")
    assert located is not None
    return Path(located).resolve()


def test_real_cli_under_the_profile_uses_the_token_route_and_only_the_proxy() -> None:
    result = run_cli_sandbox_probe(
        cli_executable=_executable(), placeholder_token=_placeholder_token()
    )
    assert result.auth_logged_in and result.auth_method == "oauth_token"
    assert result.proxied_targets == ("api.anthropic.com:443",)
    assert result.proxied_refused_count >= 1
    assert result.proxied_result_marker == "ERR_PROXY_TUNNEL"
    assert result.direct_targets_seen == 0
    assert result.direct_result_marker in {"ENOTFOUND", "EAI_AGAIN"}
    assert result.passed


def test_real_transport_sandboxed_launch_records_refused_provider_egress(tmp_path: Path) -> None:
    """The full transport path with the real CLI and a proxy that refuses the provider."""

    executable = _executable()
    launch = policy.ClaudeSandboxLaunch(
        cli_executable=executable,
        cli_root=executable.parent,
        scratch_root=policy.cli_scratch_root(os.getuid()),
        oauth_token_source=_placeholder_token,
        egress_targets=("refused.invalid:443",),
    )
    identity = policy.ClaudeRuntimeIdentity(
        cli_version=policy.CLAUDE_CLI_VERSION,
        # The frozen identity constant; the token route is a successor freeze's concern.
        auth_method=policy.AUTH_METHOD,
        subscription_type=policy.SUBSCRIPTION_TYPE,
        requested_model=policy.MODEL,
        reasoning_effort=policy.MODEL_REASONING_EFFORT,
        help_sha256="sha256:integration",
    )
    journal = policy.ClaudeInvocationJournal(tmp_path / "invocations.sqlite")
    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("1.00"), Decimal("0.00")),
        invocation_journal=journal,
        runtime_identity=identity,
        expected_resolved_model=policy.MODEL,
        api_retry_limit=0,
        sandbox_launch=launch,
    )
    claude_policy = policy.ClaudeCodePolicy()
    screenshot = bytes(policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)
    request = claude_policy.build_request(
        claude_policy.reset("Complete the visible task."), screenshot
    )
    try:
        outcome = transport.send(
            request,
            idempotency_key="sha256:cli-sandbox-integration",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )
        assert outcome.status != "response"
        [record] = transport.records
        enforcement = record["runtime_enforcement"]
        assert enforcement["mechanism_name"] == "darwin_sandbox_exec_cli_launch"
        assert enforcement["os_sandbox_applied"] is True
        egress = record["egress_attempts"]
        assert egress["allowed_count"] == 0
        assert egress["refused_count"] >= 1
        assert egress["refused_targets"] == ["api.anthropic.com:443"]
        assert _placeholder_token() not in str(record)
    finally:
        transport.close()
        journal.close()
