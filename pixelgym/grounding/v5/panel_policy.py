"""Frozen four-policy OpenRouter panel for the v5 D5.6 calibration."""

from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.grounding.v5.contracts import PolicyManifest, content_digest, sha256_bytes
from pixelgym.grounding.v5.coordinates import (
    IDENTITY_ADAPTER,
    NORMALIZED_1000_ADAPTER,
    CoordinateAdapter,
)
from pixelgym.grounding.v5.openrouter_policy import _png_data_url, _usage_cost
from pixelgym.grounding.v5.runner import TransportOutcome
from pixelgym.grounding.v5.sandbox import build_sandbox_manifest
from pixelgym.serialization import canonical_json_bytes

ENDPOINT_ORIGIN = "https://openrouter.ai"
ENDPOINT = f"{ENDPOINT_ORIGIN}/api/v1/chat/completions"
MAX_HTTP_ERROR_METADATA_BYTES = 64 * 1024
MAX_OUTPUT_TOKENS = 4_096
CONTEXT_LIMIT = 131_072
MAX_PROMPT_TOKENS = CONTEXT_LIMIT - MAX_OUTPUT_TOKENS
PANEL_MAXIMUM_SPEND_USD = Decimal("10.00")
PRIOR_AGGREGATE_SPEND_USD = Decimal("0.370889195")
SEED = 20260809

RESPONSE_SCHEMA_VERSION = "pixelgym-agent-v5-canonical-response-v2"
TASK_RENDERER_VERSION = "pixelgym-agent-v5-task-renderer-v1"
TRANSPORT_RETRY_RULE = "one-same-route-zero-completion-error-v1"


@dataclass(frozen=True)
class PanelPolicyConfig:
    """Every provider-specific field that contributes to a panel policy identity."""

    slot: str
    model: str
    provider_route: str
    response_provider: str
    prompt_price_per_token_usd: Decimal
    completion_price_per_token_usd: Decimal
    price_source: str
    adapter: CoordinateAdapter
    coordinate_input_convention: str
    stateful: bool
    temperature: int | None = 0
    quantizations: tuple[str, ...] = ()

    @property
    def request_maximum_usd(self) -> Decimal:
        return (
            self.prompt_price_per_token_usd * MAX_PROMPT_TOKENS
            + self.completion_price_per_token_usd * MAX_OUTPUT_TOKENS
        )

    @property
    def memory_policy_version(self) -> str:
        if self.stateful:
            return "pixelgym-agent-v5-visible-action-history-v1"
        return "pixelgym-agent-v5-stateless-within-episode-v1"

    @property
    def state_reducer_version(self) -> str:
        if self.stateful:
            return "pixelgym-agent-v5-visible-action-state-reducer-v1"
        return "pixelgym-agent-v5-stateless-state-reducer-v1"

    @property
    def parser_version(self) -> str:
        return f"pixelgym-agent-v5-json-action-{self.adapter.name}-parser-v1"

    @property
    def prompt_version(self) -> str:
        mode = "stateful" if self.stateful else "stateless"
        return f"pixelgym-agent-v5-panel-{mode}-{self.adapter.name}-prompt-v1"

    def provider_parameters(self) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            "only": [self.provider_route],
            "allow_fallbacks": False,
            "data_collection": "deny",
            "require_parameters": True,
        }
        if self.quantizations:
            parameters["quantizations"] = list(self.quantizations)
        return parameters


GEMINI_STATEFUL = PanelPolicyConfig(
    slot="A-gemini-stateful",
    model="google/gemini-3.7-flash",
    provider_route="google-vertex/global",
    response_provider="Google",
    prompt_price_per_token_usd=Decimal("0.000000375"),
    completion_price_per_token_usd=Decimal("0.000001875"),
    price_source=(
        "https://openrouter.ai/api/v1/models/google/gemini-3.7-flash/endpoints"
    ),
    adapter=NORMALIZED_1000_ADAPTER,
    coordinate_input_convention="integer-normalized-square/0..999-inclusive",
    stateful=True,
    temperature=None,
)
QWEN_STATEFUL = PanelPolicyConfig(
    slot="B-qwen-stateful",
    model="qwen/qwen3-vl-8b-instruct",
    provider_route="alibaba",
    response_provider="Alibaba",
    prompt_price_per_token_usd=Decimal("0.000000117"),
    completion_price_per_token_usd=Decimal("0.000000455"),
    price_source=(
        "https://openrouter.ai/api/v1/models/qwen/qwen3-vl-8b-instruct/endpoints"
    ),
    adapter=NORMALIZED_1000_ADAPTER,
    coordinate_input_convention="integer-normalized-square/0..999-inclusive",
    stateful=True,
)
LLAMA_STATEFUL = PanelPolicyConfig(
    slot="C-llama-stateful",
    model="meta-llama/llama-4-scout",
    provider_route="deepinfra",
    response_provider="DeepInfra",
    prompt_price_per_token_usd=Decimal("0.0000001"),
    completion_price_per_token_usd=Decimal("0.0000003"),
    price_source=(
        "https://openrouter.ai/api/v1/models/meta-llama/llama-4-scout/endpoints"
    ),
    adapter=IDENTITY_ADAPTER,
    coordinate_input_convention="integer-pixel/1024x768",
    stateful=True,
    quantizations=("fp8",),
)
QWEN_STATELESS = PanelPolicyConfig(
    slot="D-qwen-stateless",
    model=QWEN_STATEFUL.model,
    provider_route=QWEN_STATEFUL.provider_route,
    response_provider=QWEN_STATEFUL.response_provider,
    prompt_price_per_token_usd=QWEN_STATEFUL.prompt_price_per_token_usd,
    completion_price_per_token_usd=QWEN_STATEFUL.completion_price_per_token_usd,
    price_source=QWEN_STATEFUL.price_source,
    adapter=QWEN_STATEFUL.adapter,
    coordinate_input_convention=QWEN_STATEFUL.coordinate_input_convention,
    stateful=False,
)
PANEL = (GEMINI_STATEFUL, QWEN_STATEFUL, LLAMA_STATEFUL, QWEN_STATELESS)
PANEL_BY_SLOT = {config.slot: config for config in PANEL}


def action_schema(config: PanelPolicyConfig) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action_type": {"type": "integer", "minimum": 0, "maximum": 2},
            "x": {"type": "integer", "minimum": 0, "maximum": config.adapter.input_width - 1},
            "y": {
                "type": "integer",
                "minimum": 0,
                "maximum": config.adapter.input_height - 1,
            },
            "key": {
                "type": "integer",
                "minimum": 0,
                "maximum": len(KEY_ALLOWLIST) - 1,
            },
        },
        "required": ["action_type", "x", "y", "key"],
        "additionalProperties": False,
    }


def system_prompt(config: PanelPolicyConfig) -> str:
    key_map = ", ".join(f"{index}={key}" for index, key in enumerate(KEY_ALLOWLIST))
    if config.adapter is NORMALIZED_1000_ADAPTER:
        coordinate_rule = (
            "CLICK coordinates use a normalized 1000x1000 square grid: integers 0 through 999 "
            "on each axis with origin at the upper-left"
        )
    else:
        coordinate_rule = (
            "CLICK coordinates use native 1024x768 screenshot pixels: x is 0 through 1023 and "
            "y is 0 through 767, with origin at the upper-left"
        )
    memory_rule = (
        "the visible-action history supplied below"
        if config.stateful
        else "no prior action or outcome history"
    )
    return (
        "You control a deterministic PixelGym application using only the current screenshot, "
        f"the task instruction, and {memory_rule}. Choose exactly one next action. Return only a "
        "JSON object with action_type, x, y, and key. action_type is 0 for NOOP, 1 for CLICK, "
        f"and 2 for KEY. {coordinate_rule}; set key to 0. For KEY, set x and y to 0 and use this "
        f"versioned key index mapping: {key_map}. For NOOP set x, y, and key to 0. Do not explain "
        "the action."
    )


class OpenRouterPanelPolicy:
    """Credential-free request, state, parser, and coordinate package for one panel slot."""

    def __init__(self, config: PanelPolicyConfig) -> None:
        self.config = config

    def reset(self, task_instruction: str) -> bytes:
        value: dict[str, Any] = {"instruction": task_instruction}
        if self.config.stateful:
            value["history"] = []
        return canonical_json_bytes(value)

    def build_request(self, state: bytes, screenshot: bytes) -> dict[str, Any]:
        value = json.loads(state)
        if self.config.stateful:
            history_text = json.dumps(value["history"], sort_keys=True, separators=(",", ":"))
            context = f"Visible-action history: {history_text}\n"
        else:
            context = ""
        prompt = (
            f"Overall task: {value['instruction']}\n"
            f"{context}Choose the next action from the current screenshot."
        )
        request = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt(self.config)},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _png_data_url(screenshot)}},
                    ],
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "pixelgym_v5_action",
                    "strict": True,
                    "schema": action_schema(self.config),
                },
            },
            "provider": self.config.provider_parameters(),
            "seed": SEED,
            "max_tokens": MAX_OUTPUT_TOKENS,
        }
        if self.config.temperature is not None:
            request["temperature"] = self.config.temperature
        return request

    def reduce_state(self, state: bytes, canonical_response: bytes) -> bytes:
        if not self.config.stateful:
            return state
        value = json.loads(state)
        response = json.loads(canonical_response)
        value["history"].append({"response_digest": content_digest(response)})
        return canonical_json_bytes(value)

    def failure_state(self, state: bytes, failure_code: str) -> bytes:
        if not self.config.stateful:
            return state
        value = json.loads(state)
        value["history"].append({"failure_code": failure_code})
        return canonical_json_bytes(value)

    def retryable_response_code(self, canonical_response: bytes) -> str | None:
        response = json.loads(canonical_response)
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None
        if response.get("model") != self.config.model or str(
            usage.get("upstream_provider", "")
        ).lower() != self.config.response_provider.lower():
            return None
        try:
            cost = _usage_cost(usage)
        except ValueError:
            return None
        if (
            response.get("finish_reason") == "error"
            and response.get("content") == ""
            and type(usage.get("completion_tokens")) is int
            and usage["completion_tokens"] == 0
            and usage.get("price_guard") == "ok"
            and cost == 0
        ):
            return "zero_completion_error"
        return None

    def parse(self, canonical_response: bytes, state: bytes) -> dict[str, Any]:
        del state
        response = json.loads(canonical_response)
        if response["model"] != self.config.model:
            raise ValueError("provider response model does not match policy")
        usage = response["usage"]
        if not isinstance(usage, dict) or str(usage.get("upstream_provider", "")).lower() != (
            self.config.response_provider.lower()
        ):
            raise ValueError("provider response route does not match policy")
        if usage.get("price_guard") != "ok":
            raise ValueError("provider response failed the price guard")
        _usage_cost(usage)
        candidate = json.loads(response["content"])
        if not isinstance(candidate, dict) or set(candidate) != {
            "action_type",
            "x",
            "y",
            "key",
        }:
            raise ValueError("provider content must be one exact action object")
        if any(type(candidate[field]) is not int for field in candidate):
            raise TypeError("provider action fields must be plain integers")
        if candidate["action_type"] == 1:
            candidate["x"], candidate["y"] = self.config.adapter.transform(
                candidate["x"], candidate["y"]
            )
        return candidate

    def post_parse_state(self, state: bytes, candidate: dict[str, Any]) -> bytes:
        if not self.config.stateful:
            return state
        value = json.loads(state)
        value["pending_action_digest"] = content_digest(candidate)
        return canonical_json_bytes(value)

    def post_dispatch_state(
        self, state: bytes, action: dict[str, int], result: dict[str, Any]
    ) -> bytes:
        if not self.config.stateful:
            return state
        value = json.loads(state)
        value.pop("pending_action_digest", None)
        value["history"].append(
            {
                "action": action,
                "visible_outcome": {
                    "reward": result["reward"],
                    "terminated": result["terminated"],
                    "truncated": result["truncated"],
                },
            }
        )
        return canonical_json_bytes(value)

    def close(self) -> None:
        return None


@dataclass
class SpendLedger:
    """Shared, sequential aggregate cap across all four panel transports."""

    maximum_spend_usd: Decimal
    spent_usd: Decimal
    wire_requests_sent: int = 0
    blocked: bool = False
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.maximum_spend_usd <= 0 or self.spent_usd < 0:
            raise ValueError("spend limits must be non-negative")
        if self.spent_usd > self.maximum_spend_usd:
            raise ValueError("prior spend exceeds the aggregate cap")

    def reserve_wire(self, request_maximum_usd: Decimal) -> bool:
        with self._lock:
            if self.blocked or self.spent_usd + request_maximum_usd > self.maximum_spend_usd:
                return False
            self.wire_requests_sent += 1
            return True

    def record_cost(self, cost: Decimal, request_maximum_usd: Decimal) -> bool:
        with self._lock:
            self.spent_usd += cost
            if cost > request_maximum_usd or self.spent_usd > self.maximum_spend_usd:
                self.blocked = True
                return False
            return True

    def block(self) -> None:
        with self._lock:
            self.blocked = True


class OpenRouterPanelTransport:
    """One-send/no-retry transport sharing one fail-closed panel spend ledger."""

    def __init__(
        self,
        config: PanelPolicyConfig,
        *,
        ledger: SpendLedger,
        environment: Mapping[str, str] = os.environ,
        timeout_seconds: float = 180.0,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        api_key = environment.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        self.config = config
        self.ledger = ledger
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._urlopen = urlopen
        self.records: list[dict[str, Any]] = []

    @property
    def spent_usd(self) -> Decimal:
        return self.ledger.spent_usd

    @property
    def wire_requests_sent(self) -> int:
        return self.ledger.wire_requests_sent

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        if request.get("model") != self.config.model or request.get(
            "provider"
        ) != self.config.provider_parameters():
            return TransportOutcome("pre_send_failure", failure_code="request_identity_mismatch")
        if not self.ledger.reserve_wire(self.config.request_maximum_usd):
            return TransportOutcome("pre_send_failure", failure_code="aggregate_spend_guard")
        wire = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(request, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            method="POST",
        )
        started = time.monotonic()
        try:
            with self._urlopen(
                wire, timeout=min(self.timeout_seconds, deadline_seconds)
            ) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            self.ledger.block()
            self.records.append(
                {
                    "idempotency_key": idempotency_key,
                    "status": "unknown",
                    "failure_code": type(exc).__name__,
                    **_safe_http_error_metadata(exc),
                }
            )
            return TransportOutcome("unknown", failure_code="provider_request_unknown")
        except (
            OSError,
            TimeoutError,
            urllib.error.URLError,
            http.client.HTTPException,
            json.JSONDecodeError,
        ) as exc:
            self.ledger.block()
            self.records.append(
                {
                    "idempotency_key": idempotency_key,
                    "status": "unknown",
                    "failure_code": type(exc).__name__,
                }
            )
            return TransportOutcome("unknown", failure_code="provider_request_unknown")
        if not isinstance(body, dict):
            self.ledger.block()
            return TransportOutcome("unknown", failure_code="provider_envelope_invalid")
        try:
            content = body["choices"][0]["message"]["content"]
            finish_reason = body["choices"][0].get("finish_reason")
        except (KeyError, IndexError, TypeError):
            content = ""
            finish_reason = None
        raw_usage = body.get("usage")
        usage: dict[str, Any] = dict(raw_usage) if isinstance(raw_usage, dict) else {}
        usage["upstream_provider"] = body.get("provider")
        usage.update(_safe_response_error_metadata(body, finish_reason=finish_reason))
        try:
            cost = _usage_cost(usage)
        except ValueError:
            self.ledger.block()
            cost = None
            usage["price_guard"] = "missing_or_invalid_cost"
        if cost is not None:
            usage["price_guard"] = (
                "ok"
                if self.ledger.record_cost(cost, self.config.request_maximum_usd)
                else "exceeded"
            )
        canonical = {
            "response_id": str(body.get("id", "")),
            "model": str(body.get("model", "")),
            "content": content if isinstance(content, str) else "",
            "finish_reason": finish_reason if isinstance(finish_reason, str) else "",
            "usage": usage,
        }
        self.records.append(
            {
                "idempotency_key": idempotency_key,
                "slot": self.config.slot,
                "status": "response",
                "latency_ms": (time.monotonic() - started) * 1000,
                "response_id_digest": content_digest(str(body.get("id", ""))),
                "response_model": body.get("model"),
                "upstream_provider": body.get("provider"),
                "cost_usd": str(cost) if cost is not None else None,
            }
        )
        return TransportOutcome("response", canonical)

    def cancel(
        self, *, idempotency_key: str, mode: str
    ) -> Literal["cancelled", "unknown"]:
        del idempotency_key, mode
        return "unknown"

    def reconcile(
        self, *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        del idempotency_key, deadline_seconds
        return TransportOutcome("unknown", failure_code="reconciliation_disabled")


def _safe_http_error_metadata(exc: urllib.error.HTTPError) -> dict[str, Any]:
    """Retain bounded, non-message HTTP diagnostics without storing provider content."""

    metadata: dict[str, Any] = {"http_status": exc.code}
    try:
        body = exc.read(MAX_HTTP_ERROR_METADATA_BYTES + 1)
    except (OSError, http.client.HTTPException) as read_error:
        metadata["error_body_read_failure"] = type(read_error).__name__
        return metadata
    retained = body[:MAX_HTTP_ERROR_METADATA_BYTES]
    metadata.update(
        {
            "error_body_prefix_digest": "sha256:" + sha256_bytes(retained),
            "error_body_bytes_read": len(body),
            "error_body_truncated": len(body) > MAX_HTTP_ERROR_METADATA_BYTES,
        }
    )
    if metadata["error_body_truncated"]:
        return metadata
    try:
        value = json.loads(retained)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return metadata
    if not isinstance(value, dict) or not isinstance(value.get("error"), dict):
        return metadata
    error = value["error"]
    for source, target in (("code", "provider_error_code"), ("type", "provider_error_type")):
        field = error.get(source)
        if isinstance(field, (int, float, bool)) or (
            isinstance(field, str) and len(field) <= 128
        ):
            metadata[target] = field
    provider_metadata = error.get("metadata")
    if isinstance(provider_metadata, dict):
        provider_name = provider_metadata.get("provider_name")
        if isinstance(provider_name, str) and len(provider_name) <= 128:
            metadata["upstream_provider"] = provider_name
    return metadata


def _safe_response_error_metadata(
    body: dict[str, Any], *, finish_reason: object
) -> dict[str, Any]:
    """Retain non-message diagnostics for a successful error response envelope."""

    if finish_reason != "error":
        return {}
    metadata: dict[str, Any] = {
        "provider_error_signal": "finish_reason_error",
        "provider_error_envelope_digest": content_digest(body),
    }
    candidates: list[dict[str, Any]] = []
    top_level = body.get("error")
    if isinstance(top_level, dict):
        candidates.append(top_level)
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice_error = choices[0].get("error")
        if isinstance(choice_error, dict):
            candidates.append(choice_error)
    for error in candidates:
        for source, target in (
            ("code", "provider_error_code"),
            ("type", "provider_error_type"),
            ("status", "provider_error_status"),
        ):
            field = error.get(source)
            if target not in metadata and (
                isinstance(field, (int, float, bool))
                or (isinstance(field, str) and len(field) <= 128)
            ):
                metadata[target] = field
    return metadata


def _file_digest(path: Path) -> str:
    return "sha256:" + sha256_bytes(path.read_bytes())


def build_panel_policy_manifest(
    repository_root: Path, *, config: PanelPolicyConfig, code_revision: str
) -> PolicyManifest:
    module_path = repository_root / "pixelgym/grounding/v5/panel_policy.py"
    runtime_digest = content_digest(
        {
            "panel_module": _file_digest(module_path),
            "base_transport_module": _file_digest(
                repository_root / "pixelgym/grounding/v5/openrouter_policy.py"
            ),
            "pyproject": _file_digest(repository_root / "pyproject.toml"),
            "lock": _file_digest(repository_root / "requirements/platform-py312.lock"),
        }
    )
    sandbox = build_sandbox_manifest(
        runtime_digest=runtime_digest,
        provider_endpoint=ENDPOINT_ORIGIN,
    )
    inference_parameters = [
        ("allow_fallbacks", "false"),
        ("data_collection", "deny"),
        ("max_tokens", str(MAX_OUTPUT_TOKENS)),
        ("provider", config.provider_route),
        ("require_parameters", "true"),
        ("seed", str(SEED)),
    ]
    if config.temperature is not None:
        inference_parameters.append(("temperature", str(config.temperature)))
    if config.quantizations:
        inference_parameters.append(("quantizations", ",".join(config.quantizations)))
    return PolicyManifest.build(
        provider=f"openrouter/{config.provider_route}",
        model=config.model,
        exact_snapshot=False,
        harness_digest=_file_digest(repository_root / "pixelgym/grounding/v5/runner.py"),
        dependency_lock_digest=_file_digest(
            repository_root / "requirements/platform-py312.lock"
        ),
        system_prompt_digest=content_digest(system_prompt(config)),
        task_renderer_version=TASK_RENDERER_VERSION,
        response_schema_version=RESPONSE_SCHEMA_VERSION,
        state_reducer_version=config.state_reducer_version,
        parser_version=config.parser_version,
        memory_policy_version=config.memory_policy_version,
        coordinate_adapter=config.adapter.name,
        coordinate_adapter_digest=config.adapter.source_digest,
        coordinate_input_convention=config.coordinate_input_convention,
        max_model_attempts_per_action=2,
        max_cancellation_requests_per_attempt=0,
        max_reconciliation_requests_per_attempt=0,
        request_deadline_seconds=180.0,
        cancellation_mode="disabled",
        reconciliation_deadline_seconds=1.0,
        sandbox=sandbox,
        code_revision=code_revision,
        dirty_worktree_policy="reject-tracked-changes",
        inference_parameters=tuple(inference_parameters),
        context_limit=CONTEXT_LIMIT,
        transport_retry_rule=TRANSPORT_RETRY_RULE,
    )
