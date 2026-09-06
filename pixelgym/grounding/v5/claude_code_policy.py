"""Fail-closed Claude Code Sonnet policy with inline screenshot input and zero tools."""

from __future__ import annotations

import base64
import io
import json
import os
import signal
import sqlite3
import subprocess
import tempfile
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from PIL import Image

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.grounding.v5.codex_cli_policy import SubscriptionExemptLedger
from pixelgym.grounding.v5.contracts import (
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    PolicyManifest,
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5.coordinates import IDENTITY_ADAPTER
from pixelgym.grounding.v5.evidence import (
    CredentialValidationError,
    redact_raw_stdio,
    validate_credential_free,
)
from pixelgym.grounding.v5.runner import TransportOutcome
from pixelgym.grounding.v5.sandbox import (
    DECLARED_UNAVAILABLE_CAPABILITIES,
    PolicyClaim,
    RuntimeEnforcement,
    build_sandbox_manifest,
    runtime_enforcement,
    unbound_runtime_enforcement,
    validate_runtime_enforcement,
)
from pixelgym.serialization import canonical_json_bytes

CLAUDE_CLI_VERSION = "2.1.236 (Claude Code)"
MODEL = "claude-sonnet-5"
MODEL_REASONING_EFFORT = "medium"
AUTH_METHOD = "claude.ai"
SUBSCRIPTION_TYPE = "max"
PROVIDER_IDENTITY = "claude-code-cli/claude-ai-max-subscription"
PROVIDER_ORIGIN = "https://api.anthropic.com"
EMPTY_MCP_CONFIG = '{"mcpServers":{}}'
PROCESS_TIMEOUT_SECONDS = 120.0
RUNNER_REQUEST_DEADLINE_SECONDS = 125.0
TERMINATE_GRACE_SECONDS = 2.0
KILL_GRACE_SECONDS = 2.0
EXPERIMENT_CHARGE_USD = Decimal("0.00")
MAXIMUM_AGGREGATE_SPEND_USD = Decimal("10.00")
PRIOR_BUDGET_ACCOUNTED_SPEND_USD = Decimal("4.778164718")
CONTEXT_LIMIT_TOKENS = 1_000_000
PARSER_VERSION = "pixelgym-agent-v5-claude-stream-json-action-parser-v1"
RESPONSE_SCHEMA_VERSION = "pixelgym-agent-v5-claude-stream-json-response-v1"
STATE_REDUCER_VERSION = "pixelgym-agent-v5-stateless-current-screenshot-reducer-v1"
MEMORY_POLICY_VERSION = "pixelgym-agent-v5-stateless-current-screenshot-only-v1"
TASK_RENDERER_VERSION = "pixelgym-agent-v5-task-renderer-v1"
TRANSPORT_RETRY_RULE = "one-claude-cli-process-per-action-no-runner-retry-v1"
INVOCATION_JOURNAL_SCHEMA_VERSION = "pixelgym-agent-v5-claude-cli-invocation-journal-v2"
SYSTEM_PROMPT = (
    "You are a stateless pixel-only GUI policy. Use only the user-provided task text and "
    "inline screenshot. Do not use tools or request other context. Return exactly one action "
    "as a bare JSON object matching the required action contract, with no markdown or prose."
)

ACTION_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "action_type": {"type": "integer", "enum": [0, 1, 2]},
        "x": {"type": "integer", "minimum": 0, "maximum": SCREEN_WIDTH - 1},
        "y": {"type": "integer", "minimum": 0, "maximum": SCREEN_HEIGHT - 1},
        "key": {"type": "integer", "minimum": 0, "maximum": len(KEY_ALLOWLIST) - 1},
    },
    "required": ["action_type", "x", "y", "key"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ClaudeRuntimeIdentity:
    cli_version: str
    auth_method: str
    subscription_type: str
    requested_model: str
    reasoning_effort: str
    help_sha256: str

    def __post_init__(self) -> None:
        if self.cli_version != CLAUDE_CLI_VERSION:
            raise ValueError("Claude Code version differs from the frozen adapter")
        if self.auth_method != AUTH_METHOD or self.subscription_type != SUBSCRIPTION_TYPE:
            raise ValueError("Claude Code must use the approved Max subscription authentication")
        if self.requested_model != MODEL or self.reasoning_effort != MODEL_REASONING_EFFORT:
            raise ValueError("Claude model or reasoning effort differs from the frozen policy")
        if not self.help_sha256.startswith("sha256:"):
            raise ValueError("Claude help digest is invalid")

    def to_dict(self) -> dict[str, str]:
        return {
            "cli_version": self.cli_version,
            "auth_method": self.auth_method,
            "subscription_type": self.subscription_type,
            "requested_model": self.requested_model,
            "reasoning_effort": self.reasoning_effort,
            "help_sha256": self.help_sha256,
        }


def _run_probe(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=True)


def probe_claude_runtime(
    *, run: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run_probe
) -> ClaudeRuntimeIdentity:
    version = run(("claude", "--version")).stdout.strip()
    auth = json.loads(run(("claude", "auth", "status", "--json")).stdout)
    if not isinstance(auth, dict):
        raise TypeError("Claude authentication status is invalid")
    if auth.get("loggedIn") is not True:
        raise ValueError("Claude Code is not logged in")
    help_text = run(("claude", "--help")).stdout
    required_flags = (
        "--print",
        "--model",
        "--effort",
        "--output-format",
        "--input-format",
        "--tools",
        "--disallowedTools",
        "--permission-mode",
        "--setting-sources",
        "--safe-mode",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        "--no-chrome",
        "--no-session-persistence",
        "--system-prompt",
        "--exclude-dynamic-system-prompt-sections",
    )
    if any(flag not in help_text for flag in required_flags):
        raise ValueError("Claude help omits an approved isolation or structured-output flag")
    return ClaudeRuntimeIdentity(
        cli_version=version,
        auth_method=str(auth.get("authMethod", "")),
        subscription_type=str(auth.get("subscriptionType", "")),
        requested_model=MODEL,
        reasoning_effort=MODEL_REASONING_EFFORT,
        help_sha256="sha256:" + sha256_bytes(help_text.encode("utf-8")),
    )


def sanitized_command_contract() -> tuple[str, ...]:
    return (
        "claude",
        "--print",
        "--verbose",
        "--model",
        MODEL,
        "--effort",
        MODEL_REASONING_EFFORT,
        "--output-format",
        "stream-json",
        "--input-format",
        "stream-json",
        "--tools",
        "",
        "--disallowedTools",
        "mcp__*",
        "--max-turns",
        "1",
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "",
        "--safe-mode",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        EMPTY_MCP_CONFIG,
        "--no-chrome",
        "--no-session-persistence",
        "--exclude-dynamic-system-prompt-sections",
        "--system-prompt",
        SYSTEM_PROMPT,
    )


def command_contract_digest() -> str:
    return content_digest(list(sanitized_command_contract()))


def _runtime_command() -> list[str]:
    return list(sanitized_command_contract())


_ALLOWED_ENVIRONMENT_VARIABLES = (
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
)


def _minimal_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        name: environment[name]
        for name in _ALLOWED_ENVIRONMENT_VARIABLES
        if environment.get(name)
    }


def _claude_launch_enforcement(
    command: Sequence[str], environment: Mapping[str, str]
) -> RuntimeEnforcement:
    controls_match = tuple(command) == sanitized_command_contract()
    environment_is_allowlisted = set(environment) <= set(_ALLOWED_ENVIRONMENT_VARIABLES)
    return runtime_enforcement(
        argv=command,
        environment=environment,
        cli_restrictions_applied=controls_match,
        environment_allowlist_applied=environment_is_allowlisted,
    )


def action_prompt(task_instruction: str) -> str:
    if not task_instruction:
        raise ValueError("task instruction is required")
    key_map = ", ".join(f"{index}={key}" for index, key in enumerate(KEY_ALLOWLIST))
    return (
        "Use only the inline current screenshot to complete this task: "
        f"{task_instruction}\nReturn only one bare JSON object with exactly the integer fields "
        '"action_type", "x", "y", and "key". action_type is 0 for NOOP, '
        "1 for CLICK, and 2 for KEY. CLICK x is 0..1023 and y is 0..767. For CLICK "
        "set key=0. For KEY set x=y=0 and select the key index from: "
        f"{key_map}. For NOOP set x=y=key=0."
    )


def _png_bytes(screenshot: bytes) -> bytes:
    if len(screenshot) != SCREEN_WIDTH * SCREEN_HEIGHT * 3:
        raise ValueError("screenshot byte length differs from frozen RGB dimensions")
    image = Image.frombytes("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), screenshot)
    encoded = io.BytesIO()
    image.save(encoded, format="PNG", compress_level=9, optimize=False)
    return encoded.getvalue()


class ClaudeCodePolicy:
    def reset(self, task_instruction: str) -> bytes:
        return canonical_json_bytes({"instruction": task_instruction})

    def build_request(self, state: bytes, screenshot: bytes) -> dict[str, Any]:
        value = json.loads(state)
        if not isinstance(value, dict) or set(value) != {"instruction"}:
            raise ValueError("Claude policy state is invalid")
        png = _png_bytes(screenshot)
        return {
            "provider": PROVIDER_IDENTITY,
            "model": MODEL,
            "model_reasoning_effort": MODEL_REASONING_EFFORT,
            "prompt": action_prompt(str(value["instruction"])),
            "image_png_base64": base64.b64encode(png).decode("ascii"),
            "image_sha256": "sha256:" + sha256_bytes(png),
            "action_schema_digest": content_digest(ACTION_SCHEMA),
            "command_contract_digest": command_contract_digest(),
        }

    def reduce_state(self, state: bytes, canonical_response: bytes) -> bytes:
        del canonical_response
        return state

    def failure_state(self, state: bytes, failure_code: str) -> bytes:
        del failure_code
        return state

    def retryable_response_code(self, canonical_response: bytes) -> str | None:
        del canonical_response
        return None

    def parse(self, canonical_response: bytes, state: bytes) -> dict[str, Any]:
        del state
        response = json.loads(canonical_response)
        if response.get("model") != MODEL:
            raise ValueError("Claude response model differs from the policy")
        usage = response.get("usage")
        expected = {
            "auth_method": AUTH_METHOD,
            "subscription_type": SUBSCRIPTION_TYPE,
            "cli_version": CLAUDE_CLI_VERSION,
            "command_contract_digest": command_contract_digest(),
            "experiment_charge_usd": "0.00",
            "model_reasoning_effort": MODEL_REASONING_EFFORT,
            "policy_violation": "none",
        }
        if not isinstance(usage, dict) or any(
            usage.get(key) != expected_value for key, expected_value in expected.items()
        ):
            raise ValueError("Claude identity, accounting, or policy boundary is invalid")
        candidate = json.loads(response.get("content", ""))
        if not isinstance(candidate, dict) or set(candidate) != {
            "action_type",
            "x",
            "y",
            "key",
        }:
            raise ValueError("Claude content must be one exact action object")
        if any(type(candidate[field]) is not int for field in candidate):
            raise TypeError("Claude action fields must be plain integers")
        return candidate

    def post_parse_state(self, state: bytes, candidate: dict[str, Any]) -> bytes:
        del candidate
        return state

    def post_dispatch_state(
        self, state: bytes, action: dict[str, int], result: dict[str, Any]
    ) -> bytes:
        del action, result
        return state

    def close(self) -> None:
        return None


class RunningProcess(Protocol):
    pid: int
    returncode: int | None

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]: ...

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


def _start_process(command: Sequence[str], **kwargs: Any) -> RunningProcess:
    return cast(RunningProcess, subprocess.Popen(command, **kwargs))


class ClaudeInvocationJournal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS invocations (
                idempotency_key TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                command_contract BLOB NOT NULL,
                experiment_charge_usd TEXT NOT NULL,
                status TEXT NOT NULL,
                process_id INTEGER,
                exit_code INTEGER,
                raw_stdout BLOB,
                raw_stderr BLOB,
                raw_stdout_original_sha256 TEXT,
                raw_stderr_original_sha256 TEXT,
                credential_redacted INTEGER NOT NULL DEFAULT 0,
                outcome BLOB
            )
            """
        )

    def reserve(self, *, idempotency_key: str, request_digest: str) -> bool:
        command = list(sanitized_command_contract())
        validate_credential_free(command)
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    INSERT INTO invocations(
                        idempotency_key, schema_version, request_digest,
                        command_contract, experiment_charge_usd, status
                    ) VALUES (?, ?, ?, ?, '0.00', 'reserved')
                    """,
                    (
                        idempotency_key,
                        INVOCATION_JOURNAL_SCHEMA_VERSION,
                        request_digest,
                        canonical_json_bytes(command),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def mark_running(self, idempotency_key: str, process_id: int) -> None:
        self._update(
            idempotency_key,
            "running",
            process_id,
            None,
            None,
            None,
            None,
            None,
            False,
            None,
        )

    def finish(
        self,
        idempotency_key: str,
        *,
        status: str,
        exit_code: int | None,
        raw_stdout: str,
        raw_stderr: str,
        outcome: dict[str, Any],
    ) -> None:
        stdout = redact_raw_stdio(raw_stdout)
        stderr = redact_raw_stdio(raw_stderr)
        self._update(
            idempotency_key,
            status,
            None,
            exit_code,
            stdout.value.encode("utf-8", errors="replace"),
            stderr.value.encode("utf-8", errors="replace"),
            stdout.original_sha256,
            stderr.original_sha256,
            stdout.credential_redacted or stderr.credential_redacted,
            canonical_json_bytes(outcome),
        )

    def record(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT status, raw_stdout, raw_stderr, raw_stdout_original_sha256,
                   raw_stderr_original_sha256, credential_redacted, outcome
            FROM invocations WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return {
            "status": str(row[0]),
            "raw_stdout": bytes(row[1] or b"").decode("utf-8", errors="replace"),
            "raw_stderr": bytes(row[2] or b"").decode("utf-8", errors="replace"),
            "raw_stdout_original_sha256": row[3],
            "raw_stderr_original_sha256": row[4],
            "credential_redacted": bool(row[5]),
            "outcome": json.loads(bytes(row[6]).decode("utf-8")) if row[6] else None,
        }

    def integrity_report(self) -> dict[str, Any]:
        rows = self._connection.execute(
            """
            SELECT idempotency_key, request_digest, status, exit_code,
                   raw_stdout, raw_stderr, raw_stdout_original_sha256,
                   raw_stderr_original_sha256, credential_redacted, outcome
            FROM invocations ORDER BY idempotency_key
            """
        ).fetchall()
        records = [
            {
                "idempotency_key_digest": content_digest(str(row[0])),
                "request_digest": str(row[1]),
                "status": str(row[2]),
                "exit_code": row[3],
                "raw_stdout_sha256": "sha256:" + sha256_bytes(bytes(row[4] or b"")),
                "raw_stderr_sha256": "sha256:" + sha256_bytes(bytes(row[5] or b"")),
                "raw_stdout_original_sha256": row[6],
                "raw_stderr_original_sha256": row[7],
                "credential_redacted": bool(row[8]),
                "outcome_sha256": "sha256:" + sha256_bytes(bytes(row[9] or b"")),
            }
            for row in rows
        ]
        return {
            "schema_version": INVOCATION_JOURNAL_SCHEMA_VERSION,
            "invocation_count": len(records),
            "records": records,
        }

    def close(self) -> None:
        self._connection.close()

    def _update(
        self,
        idempotency_key: str,
        status: str,
        process_id: int | None,
        exit_code: int | None,
        raw_stdout: bytes | None,
        raw_stderr: bytes | None,
        raw_stdout_original_sha256: str | None,
        raw_stderr_original_sha256: str | None,
        credential_redacted: bool,
        outcome: bytes | None,
    ) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE invocations
                SET status=?, process_id=?, exit_code=?, raw_stdout=?, raw_stderr=?,
                    raw_stdout_original_sha256=?, raw_stderr_original_sha256=?,
                    credential_redacted=?, outcome=?
                WHERE idempotency_key=?
                """,
                (
                    status,
                    process_id,
                    exit_code,
                    raw_stdout,
                    raw_stderr,
                    raw_stdout_original_sha256,
                    raw_stderr_original_sha256,
                    int(credential_redacted),
                    outcome,
                    idempotency_key,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Claude invocation journal reservation is missing")


@dataclass(frozen=True)
class ParsedClaudeStream:
    content: str
    usage: dict[str, Any] | None
    resolved_model: str | None
    informational_cost_usd: Decimal | None
    policy_violations: tuple[str, ...]
    event_counts: dict[str, int]


def _parse_stream(raw_stdout: str) -> ParsedClaudeStream:
    violations: list[str] = []
    events: list[dict[str, Any]] = []
    for line in raw_stdout.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            violations.append("invalid_jsonl")
            continue
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            violations.append("invalid_event_envelope")
            continue
        events.append(value)
    required_events = {"system", "assistant", "result"}
    allowed_events = required_events | {"rate_limit_event"}
    event_counts = Counter(str(event["type"]) for event in events)
    for event_type in required_events:
        if event_counts[event_type] != 1:
            violations.append(f"{event_type}_event_count_mismatch")
    result_events: list[dict[str, Any]] = []
    resolved_models: set[str] = set()
    for event in events:
        event_type = str(event["type"])
        if event_type not in allowed_events:
            violations.append(f"unauthorized_event:{event_type}")
            continue
        if event_type == "rate_limit_event":
            rate_limit = event.get("rate_limit_info")
            if not isinstance(rate_limit, dict):
                violations.append("invalid_rate_limit_event")
                continue
            if rate_limit.get("status") not in (None, "allowed", "allowed_warning"):
                violations.append("subscription_rate_limit_rejected")
            if rate_limit.get("isUsingOverage") not in (None, False):
                violations.append("subscription_overage_active")
        elif event_type == "system":
            tools = event.get("tools")
            if tools not in (None, []):
                violations.append("system_advertised_tools")
            mcp_servers = event.get("mcp_servers")
            if mcp_servers not in (None, []):
                violations.append("system_advertised_mcp_servers")
        elif event_type == "assistant":
            if event.get("parent_tool_use_id") not in (None, ""):
                violations.append("assistant_has_parent_tool_use")
            message = event.get("message")
            if not isinstance(message, dict):
                violations.append("invalid_assistant_message")
                continue
            model = message.get("model")
            if isinstance(model, str):
                resolved_models.add(model)
            content = message.get("content")
            if not isinstance(content, list):
                violations.append("invalid_assistant_content")
                continue
            for block in content:
                block_type = block.get("type") if isinstance(block, dict) else None
                if block_type not in {"text", "thinking"}:
                    violations.append(f"unauthorized_content_block:{block_type}")
        else:
            result_events.append(event)
    if len(result_events) != 1:
        violations.append("result_event_count_mismatch")
        result: dict[str, Any] = {}
    else:
        result = result_events[0]
    if result.get("is_error") is not False:
        violations.append("claude_result_error")
    if result.get("subtype") != "success":
        violations.append("claude_result_subtype_mismatch")
    if result.get("num_turns") not in (None, 1):
        violations.append("claude_turn_count_exceeded")
    model_usage = result.get("modelUsage")
    if isinstance(model_usage, dict):
        resolved_models.update(str(key) for key in model_usage)
    if len(resolved_models) != 1:
        violations.append("resolved_model_count_mismatch")
        resolved_model = None
    else:
        resolved_model = next(iter(resolved_models))
        if not resolved_model.startswith(MODEL):
            violations.append("resolved_model_mismatch")
    if result.get("structured_output") is not None:
        violations.append("unexpected_structured_output")
    result_text = result.get("result")
    content = result_text if isinstance(result_text, str) else ""
    raw_usage = result.get("usage")
    usage: dict[str, int] | None = None
    if isinstance(raw_usage, dict):
        normalized_usage: dict[str, int] = {}
        usage_valid = True
        for field in ("input_tokens", "output_tokens"):
            value = raw_usage.get(field)
            if type(value) is not int or value < 0:
                usage_valid = False
                break
            normalized_usage[field] = value
        for field in ("cache_creation_input_tokens", "cache_read_input_tokens"):
            value = raw_usage.get(field, 0)
            if type(value) is not int or value < 0:
                usage_valid = False
                break
            normalized_usage[field] = value
        if usage_valid and sum(normalized_usage.values()) <= CONTEXT_LIMIT_TOKENS:
            usage = normalized_usage
        else:
            violations.append("invalid_usage_telemetry")
    elif raw_usage is not None:
        violations.append("invalid_usage_telemetry")
    cost_value = result.get("total_cost_usd")
    try:
        informational_cost = Decimal(str(cost_value)) if cost_value is not None else None
    except InvalidOperation:
        informational_cost = None
        violations.append("invalid_cost_telemetry")
    if informational_cost is not None and (
        not informational_cost.is_finite() or informational_cost < 0
    ):
        informational_cost = None
        violations.append("invalid_cost_telemetry")
    return ParsedClaudeStream(
        content=content,
        usage=usage,
        resolved_model=resolved_model,
        informational_cost_usd=informational_cost,
        policy_violations=tuple(sorted(set(violations))),
        event_counts=dict(sorted(event_counts.items())),
    )


class ClaudeCodeTransport:
    def __init__(
        self,
        *,
        ledger: SubscriptionExemptLedger,
        invocation_journal: ClaudeInvocationJournal,
        runtime_identity: ClaudeRuntimeIdentity,
        expected_resolved_model: str | None = None,
        environment: Mapping[str, str] = os.environ,
        process_factory: Callable[..., RunningProcess] = _start_process,
        process_timeout_seconds: float = PROCESS_TIMEOUT_SECONDS,
    ) -> None:
        ClaudeRuntimeIdentity(**runtime_identity.__dict__)
        if process_timeout_seconds <= 0:
            raise ValueError("Claude process timeout must be positive")
        self.ledger = ledger
        self.invocation_journal = invocation_journal
        self.runtime_identity = runtime_identity
        self.expected_resolved_model = expected_resolved_model
        self.environment = _minimal_environment(environment)
        self.process_factory = process_factory
        self.process_timeout_seconds = process_timeout_seconds
        self.records: list[dict[str, Any]] = []
        self._active: dict[str, RunningProcess] = {}
        self._started_processes: list[RunningProcess] = []
        self._active_lock = threading.Lock()
        self._closed = False

    @property
    def subprocesses_closed(self) -> bool:
        with self._active_lock:
            return all(process.poll() is not None for process in self._started_processes)

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        if self._closed:
            return TransportOutcome("pre_send_failure", failure_code="transport_closed")
        failure = self._validate_request(request, deadline_seconds)
        if failure is not None:
            return TransportOutcome("pre_send_failure", failure_code=failure)
        if not self.ledger.reserve(idempotency_key):
            return TransportOutcome("pre_send_failure", failure_code="subscription_guard")
        if not self.invocation_journal.reserve(
            idempotency_key=idempotency_key,
            request_digest=content_digest(request),
        ):
            self.ledger.release_pre_send(idempotency_key)
            return TransportOutcome("pre_send_failure", failure_code="duplicate_invocation")
        raw_stdout = ""
        raw_stderr = ""
        process: RunningProcess | None = None
        outcome: dict[str, Any]
        with tempfile.TemporaryDirectory(prefix="pixelgym-claude-cli-") as temporary:
            command = _runtime_command()
            launch_enforcement = _claude_launch_enforcement(command, self.environment)
            enforcement_record = launch_enforcement.to_dict()
            try:
                validate_runtime_enforcement(
                    PolicyClaim(DECLARED_UNAVAILABLE_CAPABILITIES),
                    launch_enforcement,
                )
            except ValueError:
                self.ledger.release_pre_send(idempotency_key)
                outcome = {
                    "failure_code": "runtime_enforcement_mismatch",
                    "runtime_enforcement": enforcement_record,
                }
                self.invocation_journal.finish(
                    idempotency_key,
                    status="pre_send_failure",
                    exit_code=None,
                    raw_stdout="",
                    raw_stderr="",
                    outcome=outcome,
                )
                self.records.append(
                    self._record(idempotency_key, "pre_send_failure", outcome)
                )
                return TransportOutcome(
                    "pre_send_failure",
                    failure_code="runtime_enforcement_mismatch",
                )
            image_block = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": request["image_png_base64"],
                },
            }
            input_event = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [image_block, {"type": "text", "text": request["prompt"]}],
                },
            }
            try:
                process = self.process_factory(
                    command,
                    cwd=temporary,
                    env=self.environment,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                self.ledger.release_pre_send(idempotency_key)
                outcome = {
                    "failure_code": "process_start_failure",
                    "type": type(exc).__name__,
                    "runtime_enforcement": enforcement_record,
                }
                self.invocation_journal.finish(
                    idempotency_key,
                    status="pre_send_failure",
                    exit_code=None,
                    raw_stdout="",
                    raw_stderr="",
                    outcome=outcome,
                )
                self.records.append(
                    self._record(idempotency_key, "pre_send_failure", outcome)
                )
                return TransportOutcome("pre_send_failure", failure_code="process_start_failure")
            self.ledger.mark_process_started()
            self.invocation_journal.mark_running(idempotency_key, process.pid)
            with self._active_lock:
                self._started_processes.append(process)
                self._active[idempotency_key] = process
            try:
                raw_stdout, raw_stderr = process.communicate(
                    json.dumps(input_event, separators=(",", ":")) + "\n",
                    timeout=min(self.process_timeout_seconds, deadline_seconds),
                )
            except subprocess.TimeoutExpired:
                raw_stdout, raw_stderr = self._terminate(process)
                self.ledger.retain_unresolved_and_block(idempotency_key)
                outcome = {
                    "failure_code": "claude_process_timeout",
                    "runtime_enforcement": enforcement_record,
                }
                self.invocation_journal.finish(
                    idempotency_key,
                    status="timeout",
                    exit_code=process.poll(),
                    raw_stdout=raw_stdout,
                    raw_stderr=raw_stderr,
                    outcome=outcome,
                )
                self.records.append(self._record(idempotency_key, "timeout", outcome))
                return TransportOutcome("deadline", failure_code="claude_process_timeout")
            except BaseException as exc:
                raw_stdout, raw_stderr = self._terminate(process)
                self.ledger.retain_unresolved_and_block(idempotency_key)
                outcome = {
                    "failure_code": "claude_process_interrupted",
                    "type": type(exc).__name__,
                    "runtime_enforcement": enforcement_record,
                }
                self.invocation_journal.finish(
                    idempotency_key,
                    status="interrupted",
                    exit_code=process.poll(),
                    raw_stdout=raw_stdout,
                    raw_stderr=raw_stderr,
                    outcome=outcome,
                )
                self.records.append(self._record(idempotency_key, "interrupted", outcome))
                raise
            finally:
                with self._active_lock:
                    self._active.pop(idempotency_key, None)
        assert process is not None
        parsed = _parse_stream(raw_stdout)
        violations = list(parsed.policy_violations)
        if process.returncode != 0:
            violations.append("nonzero_exit")
        if (
            self.expected_resolved_model is not None
            and parsed.resolved_model != self.expected_resolved_model
        ):
            violations.append("resolved_model_differs_from_smoke")
        content = parsed.content
        try:
            validate_credential_free(content)
        except CredentialValidationError:
            content = ""
            violations.append("credential_shaped_output")
        if parsed.usage is None:
            self.ledger.mark_usage_telemetry_unavailable(idempotency_key)
            usage_status = "unavailable"
        else:
            self.ledger.record_usage(idempotency_key, Decimal("0.00"))
            usage_status = "available"
        violation_value = "none" if not violations else ",".join(sorted(set(violations)))
        usage_record = {
            **(parsed.usage or {}),
            "auth_method": AUTH_METHOD,
            "subscription_type": SUBSCRIPTION_TYPE,
            "cli_version": CLAUDE_CLI_VERSION,
            "command_contract_digest": command_contract_digest(),
            "experiment_charge_usd": "0.00",
            "informational_cost_telemetry_usd": (
                str(parsed.informational_cost_usd)
                if parsed.informational_cost_usd is not None
                else None
            ),
            "model_reasoning_effort": MODEL_REASONING_EFFORT,
            "resolved_model": parsed.resolved_model,
            "policy_violation": violation_value,
            "usage_telemetry_status": usage_status,
        }
        canonical = {
            "response_id": "sha256:" + sha256_bytes(raw_stdout.encode("utf-8")),
            "model": MODEL,
            "content": content,
            "finish_reason": "stop" if violation_value == "none" else "policy_violation",
            "usage": usage_record,
        }
        outcome = {
            "event_counts": parsed.event_counts,
            "exit_code": process.returncode,
            "runtime_enforcement": enforcement_record,
            "resolved_model": parsed.resolved_model,
            "policy_violation": violation_value,
            "experiment_charge_usd": "0.00",
            "informational_cost_telemetry_usd": usage_record[
                "informational_cost_telemetry_usd"
            ],
            "usage_telemetry_status": usage_status,
            "canonical_response": canonical,
        }
        status = "response" if violation_value == "none" else "policy_violation"
        self.invocation_journal.finish(
            idempotency_key,
            status=status,
            exit_code=process.returncode,
            raw_stdout=raw_stdout,
            raw_stderr=raw_stderr,
            outcome=outcome,
        )
        self.records.append(self._record(idempotency_key, status, outcome))
        return TransportOutcome("response", canonical)

    def cancel(self, *, idempotency_key: str, mode: str) -> Literal["cancelled", "unknown"]:
        del mode
        with self._active_lock:
            process = self._active.get(idempotency_key)
        if process is None:
            return "unknown"
        self._terminate(process)
        return "cancelled" if process.poll() is not None else "unknown"

    def reconcile(self, *, idempotency_key: str, deadline_seconds: float) -> TransportOutcome:
        del deadline_seconds
        record = self.invocation_journal.record(idempotency_key)
        if record is None or record["status"] in {"reserved", "running"}:
            return TransportOutcome("unknown", failure_code="invocation_unresolved")
        outcome = record.get("outcome")
        if isinstance(outcome, dict) and isinstance(outcome.get("canonical_response"), dict):
            return TransportOutcome("response", outcome["canonical_response"])
        return TransportOutcome("unknown", failure_code="invocation_not_recoverable")

    def close(self) -> None:
        with self._active_lock:
            active = list(self._active.values())
        for process in active:
            self._terminate(process)
        self._closed = True

    def _validate_request(self, request: dict[str, Any], deadline_seconds: float) -> str | None:
        expected_keys = {
            "provider",
            "model",
            "model_reasoning_effort",
            "prompt",
            "image_png_base64",
            "image_sha256",
            "action_schema_digest",
            "command_contract_digest",
        }
        if set(request) != expected_keys:
            return "request_shape_mismatch"
        if request.get("provider") != PROVIDER_IDENTITY or request.get("model") != MODEL:
            return "request_identity_mismatch"
        if request.get("model_reasoning_effort") != MODEL_REASONING_EFFORT:
            return "reasoning_effort_mismatch"
        if request.get("action_schema_digest") != content_digest(ACTION_SCHEMA):
            return "action_schema_mismatch"
        if request.get("command_contract_digest") != command_contract_digest():
            return "command_contract_mismatch"
        if deadline_seconds < self.process_timeout_seconds:
            return "runner_deadline_below_process_timeout"
        try:
            image = base64.b64decode(request["image_png_base64"], validate=True)
        except (TypeError, ValueError):
            return "image_encoding_invalid"
        if request.get("image_sha256") != "sha256:" + sha256_bytes(image):
            return "image_digest_mismatch"
        return None

    def _terminate(self, process: RunningProcess) -> tuple[str, str]:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                process.terminate()
        try:
            return process.communicate(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    process.kill()
            try:
                return process.communicate(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                return "", ""

    def _record(
        self, idempotency_key: str, status: str, outcome: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "idempotency_key_digest": content_digest(idempotency_key),
            "status": status,
            "cli_version": CLAUDE_CLI_VERSION,
            "model": MODEL,
            "model_reasoning_effort": MODEL_REASONING_EFFORT,
            "auth_method": AUTH_METHOD,
            "subscription_type": SUBSCRIPTION_TYPE,
            "command_contract_digest": command_contract_digest(),
            "runtime_enforcement": outcome.get("runtime_enforcement"),
            "resolved_model": outcome.get("resolved_model"),
            "experiment_charge_usd": outcome.get("experiment_charge_usd", "0.00"),
            "informational_cost_telemetry_usd": outcome.get(
                "informational_cost_telemetry_usd"
            ),
            "policy_violation": outcome.get("policy_violation", "none"),
            "usage_telemetry_status": outcome.get(
                "usage_telemetry_status", "unavailable"
            ),
        }


def _file_digest(path: Path) -> str:
    return "sha256:" + sha256_bytes(path.read_bytes())


def build_claude_policy_manifest(
    repository_root: Path,
    *,
    code_revision: str,
    runtime_identity: ClaudeRuntimeIdentity,
    resolved_model: str | None,
) -> PolicyManifest:
    ClaudeRuntimeIdentity(**runtime_identity.__dict__)
    runtime_digest = content_digest(
        {
            "provider_module": _file_digest(
                repository_root / "pixelgym/grounding/v5/claude_code_policy.py"
            ),
            "runner_module": _file_digest(repository_root / "pixelgym/grounding/v5/runner.py"),
            "pyproject": _file_digest(repository_root / "pyproject.toml"),
            "lock": _file_digest(repository_root / "requirements/platform-py312.lock"),
            "runtime_identity": runtime_identity.to_dict(),
        }
    )
    sandbox = build_sandbox_manifest(
        runtime_digest=runtime_digest,
        provider_endpoint=PROVIDER_ORIGIN,
        launch_enforcement=unbound_runtime_enforcement(),
        policy_claim=PolicyClaim(()),
    )
    inference_parameters = (
        ("auth_method", AUTH_METHOD),
        ("subscription_type", SUBSCRIPTION_TYPE),
        ("cli_version", CLAUDE_CLI_VERSION),
        ("command_contract_digest", command_contract_digest()),
        ("model_reasoning_effort", MODEL_REASONING_EFFORT),
        ("resolved_model", resolved_model or "to_be_bound_by_successful_smoke"),
        ("tools", "none"),
        ("mcp", "none"),
        ("claude_max_turns", "1"),
        ("structured_output_auto_retry", "disabled_no_json_schema_flag"),
        ("runner_retries", "0"),
    )
    return PolicyManifest.build(
        provider=PROVIDER_IDENTITY,
        model=MODEL,
        exact_snapshot=resolved_model is not None,
        harness_digest=_file_digest(repository_root / "pixelgym/grounding/v5/runner.py"),
        dependency_lock_digest=_file_digest(
            repository_root / "requirements/platform-py312.lock"
        ),
        system_prompt_digest=content_digest(SYSTEM_PROMPT),
        task_renderer_version=TASK_RENDERER_VERSION,
        response_schema_version=RESPONSE_SCHEMA_VERSION,
        state_reducer_version=STATE_REDUCER_VERSION,
        parser_version=PARSER_VERSION,
        memory_policy_version=MEMORY_POLICY_VERSION,
        coordinate_adapter=IDENTITY_ADAPTER.name,
        coordinate_adapter_digest=IDENTITY_ADAPTER.source_digest,
        coordinate_input_convention="integer-pixel/1024x768",
        max_model_attempts_per_action=1,
        max_cancellation_requests_per_attempt=0,
        max_reconciliation_requests_per_attempt=0,
        request_deadline_seconds=RUNNER_REQUEST_DEADLINE_SECONDS,
        cancellation_mode="transport_process_group_termination",
        reconciliation_deadline_seconds=0.0,
        sandbox=sandbox,
        code_revision=code_revision,
        dirty_worktree_policy="reject-tracked-changes",
        inference_parameters=inference_parameters,
        context_limit=CONTEXT_LIMIT_TOKENS,
        transport_retry_rule=TRANSPORT_RETRY_RULE,
    )
