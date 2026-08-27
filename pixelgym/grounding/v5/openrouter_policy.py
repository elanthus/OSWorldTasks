"""Frozen Qwen3-VL policy package and no-retry OpenRouter transport for v5."""

from __future__ import annotations

import base64
import http.client
import io
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from PIL import Image

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.grounding.v5.contracts import (
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    PolicyManifest,
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5.coordinates import NORMALIZED_1000_ADAPTER
from pixelgym.grounding.v5.runner import TransportOutcome
from pixelgym.grounding.v5.sandbox import build_sandbox_manifest
from pixelgym.serialization import canonical_json_bytes

MODEL = "qwen/qwen3-vl-8b-instruct"
UPSTREAM_PROVIDER = "alibaba"
ENDPOINT_ORIGIN = "https://openrouter.ai"
ENDPOINT = f"{ENDPOINT_ORIGIN}/api/v1/chat/completions"
PRICE_SOURCE = "https://openrouter.ai/api/v1/models/qwen/qwen3-vl-8b-instruct/endpoints"
PROMPT_PRICE_PER_TOKEN_USD = Decimal("0.000000117")
COMPLETION_PRICE_PER_TOKEN_USD = Decimal("0.000000455")
MAX_PROMPT_TOKENS = 129_024
MAX_OUTPUT_TOKENS = 4_096
REQUEST_MAXIMUM_USD = (
    PROMPT_PRICE_PER_TOKEN_USD * MAX_PROMPT_TOKENS
    + COMPLETION_PRICE_PER_TOKEN_USD * MAX_OUTPUT_TOKENS
)

PROMPT_VERSION = "pixelgym-agent-v5-qwen3-vl-stateful-prompt-v1"
PARSER_VERSION = "pixelgym-agent-v5-json-action-qwen-normalized-parser-v2"
STATE_REDUCER_VERSION = "pixelgym-agent-v5-qwen-state-reducer-v1"
MEMORY_POLICY_VERSION = "pixelgym-agent-v5-visible-action-history-v1"
RESPONSE_SCHEMA_VERSION = "pixelgym-agent-v5-canonical-response-v1"
TASK_RENDERER_VERSION = "pixelgym-agent-v5-task-renderer-v1"

ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action_type": {"type": "integer", "minimum": 0, "maximum": 2},
        "x": {"type": "integer", "minimum": 0, "maximum": 999},
        "y": {"type": "integer", "minimum": 0, "maximum": 999},
        "key": {"type": "integer", "minimum": 0, "maximum": len(KEY_ALLOWLIST) - 1},
    },
    "required": ["action_type", "x", "y", "key"],
    "additionalProperties": False,
}


def system_prompt() -> str:
    key_map = ", ".join(f"{index}={key}" for index, key in enumerate(KEY_ALLOWLIST))
    return (
        "You control a deterministic PixelGym application using only the current screenshot, "
        "the task instruction, and the visible-action history supplied below. Choose exactly one "
        "next action. Return only a JSON object with action_type, x, y, and key. action_type is 0 "
        "for NOOP, 1 for CLICK, and 2 for KEY. CLICK coordinates use a normalized 1000x1000 "
        "square grid: integers 0 through 999 on each axis with origin at the upper-left; set key "
        "to 0. For KEY, set x and y to 0 and use this versioned key index mapping: "
        f"{key_map}. For NOOP set x, y, and key to 0. Do not explain the action."
    )


def _png_data_url(screenshot: bytes) -> str:
    expected = SCREEN_WIDTH * SCREEN_HEIGHT * 3
    if len(screenshot) != expected:
        raise ValueError("screenshot byte length does not match frozen RGB dimensions")
    image = Image.frombytes("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), screenshot)
    encoded = io.BytesIO()
    image.save(encoded, format="PNG", compress_level=9, optimize=False)
    return "data:image/png;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")


class QwenV5StatefulPolicy:
    """Credential-free policy state, request construction, parsing, and reduction."""

    def reset(self, task_instruction: str) -> bytes:
        return canonical_json_bytes({"instruction": task_instruction, "history": []})

    def build_request(self, state: bytes, screenshot: bytes) -> dict[str, Any]:
        value = json.loads(state)
        history = value["history"]
        history_text = json.dumps(history, sort_keys=True, separators=(",", ":"))
        prompt = (
            f"Overall task: {value['instruction']}\n"
            f"Visible-action history: {history_text}\n"
            "Choose the next action from the current screenshot."
        )
        return {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system_prompt()},
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
                    "schema": ACTION_SCHEMA,
                },
            },
            "provider": {
                "only": [UPSTREAM_PROVIDER],
                "allow_fallbacks": False,
                "data_collection": "deny",
                "require_parameters": True,
            },
            "temperature": 0,
            "seed": 20260809,
            "max_tokens": MAX_OUTPUT_TOKENS,
        }

    def reduce_state(self, state: bytes, canonical_response: bytes) -> bytes:
        value = json.loads(state)
        response = json.loads(canonical_response)
        value["history"].append({"response_digest": content_digest(response)})
        return canonical_json_bytes(value)

    def failure_state(self, state: bytes, failure_code: str) -> bytes:
        value = json.loads(state)
        value["history"].append({"failure_code": failure_code})
        return canonical_json_bytes(value)

    def retryable_response_code(self, canonical_response: bytes) -> str | None:
        del canonical_response
        return None

    def parse(self, canonical_response: bytes, state: bytes) -> dict[str, Any]:
        del state
        response = json.loads(canonical_response)
        if response["model"] != MODEL:
            raise ValueError("provider response model does not match policy")
        usage = response["usage"]
        if not isinstance(usage, dict) or str(usage.get("upstream_provider", "")).lower() != (
            UPSTREAM_PROVIDER
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
            candidate["x"], candidate["y"] = NORMALIZED_1000_ADAPTER.transform(
                candidate["x"], candidate["y"]
            )
        return candidate

    def post_parse_state(self, state: bytes, candidate: dict[str, Any]) -> bytes:
        value = json.loads(state)
        value["pending_action_digest"] = content_digest(candidate)
        return canonical_json_bytes(value)

    def post_dispatch_state(
        self, state: bytes, action: dict[str, int], result: dict[str, Any]
    ) -> bytes:
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


def _usage_cost(usage: Mapping[str, Any]) -> Decimal:
    try:
        cost = Decimal(str(usage["cost"]))
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise ValueError("OpenRouter usage cost is missing or invalid") from exc
    if not cost.is_finite() or cost < 0:
        raise ValueError("OpenRouter usage cost is missing or invalid")
    return cost


class OpenRouterV5Transport:
    """One-send/no-retry OpenRouter transport with fail-closed aggregate spend checks."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str] = os.environ,
        maximum_spend_usd: Decimal,
        prior_spend_usd: Decimal,
        timeout_seconds: float = 180.0,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        api_key = environment.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        if maximum_spend_usd <= 0 or prior_spend_usd < 0:
            raise ValueError("spend limits must be non-negative")
        self._api_key = api_key
        self.maximum_spend_usd = maximum_spend_usd
        self.spent_usd = prior_spend_usd
        self.timeout_seconds = timeout_seconds
        self._urlopen = urlopen
        self.records: list[dict[str, Any]] = []
        self.wire_requests_sent = 0
        self.blocked = False

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        if self.blocked or self.spent_usd + REQUEST_MAXIMUM_USD > self.maximum_spend_usd:
            return TransportOutcome("pre_send_failure", failure_code="aggregate_spend_guard")
        if request.get("model") != MODEL or request.get("provider") != {
            "only": [UPSTREAM_PROVIDER],
            "allow_fallbacks": False,
            "data_collection": "deny",
            "require_parameters": True,
        }:
            return TransportOutcome("pre_send_failure", failure_code="request_identity_mismatch")
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
        self.wire_requests_sent += 1
        try:
            with self._urlopen(
                wire, timeout=min(self.timeout_seconds, deadline_seconds)
            ) as response:
                body = json.load(response)
        except (
            OSError,
            TimeoutError,
            urllib.error.URLError,
            http.client.HTTPException,
            json.JSONDecodeError,
        ) as exc:
            self.blocked = True
            self.records.append(
                {
                    "idempotency_key": idempotency_key,
                    "status": "unknown",
                    "failure_code": type(exc).__name__,
                }
            )
            return TransportOutcome("unknown", failure_code="provider_request_unknown")
        if not isinstance(body, dict):
            self.blocked = True
            self.records.append(
                {
                    "idempotency_key": idempotency_key,
                    "status": "unknown",
                    "failure_code": "provider_envelope_invalid",
                }
            )
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
        try:
            cost = _usage_cost(usage)
        except ValueError:
            self.blocked = True
            cost = None
            usage["price_guard"] = "missing_or_invalid_cost"
        if cost is not None:
            self.spent_usd += cost
            if cost > REQUEST_MAXIMUM_USD or self.spent_usd > self.maximum_spend_usd:
                self.blocked = True
                usage["price_guard"] = "exceeded"
            else:
                usage["price_guard"] = "ok"
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


def _file_digest(path: Path) -> str:
    return "sha256:" + sha256_bytes(path.read_bytes())


def build_policy_manifest(repository_root: Path, *, code_revision: str) -> PolicyManifest:
    module_path = repository_root / "pixelgym/grounding/v5/openrouter_policy.py"
    runtime_digest = content_digest(
        {
            "module": _file_digest(module_path),
            "pyproject": _file_digest(repository_root / "pyproject.toml"),
            "lock": _file_digest(repository_root / "requirements/platform-py312.lock"),
        }
    )
    sandbox = build_sandbox_manifest(
        runtime_digest=runtime_digest,
        provider_endpoint=ENDPOINT_ORIGIN,
    )
    return PolicyManifest.build(
        provider="openrouter/alibaba",
        model=MODEL,
        exact_snapshot=False,
        harness_digest=_file_digest(repository_root / "pixelgym/grounding/v5/runner.py"),
        dependency_lock_digest=_file_digest(
            repository_root / "requirements/platform-py312.lock"
        ),
        system_prompt_digest=content_digest(system_prompt()),
        task_renderer_version=TASK_RENDERER_VERSION,
        response_schema_version=RESPONSE_SCHEMA_VERSION,
        state_reducer_version=STATE_REDUCER_VERSION,
        parser_version=PARSER_VERSION,
        memory_policy_version=MEMORY_POLICY_VERSION,
        coordinate_adapter=NORMALIZED_1000_ADAPTER.name,
        coordinate_adapter_digest=NORMALIZED_1000_ADAPTER.source_digest,
        coordinate_input_convention="integer-normalized-square/0..999-inclusive",
        max_model_attempts_per_action=1,
        max_cancellation_requests_per_attempt=0,
        max_reconciliation_requests_per_attempt=0,
        request_deadline_seconds=180.0,
        cancellation_mode="disabled",
        reconciliation_deadline_seconds=1.0,
        sandbox=sandbox,
        code_revision=code_revision,
        dirty_worktree_policy="reject-tracked-changes",
        inference_parameters=(
            ("allow_fallbacks", "false"),
            ("data_collection", "deny"),
            ("max_tokens", str(MAX_OUTPUT_TOKENS)),
            ("provider", UPSTREAM_PROVIDER),
            ("seed", "20260809"),
            ("temperature", "0"),
        ),
        context_limit=131_072,
        transport_retry_rule="no-retry-after-send-v1",
    )
