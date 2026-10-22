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
                    "model": "claude-haiku-4-5-20251001",
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
                    "claude-haiku-4-5-20251001": {
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
        environment={
            "PATH": "/bin",
            "HOME": "/private/auth-home",
            "USER": "calibration-user",
            "SECRET": "blocked",
        },
        process_factory=process_factory,
    )
    return transport, journal


def test_claude_policy_manifest_emits_declared_v3_sandbox_contract() -> None:
    manifest = policy.build_claude_policy_manifest(
        ROOT,
        code_revision="revision-test",
        runtime_identity=runtime_identity(),
        resolved_model="claude-haiku-4-5-20251001",
    )
    sandbox = manifest.to_dict()["sandbox"]

    assert sandbox["schema_version"] == "pixelgym-agent-v5-sandbox-v3"
    assert sandbox["probe_result"]["status"] == "not_run"
    assert sandbox["runtime_enforcement"]["mechanism_name"] == "not_bound_to_cli_launch"
    assert sandbox["runtime_enforcement"]["cli_restrictions_applied"] is False
    assert sandbox["runtime_enforcement"]["environment_allowlist_applied"] is False
    assert sandbox["runtime_enforcement"]["os_sandbox_applied"] is False
    assert sandbox["policy_claim"]["declared_unavailable_capabilities"] == []
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
        assert captured["environment"]["USER"] == "calibration-user"
        assert "SECRET" not in captured["environment"]
    finally:
        transport.close()
        journal.close()


def test_claude_child_launch_can_disable_internal_api_retries(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def factory(command: list[str], **kwargs: Any) -> SuccessfulProcess:
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return SuccessfulProcess()

    journal = policy.ClaudeInvocationJournal(tmp_path / "claude-invocations.sqlite")
    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
        invocation_journal=journal,
        runtime_identity=runtime_identity(),
        environment={"PATH": "/bin", "HOME": "/private/auth-home", "SECRET": "blocked"},
        process_factory=factory,
        api_retry_limit=0,
    )
    try:
        outcome = transport.send(
            request(),
            idempotency_key="sha256:claude-zero-api-retries",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "response"
        assert captured["environment"][policy.CLI_API_RETRY_ENVIRONMENT_VARIABLE] == "0"
        assert "SECRET" not in captured["environment"]
        record = journal.record("sha256:claude-zero-api-retries")
        assert record is not None
        enforcement = record["outcome"]["runtime_enforcement"]
        assert enforcement["environment_allowlist_applied"] is True
        assert (
            policy.CLI_API_RETRY_ENVIRONMENT_VARIABLE in (enforcement["environment_variable_names"])
        )
    finally:
        transport.close()
        journal.close()


def test_claude_child_strips_unbound_inherited_api_retry_limit(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def factory(command: list[str], **kwargs: Any) -> SuccessfulProcess:
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return SuccessfulProcess()

    journal = policy.ClaudeInvocationJournal(tmp_path / "claude-invocations.sqlite")
    transport = policy.ClaudeCodeTransport(
        ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
        invocation_journal=journal,
        runtime_identity=runtime_identity(),
        environment={
            "PATH": "/bin",
            "HOME": "/private/auth-home",
            policy.CLI_API_RETRY_ENVIRONMENT_VARIABLE: "7",
        },
        process_factory=factory,
    )
    try:
        outcome = transport.send(
            request(),
            idempotency_key="sha256:claude-strip-unbound-api-retries",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "response"
        assert policy.CLI_API_RETRY_ENVIRONMENT_VARIABLE not in captured["environment"]
    finally:
        transport.close()
        journal.close()


def test_claude_api_retry_limit_rejects_invalid_values(tmp_path: Path) -> None:
    journal = policy.ClaudeInvocationJournal(tmp_path / "claude-invocations.sqlite")
    with pytest.raises(ValueError, match="non-negative integer"):
        policy.ClaudeCodeTransport(
            ledger=policy.SubscriptionExemptLedger(Decimal("10.00"), Decimal("0.00")),
            invocation_journal=journal,
            runtime_identity=runtime_identity(),
            api_retry_limit=-1,
        )
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


def test_haiku_contract_pins_model_and_leaves_unsupported_effort_unset() -> None:
    command = policy.sanitized_command_contract()
    assert command[command.index("--model") + 1] == "claude-haiku-4-5-20251001"
    assert "--effort" not in command
    assert request()["model_reasoning_effort"] == "default"
    manifest = policy.build_claude_policy_manifest(
        ROOT,
        code_revision="revision-test",
        runtime_identity=runtime_identity(),
        resolved_model="claude-haiku-4-5-20251001",
    ).to_dict()
    assert manifest["context_limit"] == 200_000
    assert dict(manifest["inference_parameters"])["model_reasoning_effort"] == "default"


def test_haiku_successor_manifest_binds_zero_cli_api_retries() -> None:
    manifest = policy.build_claude_policy_manifest(
        ROOT,
        code_revision="revision-test",
        runtime_identity=runtime_identity(),
        resolved_model="claude-haiku-4-5-20251001",
        api_retry_limit=0,
    ).to_dict()
    inference = dict(manifest["inference_parameters"])
    assert inference["cli_api_retry_limit"] == "0"
    assert (
        inference["cli_api_retry_environment_variable"] == policy.CLI_API_RETRY_ENVIRONMENT_VARIABLE
    )


def test_api_retry_event_remains_a_fail_closed_policy_violation() -> None:
    stdout, _ = SuccessfulProcess().communicate()
    events = [json.loads(line) for line in stdout.splitlines()]
    events.insert(
        2,
        {
            "type": "system",
            "subtype": "api_retry",
            "attempt": 1,
            "max_retries": 10,
            "retry_delay_ms": 500,
            "error_status": None,
            "error": "unknown",
            "session_id": "synthetic-session",
            "uuid": "synthetic-event",
        },
    )
    parsed = policy._parse_stream("\n".join(json.dumps(event) for event in events) + "\n")
    assert "unauthorized_system_event:api_retry" in parsed.policy_violations


@pytest.mark.parametrize(
    "unexpected_model", ["claude-sonnet-5-20260801", "claude-haiku-4-5-20251001-other"]
)
def test_haiku_parser_rejects_a_different_model_snapshot(unexpected_model: str) -> None:
    stdout, _ = SuccessfulProcess().communicate()
    result = policy._parse_stream(stdout.replace("claude-haiku-4-5-20251001", unexpected_model))
    assert "resolved_model_mismatch" in result.policy_violations


def history_request() -> dict[str, Any]:
    return policy.ClaudeCodePolicy().build_request(
        policy.ClaudeCodePolicy().reset("Use the supplied chronological frames."),
        bytes([2]) * (policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3),
        screenshot_history=[bytes([1]) * (policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)],
    )


def test_claude_history_frames_are_sent_in_order_before_current(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class CapturingProcess(SuccessfulProcess):
        def communicate(
            self, input: str | None = None, timeout: float | None = None
        ) -> tuple[str, str]:
            captured["event"] = json.loads(input or "{}")
            return super().communicate(input, timeout)

    transport, journal = make_transport(tmp_path, lambda *_args, **_kwargs: CapturingProcess())
    value = history_request()
    try:
        outcome = transport.send(value, idempotency_key="sha256:history", deadline_seconds=125)
        assert outcome.status == "response"
        blocks = captured["event"]["message"]["content"]
        assert [block["source"]["data"] for block in blocks[:-1]] == [
            value["image_history"][0]["image_png_base64"],
            value["image_png_base64"],
        ]
        assert blocks[-1] == {"type": "text", "text": value["prompt"]}
    finally:
        transport.close()
        journal.close()


@pytest.mark.parametrize("fault", ["digest", "shape", "limit"])
def test_claude_invalid_history_fails_before_process_start(tmp_path: Path, fault: str) -> None:
    def forbidden_factory(*_args: Any, **_kwargs: Any) -> SuccessfulProcess:
        pytest.fail("invalid history must not launch a provider process")

    value = history_request()
    if fault == "digest":
        value["image_history"][0]["image_sha256"] = "sha256:wrong"
    elif fault == "shape":
        value["image_history"][0]["path"] = "/forbidden"
    else:
        value["image_history"] *= 32
    transport, journal = make_transport(tmp_path, forbidden_factory)
    try:
        outcome = transport.send(
            value, idempotency_key="sha256:invalid-history", deadline_seconds=125
        )
        assert outcome.status == "pre_send_failure"
    finally:
        transport.close()
        journal.close()


def extended_thinking_stream(
    text: str = '{"action_type":1,"x":100,"y":100,"key":0}',
) -> list[dict[str, Any]]:
    # Sanitized structure of the retained Haiku v2 response: two assistant
    # records share a single message/request identity and one final result.
    original = [json.loads(line) for line in SuccessfulProcess().communicate()[0].splitlines()]
    init = next(event for event in original if event["type"] == "system")
    result = next(event for event in original if event["type"] == "result")
    result["result"] = text
    records = [
        init,
        {
            "type": "system",
            "subtype": "thinking_tokens",
            "estimated_tokens": 1,
            "estimated_tokens_delta": 1,
        },
        {
            "type": "system",
            "subtype": "thinking_tokens",
            "estimated_tokens": 3,
            "estimated_tokens_delta": 2,
        },
        {
            "type": "assistant",
            "request_id": "request-test",
            "message": {
                "id": "message-test",
                "model": policy.MODEL,
                "content": [{"type": "thinking", "thinking": "sanitized"}],
            },
        },
        {
            "type": "assistant",
            "request_id": "request-test",
            "message": {
                "id": "message-test",
                "model": policy.MODEL,
                "content": [{"type": "text", "text": text}],
            },
        },
        result,
    ]
    for index, event in enumerate(records):
        event.update(session_id="session-test", uuid=f"event-{index}")
    return records


def parse_events(events: list[dict[str, Any]]) -> policy.ParsedClaudeStream:
    return policy._parse_stream("\n".join(json.dumps(event) for event in events))


def test_haiku_single_turn_thinking_telemetry_and_split_response_are_valid() -> None:
    events = extended_thinking_stream()
    parsed = parse_events(events)
    assert parsed.policy_violations == ()
    assert parsed.content == events[-1]["result"]
    assert parsed.event_counts == {"assistant": 2, "result": 1, "system": 3}


def ui_render_stream() -> list[dict[str, Any]]:
    # The four retained pilot streams all begin with this exact UI envelope,
    # before init, followed by thinking telemetry and one split assistant message.
    return [
        {
            "type": "system",
            "subtype": "ui_invalidate",
            "event": "ui.render",
            "uuid": "ui-event-test",
            "session_id": "session-test",
        },
        *extended_thinking_stream(),
    ]


def test_ui_render_preamble_preserves_response_usage_identity_and_event_count() -> None:
    events = ui_render_stream()
    parsed = parse_events(events)
    previous = parse_events(events[1:])
    assert parsed.policy_violations == ()
    assert parsed.content == previous.content
    assert parsed.usage == previous.usage
    assert parsed.resolved_model == previous.resolved_model == policy.MODEL
    assert parsed.informational_cost_usd == previous.informational_cost_usd
    assert parsed.event_counts == {"assistant": 2, "result": 1, "system": 4}


@pytest.mark.parametrize(
    "fault",
    [
        "event",
        "extra_field",
        "missing_uuid",
        "empty_uuid",
        "duplicate_uuid",
        "session",
        "empty_session",
        "duplicate_ui",
        "after_init",
        "after_result",
        "unknown_system",
        "model",
        "tools",
        "mcp",
        "retry",
        "tool_use",
        "result",
    ],
)
def test_ui_render_preamble_does_not_relax_stream_security(fault: str) -> None:
    events = ui_render_stream()
    if fault == "event":
        events[0]["event"] = "ui.other"
    elif fault == "extra_field":
        events[0]["tools"] = ["Read"]
    elif fault == "missing_uuid":
        del events[0]["uuid"]
    elif fault == "empty_uuid":
        events[0]["uuid"] = ""
    elif fault == "duplicate_uuid":
        events[0]["uuid"] = events[1]["uuid"]
    elif fault == "session":
        events[0]["session_id"] = "another-session"
    elif fault == "empty_session":
        events[0]["session_id"] = ""
    elif fault == "duplicate_ui":
        events.insert(1, dict(events[0], uuid="second-ui"))
    elif fault == "after_init":
        events.insert(1, events.pop(0))
    elif fault == "after_result":
        events.append(events.pop(0))
    elif fault == "unknown_system":
        events[0]["subtype"] = "unknown"
    elif fault == "model":
        events[4]["message"]["model"] = "another-model"
    elif fault == "tools":
        events[1]["tools"] = ["Read"]
    elif fault == "mcp":
        events[1]["mcp_servers"] = ["server"]
    elif fault == "retry":
        events[0]["subtype"] = "api_retry"
    elif fault == "tool_use":
        events[4]["message"]["content"] = [{"type": "tool_use"}]
    else:
        events[-1]["result"] = "changed"
    assert parse_events(events).policy_violations


def test_ui_render_preamble_is_accepted_by_the_journaled_transport(tmp_path: Path) -> None:
    class Process(SuccessfulProcess):
        def communicate(self, input=None, timeout=None):
            return "\n".join(json.dumps(event) for event in ui_render_stream()), ""

    transport, journal = make_transport(tmp_path, lambda *_a, **_kw: Process())
    try:
        outcome = transport.send(
            request(), idempotency_key="sha256:ui-render", deadline_seconds=125
        )
        assert outcome.status == "response"
        record = journal.record("sha256:ui-render")
        assert record is not None
        assert record["outcome"]["policy_violation"] == "none"
        assert record["outcome"]["usage_telemetry_status"] == "available"
        assert transport.subprocesses_closed
    finally:
        transport.close()
        journal.close()


@pytest.mark.parametrize(
    "fault",
    [
        "session",
        "message",
        "request",
        "uuid",
        "negative",
        "delta",
        "extra_field",
        "duplicate_text",
        "duplicate_result",
        "tool",
        "unknown_system",
        "changed_result",
    ],
)
def test_haiku_extended_stream_rejects_incoherent_or_extra_records(fault: str) -> None:
    events = extended_thinking_stream()
    if fault == "session":
        events[1]["session_id"] = "another-session"
    elif fault == "message":
        events[4]["message"]["id"] = "another-message"
    elif fault == "request":
        events[4]["request_id"] = "another-request"
    elif fault == "uuid":
        events[1]["uuid"] = events[0]["uuid"]
    elif fault == "negative":
        events[1]["estimated_tokens"] = -1
    elif fault == "delta":
        events[1]["estimated_tokens_delta"] = 2
    elif fault == "extra_field":
        events[1]["tools"] = ["Read"]
    elif fault == "duplicate_text":
        events[4]["message"]["content"] *= 2
    elif fault == "duplicate_result":
        events.append(events[-1])
    elif fault == "tool":
        events[3]["message"]["content"] = [{"type": "tool_use"}]
    elif fault == "unknown_system":
        events[1]["subtype"] = "unrecognized"
    else:
        events[-1]["result"] = "changed"
    assert parse_events(events).policy_violations


def test_haiku_fenced_json_adapter_preserves_raw_model_output() -> None:
    text = '```json\n{"action_type":1,"x":100,"y":100,"key":0}\n```'
    parsed = parse_events(extended_thinking_stream(text))
    assert parsed.policy_violations == ()
    assert parsed.content == text
    response = {
        "model": policy.MODEL,
        "content": parsed.content,
        "usage": {
            "auth_method": policy.AUTH_METHOD,
            "subscription_type": policy.SUBSCRIPTION_TYPE,
            "cli_version": policy.CLAUDE_CLI_VERSION,
            "command_contract_digest": policy.command_contract_digest(),
            "experiment_charge_usd": "0.00",
            "model_reasoning_effort": "default",
            "policy_violation": "none",
        },
    }
    assert policy.ClaudeCodePolicy().parse(json.dumps(response).encode(), b"{}") == {
        "action_type": 1,
        "x": 100,
        "y": 100,
        "key": 0,
    }
    assert response["content"] == text


@pytest.mark.parametrize(
    "wrapper", ["{}", "```json\n{}\n```", "```\n{}\n```", " \n```json\r\n{}\r\n```\n"]
)
def test_action_envelope_accepts_only_complete_json(wrapper: str) -> None:
    text = '{"action_type":1,"x":100,"y":100,"key":0}'
    assert policy._decode_action_content(wrapper.format(text)) == json.loads(text)


@pytest.mark.parametrize(
    "content",
    [
        "Here is the action: ```json\n{}\n```",
        "```json\n{}\n``` extra",
        "```json\n{}\n```\n```json\n{}\n```",
        "```python\n{}\n```",
        "```json {} ```",
        "{} {}",
        '{"x":1,"x":2}',
        '```json\n{"x":1,"x":2}\n```',
    ],
)
def test_action_envelope_rejects_prose_multiple_objects_and_duplicate_fields(content: str) -> None:
    with pytest.raises(ValueError):
        policy._decode_action_content(content)


def test_claude_policy_manifest_declares_v4_sandboxed_launch_and_changes_identity() -> None:
    from pixelgym.grounding.v5.egress_proxy import egress_allowlist_digest
    from pixelgym.grounding.v5.os_sandbox import cli_profile_template_digest

    plain = policy.build_claude_policy_manifest(
        ROOT,
        code_revision="revision-test",
        runtime_identity=runtime_identity(),
        resolved_model="claude-haiku-4-5-20251001",
        api_retry_limit=0,
    )
    sandboxed = policy.build_claude_policy_manifest(
        ROOT,
        code_revision="revision-test",
        runtime_identity=runtime_identity(),
        resolved_model="claude-haiku-4-5-20251001",
        api_retry_limit=0,
        sandboxed_launch=True,
    )
    sandbox = sandboxed.to_dict()["sandbox"]
    enforcement = sandbox["runtime_enforcement"]
    assert sandbox["schema_version"] == "pixelgym-agent-v5-sandbox-v4"
    assert enforcement["mechanism_name"] == "darwin_sandbox_exec_cli_launch"
    assert enforcement["os_sandbox_applied"] is True
    assert enforcement["cli_restrictions_applied"] is True
    assert enforcement["environment_allowlist_applied"] is True
    assert enforcement["profile_template_digest"] == cli_profile_template_digest()
    assert enforcement["profile_digest"] is None  # bound per launch, not at plan time
    assert enforcement["egress_allowlist_digest"] == egress_allowlist_digest(
        policy.DEFAULT_EGRESS_TARGETS
    )
    assert enforcement["environment_variable_names"] == list(
        policy.sandboxed_environment_variable_names(0)
    )
    assert "CLAUDE_CODE_OAUTH_TOKEN" in enforcement["environment_variable_names"]
    assert "USER" not in enforcement["environment_variable_names"]
    parameters = dict(sandboxed.inference_parameters)
    assert parameters["os_sandbox_mechanism"] == "darwin_sandbox_exec_cli_launch"
    assert parameters["egress_targets"] == "api.anthropic.com:443"
    assert parameters["auth_route"] == "oauth_token_environment"
    assert sandboxed.policy_id != plain.policy_id
    assert plain.to_dict()["sandbox"]["schema_version"] == "pixelgym-agent-v5-sandbox-v3"


# --- Approved routes ------------------------------------------------------------------


def _identity(**overrides: str) -> policy.ClaudeRuntimeIdentity:
    fields = {
        "cli_version": policy.CLAUDE_CLI_VERSION,
        "auth_method": policy.AUTH_METHOD,
        "subscription_type": policy.SUBSCRIPTION_TYPE,
        "requested_model": policy.MODEL,
        "reasoning_effort": policy.MODEL_REASONING_EFFORT,
        "help_sha256": "sha256:help",
    }
    fields.update(overrides)
    return policy.ClaudeRuntimeIdentity(**fields)


def test_identity_accepts_only_the_two_approved_routes() -> None:
    frozen = _identity()
    assert frozen.route == policy.FROZEN_ROUTE
    replication = _identity(cli_version=policy.REPLICATION_CLI_VERSION, auth_method="oauth_token")
    assert replication.route == policy.REPLICATION_ROUTE
    with pytest.raises(ValueError, match="version differs"):
        _identity(cli_version="9.9.9 (Claude Code)")
    # The frozen CLI with the token route, and the new CLI with the Keychain route, are
    # not approved combinations.
    with pytest.raises(ValueError, match="approved Max subscription authentication"):
        _identity(auth_method="oauth_token")
    with pytest.raises(ValueError, match="approved Max subscription authentication"):
        _identity(cli_version=policy.REPLICATION_CLI_VERSION)
    with pytest.raises(ValueError, match="approved Max subscription authentication"):
        _identity(
            cli_version=policy.REPLICATION_CLI_VERSION,
            auth_method="oauth_token",
            subscription_type="pro",
        )


def test_route_probe_binds_the_installed_cli_without_a_login() -> None:
    import subprocess

    help_text = (
        "--print --model --output-format --input-format --tools --disallowedTools "
        "--permission-mode --setting-sources --safe-mode --disable-slash-commands "
        "--strict-mcp-config --mcp-config --no-chrome --no-session-persistence "
        "--system-prompt --exclude-dynamic-system-prompt-sections"
    )
    calls: list[tuple[str, ...]] = []

    def run(
        command: tuple[str, ...], environment: object = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        if command[1] == "--version":
            return subprocess.CompletedProcess(
                command, 0, policy.REPLICATION_CLI_VERSION + "\n", ""
            )
        if command[1] == "--help":
            return subprocess.CompletedProcess(command, 0, help_text, "")
        raise AssertionError(f"unexpected probe command {command}")

    identity = policy.probe_claude_route(policy.REPLICATION_ROUTE, run=run)
    assert identity.route == policy.REPLICATION_ROUTE
    assert identity.help_sha256.startswith("sha256:")
    # No auth status call: the route is declared, to be confirmed before execution.
    assert [c[1] for c in calls] == ["--version", "--help"]
    with pytest.raises(ValueError, match="differs from the declared route"):
        policy.probe_claude_route(policy.FROZEN_ROUTE, run=run)
    with pytest.raises(ValueError, match="route is not approved"):
        policy.probe_claude_route(
            policy.ClaudeRoute("9.9.9 (Claude Code)", "oauth_token", "max"), run=run
        )


def test_replication_route_manifest_changes_identity_and_records_the_route() -> None:
    frozen = policy.build_claude_policy_manifest(
        ROOT,
        code_revision="revision-test",
        runtime_identity=_identity(),
        resolved_model="claude-haiku-4-5-20251001",
        api_retry_limit=0,
    )
    replication = policy.build_claude_policy_manifest(
        ROOT,
        code_revision="revision-test",
        runtime_identity=_identity(
            cli_version=policy.REPLICATION_CLI_VERSION, auth_method="oauth_token"
        ),
        resolved_model="claude-haiku-4-5-20251001",
        api_retry_limit=0,
        sandboxed_launch=True,
    )
    parameters = dict(replication.inference_parameters)
    assert parameters["cli_version"] == policy.REPLICATION_CLI_VERSION
    assert parameters["auth_method"] == "oauth_token"
    assert dict(frozen.inference_parameters)["cli_version"] == policy.CLAUDE_CLI_VERSION
    assert replication.policy_id != frozen.policy_id
