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
        "action_type": 1, "x": 100, "y": 100, "key": 0,
    }
    assert response["content"] == text


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "```\n{}\n```", " \n```json\r\n{}\r\n```\n"])
def test_action_envelope_accepts_only_complete_json(wrapper: str) -> None:
    text = '{"action_type":1,"x":100,"y":100,"key":0}'
    assert policy._decode_action_content(wrapper.format(text)) == json.loads(text)


@pytest.mark.parametrize("content", [
    'Here is the action: ```json\n{}\n```',
    '```json\n{}\n``` extra',
    '```json\n{}\n```\n```json\n{}\n```',
    '```python\n{}\n```',
    '```json {} ```',
    '{} {}',
    '{"x":1,"x":2}',
    '```json\n{"x":1,"x":2}\n```',
])
def test_action_envelope_rejects_prose_multiple_objects_and_duplicate_fields(content: str) -> None:
    with pytest.raises(ValueError):
        policy._decode_action_content(content)
