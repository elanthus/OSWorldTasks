"""Fail-closed Claude Code Haiku policy with inline screenshot input and zero tools."""

from __future__ import annotations

import base64
import io
import json
import os
import platform
import re
import sqlite3
import subprocess
import tempfile
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from PIL import Image

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.grounding.v5 import cli_transport
from pixelgym.grounding.v5.claude_subscription import verify_max_subscription

if TYPE_CHECKING:
    from pixelgym.grounding.v5.auth_recovery import OAuthRecoveryGate
from pixelgym.grounding.v5.cli_transport import (
    CliProcessInterrupted,
    CliSubprocessTransport,
    StreamParseEnvelope,
    start_process,
)
from pixelgym.grounding.v5.codex_cli_policy import SubscriptionExemptLedger
from pixelgym.grounding.v5.contracts import (
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    CliFault,
    CliFaultKind,
    PolicyManifest,
    TransportOutcome,
    classify_cli_process_fault,
    cli_fault_outcome,
    cli_pre_send_fault,
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5.coordinates import IDENTITY_ADAPTER
from pixelgym.grounding.v5.egress_proxy import (
    EgressProxy,
    egress_allowlist_digest,
    summarize_attempts,
)
from pixelgym.grounding.v5.evidence import (
    CredentialValidationError,
    RedactedRawStdio,
    redact_raw_stdio,
    validate_credential_free,
)
from pixelgym.grounding.v5.os_sandbox import (
    CliLaunchLayout,
    cli_profile_digest,
    cli_profile_template_digest,
    cli_scratch_root,
    darwin_cli_profile,
    sandboxed_command,
)
from pixelgym.grounding.v5.runner import PolicyVisibleResult
from pixelgym.grounding.v5.sandbox import (
    DECLARED_UNAVAILABLE_CAPABILITIES,
    OS_SANDBOX_CLI_LAUNCH_MECHANISM,
    PolicyClaim,
    RuntimeEnforcement,
    build_sandbox_manifest,
    runtime_enforcement,
    sandboxed_launch_enforcement,
    unbound_runtime_enforcement,
    validate_runtime_enforcement,
)
from pixelgym.serialization import canonical_json_bytes

RunningProcess = cli_transport.RunningProcess

CLAUDE_CLI_VERSION = "2.1.267 (Claude Code)"
MODEL = "claude-haiku-4-5-20251001"
# Haiku 4.5 has no effort control; retain the field as truthful default metadata.
MODEL_REASONING_EFFORT = "default"
AUTH_METHOD = "claude.ai"
SUBSCRIPTION_TYPE = "max"
PROVIDER_IDENTITY = "claude-code-cli/claude-ai-max-subscription"


@dataclass(frozen=True)
class ClaudeRoute:
    """One approved CLI version and authentication route; part of policy identity."""

    cli_version: str
    auth_method: str
    subscription_type: str


FROZEN_ROUTE = ClaudeRoute(CLAUDE_CLI_VERSION, AUTH_METHOD, SUBSCRIPTION_TYPE)
# The D5.9 replication binds the installed CLI and the token route that the kernel
# sandbox can host (design record: docs/v5-policy-sandbox.md). Nothing else is approved.
REPLICATION_CLI_VERSION = "2.1.292 (Claude Code)"
REPLICATION_ROUTE = ClaudeRoute(REPLICATION_CLI_VERSION, "oauth_token", SUBSCRIPTION_TYPE)
APPROVED_ROUTES = (FROZEN_ROUTE, REPLICATION_ROUTE)
PROVIDER_ORIGIN = "https://api.anthropic.com"
EMPTY_MCP_CONFIG = '{"mcpServers":{}}'
PROCESS_TIMEOUT_SECONDS = 120.0
RUNNER_REQUEST_DEADLINE_SECONDS = 125.0
TERMINATE_GRACE_SECONDS = 2.0
KILL_GRACE_SECONDS = 2.0
EXPERIMENT_CHARGE_USD = Decimal("0.00")
CLI_API_RETRY_ENVIRONMENT_VARIABLE = "CLAUDE_CODE_MAX_RETRIES"
MAXIMUM_AGGREGATE_SPEND_USD = Decimal("10.00")
PRIOR_BUDGET_ACCOUNTED_SPEND_USD = Decimal("4.778164718")
CONTEXT_LIMIT_TOKENS = 200_000
PARSER_VERSION = "pixelgym-agent-v5-claude-stream-json-action-parser-v4-ui-render-preamble"
RESPONSE_SCHEMA_VERSION = "pixelgym-agent-v5-claude-stream-json-response-v1"
STATE_REDUCER_VERSION = "pixelgym-agent-v5-stateless-current-screenshot-reducer-v1"
MEMORY_POLICY_VERSION = "pixelgym-agent-v5-stateless-current-screenshot-only-v1"
TASK_RENDERER_VERSION = "pixelgym-agent-v5-task-renderer-v1"
TRANSPORT_RETRY_RULE = "one-claude-cli-process-per-action-no-runner-retry-v1"
INVOCATION_JOURNAL_SCHEMA_VERSION = "pixelgym-agent-v5-claude-cli-invocation-journal-v2"
SYSTEM_PROMPT = (
    "You are a pixel-only GUI policy. Use only the user-provided task text and "
    "inline screenshots, including any supplied history. The last screenshot is current. "
    "Do not use tools or request other context. Return exactly one action "
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

_MALFORMED_STREAM_VIOLATIONS = frozenset(
    {
        "invalid_jsonl",
        "invalid_event_envelope",
        "system_event_count_mismatch",
        "assistant_event_count_mismatch",
        "result_event_count_mismatch",
        "invalid_rate_limit_event",
        "invalid_assistant_message",
        "invalid_assistant_content",
        "invalid_thinking_telemetry",
        "invalid_ui_invalidation",
        "invalid_extended_stream_identity",
        "invalid_extended_stream_order",
        "assistant_text_count_mismatch",
        "assistant_result_text_mismatch",
    }
)


@dataclass(frozen=True)
class ClaudeRuntimeIdentity:
    cli_version: str
    auth_method: str
    subscription_type: str
    requested_model: str
    reasoning_effort: str
    help_sha256: str

    def __post_init__(self) -> None:
        if self.cli_version not in {route.cli_version for route in APPROVED_ROUTES}:
            raise ValueError("Claude Code version differs from the frozen adapter")
        if self.route not in APPROVED_ROUTES:
            raise ValueError("Claude Code must use the approved Max subscription authentication")
        if self.requested_model != MODEL or self.reasoning_effort != MODEL_REASONING_EFFORT:
            raise ValueError("Claude model or reasoning effort differs from the frozen policy")
        if not self.help_sha256.startswith("sha256:"):
            raise ValueError("Claude help digest is invalid")

    @property
    def route(self) -> ClaudeRoute:
        return ClaudeRoute(self.cli_version, self.auth_method, self.subscription_type)

    def to_dict(self) -> dict[str, str]:
        return {
            "cli_version": self.cli_version,
            "auth_method": self.auth_method,
            "subscription_type": self.subscription_type,
            "requested_model": self.requested_model,
            "reasoning_effort": self.reasoning_effort,
            "help_sha256": self.help_sha256,
        }


PROBE_TIMEOUT_SECONDS = 60.0


def _run_probe(
    command: Sequence[str],
    environment: Mapping[str, str] | None = None,
    *,
    timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run one CLI probe with a bound; a hung CLI fails the probe instead of blocking."""

    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=True,
        timeout=timeout_seconds,
        env=None if environment is None else dict(environment),
    )


ProbeRunner = Callable[..., subprocess.CompletedProcess[str]]


def _probe(
    run: ProbeRunner, command: tuple[str, ...], environment: Mapping[str, str] | None
) -> subprocess.CompletedProcess[str]:
    # Single-argument runners (the frozen D5.9 probe contract) stay valid.
    return run(command) if environment is None else run(command, environment)


def _required_help_flags(help_text: str) -> None:
    required_flags = (
        "--print",
        "--model",
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


def probe_claude_route(
    route: ClaudeRoute,
    *,
    run: ProbeRunner = _run_probe,
    environment: Mapping[str, str] | None = None,
) -> ClaudeRuntimeIdentity:
    """Bind the installed CLI to a declared route without a login or a provider call.

    Used to freeze a candidate before the owner has created the route's credential.
    The route's authentication fields are declared here and must be confirmed by
    ``probe_claude_runtime`` before any execution.
    """

    if route not in APPROVED_ROUTES:
        raise ValueError("route is not approved")
    version = _probe(run, ("claude", "--version"), environment).stdout.strip()
    if version != route.cli_version:
        raise ValueError("installed Claude Code version differs from the declared route")
    help_text = _probe(run, ("claude", "--help"), environment).stdout
    _required_help_flags(help_text)
    return ClaudeRuntimeIdentity(
        cli_version=version,
        auth_method=route.auth_method,
        subscription_type=route.subscription_type,
        requested_model=MODEL,
        reasoning_effort=MODEL_REASONING_EFFORT,
        help_sha256="sha256:" + sha256_bytes(help_text.encode("utf-8")),
    )


def probe_claude_runtime(
    *,
    run: ProbeRunner = _run_probe,
    environment: Mapping[str, str] | None = None,
    subscription_probe: Callable[[str], dict[str, Any]] = verify_max_subscription,
    on_subscription_verified: Callable[[dict[str, Any]], None] | None = None,
) -> ClaudeRuntimeIdentity:
    version = _probe(run, ("claude", "--version"), environment).stdout.strip()
    auth = json.loads(_probe(run, ("claude", "auth", "status", "--json"), environment).stdout)
    if not isinstance(auth, dict):
        raise TypeError("Claude authentication status is invalid")
    if auth.get("loggedIn") is not True:
        raise ValueError("Claude Code is not logged in")
    help_text = _probe(run, ("claude", "--help"), environment).stdout
    _required_help_flags(help_text)
    method = auth.get("authMethod")
    tier = auth.get("subscriptionType")
    proof = None
    if method == "oauth_token":
        if version != REPLICATION_CLI_VERSION or tier not in (None, SUBSCRIPTION_TYPE):
            raise ValueError("Claude token route differs from the approved Max identity")
        active_environment = os.environ if environment is None else environment
        access_value = active_environment.get(OAUTH_TOKEN_ENVIRONMENT_VARIABLE, "")
        if not access_value:
            raise ValueError("Claude token route requires the active runner credential")
        proof = subscription_probe(access_value)
        # The verifier authenticates with exactly the value supplied to the CLI.
        # Missing CLI metadata is filled only by its fresh Max organization proof.
        if proof.get("organization_type") != "claude_max":
            raise ValueError("OAuth profile does not verify the Max organization")
        tier = SUBSCRIPTION_TYPE
    identity = ClaudeRuntimeIdentity(
        cli_version=version,
        auth_method=str(method),
        subscription_type=str(tier),
        requested_model=MODEL,
        reasoning_effort=MODEL_REASONING_EFFORT,
        help_sha256="sha256:" + sha256_bytes(help_text.encode("utf-8")),
    )
    if proof is not None and on_subscription_verified is not None:
        on_subscription_verified(
            {**proof, "cli_reported_subscription_type": auth.get("subscriptionType")}
        )
    return identity


def sanitized_command_contract() -> tuple[str, ...]:
    return (
        "claude",
        "--print",
        "--verbose",
        "--model",
        MODEL,
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
    "USER",  # macOS Claude keychain account lookup requires this nonsecret identity.
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
)


def _minimal_environment(
    environment: Mapping[str, str], *, api_retry_limit: int | None = None
) -> dict[str, str]:
    if api_retry_limit is not None and (type(api_retry_limit) is not int or api_retry_limit < 0):
        raise ValueError("Claude CLI API retry limit must be a non-negative integer")
    result = {
        name: environment[name] for name in _ALLOWED_ENVIRONMENT_VARIABLES if environment.get(name)
    }
    if api_retry_limit is not None:
        result[CLI_API_RETRY_ENVIRONMENT_VARIABLE] = str(api_retry_limit)
    return result


def _claude_launch_enforcement(
    command: Sequence[str],
    environment: Mapping[str, str],
    *,
    api_retry_limit: int | None = None,
) -> RuntimeEnforcement:
    controls_match = tuple(command) == sanitized_command_contract()
    allowed_names = set(_ALLOWED_ENVIRONMENT_VARIABLES)
    if api_retry_limit is not None:
        allowed_names.add(CLI_API_RETRY_ENVIRONMENT_VARIABLE)
    environment_is_allowlisted = set(environment) <= allowed_names and (
        api_retry_limit is None
        or environment.get(CLI_API_RETRY_ENVIRONMENT_VARIABLE) == str(api_retry_limit)
    )
    return runtime_enforcement(
        argv=command,
        environment=environment,
        cli_restrictions_applied=controls_match,
        environment_allowlist_applied=environment_is_allowlisted,
    )


# --- Sandboxed launch (design record: docs/v5-policy-sandbox.md) ----------------------
#
# Under the kernel sandbox the CLI sees one per-launch workspace as HOME, config, and
# TMPDIR, reaches the network only through the runner's loopback CONNECT proxy, and
# authenticates with a token the runner injects into the child environment. Journals and
# records keep environment variable *names* only; `validate_credential_free` rejects any
# payload carrying a credential-shaped value.

DEFAULT_EGRESS_TARGETS = ("api.anthropic.com:443",)
OAUTH_TOKEN_ENVIRONMENT_VARIABLE = "CLAUDE_CODE_OAUTH_TOKEN"
_SANDBOXED_PATH = "/usr/bin:/bin"
_SANDBOXED_FIXED_ENVIRONMENT = (
    ("DISABLE_TELEMETRY", "1"),
    ("DISABLE_ERROR_REPORTING", "1"),
    ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1"),
)
_SANDBOXED_PASSTHROUGH_VARIABLES = ("LANG", "LC_ALL")
_SANDBOXED_RUNNER_VARIABLES = (
    "PATH",
    "HOME",
    "TMPDIR",
    "CLAUDE_CONFIG_DIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
)


@dataclass(frozen=True)
class ClaudeSandboxLaunch:
    """Runner-owned facts for launching the CLI under the kernel sandbox.

    The token source is called at each launch and its value goes only into the child
    environment. Nothing here is journaled directly; the launch record derives from it.
    """

    cli_executable: Path
    cli_root: Path
    scratch_root: Path
    oauth_token_source: Callable[[], str]
    egress_targets: tuple[str, ...] = DEFAULT_EGRESS_TARGETS
    denied_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not self.cli_executable.is_absolute() or not self.cli_root.is_absolute():
            raise ValueError("sandboxed CLI paths must be absolute")
        if not self.egress_targets:
            raise ValueError("sandboxed launch requires at least one egress target")

    @classmethod
    def from_environment(
        cls,
        *,
        cli_executable: Path,
        denied_paths: Sequence[Path] = (),
        environment: Mapping[str, str] = os.environ,
        egress_targets: Sequence[str] = DEFAULT_EGRESS_TARGETS,
    ) -> ClaudeSandboxLaunch:
        """Read the token from the runner's environment at each launch; never store it."""

        executable = cli_executable.resolve()
        return cls(
            cli_executable=executable,
            cli_root=executable.parent,
            scratch_root=cli_scratch_root(os.getuid()),
            oauth_token_source=lambda: environment.get(OAUTH_TOKEN_ENVIRONMENT_VARIABLE, ""),
            egress_targets=tuple(egress_targets),
            denied_paths=tuple(denied_paths),
        )


def sandboxed_environment_variable_names(api_retry_limit: int | None = None) -> tuple[str, ...]:
    """Every variable name a sandboxed launch may carry, sorted; values are never recorded."""

    names = {
        *_SANDBOXED_RUNNER_VARIABLES,
        *_SANDBOXED_PASSTHROUGH_VARIABLES,
        *(name for name, _ in _SANDBOXED_FIXED_ENVIRONMENT),
        OAUTH_TOKEN_ENVIRONMENT_VARIABLE,
    }
    if api_retry_limit is not None:
        names.add(CLI_API_RETRY_ENVIRONMENT_VARIABLE)
    return tuple(sorted(names))


def _sandboxed_environment(
    environment: Mapping[str, str],
    *,
    layout: CliLaunchLayout,
    proxy_url: str,
    oauth_token: str,
    api_retry_limit: int | None,
) -> dict[str, str]:
    """Child environment for one launch; an empty token is omitted, never sent as empty."""

    result = {
        "PATH": _SANDBOXED_PATH,
        "HOME": str(layout.home),
        "TMPDIR": str(layout.tmp),
        "CLAUDE_CONFIG_DIR": str(layout.config),
        "HTTPS_PROXY": proxy_url,
        "HTTP_PROXY": proxy_url,
    }
    for name in _SANDBOXED_PASSTHROUGH_VARIABLES:
        if environment.get(name):
            result[name] = environment[name]
    result.update(dict(_SANDBOXED_FIXED_ENVIRONMENT))
    if api_retry_limit is not None:
        result[CLI_API_RETRY_ENVIRONMENT_VARIABLE] = str(api_retry_limit)
    if oauth_token:
        result[OAUTH_TOKEN_ENVIRONMENT_VARIABLE] = oauth_token
    return result


def declared_sandboxed_launch_enforcement(
    *,
    api_retry_limit: int | None = None,
    egress_targets: Sequence[str] = DEFAULT_EGRESS_TARGETS,
) -> RuntimeEnforcement:
    """Plan-time declaration that every launch under a manifest runs sandboxed."""

    return sandboxed_launch_enforcement(
        argv=sanitized_command_contract(),
        environment_variable_names=sandboxed_environment_variable_names(api_retry_limit),
        cli_restrictions_applied=True,
        environment_allowlist_applied=True,
        profile_template_digest=cli_profile_template_digest(),
        egress_allowlist_digest=egress_allowlist_digest(egress_targets),
    )


@dataclass(frozen=True)
class _PreparedLaunch:
    command: tuple[str, ...]
    environment: dict[str, str]
    cwd: Path
    enforcement: RuntimeEnforcement
    egress_marker: int | None
    pre_send_failure: str | None
    error_type: str | None = None


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


def _decode_action_content(content: str) -> Any:
    """Accept bare JSON or one complete JSON fence, never extract from prose.

    This versioned envelope adapter leaves stored model output unchanged. Action
    fields still undergo exact-shape/type validation and host-side bounds checks.
    """
    if not isinstance(content, str):
        raise TypeError("Claude action content must be text")
    text = content.strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text, re.DOTALL)
        if match is None:
            raise ValueError("Claude action must be one complete JSON fence")
        text = match.group(1)

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate action field")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=unique_object)


class ClaudeCodePolicy:
    def __init__(self, *, runtime_identity: ClaudeRuntimeIdentity | None = None) -> None:
        self.expected_route = FROZEN_ROUTE if runtime_identity is None else runtime_identity.route
        if self.expected_route not in APPROVED_ROUTES:
            raise ValueError("unsupported Claude runtime route")

    def reset(self, task_instruction: str) -> bytes:
        return canonical_json_bytes({"instruction": task_instruction})

    def build_request(
        self, state: bytes, screenshot: bytes, *, screenshot_history: Sequence[bytes] = ()
    ) -> dict[str, Any]:
        if len(screenshot_history) > 31:
            raise ValueError("screenshot history exceeds 31 prior frames")
        value = json.loads(state)
        if not isinstance(value, dict) or set(value) != {"instruction"}:
            raise ValueError("Claude policy state is invalid")
        png = _png_bytes(screenshot)
        request: dict[str, Any] = {
            "provider": PROVIDER_IDENTITY,
            "model": MODEL,
            "model_reasoning_effort": MODEL_REASONING_EFFORT,
            "prompt": action_prompt(str(value["instruction"])),
            "image_png_base64": base64.b64encode(png).decode("ascii"),
            "image_sha256": "sha256:" + sha256_bytes(png),
            "action_schema_digest": content_digest(ACTION_SCHEMA),
            "command_contract_digest": command_contract_digest(),
        }
        if screenshot_history:
            request["image_history"] = [
                {
                    "image_png_base64": base64.b64encode(history_png).decode("ascii"),
                    "image_sha256": "sha256:" + sha256_bytes(history_png),
                }
                for history_png in map(_png_bytes, screenshot_history)
            ]
        return request

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
            "command_contract_digest": command_contract_digest(),
            "experiment_charge_usd": "0.00",
            "model_reasoning_effort": MODEL_REASONING_EFFORT,
            "policy_violation": "none",
        }
        if not isinstance(usage, dict) or any(
            usage.get(key) != expected_value for key, expected_value in expected.items()
        ):
            raise ValueError("Claude identity, accounting, or policy boundary is invalid")
        route = ClaudeRoute(
            str(usage.get("cli_version", "")),
            str(usage.get("auth_method", "")),
            str(usage.get("subscription_type", "")),
        )
        if route != self.expected_route:
            raise ValueError("Claude identity, accounting, or policy boundary is invalid")
        candidate = self._decode_action_content(response.get("content", ""))
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

    def _decode_action_content(self, content: str) -> Any:
        return _decode_action_content(content)

    def post_parse_state(self, state: bytes, candidate: dict[str, Any]) -> bytes:
        del candidate
        return state

    def post_dispatch_state(
        self, state: bytes, action: dict[str, int], result: PolicyVisibleResult
    ) -> bytes:
        del action, result
        return state

    def close(self) -> None:
        return None


def _start_process(command: Sequence[str], **kwargs: Any) -> RunningProcess:
    return start_process(command, **kwargs)


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
        raw_stdout: str | RedactedRawStdio,
        raw_stderr: str | RedactedRawStdio,
        outcome: dict[str, Any],
    ) -> None:
        stdout = (
            raw_stdout if isinstance(raw_stdout, RedactedRawStdio) else redact_raw_stdio(raw_stdout)
        )
        stderr = (
            raw_stderr if isinstance(raw_stderr, RedactedRawStdio) else redact_raw_stdio(raw_stderr)
        )
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
    initial_events = [
        event for event in events if event["type"] == "system" and event.get("subtype") == "init"
    ]
    if len(initial_events) != 1:
        violations.append("system_event_count_mismatch")
    assistant_events = [event for event in events if event["type"] == "assistant"]
    telemetry_events = [
        event
        for event in events
        if event["type"] == "system" and event.get("subtype") == "thinking_tokens"
    ]
    ui_events = [
        event
        for event in events
        if event["type"] == "system" and event.get("subtype") == "ui_invalidate"
    ]
    if ui_events and (
        len(ui_events) != 1
        or events[0] is not ui_events[0]
        or set(ui_events[0]) != {"type", "subtype", "event", "uuid", "session_id"}
        or ui_events[0].get("event") != "ui.render"
    ):
        violations.append("invalid_ui_invalidation")
    if len(assistant_events) not in (1, 2):
        violations.append("assistant_event_count_mismatch")
    if event_counts["result"] != 1:
        violations.append("result_event_count_mismatch")
    extended_stream = bool(telemetry_events or ui_events) or len(assistant_events) == 2
    if extended_stream:
        sessions = [event.get("session_id") for event in events]
        uuids = [event.get("uuid") for event in events]
        if (
            not all(isinstance(value, str) and value for value in sessions + uuids)
            or len(set(sessions)) != 1
            or len(set(uuids)) != len(uuids)
        ):
            violations.append("invalid_extended_stream_identity")
        semantic_events = [event for event in events if event not in ui_events]
        if (
            not semantic_events
            or semantic_events[0] not in initial_events
            or semantic_events[-1]["type"] != "result"
        ):
            violations.append("invalid_extended_stream_order")
        previous_estimate = 0
        for telemetry in telemetry_events:
            if set(telemetry) != {
                "type",
                "subtype",
                "estimated_tokens",
                "estimated_tokens_delta",
                "uuid",
                "session_id",
            }:
                violations.append("invalid_thinking_telemetry")
                continue
            estimate = telemetry["estimated_tokens"]
            delta = telemetry["estimated_tokens_delta"]
            if (
                type(estimate) is not int
                or type(delta) is not int
                or estimate < 0
                or delta < 0
                or estimate != previous_estimate + delta
            ):
                violations.append("invalid_thinking_telemetry")
            else:
                previous_estimate = estimate
    if len(assistant_events) == 2:
        messages = [event.get("message") for event in assistant_events]
        if not all(isinstance(message, dict) for message in messages):
            violations.append("invalid_assistant_message")
        else:
            validated_messages = cast(list[dict[str, Any]], messages)
            message_ids = [message.get("id") for message in validated_messages]
            request_ids = [event.get("request_id") for event in assistant_events]
            if (
                not all(isinstance(value, str) and value for value in message_ids + request_ids)
                or len(set(message_ids)) != 1
                or len(set(request_ids)) != 1
            ):
                violations.append("invalid_extended_stream_identity")
            contents: list[Any] = [message.get("content") for message in validated_messages]
            if (
                not all(isinstance(content, list) and content for content in contents)
                or not all(
                    isinstance(block, dict) and block.get("type") == "thinking"
                    for block in contents[0]
                )
                or len(contents[1]) != 1
                or not isinstance(contents[1][0], dict)
                or contents[1][0].get("type") != "text"
            ):
                violations.append("invalid_assistant_content")
    assistant_texts: list[str] = []
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
            if event.get("subtype") in {"thinking_tokens", "ui_invalidate"}:
                continue
            if event.get("subtype") != "init":
                violations.append(f"unauthorized_system_event:{event.get('subtype')}")
                continue
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
                if block_type == "text":
                    if not isinstance(block.get("text"), str):
                        violations.append("invalid_assistant_content")
                    else:
                        assistant_texts.append(block["text"])
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
        if resolved_model != MODEL:
            violations.append("resolved_model_mismatch")
    if result.get("structured_output") is not None:
        violations.append("unexpected_structured_output")
    result_text = result.get("result")
    content = result_text if isinstance(result_text, str) else ""
    if len(assistant_texts) != 1:
        if not any(value.startswith("unauthorized_content_block:") for value in violations):
            violations.append("assistant_text_count_mismatch")
    elif extended_stream and assistant_texts[0] != content:
        violations.append("assistant_result_text_mismatch")
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


def _claude_parse_envelope(raw_stdout: str) -> StreamParseEnvelope[ParsedClaudeStream]:
    parsed = _parse_stream(raw_stdout)
    return StreamParseEnvelope(
        parsed=parsed,
        stream_malformed=bool(_MALFORMED_STREAM_VIOLATIONS.intersection(parsed.policy_violations)),
        subscription_rate_limited=("subscription_rate_limit_rejected" in parsed.policy_violations),
        usage_observed=parsed.usage is not None,
        cost_observed=parsed.informational_cost_usd is not None,
    )


def _synthetic_connection_fault(
    raw_stdout: str, parsed: ParsedClaudeStream, return_code: int | None
) -> CliFault | None:
    """Recognize the CLI's error envelope, never a model answer containing error text."""
    if return_code != 1 or set(parsed.policy_violations) != {
        "claude_result_error",
        "resolved_model_mismatch",
    }:
        return None
    events = [json.loads(line) for line in raw_stdout.splitlines() if line.strip()]
    # The parser has already validated any UI preamble, including its identity.
    if events and events[0].get("subtype") == "ui_invalidate":
        events = events[1:]
    if [event.get("type") for event in events] != ["system", "assistant", "result"]:
        return None
    assistant, result = events[1:]
    message = assistant.get("message", {})
    if (
        assistant.get("error") != "server_error"
        or message.get("model") != "<synthetic>"
        or result.get("is_error") is not True
        or result.get("modelUsage") != {}
        or result.get("result") != "API Error: Connection dropped (ECONNRESET)"
        or message.get("content") != [{"type": "text", "text": result["result"]}]
        or parsed.usage is None
        or any(parsed.usage.values())
        or parsed.informational_cost_usd != 0
    ):
        return None
    return classify_cli_process_fault(
        return_code=return_code,
        stderr="ECONNRESET",
        stream_malformed=False,
    )


def _synthetic_authentication_fault(
    raw_stdout: str, parsed: ParsedClaudeStream, return_code: int | None
) -> CliFault | None:
    """Only a stopped, zero-usage synthetic OAuth-expiry rejection is retryable."""
    if return_code != 1 or set(parsed.policy_violations) != {
        "claude_result_error",
        "resolved_model_mismatch",
    }:
        return None
    try:
        events = [json.loads(line) for line in raw_stdout.splitlines() if line.strip()]
    except (ValueError, TypeError):
        return None
    if not all(isinstance(event, dict) for event in events):
        return None
    if events and events[0].get("subtype") == "ui_invalidate":
        events = events[1:]
    if [event.get("type") for event in events] != ["system", "assistant", "result"]:
        return None
    assistant, result = events[1:]
    message = assistant.get("message", {})
    text = result.get("result")
    if not isinstance(message, dict):
        return None
    if (
        assistant.get("error") != "authentication_failed"
        or message.get("model") != "<synthetic>"
        or result.get("is_error") is not True
        or result.get("modelUsage") != {}
        or not isinstance(text, str)
        or not text.startswith("API Error: 401 ")
        or message.get("content") != [{"type": "text", "text": text}]
        or parsed.usage is None
        or any(parsed.usage.values())
        or parsed.informational_cost_usd != 0
    ):
        return None
    try:
        body = json.loads(text[len("API Error: 401 ") :])
        error = body["error"]
        expired = (
            body.get("type") == "error"
            and error.get("type") == "authentication_error"
            and error.get("message", "").lower()
            in {
                (
                    "oauth token has expired. please obtain a new token "
                    "or refresh your existing token."
                ),
                "oauth token has expired. please run /login.",
            }
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if not expired:
        return None
    from pixelgym.grounding.v5.contracts import CostKnowledge, ModelAttemptConsumption

    return CliFault(
        kind=CliFaultKind.AUTHENTICATION_EXPIRED,
        code="cli_oauth_expired",
        phase="post_send",
        classification="request_failure",
        model_attempt_consumption=ModelAttemptConsumption.NOT_CONSUMED,
        cost_knowledge=CostKnowledge.ZERO,
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
        allow_timeout_retry: bool = False,
        allow_connection_retry: bool = False,
        api_retry_limit: int | None = None,
        sandbox_launch: ClaudeSandboxLaunch | None = None,
        authentication_recovery: OAuthRecoveryGate | None = None,
    ) -> None:
        ClaudeRuntimeIdentity(**runtime_identity.__dict__)
        if process_timeout_seconds <= 0:
            raise ValueError("Claude process timeout must be positive")
        if allow_connection_retry and api_retry_limit != 0:
            raise ValueError("counted connection retries require zero internal CLI retries")
        self.sandbox_launch = sandbox_launch
        if authentication_recovery is not None and (
            runtime_identity.route != REPLICATION_ROUTE or api_retry_limit != 0
        ):
            raise ValueError("authentication recovery requires the zero-internal-retry token route")
        self.authentication_recovery = authentication_recovery
        self.allow_authentication_retry = authentication_recovery is not None
        self.allow_connection_retry = allow_connection_retry
        self.ledger = ledger
        self.invocation_journal = invocation_journal
        self.runtime_identity = runtime_identity
        self.expected_resolved_model = expected_resolved_model
        self.api_retry_limit = api_retry_limit
        self.environment = _minimal_environment(environment, api_retry_limit=api_retry_limit)
        self.process_factory = process_factory
        self.process_timeout_seconds = process_timeout_seconds
        self.allow_timeout_retry = allow_timeout_retry
        self.records: list[dict[str, Any]] = []
        self._lifecycle = CliSubprocessTransport(
            parser=_claude_parse_envelope,
            process_factory=process_factory,
            terminate_grace_seconds=TERMINATE_GRACE_SECONDS,
            kill_grace_seconds=KILL_GRACE_SECONDS,
        )
        self._closed = False
        # Last, after every validation: a failed constructor must not leave a bound socket.
        self._proxy: EgressProxy | None = (
            None if sandbox_launch is None else EgressProxy(sandbox_launch.egress_targets).start()
        )

    @property
    def subprocesses_closed(self) -> bool:
        return self._lifecycle.subprocesses_closed

    @contextmanager
    def attempt_guard(self) -> Iterator[None]:
        recovery = self.authentication_recovery
        if recovery is not None:
            recovery.begin_attempt()
        try:
            yield
        finally:
            if recovery is not None:
                recovery.end_attempt()

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        if self._closed:
            return cli_fault_outcome(cli_pre_send_fault("transport_closed"))
        failure = self._validate_request(request, deadline_seconds)
        if failure is not None:
            return cli_fault_outcome(cli_pre_send_fault(failure))
        if not self.ledger.reserve(idempotency_key):
            return cli_fault_outcome(cli_pre_send_fault("subscription_guard"))
        if not self.invocation_journal.reserve(
            idempotency_key=idempotency_key,
            request_digest=content_digest(request),
        ):
            self.ledger.release_pre_send(idempotency_key)
            return cli_fault_outcome(cli_pre_send_fault("duplicate_invocation"))
        outcome: dict[str, Any]
        with tempfile.TemporaryDirectory(prefix="pixelgym-claude-cli-") as temporary:
            try:
                launch = self._prepare_launch(Path(temporary))
            except (ValueError, OSError) as exc:
                # A launch that cannot be prepared costs nothing and must not strand the
                # ledger reservation or the journal row.
                launch = _PreparedLaunch(
                    command=tuple(_runtime_command()),
                    environment={},
                    cwd=Path(temporary),
                    enforcement=unbound_runtime_enforcement(),
                    egress_marker=None,
                    pre_send_failure="launch_preparation_failure",
                    error_type=type(exc).__name__,
                )
            command = launch.command
            launch_enforcement = launch.enforcement
            enforcement_record = launch_enforcement.to_dict()
            failure_code = launch.pre_send_failure
            if failure_code is None:
                try:
                    validate_runtime_enforcement(
                        PolicyClaim(DECLARED_UNAVAILABLE_CAPABILITIES),
                        launch_enforcement,
                    )
                except ValueError:
                    failure_code = "runtime_enforcement_mismatch"
            if failure_code is not None:
                self.ledger.release_pre_send(idempotency_key)
                fault = cli_pre_send_fault(failure_code)
                outcome = {
                    "failure_code": failure_code,
                    "cli_fault": fault.to_dict(),
                    "runtime_enforcement": enforcement_record,
                    **({} if launch.error_type is None else {"type": launch.error_type}),
                }
                transport_outcome = cli_fault_outcome(fault)
                outcome["transport_outcome"] = transport_outcome.to_dict()
                self.invocation_journal.finish(
                    idempotency_key,
                    status="pre_send_failure",
                    exit_code=None,
                    raw_stdout="",
                    raw_stderr="",
                    outcome=outcome,
                )
                self.records.append(self._record(idempotency_key, "pre_send_failure", outcome))
                return transport_outcome
            image_blocks = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image["image_png_base64"],
                    },
                }
                for image in [*request.get("image_history", []), request]
            ]
            input_event = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [*image_blocks, {"type": "text", "text": request["prompt"]}],
                },
            }

            def mark_started(started: RunningProcess) -> None:
                self.ledger.mark_process_started()
                self.invocation_journal.mark_running(idempotency_key, started.pid)

            try:
                execution = self._lifecycle.run(
                    invocation_id=idempotency_key,
                    command=command,
                    input_text=json.dumps(input_event, separators=(",", ":")) + "\n",
                    cwd=launch.cwd,
                    environment=launch.environment,
                    timeout_seconds=min(self.process_timeout_seconds, deadline_seconds),
                    timeout_failure_code="claude_process_timeout",
                    on_started=mark_started,
                )
            except CliProcessInterrupted as interrupted:
                execution = interrupted.envelope
                self.ledger.retain_unresolved_and_block(idempotency_key)
                assert execution.fault is not None
                fault = execution.fault
                outcome = {
                    "failure_code": fault.code,
                    "type": type(interrupted.cause).__name__,
                    "cli_fault": fault.to_dict(),
                    "runtime_enforcement": enforcement_record,
                    "egress_attempts": self._egress_summary(launch),
                }
                outcome["transport_outcome"] = cli_fault_outcome(fault).to_dict()
                self.invocation_journal.finish(
                    idempotency_key,
                    status="interrupted",
                    exit_code=execution.return_code,
                    raw_stdout=execution.stdout,
                    raw_stderr=execution.stderr,
                    outcome=outcome,
                )
                self.records.append(self._record(idempotency_key, "interrupted", outcome))
                raise interrupted.cause

            execution_fault = execution.fault
            if execution_fault is not None and execution.parsed is None:
                failure_status: str = (
                    "pre_send_failure"
                    if execution_fault.phase == "pre_send"
                    else execution_fault.classification
                )
                if execution_fault.phase == "pre_send":
                    self.ledger.release_pre_send(idempotency_key)
                elif (
                    (
                        self.allow_timeout_retry
                        and execution_fault.kind is CliFaultKind.PROCESS_TIMEOUT
                    )
                    or (
                        self.allow_connection_retry
                        and execution_fault.kind is CliFaultKind.CONNECTION_RESET
                    )
                ) and execution.process_confirmed_stopped:
                    self.ledger.retain_stopped_timeout(idempotency_key)
                else:
                    self.ledger.retain_unresolved_and_block(idempotency_key)
                if execution_fault.kind is CliFaultKind.PROCESS_TIMEOUT:
                    failure_status = "timeout"
                transport_outcome = cli_fault_outcome(execution_fault)
                outcome = {
                    "failure_code": execution_fault.code,
                    "type": execution.error_type,
                    "process_confirmed_stopped": execution.process_confirmed_stopped,
                    "cli_fault": execution_fault.to_dict(),
                    "runtime_enforcement": enforcement_record,
                    "egress_attempts": self._egress_summary(launch),
                    "transport_outcome": transport_outcome.to_dict(),
                }
                self.invocation_journal.finish(
                    idempotency_key,
                    status=failure_status,
                    exit_code=execution.return_code,
                    raw_stdout=execution.stdout,
                    raw_stderr=execution.stderr,
                    outcome=outcome,
                )
                self.records.append(self._record(idempotency_key, failure_status, outcome))
                return transport_outcome
            if execution.parsed is None:
                self.ledger.retain_unresolved_and_block(idempotency_key)
                raise RuntimeError("CLI lifecycle returned neither parsed output nor a fault")
        parsed = execution.parsed
        violations = list(parsed.policy_violations)
        completed_fault = execution.fault
        if self.allow_connection_retry:
            # Stderr is diagnostic text, not authority to retry a parsed response.
            # Only the verified synthetic error envelope may opt that stream in.
            if (
                completed_fault is not None
                and completed_fault.kind is CliFaultKind.CONNECTION_RESET
            ):
                completed_fault = replace(
                    completed_fault,
                    kind=CliFaultKind.NONZERO_EXIT,
                    code="cli_connection_reset_in_diagnostic",
                )
            if execution.process_confirmed_stopped and not execution.stdout.credential_redacted:
                connection_fault = _synthetic_connection_fault(
                    execution.stdout.value, parsed, execution.return_code
                )
                if connection_fault is not None:
                    completed_fault = connection_fault
        if (
            self.expected_resolved_model is not None
            and parsed.resolved_model != self.expected_resolved_model
        ):
            violations.append("resolved_model_differs_from_smoke")
        egress = self._egress_summary(launch)
        if egress is not None and egress["refused_count"]:
            violations.append("unauthorized_egress_attempt")
        if (
            self.authentication_recovery is not None
            and execution.process_confirmed_stopped
            and not execution.stdout.credential_redacted
            and not execution.stderr.credential_redacted
            and not (egress and egress["refused_count"])
        ):
            auth_fault = _synthetic_authentication_fault(
                execution.stdout.value, parsed, execution.return_code
            )
            if auth_fault is not None:
                completed_fault = auth_fault
                violations = []  # The verified envelope is a rejection, not a model response.
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
        if completed_fault is not None:
            fault = completed_fault
            transport_outcome = cli_fault_outcome(fault)
            outcome = {
                "event_counts": parsed.event_counts,
                "exit_code": execution.return_code,
                "runtime_enforcement": enforcement_record,
                "egress_attempts": egress,
                "resolved_model": parsed.resolved_model,
                "policy_violation": violation_value,
                "stream_violations": violations,
                "cli_fault": fault.to_dict(),
                "experiment_charge_usd": "0.00",
                "informational_cost_telemetry_usd": (
                    str(parsed.informational_cost_usd)
                    if parsed.informational_cost_usd is not None
                    else None
                ),
                "usage_telemetry_status": usage_status,
                "transport_outcome": transport_outcome.to_dict(),
            }
            self.invocation_journal.finish(
                idempotency_key,
                status=fault.classification,
                exit_code=execution.return_code,
                raw_stdout=execution.stdout,
                raw_stderr=execution.stderr,
                outcome=outcome,
            )
            self.records.append(self._record(idempotency_key, fault.classification, outcome))
            if (
                fault.kind is CliFaultKind.AUTHENTICATION_EXPIRED
                and self.authentication_recovery is not None
            ):
                self.authentication_recovery.require_login()
            return transport_outcome
        usage_record = {
            **(parsed.usage or {}),
            "auth_method": self.runtime_identity.auth_method,
            "subscription_type": self.runtime_identity.subscription_type,
            "cli_version": self.runtime_identity.cli_version,
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
        if egress is not None:
            usage_record["egress_connect_allowed"] = egress["allowed_count"]
            usage_record["egress_connect_refused"] = egress["refused_count"]
        canonical = {
            "response_id": execution.stdout.original_sha256,
            "model": MODEL,
            "content": content,
            "finish_reason": "stop" if violation_value == "none" else "policy_violation",
            "usage": usage_record,
        }
        outcome = {
            "event_counts": parsed.event_counts,
            "exit_code": execution.return_code,
            "runtime_enforcement": enforcement_record,
            "egress_attempts": egress,
            "resolved_model": parsed.resolved_model,
            "policy_violation": violation_value,
            "experiment_charge_usd": "0.00",
            "informational_cost_telemetry_usd": usage_record["informational_cost_telemetry_usd"],
            "usage_telemetry_status": usage_status,
            "canonical_response": canonical,
        }
        status: Literal["response", "policy_violation"] = (
            "response" if violation_value == "none" else "policy_violation"
        )
        transport_outcome = TransportOutcome(status, canonical)
        outcome["transport_outcome"] = transport_outcome.to_dict()
        self.invocation_journal.finish(
            idempotency_key,
            status=status,
            exit_code=execution.return_code,
            raw_stdout=execution.stdout,
            raw_stderr=execution.stderr,
            outcome=outcome,
        )
        self.records.append(self._record(idempotency_key, status, outcome))
        return transport_outcome

    def cancel(self, *, idempotency_key: str, mode: str) -> Literal["cancelled", "unknown"]:
        del mode
        return "cancelled" if self._lifecycle.cancel(idempotency_key) else "unknown"

    def reconcile(self, *, idempotency_key: str, deadline_seconds: float) -> TransportOutcome:
        del deadline_seconds
        record = self.invocation_journal.record(idempotency_key)
        if record is None or record["status"] in {"reserved", "running"}:
            return TransportOutcome("unknown", failure_code="invocation_unresolved")
        outcome = record.get("outcome")
        if isinstance(outcome, dict) and isinstance(outcome.get("transport_outcome"), dict):
            return TransportOutcome.from_dict(outcome["transport_outcome"])
        if isinstance(outcome, dict) and isinstance(outcome.get("canonical_response"), dict):
            return TransportOutcome("response", outcome["canonical_response"])
        return TransportOutcome("unknown", failure_code="invocation_not_recoverable")

    def close(self) -> None:
        self._lifecycle.close()
        if self._proxy is not None:
            self._proxy.close()
            self._proxy = None
        self._closed = True

    def _prepare_launch(self, temporary: Path) -> _PreparedLaunch:
        command = _runtime_command()
        if self.sandbox_launch is None:
            return _PreparedLaunch(
                command=tuple(command),
                environment=dict(self.environment),
                cwd=temporary,
                enforcement=_claude_launch_enforcement(
                    command, self.environment, api_retry_limit=self.api_retry_limit
                ),
                egress_marker=None,
                pre_send_failure=None,
            )
        launch = self.sandbox_launch
        proxy = self._proxy
        if proxy is None:
            raise RuntimeError("sandboxed transport has no egress proxy")
        layout = CliLaunchLayout(temporary).create()
        profile = darwin_cli_profile(
            layout,
            cli_executable=launch.cli_executable,
            cli_root=launch.cli_root,
            proxy_port=proxy.port,
            scratch_root=launch.scratch_root,
            denied_paths=launch.denied_paths,
        )
        oauth_token = launch.oauth_token_source()
        environment = _sandboxed_environment(
            self.environment,
            layout=layout,
            proxy_url=proxy.url,
            oauth_token=oauth_token,
            api_retry_limit=self.api_retry_limit,
        )
        contract = sanitized_command_contract()
        allowed_names = set(sandboxed_environment_variable_names(self.api_retry_limit))
        enforcement = sandboxed_launch_enforcement(
            argv=contract,
            environment_variable_names=tuple(environment),
            cli_restrictions_applied=tuple(command) == contract,
            environment_allowlist_applied=set(environment) <= allowed_names,
            profile_template_digest=cli_profile_template_digest(),
            egress_allowlist_digest=proxy.allowlist_digest,
            profile_digest=cli_profile_digest(profile),
        )
        executable_command = (str(launch.cli_executable.resolve()), *command[1:])
        return _PreparedLaunch(
            command=sandboxed_command(profile, executable_command),
            environment=environment,
            cwd=layout.cwd,
            enforcement=enforcement,
            egress_marker=proxy.sequence,
            pre_send_failure=(
                "os_sandbox_unsupported_platform"
                if platform.system() != "Darwin"
                else "oauth_token_unavailable"
                if not oauth_token
                else None
            ),
        )

    def _egress_summary(self, launch: _PreparedLaunch) -> dict[str, Any] | None:
        if launch.egress_marker is None or self._proxy is None:
            return None
        return summarize_attempts(self._proxy.attempts_since(launch.egress_marker))

    def retry_allowed(self, outcome: TransportOutcome) -> bool:
        return (
            outcome.fault is not None
            and (
                (self.allow_timeout_retry and outcome.fault.kind is CliFaultKind.PROCESS_TIMEOUT)
                or (
                    self.allow_connection_retry
                    and outcome.fault.kind is CliFaultKind.CONNECTION_RESET
                )
                or (
                    self.allow_authentication_retry
                    and outcome.fault.kind is CliFaultKind.AUTHENTICATION_EXPIRED
                )
            )
            and self.subprocesses_closed
            and not self.ledger.blocked
        )

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
        if "image_history" in request:
            expected_keys.add("image_history")
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
        history = request.get("image_history", [])
        if (
            not isinstance(history, list)
            or len(history) > 31
            or any(
                not isinstance(item, dict) or set(item) != {"image_png_base64", "image_sha256"}
                for item in history
            )
        ):
            return "image_history_shape_mismatch"
        for item in [*history, request]:
            try:
                image = base64.b64decode(item["image_png_base64"], validate=True)
            except (TypeError, ValueError):
                return "image_encoding_invalid"
            if item.get("image_sha256") != "sha256:" + sha256_bytes(image):
                return "image_digest_mismatch"
        return None

    def _record(
        self, idempotency_key: str, status: str, outcome: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "idempotency_key_digest": content_digest(idempotency_key),
            "status": status,
            "cli_version": self.runtime_identity.cli_version,
            "model": MODEL,
            "model_reasoning_effort": MODEL_REASONING_EFFORT,
            "auth_method": self.runtime_identity.auth_method,
            "subscription_type": self.runtime_identity.subscription_type,
            "command_contract_digest": command_contract_digest(),
            "runtime_enforcement": outcome.get("runtime_enforcement"),
            "egress_attempts": outcome.get("egress_attempts"),
            "resolved_model": outcome.get("resolved_model"),
            "experiment_charge_usd": outcome.get("experiment_charge_usd", "0.00"),
            "informational_cost_telemetry_usd": outcome.get("informational_cost_telemetry_usd"),
            "policy_violation": outcome.get("policy_violation", "none"),
            "type": outcome.get("type"),
            "cli_fault": outcome.get("cli_fault"),
            "usage_telemetry_status": outcome.get("usage_telemetry_status", "unavailable"),
        }


def _file_digest(path: Path) -> str:
    return "sha256:" + sha256_bytes(path.read_bytes())


def build_claude_policy_manifest(
    repository_root: Path,
    *,
    code_revision: str,
    runtime_identity: ClaudeRuntimeIdentity,
    resolved_model: str | None,
    api_retry_limit: int | None = None,
    sandboxed_launch: bool = False,
) -> PolicyManifest:
    ClaudeRuntimeIdentity(**runtime_identity.__dict__)
    runtime_inputs = {
        "provider_module": _file_digest(
            repository_root / "pixelgym/grounding/v5/claude_code_policy.py"
        ),
        "cli_transport_module": _file_digest(
            repository_root / "pixelgym/grounding/v5/cli_transport.py"
        ),
        "runner_module": _file_digest(repository_root / "pixelgym/grounding/v5/runner.py"),
        "pyproject": _file_digest(repository_root / "pyproject.toml"),
        "lock": _file_digest(repository_root / "requirements/platform-py312.lock"),
        "runtime_identity": runtime_identity.to_dict(),
    }
    # The environment-token route requires this verifier. Older Keychain-route
    # evidence predates it and must retain its recorded runtime-input schema.
    if runtime_identity.auth_method == "oauth_token":
        runtime_inputs["subscription_verifier_module"] = _file_digest(
            repository_root / "pixelgym/grounding/v5/claude_subscription.py"
        )
    runtime_digest = content_digest(runtime_inputs)
    sandbox = build_sandbox_manifest(
        runtime_digest=runtime_digest,
        provider_endpoint=PROVIDER_ORIGIN,
        launch_enforcement=(
            declared_sandboxed_launch_enforcement(api_retry_limit=api_retry_limit)
            if sandboxed_launch
            else unbound_runtime_enforcement()
        ),
        policy_claim=PolicyClaim(()),
    )
    inference_parameters = (
        ("auth_method", runtime_identity.auth_method),
        ("subscription_type", runtime_identity.subscription_type),
        ("cli_version", runtime_identity.cli_version),
        ("command_contract_digest", command_contract_digest()),
        ("model_reasoning_effort", MODEL_REASONING_EFFORT),
        ("resolved_model", resolved_model or "to_be_bound_by_successful_smoke"),
        ("tools", "none"),
        ("mcp", "none"),
        ("claude_max_turns", "1"),
        ("structured_output_auto_retry", "disabled_no_json_schema_flag"),
        ("runner_retries", "0"),
        *(
            (
                ("os_sandbox_mechanism", OS_SANDBOX_CLI_LAUNCH_MECHANISM),
                ("egress_targets", ",".join(DEFAULT_EGRESS_TARGETS)),
                ("auth_route", "oauth_token_environment"),
            )
            if sandboxed_launch
            else ()
        ),
        *(
            ()
            if api_retry_limit is None
            else (
                ("cli_api_retry_limit", str(api_retry_limit)),
                (
                    "cli_api_retry_environment_variable",
                    CLI_API_RETRY_ENVIRONMENT_VARIABLE,
                ),
            )
        ),
    )
    return PolicyManifest.build(
        provider=PROVIDER_IDENTITY,
        model=MODEL,
        exact_snapshot=resolved_model is not None,
        harness_digest=_file_digest(repository_root / "pixelgym/grounding/v5/runner.py"),
        dependency_lock_digest=_file_digest(repository_root / "requirements/platform-py312.lock"),
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
