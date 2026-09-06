from __future__ import annotations

import hashlib
import json
import signal
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5 import codex_cli_policy as policy
from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import CallCaps
from pixelgym.grounding.v5.evidence import V5EvidenceStore, validate_credential_free
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.runner import V5Runner
from pixelgym.grounding.v5.sandbox import environment_allowlist_digest

ROOT = Path(__file__).parents[2]


def runtime_identity() -> policy.CodexRuntimeIdentity:
    return policy.CodexRuntimeIdentity(
        cli_version=policy.CODEX_CLI_VERSION,
        authentication_mode=policy.AUTH_MODE,
        model=policy.MODEL,
        model_catalog_comp_hash=policy.MODEL_CATALOG_COMP_HASH,
        supported_reasoning_efforts=("low", "medium"),
        input_modalities=("text", "image"),
        context_window_tokens=policy.MODEL_CONTEXT_WINDOW_TOKENS,
        exec_help_sha256="sha256:help",
        feature_inventory_sha256="sha256:features",
        configuration_preflight_validated=True,
    )


def cli_stream(
    action: dict[str, int] | None = None,
    *,
    tool_item_type: str | None = None,
    diagnostic_message: str | None = None,
    diagnostic_count: int = 1,
    include_usage: bool = True,
) -> str:
    value = action or {"action_type": 1, "x": 100, "y": 100, "key": 0}
    events: list[dict[str, Any]] = [
        {"type": "thread.started", "thread_id": "private-thread-id"},
    ]
    if diagnostic_message is not None:
        events.extend(
            {
                "type": "item.completed",
                "item": {
                    "id": f"diagnostic-{index}",
                    "type": "error",
                    "message": diagnostic_message,
                },
            }
            for index in range(diagnostic_count)
        )
    events.append({"type": "turn.started"})
    if tool_item_type is not None:
        events.append(
            {
                "type": "item.started",
                "item": {"id": "tool-1", "type": tool_item_type},
            }
        )
    events.append(
        {
            "type": "item.completed",
            "item": {
                "id": "message-1",
                "type": "agent_message",
                "text": json.dumps(value, separators=(",", ":")),
            },
        }
    )
    if include_usage:
        events.append(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 500,
                    "output_tokens": 100,
                    "reasoning_output_tokens": 20,
                },
            }
        )
    return "\n".join(json.dumps(event, separators=(",", ":")) for event in events) + "\n"


class FakeProcess:
    def __init__(
        self,
        stdout: str,
        *,
        stderr: str = "",
        failure: BaseException | None = None,
        pid: int = 900_001,
    ) -> None:
        self.pid = pid
        self.returncode: int | None = 0 if failure is None else None
        self.stdout = stdout
        self.stderr = stderr
        self.failure = failure
        self.communicate_calls = 0
        self.terminated = False
        self.killed = False

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        del input, timeout
        self.communicate_calls += 1
        if self.communicate_calls == 1 and self.failure is not None:
            raise self.failure
        return self.stdout, self.stderr

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -signal.SIGTERM

    def kill(self) -> None:
        self.killed = True
        self.returncode = -signal.SIGKILL


def make_transport(
    tmp_path: Path,
    process: FakeProcess,
    *,
    prior: Decimal = Decimal("4.778164718"),
) -> tuple[
    policy.CodexCliTransport,
    policy.SubscriptionExemptLedger,
    policy.CodexCliInvocationJournal,
    dict[str, Any],
]:
    captured: dict[str, Any] = {}

    def factory(command: list[str], **kwargs: Any) -> FakeProcess:
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        captured["cwd_was_empty"] = not any(Path(kwargs["cwd"]).iterdir())
        captured["environment"] = kwargs["env"]
        return process

    invocation_journal = policy.CodexCliInvocationJournal(tmp_path / "invocations.sqlite")
    ledger = policy.SubscriptionExemptLedger(Decimal("10.00"), prior)
    transport = policy.CodexCliTransport(
        ledger=ledger,
        invocation_journal=invocation_journal,
        runtime_identity=runtime_identity(),
        environment={"PATH": "/bin", "HOME": "/private/auth-home", "SECRET": "blocked"},
        process_factory=factory,
    )
    return transport, ledger, invocation_journal, captured


def request() -> dict[str, Any]:
    screenshot = bytes(policy.SCREEN_WIDTH * policy.SCREEN_HEIGHT * 3)
    return policy.CodexCliPolicy().build_request(
        policy.CodexCliPolicy().reset("Complete the visible task."),
        screenshot,
    )


def credential_shaped_value() -> str:
    """Build a detector fixture without retaining credential material in source."""

    return "".join(("s", "k", "-", "synthetic", "0" * 16))


def cli_stream_with_diagnostic(value: str) -> str:
    events = [json.loads(line) for line in cli_stream().splitlines()]
    events[0]["diagnostic"] = value
    return "\n".join(json.dumps(event, separators=(",", ":")) for event in events) + "\n"


def test_command_contract_disables_tools_context_and_retries() -> None:
    contract = policy.sanitized_command_contract()
    joined = " ".join(contract)

    assert contract[:4] == ("codex", "exec", "--model", "gpt-5.6-luna")
    assert 'model_reasoning_effort="low"' in contract
    assert 'web_search="disabled"' in contract
    assert f'model_provider="{policy.MODEL_PROVIDER_ID}"' in contract
    assert f"model_providers.{policy.MODEL_PROVIDER_ID}.requires_openai_auth=true" in contract
    assert f"model_providers.{policy.MODEL_PROVIDER_ID}.supports_websockets=false" in contract
    assert f"model_providers.{policy.MODEL_PROVIDER_ID}.request_max_retries=0" in contract
    assert f"model_providers.{policy.MODEL_PROVIDER_ID}.stream_max_retries=0" in contract
    for feature in (
        "shell_tool",
        "apps",
        "plugins",
        "skill_search",
        "multi_agent",
        "computer_use",
        "in_app_browser",
        "view_image",
        "workspace_dependencies",
    ):
        index = contract.index(feature)
        assert contract[index - 1] == "--disable"
    assert "--ephemeral" in contract
    assert "--ignore-user-config" in contract
    assert "--ignore-rules" in contract
    assert "--sandbox read-only" in joined
    assert "--yolo" not in contract
    assert "dangerously-bypass" not in joined
    assert "tools.view_image" not in joined
    assert str(ROOT) not in joined

    luna_medium = policy.sanitized_command_contract(policy.LUNA_MEDIUM)
    assert luna_medium[:4] == ("codex", "exec", "--model", "gpt-5.6-luna")
    assert 'model_reasoning_effort="medium"' in luna_medium
    terra = policy.sanitized_command_contract(policy.TERRA_MEDIUM)
    assert terra[:4] == ("codex", "exec", "--model", "gpt-5.6-terra")
    assert 'model_reasoning_effort="medium"' in terra
    assert policy.command_contract_digest(policy.LUNA_MEDIUM) != (
        policy.command_contract_digest(policy.TERRA_MEDIUM)
    )


def test_codex_policy_manifest_emits_declared_v3_sandbox_contract() -> None:
    manifest = policy.build_codex_cli_policy_manifest(
        ROOT,
        code_revision="revision-test",
        runtime_identity=runtime_identity(),
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


def test_successful_invocation_is_isolated_schema_constrained_and_cost_accounted(
    tmp_path: Path,
) -> None:
    process = FakeProcess(cli_stream())
    transport, ledger, invocation_journal, captured = make_transport(tmp_path, process)
    try:
        outcome = transport.send(
            request(),
            idempotency_key="sha256:one",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )
        assert outcome.status == "response"
        assert outcome.response is not None
        assert policy.CodexCliPolicy().parse(
            policy.canonical_json_bytes(outcome.response), b"{}"
        ) == {"action_type": 1, "x": 100, "y": 100, "key": 0}
        assert captured["cwd_was_empty"] is True
        assert "SECRET" not in captured["environment"]
        command = captured["command"]
        assert str(ROOT) not in " ".join(command)
        schema_path = Path(command[command.index("--output-schema") + 1])
        image_path = Path(command[command.index("--image") + 1])
        working_path = Path(command[command.index("--cd") + 1])
        assert not schema_path.exists()
        assert not image_path.exists()
        assert not working_path.exists()
        expected = Decimal(1000) * Decimal("0.00000050") + Decimal(120) * Decimal("0.00000180")
        assert ledger.incremental_informational_list_price_equivalent_usd == expected
        assert ledger.incremental_experiment_charge_usd == Decimal("0.00")
        assert ledger.budget_accounted_usd == Decimal("4.778164718")
        assert not ledger.unresolved
        record = invocation_journal.record("sha256:one")
        assert record is not None
        assert record["status"] == "response"
        recorded_enforcement = record["outcome"]["runtime_enforcement"]
        assert recorded_enforcement["mechanism_name"] == (
            "cli_flags_and_environment_allowlist"
        )
        assert recorded_enforcement["argv_digest"] == policy.content_digest(
            list(policy.sanitized_command_contract())
        )
        assert recorded_enforcement["environment_allowlist_digest"] == (
            environment_allowlist_digest(captured["environment"])
        )
        assert recorded_enforcement["environment_variable_names"] == sorted(
            captured["environment"]
        )
        assert recorded_enforcement["os_sandbox_applied"] is False
        assert "private-thread-id" in record["raw_stdout"]
        assert "private-thread-id" not in json.dumps(transport.records)
    finally:
        transport.close()
        invocation_journal.close()


def test_missing_required_launch_restriction_changes_evidence_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_runtime_command = policy._runtime_command

    def command_without_read_only_sandbox(**kwargs: Any) -> list[str]:
        complete = original_runtime_command(**kwargs)
        restricted = list(complete)
        sandbox_index = restricted.index("--sandbox")
        del restricted[sandbox_index : sandbox_index + 2]
        return restricted

    monkeypatch.setattr(policy, "_runtime_command", command_without_read_only_sandbox)
    process = FakeProcess(cli_stream())
    transport, _ledger, invocation_journal, captured = make_transport(tmp_path, process)
    try:
        outcome = transport.send(
            request(),
            idempotency_key="sha256:missing-restriction",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "pre_send_failure"
        assert outcome.failure_code == "runtime_enforcement_mismatch"
        assert captured == {}
        record = invocation_journal.record("sha256:missing-restriction")
        assert record is not None
        enforcement = record["outcome"]["runtime_enforcement"]
        approved_contract = list(policy.sanitized_command_contract())
        altered_contract = list(approved_contract)
        sandbox_index = altered_contract.index("--sandbox")
        del altered_contract[sandbox_index : sandbox_index + 2]
        assert enforcement["argv_digest"] == policy.content_digest(altered_contract)
        assert enforcement["argv_digest"] != policy.content_digest(approved_contract)
        assert enforcement["cli_restrictions_applied"] is False
        assert enforcement["environment_allowlist_applied"] is True
    finally:
        transport.close()
        invocation_journal.close()


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_credential_shaped_raw_stdio_is_redacted_without_changing_outcome(
    tmp_path: Path,
    stream_name: str,
) -> None:
    candidate = credential_shaped_value()
    clean_stdout = cli_stream()
    original_stdout = (
        cli_stream_with_diagnostic(candidate) if stream_name == "stdout" else clean_stdout
    )
    original_stderr = candidate if stream_name == "stderr" else ""
    process = FakeProcess(original_stdout, stderr=original_stderr)
    transport, _ledger, invocation_journal, _captured = make_transport(tmp_path, process)
    try:
        outcome = transport.send(
            request(),
            idempotency_key=f"sha256:redacted-{stream_name}",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "response"
        assert outcome.response is not None
        assert policy.CodexCliPolicy().parse(
            policy.canonical_json_bytes(outcome.response), b"{}"
        ) == {"action_type": 1, "x": 100, "y": 100, "key": 0}
        record = invocation_journal.record(f"sha256:redacted-{stream_name}")
        assert record is not None
        assert record["credential_redacted"] is True
        assert record[f"raw_{stream_name}_original_sha256"] == (
            "sha256:"
            + hashlib.sha256(
                (original_stdout if stream_name == "stdout" else original_stderr).encode()
            ).hexdigest()
        )
        stored = record[f"raw_{stream_name}"]
        assert candidate not in stored
        validate_credential_free(stored)
    finally:
        transport.close()
        invocation_journal.close()


def test_clean_raw_stdout_is_stored_unchanged_without_redaction_marker(tmp_path: Path) -> None:
    original_stdout = cli_stream()
    process = FakeProcess(original_stdout)
    transport, _ledger, invocation_journal, _captured = make_transport(tmp_path, process)
    try:
        outcome = transport.send(
            request(),
            idempotency_key="sha256:clean-stdio",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )

        assert outcome.status == "response"
        record = invocation_journal.record("sha256:clean-stdio")
        assert record is not None
        assert record["raw_stdout"] == original_stdout
        assert record["credential_redacted"] is False
    finally:
        transport.close()
        invocation_journal.close()


def test_generated_publication_relation_names_raw_stdio_as_restricted(tmp_path: Path) -> None:
    authoritative = {"result": "ok"}
    store = V5EvidenceStore(tmp_path / "evidence")
    store.put_authoritative("result.json", authoritative)

    _derivative, relation = store.publish_derivative("result.json", authoritative)
    relation_document = json.loads(store.store.get_verified(relation))

    assert relation_document["excluded_authoritative_artifacts"] == [
        {
            "artifact_class": "invocation_journal_raw_stdio",
            "fields": ["raw_stdout", "raw_stderr"],
            "restriction": "restricted_local_only",
        }
    ]


def test_exact_digest_bound_disabled_code_mode_diagnostic_is_accepted_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic = "synthetic disabled code mode diagnostic"
    monkeypatch.setattr(
        policy,
        "ALLOWED_DISABLED_CODE_MODE_DIAGNOSTIC_SHA256",
        "sha256:" + hashlib.sha256(diagnostic.encode("utf-8")).hexdigest(),
    )
    process = FakeProcess(cli_stream(diagnostic_message=diagnostic))
    transport, _ledger, invocation_journal, _captured = make_transport(tmp_path, process)
    try:
        outcome = transport.send(
            request(),
            idempotency_key="sha256:allowed-diagnostic",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )
        assert outcome.status == "response"
        assert outcome.response is not None
        assert outcome.response["usage"]["policy_violation"] == "none"
        assert outcome.response["usage"]["accepted_cli_diagnostic_count"] == 1
        assert policy.CodexCliPolicy().parse(
            policy.canonical_json_bytes(outcome.response), b"{}"
        ) == {"action_type": 1, "x": 100, "y": 100, "key": 0}
    finally:
        transport.close()
        invocation_journal.close()


@pytest.mark.parametrize(
    ("diagnostic", "count", "expected_violation"),
    [
        ("unmatched error", 1, "unauthorized_item:error"),
        ("synthetic disabled code mode diagnostic", 2, "allowed_cli_diagnostic_count_exceeded"),
    ],
)
def test_unmatched_or_repeated_error_items_remain_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    diagnostic: str,
    count: int,
    expected_violation: str,
) -> None:
    allowed = "synthetic disabled code mode diagnostic"
    monkeypatch.setattr(
        policy,
        "ALLOWED_DISABLED_CODE_MODE_DIAGNOSTIC_SHA256",
        "sha256:" + hashlib.sha256(allowed.encode("utf-8")).hexdigest(),
    )
    process = FakeProcess(
        cli_stream(diagnostic_message=diagnostic, diagnostic_count=count)
    )
    transport, _ledger, invocation_journal, _captured = make_transport(tmp_path, process)
    try:
        outcome = transport.send(
            request(),
            idempotency_key=f"sha256:rejected-diagnostic-{count}",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )
        assert outcome.status == "response"
        assert outcome.response is not None
        assert expected_violation in outcome.response["usage"]["policy_violation"]
        with pytest.raises(ValueError, match="policy boundary"):
            policy.CodexCliPolicy().parse(
                policy.canonical_json_bytes(outcome.response), b"{}"
            )
    finally:
        transport.close()
        invocation_journal.close()


@pytest.mark.parametrize(
    ("stream", "expected_classification"),
    [
        (cli_stream(tool_item_type="command_execution"), "invalid_output"),
        (
            cli_stream({"action_type": 1, "x": policy.SCREEN_WIDTH, "y": 0, "key": 0}),
            "invalid_output",
        ),
    ],
)
def test_tool_use_and_invalid_actions_fail_before_backend_execution(
    tmp_path: Path,
    stream: str,
    expected_classification: str,
) -> None:
    process = FakeProcess(stream)
    transport, _ledger, invocation_journal, _captured = make_transport(tmp_path, process)
    journal = V5AttemptJournal(tmp_path / "attempts.sqlite")
    backend = V5FakeBackend()
    task = generate_task(5002)
    manifest = policy.build_codex_cli_policy_manifest(
        ROOT,
        code_revision="revision-test",
        runtime_identity=runtime_identity(),
    )
    try:
        result = V5Runner(
            journal=journal,
            manifest=manifest,
            transport=transport,
            policy=policy.CodexCliPolicy(),
            approved_caps=CallCaps(1, 1, 0, 1),
        ).run(
            trial_id="codex-policy-boundary",
            task=task,
            backend=backend,
            action_limit=1,
        )
        assert result.classification == expected_classification
        assert backend.action_count == 0
    finally:
        transport.close()
        journal.close()
        invocation_journal.close()


def test_missing_usage_is_nonblocking_and_keeps_luna_experiment_charge_zero(
    tmp_path: Path,
) -> None:
    process = FakeProcess(cli_stream(include_usage=False))
    transport, ledger, invocation_journal, _captured = make_transport(tmp_path, process)
    try:
        outcome = transport.send(
            request(),
            idempotency_key="sha256:unknown",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )
        assert outcome.status == "response"
        assert outcome.response is not None
        assert policy.CodexCliPolicy().parse(
            policy.canonical_json_bytes(outcome.response), b"{}"
        ) == {"action_type": 1, "x": 100, "y": 100, "key": 0}
        assert ledger.blocked is False
        assert ledger.experiment_charges["sha256:unknown"] == policy.LUNA_EXPERIMENT_CHARGE_USD
        assert ledger.usage_telemetry_unavailable == {"sha256:unknown"}
        assert ledger.budget_accounted_usd == Decimal("4.778164718")
        assert outcome.response["usage"]["usage_telemetry_status"] == "unavailable"
        assert outcome.response["usage"]["informational_list_price_equivalent_usd"] is None
        assert outcome.response["usage"]["experiment_charge_usd"] == "0.00"
    finally:
        transport.close()
        invocation_journal.close()


def test_missing_usage_valid_action_reaches_backend(tmp_path: Path) -> None:
    process = FakeProcess(cli_stream(include_usage=False))
    transport, _ledger, invocation_journal, _captured = make_transport(tmp_path, process)
    journal = V5AttemptJournal(tmp_path / "attempts.sqlite")
    backend = V5FakeBackend()
    task = generate_task(5002)
    manifest = policy.build_codex_cli_policy_manifest(
        ROOT,
        code_revision="revision-test",
        runtime_identity=runtime_identity(),
    )
    try:
        result = V5Runner(
            journal=journal,
            manifest=manifest,
            transport=transport,
            policy=policy.CodexCliPolicy(),
            approved_caps=CallCaps(1, 1, 0, 1),
        ).run(
            trial_id="codex-missing-usage",
            task=task,
            backend=backend,
            action_limit=1,
        )
        assert result.classification == "pilot_action_limit"
        assert result.environment_actions == 1
        assert backend.action_count == 1
    finally:
        transport.close()
        journal.close()
        invocation_journal.close()


def test_subscription_exempt_luna_is_not_blocked_by_non_luna_dollar_headroom(
    tmp_path: Path,
) -> None:
    process = FakeProcess(cli_stream())
    transport, ledger, invocation_journal, _captured = make_transport(
        tmp_path,
        process,
        prior=Decimal("9.50"),
    )
    try:
        outcome = transport.send(
            request(),
            idempotency_key="sha256:too-expensive",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )
        assert outcome.status == "response"
        assert ledger.processes_started == 1
        assert ledger.incremental_experiment_charge_usd == Decimal("0.00")
        assert ledger.budget_accounted_usd == Decimal("9.50")
        assert invocation_journal.record("sha256:too-expensive") is not None
    finally:
        transport.close()
        invocation_journal.close()


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired(cmd="codex", timeout=1),
        KeyboardInterrupt(),
    ],
)
def test_timeout_and_interruption_terminate_process_and_retain_raw_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    process = FakeProcess("partial-jsonl\n", failure=failure)

    def killpg(pid: int, sent_signal: signal.Signals) -> None:
        assert pid == process.pid
        if sent_signal == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()

    monkeypatch.setattr(policy.os, "killpg", killpg)
    transport, ledger, invocation_journal, captured = make_transport(tmp_path, process)
    try:
        if isinstance(failure, KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                transport.send(
                    request(),
                    idempotency_key="sha256:interrupted",
                    deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
                )
            key = "sha256:interrupted"
            expected_status = "interrupted"
        else:
            outcome = transport.send(
                request(),
                idempotency_key="sha256:timeout",
                deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
            )
            assert outcome.status == "deadline"
            key = "sha256:timeout"
            expected_status = "timeout"
        assert process.poll() is not None
        assert process.terminated is True
        assert not Path(captured["cwd"]).exists()
        assert ledger.blocked is True
        assert key in ledger.unresolved
        assert ledger.experiment_charges[key] == Decimal("0.00")
        record = invocation_journal.record(key)
        assert record is not None
        assert record["status"] == expected_status
        assert "partial-jsonl" in record["raw_stdout"]
        assert transport.records[-1]["policy_violation"] == "none"
        assert transport.records[-1]["experiment_charge_usd"] == "0.00"
        assert transport.records[-1]["usage_telemetry_status"] == "unavailable"
        assert transport.reconcile(idempotency_key=key, deadline_seconds=1).status == "unknown"
    finally:
        transport.close()
        invocation_journal.close()


def test_duplicate_idempotency_key_never_replays_process(tmp_path: Path) -> None:
    process = FakeProcess(cli_stream())
    transport, ledger, invocation_journal, _captured = make_transport(tmp_path, process)
    try:
        first = transport.send(
            request(),
            idempotency_key="sha256:same",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )
        second = transport.send(
            request(),
            idempotency_key="sha256:same",
            deadline_seconds=policy.RUNNER_REQUEST_DEADLINE_SECONDS,
        )
        assert first.status == "response"
        assert second.status == "pre_send_failure"
        assert second.failure_code in {
            "subscription_exempt_invocation_guard",
            "duplicate_invocation_blocked",
        }
        assert ledger.processes_started == 1
    finally:
        transport.close()
        invocation_journal.close()
