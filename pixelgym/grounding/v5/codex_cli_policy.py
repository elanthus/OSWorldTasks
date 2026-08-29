"""Fail-closed Codex CLI policy and transport for the PixelGym v5 workload."""

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
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from PIL import Image

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.grounding.v5.contracts import (
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    PolicyManifest,
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5.coordinates import IDENTITY_ADAPTER
from pixelgym.grounding.v5.evidence import CredentialValidationError, validate_credential_free
from pixelgym.grounding.v5.runner import TransportOutcome
from pixelgym.grounding.v5.sandbox import build_sandbox_manifest
from pixelgym.serialization import canonical_json_bytes

CODEX_CLI_VERSION = "codex-cli 0.150.1"
MODEL = "gpt-5.6-luna"
MODEL_CATALOG_COMP_HASH = "3000"
MODEL_REASONING_EFFORT = "low"
AUTH_MODE = "chatgpt_subscription"
PROVIDER_IDENTITY = "codex-cli/chatgpt-subscription"
PROVIDER_ORIGIN = "https://chatgpt.com"
MODEL_PROVIDER_ID = "pixelgym_openai_zero_retry"

MODEL_CONTEXT_WINDOW_TOKENS = 272_000
ROLLOUT_BUDGET_TOKENS = 8_192
ROLLOUT_REMINDER_INTERVAL_TOKENS = 2_048
PROCESS_TIMEOUT_SECONDS = 90.0
RUNNER_REQUEST_DEADLINE_SECONDS = 95.0
TERMINATE_GRACE_SECONDS = 2.0
KILL_GRACE_SECONDS = 2.0

# Official standard long-context prices observed on 2026-08-29. Input is charged at
# the higher cache-write rate and reasoning tokens are charged again in addition to
# output tokens. This deliberately overstates the published standard list price.
PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing/"
SUBSCRIPTION_SOURCE = "https://learn.chatgpt.com/docs/pricing"
PRICE_OBSERVED_AT_UTC = "2026-08-29T00:00:00Z"
CONSERVATIVE_INPUT_PER_TOKEN_USD = Decimal("0.00000050")
CONSERVATIVE_OUTPUT_PER_TOKEN_USD = Decimal("0.00000180")
REQUEST_MAXIMUM_INFORMATIONAL_LIST_PRICE_EQUIVALENT_USD = (
    Decimal(MODEL_CONTEXT_WINDOW_TOKENS) * CONSERVATIVE_OUTPUT_PER_TOKEN_USD * 2
)
LUNA_EXPERIMENT_CHARGE_USD = Decimal("0.00")

PROMPT_VERSION = "pixelgym-agent-v5-codex-cli-current-screenshot-prompt-v1"
PARSER_VERSION = "pixelgym-agent-v5-json-action-codex-cli-native-parser-v1"
STATE_REDUCER_VERSION = "pixelgym-agent-v5-stateless-current-screenshot-reducer-v1"
MEMORY_POLICY_VERSION = "pixelgym-agent-v5-stateless-current-screenshot-only-v1"
RESPONSE_SCHEMA_VERSION = "pixelgym-agent-v5-codex-cli-jsonl-response-v1"
TASK_RENDERER_VERSION = "pixelgym-agent-v5-task-renderer-v1"
TRANSPORT_RETRY_RULE = "codex-cli-zero-request-zero-stream-zero-runner-retries-v1"
INVOCATION_JOURNAL_SCHEMA_VERSION = "pixelgym-agent-v5-codex-cli-invocation-journal-v2"

ACTION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "action_type": {"type": "integer", "enum": [0, 1, 2]},
        "x": {"type": "integer", "minimum": 0, "maximum": SCREEN_WIDTH - 1},
        "y": {"type": "integer", "minimum": 0, "maximum": SCREEN_HEIGHT - 1},
        "key": {
            "type": "integer",
            "minimum": 0,
            "maximum": len(KEY_ALLOWLIST) - 1,
        },
    },
    "required": ["action_type", "x", "y", "key"],
    "additionalProperties": False,
}

_DISABLED_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "code_mode",
    "code_mode_host",
    "computer_use",
    "enable_mcp_apps",
    "goals",
    "hooks",
    "image_generation",
    "in_app_browser",
    "memories",
    "multi_agent",
    "multi_agent_v2",
    "plugins",
    "recommended_plugins",
    "remote_plugin",
    "shell_snapshot",
    "shell_snapshot_v2",
    "shell_tool",
    "skill_mcp_dependency_install",
    "skill_search",
    "standalone_web_search",
    "tool_call_mcp_elicitation",
    "tool_suggest",
    "unbounded_connection_retries",
    "unified_exec",
    "view_image",
    "workspace_dependencies",
)

_CONFIG_OVERRIDES = (
    'approval_policy="never"',
    f'model_provider="{MODEL_PROVIDER_ID}"',
    f'model_providers.{MODEL_PROVIDER_ID}.name="OpenAI"',
    f'model_providers.{MODEL_PROVIDER_ID}.wire_api="responses"',
    f"model_providers.{MODEL_PROVIDER_ID}.requires_openai_auth=true",
    f"model_providers.{MODEL_PROVIDER_ID}.supports_websockets=false",
    f"model_providers.{MODEL_PROVIDER_ID}.supports_standalone_web_search=false",
    f'model_providers.{MODEL_PROVIDER_ID}.http_headers={{version="0.150.1"}}',
    f"model_providers.{MODEL_PROVIDER_ID}.request_max_retries=0",
    f"model_providers.{MODEL_PROVIDER_ID}.stream_max_retries=0",
    f'model_reasoning_effort="{MODEL_REASONING_EFFORT}"',
    f"model_context_window={MODEL_CONTEXT_WINDOW_TOKENS}",
    'web_search="disabled"',
    "tools.web_search=false",
    'shell_environment_policy.inherit="none"',
    "shell_environment_policy.ignore_default_excludes=false",
    f"features.rollout_budget.limit_tokens={ROLLOUT_BUDGET_TOKENS}",
    (f"features.rollout_budget.reminder_at_remaining_tokens=[{ROLLOUT_REMINDER_INTERVAL_TOKENS}]"),
    "features.rollout_budget.sampling_token_weight=1.0",
    "features.rollout_budget.prefill_token_weight=1.0",
)

_ALLOWED_EVENT_TYPES = frozenset(
    {
        "thread.started",
        "turn.started",
        "item.started",
        "item.updated",
        "item.completed",
        "turn.completed",
    }
)
_ALLOWED_ITEM_TYPES = frozenset({"agent_message", "reasoning"})


@dataclass(frozen=True)
class CodexRuntimeIdentity:
    cli_version: str
    authentication_mode: str
    model: str
    model_catalog_comp_hash: str
    supported_reasoning_efforts: tuple[str, ...]
    input_modalities: tuple[str, ...]
    context_window_tokens: int
    exec_help_sha256: str
    feature_inventory_sha256: str
    configuration_preflight_validated: bool

    def __post_init__(self) -> None:
        if self.cli_version != CODEX_CLI_VERSION:
            raise ValueError("Codex CLI version differs from the frozen adapter")
        if self.authentication_mode != AUTH_MODE:
            raise ValueError("Codex CLI must use the approved ChatGPT authentication mode")
        if self.model != MODEL or self.model_catalog_comp_hash != MODEL_CATALOG_COMP_HASH:
            raise ValueError("Codex model catalog identity differs from the frozen adapter")
        if MODEL_REASONING_EFFORT not in self.supported_reasoning_efforts:
            raise ValueError("Codex model catalog does not advertise low reasoning effort")
        if "image" not in self.input_modalities or "text" not in self.input_modalities:
            raise ValueError("Codex model catalog does not advertise text and image input")
        if self.context_window_tokens != MODEL_CONTEXT_WINDOW_TOKENS:
            raise ValueError("Codex model context window differs from the frozen adapter")
        if not self.exec_help_sha256.startswith("sha256:"):
            raise ValueError("Codex exec help digest is invalid")
        if not self.feature_inventory_sha256.startswith("sha256:"):
            raise ValueError("Codex feature inventory digest is invalid")
        if self.configuration_preflight_validated is not True:
            raise ValueError("Codex configuration preflight was not validated")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cli_version": self.cli_version,
            "authentication_mode": self.authentication_mode,
            "model": self.model,
            "model_catalog_comp_hash": self.model_catalog_comp_hash,
            "supported_reasoning_efforts": list(self.supported_reasoning_efforts),
            "input_modalities": list(self.input_modalities),
            "context_window_tokens": self.context_window_tokens,
            "exec_help_sha256": self.exec_help_sha256,
            "feature_inventory_sha256": self.feature_inventory_sha256,
            "configuration_preflight_validated": self.configuration_preflight_validated,
        }


def _run_probe(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=True)


def _configuration_preflight_command() -> tuple[str, ...]:
    command: list[str] = ["codex", "debug", "prompt-input"]
    for value in _CONFIG_OVERRIDES:
        command.extend(("--config", value))
    for feature in _DISABLED_FEATURES:
        command.extend(("--disable", feature))
    command.append("PixelGym configuration preflight; do not run a model.")
    return tuple(command)


def probe_codex_runtime(
    *, run: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run_probe
) -> CodexRuntimeIdentity:
    """Inspect only local CLI metadata and authentication; no model request is made."""

    version = run(("codex", "--version")).stdout.strip()
    auth_process = run(("codex", "login", "status"))
    auth_lines = {
        line.strip()
        for line in (auth_process.stdout + "\n" + auth_process.stderr).splitlines()
        if line.strip()
    }
    if "Logged in using ChatGPT" not in auth_lines or any("API key" in line for line in auth_lines):
        raise ValueError("Codex CLI is not logged in through ChatGPT")
    exec_help = run(("codex", "exec", "--help")).stdout
    required_flags = (
        "--model",
        "--config",
        "--disable",
        "--strict-config",
        "--image",
        "--sandbox",
        "--cd",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--output-schema",
        "--json",
        "--skip-git-repo-check",
    )
    if any(flag not in exec_help for flag in required_flags):
        raise ValueError("installed Codex exec help omits an approved command flag")
    feature_inventory = run(("codex", "features", "list")).stdout
    if any(feature not in feature_inventory for feature in _DISABLED_FEATURES):
        raise ValueError("installed Codex feature inventory omits an isolation control")
    preflight = json.loads(run(_configuration_preflight_command()).stdout)
    if not isinstance(preflight, list):
        raise TypeError("Codex configuration preflight output is invalid")
    catalog_value = json.loads(run(("codex", "debug", "models", "--bundled")).stdout)
    if not isinstance(catalog_value, dict) or not isinstance(catalog_value.get("models"), list):
        raise TypeError("Codex bundled model catalog is invalid")
    matches = [
        item
        for item in catalog_value["models"]
        if isinstance(item, dict) and item.get("slug") == MODEL
    ]
    if len(matches) != 1:
        raise ValueError("Codex bundled model catalog does not contain one approved model")
    model_record = matches[0]
    reasoning_records = model_record.get("supported_reasoning_levels")
    if not isinstance(reasoning_records, list):
        raise TypeError("Codex bundled model reasoning metadata is invalid")
    efforts = tuple(
        str(item["effort"])
        for item in reasoning_records
        if isinstance(item, dict) and isinstance(item.get("effort"), str)
    )
    modalities = model_record.get("input_modalities")
    if not isinstance(modalities, list) or any(not isinstance(item, str) for item in modalities):
        raise ValueError("Codex bundled model modality metadata is invalid")
    return CodexRuntimeIdentity(
        cli_version=version,
        authentication_mode=AUTH_MODE,
        model=MODEL,
        model_catalog_comp_hash=str(model_record.get("comp_hash", "")),
        supported_reasoning_efforts=efforts,
        input_modalities=tuple(modalities),
        context_window_tokens=int(model_record.get("context_window", 0)),
        exec_help_sha256="sha256:" + sha256_bytes(exec_help.encode("utf-8")),
        feature_inventory_sha256=("sha256:" + sha256_bytes(feature_inventory.encode("utf-8"))),
        configuration_preflight_validated=True,
    )


def sanitized_command_contract() -> tuple[str, ...]:
    command: list[str] = ["codex", "exec", "--model", MODEL]
    for value in _CONFIG_OVERRIDES:
        command.extend(("--config", value))
    for feature in _DISABLED_FEATURES:
        command.extend(("--disable", feature))
    command.extend(
        (
            "--strict-config",
            "--json",
            "--output-schema",
            "<isolated-action-schema>",
            "--image",
            "<current-screenshot>",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--cd",
            "<isolated-empty-working-directory>",
            "--skip-git-repo-check",
            "-",
        )
    )
    return tuple(command)


def command_contract_digest() -> str:
    return content_digest(list(sanitized_command_contract()))


def _runtime_command(*, schema_path: Path, image_path: Path, working_directory: Path) -> list[str]:
    return [
        value.replace("<isolated-action-schema>", str(schema_path))
        .replace("<current-screenshot>", str(image_path))
        .replace("<isolated-empty-working-directory>", str(working_directory))
        for value in sanitized_command_contract()
    ]


def _minimal_environment(environment: Mapping[str, str]) -> dict[str, str]:
    allowed = (
        "PATH",
        "HOME",
        "CODEX_HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
    )
    return {name: environment[name] for name in allowed if environment.get(name)}


def action_prompt(task_instruction: str) -> str:
    if not isinstance(task_instruction, str) or not task_instruction:
        raise ValueError("task instruction is required")
    key_map = ", ".join(f"{index}={key}" for index, key in enumerate(KEY_ALLOWLIST))
    return (
        "Control the PixelGym application using only the attached current screenshot as the "
        "application-state observation and this task instruction: "
        f"{task_instruction}\n"
        "Return exactly one next GUI action matching the supplied JSON Schema. action_type is "
        "0 for NOOP, 1 for CLICK, and 2 for KEY. CLICK coordinates are native screenshot "
        f"pixels: x is 0 through {SCREEN_WIDTH - 1}, y is 0 through {SCREEN_HEIGHT - 1}, and "
        "the origin is the upper-left. For CLICK set key to 0. For KEY set x and y to 0 and "
        f"use this versioned key index mapping: {key_map}. For NOOP set x, y, and key to 0. "
        "Do not use tools, inspect files, search the web, or explain the action."
    )


def _png_bytes(screenshot: bytes) -> bytes:
    expected = SCREEN_WIDTH * SCREEN_HEIGHT * 3
    if len(screenshot) != expected:
        raise ValueError("screenshot byte length does not match frozen RGB dimensions")
    image = Image.frombytes("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), screenshot)
    encoded = io.BytesIO()
    image.save(encoded, format="PNG", compress_level=9, optimize=False)
    return encoded.getvalue()


class CodexCliPolicy:
    """Stateless policy package: the task instruction and current screenshot only."""

    def reset(self, task_instruction: str) -> bytes:
        return canonical_json_bytes({"instruction": task_instruction})

    def build_request(self, state: bytes, screenshot: bytes) -> dict[str, Any]:
        value = json.loads(state)
        if not isinstance(value, dict) or set(value) != {"instruction"}:
            raise ValueError("Codex policy state is invalid")
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
            raise ValueError("Codex response model does not match the approved policy")
        usage = response.get("usage")
        if not isinstance(usage, dict):
            raise TypeError("Codex response usage is missing")
        expected = {
            "authentication_mode": AUTH_MODE,
            "cli_version": CODEX_CLI_VERSION,
            "command_contract_digest": command_contract_digest(),
            "experiment_charge_usd": str(LUNA_EXPERIMENT_CHARGE_USD),
            "model_reasoning_effort": MODEL_REASONING_EFFORT,
            "price_guard": "subscription_exempt",
            "policy_violation": "none",
        }
        if any(usage.get(key) != expected_value for key, expected_value in expected.items()):
            raise ValueError("Codex invocation identity, usage, or policy boundary is invalid")
        candidate = json.loads(response.get("content", ""))
        if not isinstance(candidate, dict) or set(candidate) != {
            "action_type",
            "x",
            "y",
            "key",
        }:
            raise ValueError("Codex content must be one exact action object")
        if any(type(candidate[field]) is not int for field in candidate):
            raise TypeError("Codex action fields must be plain integers")
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


@dataclass
class SubscriptionExemptLedger:
    maximum_aggregate_usd: Decimal
    prior_budget_accounted_usd: Decimal
    experiment_charges: dict[str, Decimal] = field(default_factory=dict)
    informational_list_price_equivalents: dict[str, Decimal] = field(default_factory=dict)
    usage_telemetry_unavailable: set[str] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)
    blocked: bool = False
    processes_started: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.maximum_aggregate_usd <= 0 or self.prior_budget_accounted_usd < 0:
            raise ValueError("experiment dollar limits must be non-negative")
        if self.prior_budget_accounted_usd > self.maximum_aggregate_usd:
            raise ValueError("prior budget-accounted spend exceeds the aggregate cap")

    @property
    def budget_accounted_usd(self) -> Decimal:
        return self.prior_budget_accounted_usd + sum(self.experiment_charges.values(), Decimal(0))

    @property
    def incremental_informational_list_price_equivalent_usd(self) -> Decimal:
        return sum(self.informational_list_price_equivalents.values(), Decimal(0))

    @property
    def incremental_experiment_charge_usd(self) -> Decimal:
        return sum(self.experiment_charges.values(), Decimal(0))

    def reserve(self, idempotency_key: str) -> bool:
        with self._lock:
            if self.blocked or idempotency_key in self.experiment_charges:
                return False
            self.experiment_charges[idempotency_key] = LUNA_EXPERIMENT_CHARGE_USD
            return True

    def mark_process_started(self) -> None:
        with self._lock:
            self.processes_started += 1

    def release_pre_send(self, idempotency_key: str) -> None:
        with self._lock:
            self.experiment_charges.pop(idempotency_key, None)
            self.informational_list_price_equivalents.pop(idempotency_key, None)
            self.usage_telemetry_unavailable.discard(idempotency_key)
            self.unresolved.discard(idempotency_key)

    def record_usage(
        self, idempotency_key: str, informational_list_price_equivalent_usd: Decimal
    ) -> bool:
        with self._lock:
            if (
                idempotency_key not in self.experiment_charges
                or informational_list_price_equivalent_usd < 0
                or informational_list_price_equivalent_usd
                > REQUEST_MAXIMUM_INFORMATIONAL_LIST_PRICE_EQUIVALENT_USD
            ):
                self.blocked = True
                return False
            self.informational_list_price_equivalents[idempotency_key] = (
                informational_list_price_equivalent_usd
            )
            self.usage_telemetry_unavailable.discard(idempotency_key)
            self.unresolved.discard(idempotency_key)
            return True

    def mark_usage_telemetry_unavailable(self, idempotency_key: str) -> bool:
        with self._lock:
            if idempotency_key not in self.experiment_charges:
                self.blocked = True
                return False
            self.usage_telemetry_unavailable.add(idempotency_key)
            self.unresolved.discard(idempotency_key)
            return True

    def retain_unresolved_and_block(self, idempotency_key: str) -> None:
        with self._lock:
            if idempotency_key in self.experiment_charges:
                self.unresolved.add(idempotency_key)
            self.blocked = True


class CodexCliInvocationJournal:
    """Restricted, FULL-synchronous raw CLI journal keyed by runner idempotency."""

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
                outcome BLOB
            )
            """
        )

    def reserve(self, *, idempotency_key: str, request_digest: str) -> bool:
        command = list(sanitized_command_contract())
        validate_credential_free(command)
        with self._lock:
            try:
                with self._connection:
                    self._connection.execute(
                        """
                        INSERT INTO invocations(
                            idempotency_key, schema_version, request_digest,
                            command_contract, experiment_charge_usd, status
                        ) VALUES (?, ?, ?, ?, ?, 'reserved')
                        """,
                        (
                            idempotency_key,
                            INVOCATION_JOURNAL_SCHEMA_VERSION,
                            request_digest,
                            canonical_json_bytes(command),
                            str(LUNA_EXPERIMENT_CHARGE_USD),
                        ),
                    )
            except sqlite3.IntegrityError:
                return False
        return True

    def mark_running(self, idempotency_key: str, process_id: int) -> None:
        self._update(
            idempotency_key,
            status="running",
            process_id=process_id,
            exit_code=None,
            raw_stdout=None,
            raw_stderr=None,
            outcome=None,
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
        validate_credential_free(outcome)
        self._update(
            idempotency_key,
            status=status,
            process_id=None,
            exit_code=exit_code,
            raw_stdout=raw_stdout.encode("utf-8", errors="replace"),
            raw_stderr=raw_stderr.encode("utf-8", errors="replace"),
            outcome=canonical_json_bytes(outcome),
        )

    def record(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT request_digest, status, exit_code, raw_stdout, raw_stderr, outcome
            FROM invocations WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return {
            "request_digest": str(row[0]),
            "status": str(row[1]),
            "exit_code": row[2],
            "raw_stdout": bytes(row[3] or b"").decode("utf-8", errors="replace"),
            "raw_stderr": bytes(row[4] or b"").decode("utf-8", errors="replace"),
            "outcome": (json.loads(bytes(row[5]).decode("utf-8")) if row[5] is not None else None),
        }

    def integrity_report(self) -> dict[str, Any]:
        rows = self._connection.execute(
            """
            SELECT idempotency_key, request_digest, status, exit_code,
                   raw_stdout, raw_stderr, outcome
            FROM invocations ORDER BY idempotency_key
            """
        ).fetchall()
        records = []
        for row in rows:
            records.append(
                {
                    "idempotency_key_digest": content_digest(str(row[0])),
                    "request_digest": str(row[1]),
                    "status": str(row[2]),
                    "exit_code": row[3],
                    "raw_stdout_sha256": "sha256:" + sha256_bytes(bytes(row[4] or b"")),
                    "raw_stderr_sha256": "sha256:" + sha256_bytes(bytes(row[5] or b"")),
                    "outcome_sha256": "sha256:" + sha256_bytes(bytes(row[6] or b"")),
                }
            )
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
        *,
        status: str,
        process_id: int | None,
        exit_code: int | None,
        raw_stdout: bytes | None,
        raw_stderr: bytes | None,
        outcome: bytes | None,
    ) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE invocations
                SET status = ?, process_id = ?, exit_code = ?, raw_stdout = ?,
                    raw_stderr = ?, outcome = ?
                WHERE idempotency_key = ?
                """,
                (
                    status,
                    process_id,
                    exit_code,
                    raw_stdout,
                    raw_stderr,
                    outcome,
                    idempotency_key,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Codex invocation journal reservation is missing")


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


@dataclass(frozen=True)
class ParsedCliStream:
    content: str
    usage: dict[str, int] | None
    usage_telemetry_status: str
    policy_violations: tuple[str, ...]
    event_counts: dict[str, int]


def _parse_cli_stream(raw_stdout: str) -> ParsedCliStream:
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
    event_counts = Counter(str(event["type"]) for event in events)
    messages: list[str] = []
    completed_usage: list[dict[str, int]] = []
    unavailable_usage_count = 0
    for event in events:
        event_type = str(event["type"])
        if event_type not in _ALLOWED_EVENT_TYPES:
            violations.append(f"unauthorized_event:{event_type}")
            continue
        if event_type.startswith("item."):
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                violations.append("invalid_item_envelope")
                continue
            item_type = str(item["type"])
            if item_type not in _ALLOWED_ITEM_TYPES:
                violations.append(f"unauthorized_item:{item_type}")
            elif event_type == "item.completed" and item_type == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    messages.append(text)
                else:
                    violations.append("agent_message_missing_text")
        if event_type == "turn.completed":
            usage = event.get("usage")
            if not isinstance(usage, dict):
                unavailable_usage_count += 1
                continue
            required = (
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_output_tokens",
            )
            if any(type(usage.get(key)) is not int or usage[key] < 0 for key in required):
                unavailable_usage_count += 1
                continue
            completed_usage.append({key: int(usage[key]) for key in required})
    if len(messages) != 1:
        violations.append("final_agent_message_count_mismatch")
    if event_counts.get("turn.completed", 0) > 1:
        violations.append("completed_turn_count_exceeded")
    usage_value = completed_usage[0] if len(completed_usage) == 1 else None
    if len(completed_usage) == 1 and unavailable_usage_count == 0:
        usage_telemetry_status = "available"
    elif unavailable_usage_count > 0 or len(completed_usage) > 1:
        usage_telemetry_status = "invalid_or_ambiguous"
        usage_value = None
    else:
        usage_telemetry_status = "unavailable"
    if usage_value is not None:
        if usage_value["cached_input_tokens"] > usage_value["input_tokens"]:
            violations.append("cached_input_exceeds_input")
        if usage_value["reasoning_output_tokens"] > usage_value["output_tokens"]:
            violations.append("reasoning_output_exceeds_output")
        if usage_value["input_tokens"] + usage_value["output_tokens"] > MODEL_CONTEXT_WINDOW_TOKENS:
            violations.append("reported_usage_exceeds_context_window")
    return ParsedCliStream(
        content=messages[0] if len(messages) == 1 else "",
        usage=usage_value,
        usage_telemetry_status=usage_telemetry_status,
        policy_violations=tuple(sorted(set(violations))),
        event_counts=dict(sorted(event_counts.items())),
    )


def informational_list_price_equivalent(usage: Mapping[str, int]) -> Decimal:
    input_tokens = usage["input_tokens"]
    output_tokens = usage["output_tokens"]
    reasoning_tokens = usage["reasoning_output_tokens"]
    return (
        Decimal(input_tokens) * CONSERVATIVE_INPUT_PER_TOKEN_USD
        + Decimal(output_tokens + reasoning_tokens) * CONSERVATIVE_OUTPUT_PER_TOKEN_USD
    )


class CodexCliTransport:
    """One subprocess per action with raw journaling and no replay or retry."""

    def __init__(
        self,
        *,
        ledger: SubscriptionExemptLedger,
        invocation_journal: CodexCliInvocationJournal,
        runtime_identity: CodexRuntimeIdentity,
        environment: Mapping[str, str] = os.environ,
        process_factory: Callable[..., RunningProcess] = _start_process,
        process_timeout_seconds: float = PROCESS_TIMEOUT_SECONDS,
    ) -> None:
        CodexRuntimeIdentity(**runtime_identity.__dict__)
        if process_timeout_seconds <= 0:
            raise ValueError("Codex process timeout must be positive")
        self.ledger = ledger
        self.invocation_journal = invocation_journal
        self.runtime_identity = runtime_identity
        self.environment = _minimal_environment(environment)
        self.process_factory = process_factory
        self.process_timeout_seconds = process_timeout_seconds
        self.records: list[dict[str, Any]] = []
        self._active: dict[str, RunningProcess] = {}
        self._active_lock = threading.Lock()
        self._closed = False

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        if self._closed:
            return TransportOutcome("pre_send_failure", failure_code="transport_closed")
        failure = self._validate_request(request, deadline_seconds=deadline_seconds)
        if failure is not None:
            return TransportOutcome("pre_send_failure", failure_code=failure)
        request_digest = content_digest(request)
        if not self.ledger.reserve(idempotency_key):
            return TransportOutcome(
                "pre_send_failure", failure_code="subscription_exempt_invocation_guard"
            )
        if not self.invocation_journal.reserve(
            idempotency_key=idempotency_key, request_digest=request_digest
        ):
            self.ledger.release_pre_send(idempotency_key)
            return TransportOutcome("pre_send_failure", failure_code="duplicate_invocation_blocked")

        raw_stdout = ""
        raw_stderr = ""
        process: RunningProcess | None = None
        with tempfile.TemporaryDirectory(prefix="pixelgym-codex-cli-") as temporary:
            temporary_root = Path(temporary)
            working_directory = temporary_root / "work"
            input_directory = temporary_root / "inputs"
            working_directory.mkdir()
            input_directory.mkdir()
            schema_path = input_directory / "action-schema.json"
            image_path = input_directory / "current-screenshot.png"
            schema_path.write_bytes(canonical_json_bytes(ACTION_SCHEMA))
            image_path.write_bytes(base64.b64decode(request["image_png_base64"], validate=True))
            command = _runtime_command(
                schema_path=schema_path,
                image_path=image_path,
                working_directory=working_directory,
            )
            try:
                process = self.process_factory(
                    command,
                    cwd=working_directory,
                    env=self.environment,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                self.ledger.release_pre_send(idempotency_key)
                outcome = {"failure_code": "process_start_failure", "type": type(exc).__name__}
                self.invocation_journal.finish(
                    idempotency_key,
                    status="pre_send_failure",
                    exit_code=None,
                    raw_stdout="",
                    raw_stderr="",
                    outcome=outcome,
                )
                self.records.append(self._record(idempotency_key, "pre_send_failure", outcome))
                return TransportOutcome("pre_send_failure", failure_code="process_start_failure")
            self.ledger.mark_process_started()
            self.invocation_journal.mark_running(idempotency_key, process.pid)
            with self._active_lock:
                self._active[idempotency_key] = process
            try:
                raw_stdout, raw_stderr = process.communicate(
                    str(request["prompt"]),
                    timeout=min(self.process_timeout_seconds, deadline_seconds),
                )
            except subprocess.TimeoutExpired:
                raw_stdout, raw_stderr = self._terminate_process(process)
                self.ledger.retain_unresolved_and_block(idempotency_key)
                timeout_outcome: dict[str, Any] = {
                    "failure_code": "codex_process_timeout",
                    "process_confirmed_stopped": process.poll() is not None,
                }
                self.invocation_journal.finish(
                    idempotency_key,
                    status="timeout",
                    exit_code=process.poll(),
                    raw_stdout=raw_stdout,
                    raw_stderr=raw_stderr,
                    outcome=timeout_outcome,
                )
                self.records.append(self._record(idempotency_key, "timeout", timeout_outcome))
                return TransportOutcome("deadline", failure_code="codex_process_timeout")
            except BaseException as exc:
                raw_stdout, raw_stderr = self._terminate_process(process)
                self.ledger.retain_unresolved_and_block(idempotency_key)
                interrupt_outcome: dict[str, Any] = {
                    "failure_code": "codex_process_interrupted",
                    "process_confirmed_stopped": process.poll() is not None,
                    "type": type(exc).__name__,
                }
                self.invocation_journal.finish(
                    idempotency_key,
                    status="interrupted",
                    exit_code=process.poll(),
                    raw_stdout=raw_stdout,
                    raw_stderr=raw_stderr,
                    outcome=interrupt_outcome,
                )
                self.records.append(self._record(idempotency_key, "interrupted", interrupt_outcome))
                raise
            finally:
                with self._active_lock:
                    self._active.pop(idempotency_key, None)

        assert process is not None
        parsed = _parse_cli_stream(raw_stdout)
        policy_violations = list(parsed.policy_violations)
        if process.returncode != 0:
            policy_violations.append("nonzero_exit")
        content = parsed.content
        try:
            validate_credential_free(content)
        except CredentialValidationError:
            content = ""
            policy_violations.append("credential_shaped_output")
        list_price_equivalent: Decimal | None = None
        accounting_ok = True
        if parsed.usage is not None:
            list_price_equivalent = informational_list_price_equivalent(parsed.usage)
            accounting_ok = self.ledger.record_usage(idempotency_key, list_price_equivalent)
        else:
            accounting_ok = self.ledger.mark_usage_telemetry_unavailable(idempotency_key)
        if not accounting_ok:
            policy_violations.append("cost_accounting_failure")
        violation_value = (
            "none" if not policy_violations else ",".join(sorted(set(policy_violations)))
        )
        usage_record: dict[str, Any] = {
            **(parsed.usage or {}),
            "authentication_mode": AUTH_MODE,
            "cli_version": CODEX_CLI_VERSION,
            "command_contract_digest": command_contract_digest(),
            "cost_accounting_method": "luna_chatgpt_subscription_experiment_charge_zero_v1",
            "experiment_charge_usd": str(LUNA_EXPERIMENT_CHARGE_USD),
            "informational_list_price_equivalent_usd": (
                str(list_price_equivalent) if list_price_equivalent is not None else None
            ),
            "model_reasoning_effort": MODEL_REASONING_EFFORT,
            "policy_violation": violation_value,
            "price_guard": "subscription_exempt",
            "usage_telemetry_status": parsed.usage_telemetry_status,
            "raw_stdout_sha256": "sha256:" + sha256_bytes(raw_stdout.encode("utf-8")),
            "raw_stderr_sha256": "sha256:" + sha256_bytes(raw_stderr.encode("utf-8")),
        }
        canonical = {
            "response_id": "sha256:" + sha256_bytes(raw_stdout.encode("utf-8")),
            "model": MODEL,
            "content": content,
            "finish_reason": "stop" if violation_value == "none" else "policy_violation",
            "usage": usage_record,
        }
        outcome_record = {
            "event_counts": parsed.event_counts,
            "exit_code": process.returncode,
            "policy_violation": violation_value,
            "price_guard": "subscription_exempt",
            "experiment_charge_usd": usage_record["experiment_charge_usd"],
            "informational_list_price_equivalent_usd": usage_record[
                "informational_list_price_equivalent_usd"
            ],
            "usage_telemetry_status": parsed.usage_telemetry_status,
            "canonical_response": canonical,
        }
        status = "response" if violation_value == "none" else "policy_violation"
        self.invocation_journal.finish(
            idempotency_key,
            status=status,
            exit_code=process.returncode,
            raw_stdout=raw_stdout,
            raw_stderr=raw_stderr,
            outcome=outcome_record,
        )
        self.records.append(self._record(idempotency_key, status, outcome_record))
        return TransportOutcome("response", canonical)

    def cancel(self, *, idempotency_key: str, mode: str) -> Literal["cancelled", "unknown"]:
        del mode
        with self._active_lock:
            process = self._active.get(idempotency_key)
        if process is not None:
            self._terminate_process(process)
            return "cancelled" if process.poll() is not None else "unknown"
        record = self.invocation_journal.record(idempotency_key)
        if record is not None and record["status"] in {"timeout", "interrupted"}:
            return "cancelled"
        return "unknown"

    def reconcile(self, *, idempotency_key: str, deadline_seconds: float) -> TransportOutcome:
        del deadline_seconds
        record = self.invocation_journal.record(idempotency_key)
        if record is None or record["status"] in {"reserved", "running"}:
            return TransportOutcome("unknown", failure_code="invocation_outcome_unresolved")
        outcome = record.get("outcome")
        if isinstance(outcome, dict) and isinstance(outcome.get("canonical_response"), dict):
            return TransportOutcome("response", outcome["canonical_response"])
        return TransportOutcome("unknown", failure_code="invocation_outcome_not_recoverable")

    def close(self) -> None:
        with self._active_lock:
            active = list(self._active.values())
        for process in active:
            self._terminate_process(process)
        self._closed = True

    def _validate_request(self, request: dict[str, Any], *, deadline_seconds: float) -> str | None:
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
        if not isinstance(request.get("prompt"), str) or not request["prompt"]:
            return "prompt_missing"
        if deadline_seconds < self.process_timeout_seconds:
            return "runner_deadline_below_process_timeout"
        try:
            image = base64.b64decode(request["image_png_base64"], validate=True)
        except (TypeError, ValueError):
            return "image_encoding_invalid"
        if request.get("image_sha256") != "sha256:" + sha256_bytes(image):
            return "image_digest_mismatch"
        return None

    def _terminate_process(self, process: RunningProcess) -> tuple[str, str]:
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
            "cli_version": CODEX_CLI_VERSION,
            "model": MODEL,
            "model_reasoning_effort": MODEL_REASONING_EFFORT,
            "authentication_mode": AUTH_MODE,
            "command_contract_digest": command_contract_digest(),
            "experiment_charge_usd": outcome.get("experiment_charge_usd"),
            "informational_list_price_equivalent_usd": outcome.get(
                "informational_list_price_equivalent_usd"
            ),
            "policy_violation": outcome.get("policy_violation"),
            "price_guard": outcome.get("price_guard"),
            "usage_telemetry_status": outcome.get("usage_telemetry_status"),
        }


def _file_digest(path: Path) -> str:
    return "sha256:" + sha256_bytes(path.read_bytes())


def build_codex_cli_policy_manifest(
    repository_root: Path,
    *,
    code_revision: str,
    runtime_identity: CodexRuntimeIdentity,
) -> PolicyManifest:
    CodexRuntimeIdentity(**runtime_identity.__dict__)
    module_path = repository_root / "pixelgym/grounding/v5/codex_cli_policy.py"
    runtime_digest = content_digest(
        {
            "provider_module": _file_digest(module_path),
            "runner_module": _file_digest(repository_root / "pixelgym/grounding/v5/runner.py"),
            "pyproject": _file_digest(repository_root / "pyproject.toml"),
            "lock": _file_digest(repository_root / "requirements/platform-py312.lock"),
            "runtime_identity": runtime_identity.to_dict(),
        }
    )
    sandbox = build_sandbox_manifest(
        runtime_digest=runtime_digest,
        provider_endpoint=PROVIDER_ORIGIN,
    )
    inference_parameters = (
        ("authentication_mode", AUTH_MODE),
        ("cli_version", CODEX_CLI_VERSION),
        ("command_contract_digest", command_contract_digest()),
        ("model_catalog_comp_hash", MODEL_CATALOG_COMP_HASH),
        ("model_reasoning_effort", MODEL_REASONING_EFFORT),
        ("request_max_retries", "0"),
        ("stream_max_retries", "0"),
        ("rollout_budget_tokens", str(ROLLOUT_BUDGET_TOKENS)),
        ("shell_tool", "disabled"),
        ("web_search", "disabled"),
    )
    return PolicyManifest.build(
        provider=PROVIDER_IDENTITY,
        model=MODEL,
        exact_snapshot=False,
        harness_digest=_file_digest(repository_root / "pixelgym/grounding/v5/runner.py"),
        dependency_lock_digest=_file_digest(repository_root / "requirements/platform-py312.lock"),
        system_prompt_digest=content_digest(action_prompt("<task-instruction>")),
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
        context_limit=MODEL_CONTEXT_WINDOW_TOKENS,
        transport_retry_rule=TRANSPORT_RETRY_RULE,
    )
