"""Frozen four-policy OpenRouter panel for the v5 D5.6 calibration."""

from __future__ import annotations

import http.client
import json
import math
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.grounding.v5.contracts import PolicyManifest, content_digest, sha256_bytes
from pixelgym.grounding.v5.coordinates import (
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
MAX_RUNNER_DEADLINE_SAFETY_MARGIN_SECONDS = 1.0
MAX_OUTPUT_TOKENS = 4_096
CONTEXT_LIMIT = 131_072
MAX_PROMPT_TOKENS = CONTEXT_LIMIT - MAX_OUTPUT_TOKENS
PANEL_MAXIMUM_SPEND_USD = Decimal("10.00")
PRIOR_AGGREGATE_SPEND_USD = Decimal("0.370889195")
SEED = 20260809
GEMINI_FULL_CALIBRATION_POLICY_GENERATION = "v3"

RESPONSE_SCHEMA_VERSION = "pixelgym-agent-v5-canonical-response-v2"
TASK_RENDERER_VERSION = "pixelgym-agent-v5-task-renderer-v1"
TRANSPORT_RETRY_RULE = (
    "bounded-same-route-zero-completion-http-429-or-transient-transport-fault-"
    "after-bounded-backoff-v3"
)
def bounded_retry_stop_rule(*, ledger: str) -> str:
    """Describe bounded retries against the plan's actual spend-ledger scope."""

    return (
        "retry on the same pinned route, up to the per-action bounded-retry budget declared "
        "in the policy manifest, after a confirmed HTTP 429, a transient transport fault "
        "(dropped connection, timeout, retryable 5xx, or unreadable envelope), or a canonical "
        "zero-token, zero-cost, empty response with finish_reason error; wait for bounded "
        "Retry-After or exponential backoff before each retry; reserve the per-request "
        f"theoretical maximum against {ledger} for every send whose charge cannot be "
        "observed; retain every attempt"
    )


BOUNDED_RETRY_STOP_RULE = bounded_retry_stop_rule(ledger="the shared ledger")
RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 500, 502, 503, 504})
# How much of an unobservable charge to hold, as a multiple of the most expensive
# response this run has actually priced. The per-request theoretical maximum stays
# the ceiling; this only tightens the hold once the run has real evidence.
UNOBSERVED_CHARGE_CEILING_MULTIPLIER = Decimal(3)


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
    strict_response_schema: bool = True
    response_format_type: Literal["json_schema", "json_object"] = "json_schema"
    router_metadata: bool = False
    max_model_attempts_per_action: int = 2
    max_rate_limit_retries_per_action: int = 1
    max_bounded_retries_per_action: int | None = None
    rate_limit_backoff_base_seconds: float = 2.0
    rate_limit_backoff_max_seconds: float = 60.0
    request_deadline_seconds: float = 180.0

    def __post_init__(self) -> None:
        if not 0 <= self.max_rate_limit_retries_per_action < self.max_model_attempts_per_action:
            raise ValueError("rate-limit retry cap must fit within the model-attempt cap")
        if not (
            self.max_rate_limit_retries_per_action
            <= self.bounded_retry_budget
            < self.max_model_attempts_per_action
        ):
            raise ValueError("bounded retry cap must fit within the model-attempt cap")
        if not 0 < self.rate_limit_backoff_base_seconds <= self.rate_limit_backoff_max_seconds:
            raise ValueError("rate-limit backoff bounds are invalid")

    @property
    def bounded_retry_budget(self) -> int:
        """Retries per action shared by rate limits and transient transport faults.

        A config that predates transport-fault retries leaves this unset and keeps
        its original rate-limit-only budget.
        """

        if self.max_bounded_retries_per_action is None:
            return self.max_rate_limit_retries_per_action
        return self.max_bounded_retries_per_action

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
    price_source=("https://openrouter.ai/api/v1/models/google/gemini-3.7-flash/endpoints"),
    adapter=NORMALIZED_1000_ADAPTER,
    coordinate_input_convention="integer-normalized-square/0..999-inclusive",
    stateful=True,
    temperature=None,
)
GEMINI_STATEFUL_ONE_CALL_SMOKE = PanelPolicyConfig(
    slot="A-gemini-stateful-one-call-smoke",
    model=GEMINI_STATEFUL.model,
    provider_route=GEMINI_STATEFUL.provider_route,
    response_provider=GEMINI_STATEFUL.response_provider,
    # google-vertex/global can match its /flex and /priority variants. Reserve
    # against the highest-priced matching endpoint instead of the base price.
    prompt_price_per_token_usd=Decimal("0.000000675"),
    completion_price_per_token_usd=Decimal("0.000003375"),
    price_source=GEMINI_STATEFUL.price_source,
    adapter=GEMINI_STATEFUL.adapter,
    coordinate_input_convention=GEMINI_STATEFUL.coordinate_input_convention,
    stateful=True,
    temperature=None,
    router_metadata=True,
    max_model_attempts_per_action=1,
    max_rate_limit_retries_per_action=0,
)
GEMINI_STATEFUL_FULL_CALIBRATION = PanelPolicyConfig(
    slot=f"A-gemini-stateful-{GEMINI_FULL_CALIBRATION_POLICY_GENERATION}",
    model=GEMINI_STATEFUL_ONE_CALL_SMOKE.model,
    provider_route=GEMINI_STATEFUL_ONE_CALL_SMOKE.provider_route,
    response_provider=GEMINI_STATEFUL_ONE_CALL_SMOKE.response_provider,
    prompt_price_per_token_usd=(
        GEMINI_STATEFUL_ONE_CALL_SMOKE.prompt_price_per_token_usd
    ),
    completion_price_per_token_usd=(
        GEMINI_STATEFUL_ONE_CALL_SMOKE.completion_price_per_token_usd
    ),
    price_source=GEMINI_STATEFUL_ONE_CALL_SMOKE.price_source,
    adapter=GEMINI_STATEFUL_ONE_CALL_SMOKE.adapter,
    coordinate_input_convention=(
        GEMINI_STATEFUL_ONE_CALL_SMOKE.coordinate_input_convention
    ),
    stateful=True,
    temperature=None,
    router_metadata=True,
    max_model_attempts_per_action=4,
    max_rate_limit_retries_per_action=3,
    max_bounded_retries_per_action=3,
    # The transport retains its 180-second timeout. The extra outer margin prevents
    # a transport timeout from racing the runner deadline and becoming ambiguous.
    request_deadline_seconds=210.0,
)
QWEN_STATEFUL = PanelPolicyConfig(
    slot="B-qwen-stateful",
    model="qwen/qwen3-vl-8b-instruct",
    provider_route="alibaba",
    response_provider="Alibaba",
    prompt_price_per_token_usd=Decimal("0.000000117"),
    completion_price_per_token_usd=Decimal("0.000000455"),
    price_source=("https://openrouter.ai/api/v1/models/qwen/qwen3-vl-8b-instruct/endpoints"),
    adapter=NORMALIZED_1000_ADAPTER,
    coordinate_input_convention="integer-normalized-square/0..999-inclusive",
    stateful=True,
)
QWEN_STATEFUL_RETRY_SUCCESSOR = PanelPolicyConfig(
    slot="B-qwen-stateful-v3",
    model=QWEN_STATEFUL.model,
    provider_route=QWEN_STATEFUL.provider_route,
    response_provider=QWEN_STATEFUL.response_provider,
    prompt_price_per_token_usd=QWEN_STATEFUL.prompt_price_per_token_usd,
    completion_price_per_token_usd=QWEN_STATEFUL.completion_price_per_token_usd,
    price_source=QWEN_STATEFUL.price_source,
    adapter=QWEN_STATEFUL.adapter,
    coordinate_input_convention=QWEN_STATEFUL.coordinate_input_convention,
    stateful=True,
    max_model_attempts_per_action=4,
    max_rate_limit_retries_per_action=3,
    max_bounded_retries_per_action=3,
    # Match the Gemini successor: keep the runner deadline clear of the
    # transport's own 180-second timeout so a slow send stays unambiguous.
    request_deadline_seconds=210.0,
)
LLAMA_STATEFUL = PanelPolicyConfig(
    slot="C-llama-stateful",
    model="meta-llama/llama-4-scout",
    provider_route="deepinfra",
    response_provider="DeepInfra",
    prompt_price_per_token_usd=Decimal("0.0000001"),
    completion_price_per_token_usd=Decimal("0.0000003"),
    price_source=("https://openrouter.ai/api/v1/models/meta-llama/llama-4-scout/endpoints"),
    adapter=NORMALIZED_1000_ADAPTER,
    coordinate_input_convention="integer-normalized-square/0..999-inclusive",
    stateful=True,
    quantizations=("fp8",),
)
GLM_STATEFUL_CANDIDATE = PanelPolicyConfig(
    slot="C-glm-stateful-candidate",
    model="z-ai/glm-5.3-flash",
    provider_route="novita",
    response_provider="Novita",
    prompt_price_per_token_usd=Decimal("0.000000075"),
    completion_price_per_token_usd=Decimal("0.00000025"),
    price_source=("https://openrouter.ai/api/v1/models/z-ai/glm-5.3-flash/endpoints"),
    adapter=NORMALIZED_1000_ADAPTER,
    coordinate_input_convention="integer-normalized-square/0..999-inclusive",
    stateful=True,
    quantizations=("fp8",),
)
GLM_STATEFUL_RELAXED_SCHEMA_CANDIDATE = PanelPolicyConfig(
    slot="C-glm-stateful-relaxed-schema-candidate",
    model=GLM_STATEFUL_CANDIDATE.model,
    provider_route=GLM_STATEFUL_CANDIDATE.provider_route,
    response_provider=GLM_STATEFUL_CANDIDATE.response_provider,
    prompt_price_per_token_usd=GLM_STATEFUL_CANDIDATE.prompt_price_per_token_usd,
    completion_price_per_token_usd=GLM_STATEFUL_CANDIDATE.completion_price_per_token_usd,
    price_source=GLM_STATEFUL_CANDIDATE.price_source,
    adapter=GLM_STATEFUL_CANDIDATE.adapter,
    coordinate_input_convention=GLM_STATEFUL_CANDIDATE.coordinate_input_convention,
    stateful=True,
    quantizations=("fp8",),
    strict_response_schema=False,
)
GLM_STATEFUL_JSON_OBJECT_SMOKE_CANDIDATE = PanelPolicyConfig(
    slot="C-glm-stateful-json-object-smoke-candidate",
    model=GLM_STATEFUL_CANDIDATE.model,
    provider_route="novita/fp8",
    response_provider=GLM_STATEFUL_CANDIDATE.response_provider,
    prompt_price_per_token_usd=GLM_STATEFUL_CANDIDATE.prompt_price_per_token_usd,
    completion_price_per_token_usd=GLM_STATEFUL_CANDIDATE.completion_price_per_token_usd,
    price_source=GLM_STATEFUL_CANDIDATE.price_source,
    adapter=GLM_STATEFUL_CANDIDATE.adapter,
    coordinate_input_convention=GLM_STATEFUL_CANDIDATE.coordinate_input_convention,
    stateful=True,
    quantizations=("fp8",),
    response_format_type="json_object",
    router_metadata=True,
    max_model_attempts_per_action=1,
    max_rate_limit_retries_per_action=0,
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
        response_format: dict[str, Any] = {"type": self.config.response_format_type}
        if self.config.response_format_type == "json_schema":
            response_format["json_schema"] = {
                "name": "pixelgym_v5_action",
                "strict": self.config.strict_response_schema,
                "schema": action_schema(self.config),
            }
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
            "response_format": response_format,
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
        if (
            response.get("model") != self.config.model
            or str(usage.get("upstream_provider", "")).lower()
            != self.config.response_provider.lower()
        ):
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
    """Sequential spend cap for one explicitly configured ledger scope."""

    maximum_spend_usd: Decimal
    spent_usd: Decimal
    wire_requests_sent: int = 0
    unknown_reservation_usd: Decimal = Decimal(0)
    unknown_charge_outcomes: int = 0
    max_observed_cost_usd: Decimal = Decimal(0)
    blocked: bool = False
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        balances = (
            self.maximum_spend_usd,
            self.spent_usd,
            self.unknown_reservation_usd,
            self.max_observed_cost_usd,
        )
        if not all(value.is_finite() for value in balances):
            raise ValueError("spend limits and balances must be finite")
        if self.maximum_spend_usd <= 0 or any(value < 0 for value in balances[1:]):
            raise ValueError("spend limits and balances must be non-negative")
        if self.budget_accounted_spend_usd > self.maximum_spend_usd:
            raise ValueError("prior spend and reservations exceed the aggregate cap")

    @property
    def budget_accounted_spend_usd(self) -> Decimal:
        """Known spend plus worst-case reservations for unobservable charges."""

        return self.spent_usd + self.unknown_reservation_usd

    def reserve_wire(self, request_maximum_usd: Decimal) -> bool:
        with self._lock:
            projected = (
                self.spent_usd + self.unknown_reservation_usd + request_maximum_usd
            )
            if self.blocked or projected > self.maximum_spend_usd:
                return False
            self.wire_requests_sent += 1
            return True

    def record_cost(self, cost: Decimal, request_maximum_usd: Decimal) -> bool:
        with self._lock:
            self.spent_usd += cost
            self.max_observed_cost_usd = max(self.max_observed_cost_usd, cost)
            if (
                cost > request_maximum_usd
                or self.spent_usd + self.unknown_reservation_usd > self.maximum_spend_usd
            ):
                self.blocked = True
                return False
            return True

    def unknown_charge_reservation_usd(self, request_maximum_usd: Decimal) -> Decimal:
        """What to hold for one send whose charge cannot be read."""

        with self._lock:
            return self._unknown_charge_reservation(request_maximum_usd)

    def _unknown_charge_reservation(self, request_maximum_usd: Decimal) -> Decimal:
        """Caller holds the lock.

        The per-request theoretical maximum assumes a full context window that
        this workload never approaches, so holding it for every fault drains the
        budget for charges that are often never incurred. Once the run has priced
        real responses, hold a multiple of the most expensive one instead. Before
        any response has been priced there is no evidence, so the full theoretical
        maximum is held, and it remains the ceiling in every case.
        """

        if self.max_observed_cost_usd <= 0:
            return request_maximum_usd
        return min(
            request_maximum_usd,
            self.max_observed_cost_usd * UNOBSERVED_CHARGE_CEILING_MULTIPLIER,
        )

    def reserve_unknown_charge(self, request_maximum_usd: Decimal) -> Decimal:
        """Charge a sent request whose actual cost cannot be observed.

        The request may have been served and billed upstream. Reserving against
        it keeps the guard fail-closed while letting the run continue, instead of
        blocking every remaining request. Returns the amount held.
        """

        with self._lock:
            reservation = self._unknown_charge_reservation(request_maximum_usd)
            self.unknown_reservation_usd += reservation
            self.unknown_charge_outcomes += 1
            if self.spent_usd + self.unknown_reservation_usd > self.maximum_spend_usd:
                self.blocked = True
            return reservation

    def block(self) -> None:
        with self._lock:
            self.blocked = True


class OpenRouterPanelTransport:
    """One-wire-send transport with an observable, shared 429 cooldown."""

    def __init__(
        self,
        config: PanelPolicyConfig,
        *,
        ledger: SpendLedger,
        environment: Mapping[str, str] = os.environ,
        timeout_seconds: float = 180.0,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        api_key = environment.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        self.config = config
        self.ledger = ledger
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._urlopen = urlopen
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._sleep = sleep
        self._cooldown_until = 0.0
        self._consecutive_rate_limits = 0
        self._consecutive_transport_faults = 0
        self.records: list[dict[str, Any]] = []

    @property
    def spent_usd(self) -> Decimal:
        return self.ledger.spent_usd

    @property
    def unknown_reservation_usd(self) -> Decimal:
        return self.ledger.unknown_reservation_usd

    def _transport_fault(
        self,
        *,
        idempotency_key: str,
        failure_code: str,
        started: float,
        cooldown_wait: float,
        metadata: dict[str, Any] | None = None,
    ) -> TransportOutcome:
        """Reserve an unobservable charge and hand the runner a retryable outcome.

        The send reached the wire, so the provider may already have served and
        billed it. The worst-case reservation keeps the aggregate guard honest
        without blocking every request that follows.
        """

        self._consecutive_transport_faults += 1
        reservation = self.ledger.reserve_unknown_charge(self.config.request_maximum_usd)
        backoff_seconds = min(
            self.config.rate_limit_backoff_base_seconds
            * (2 ** min(max(0, self._consecutive_transport_faults - 1), 63)),
            self.config.rate_limit_backoff_max_seconds,
        )
        self._cooldown_until = max(
            self._cooldown_until, self._monotonic() + backoff_seconds
        )
        self.records.append(
            {
                "idempotency_key": idempotency_key,
                "slot": self.config.slot,
                "status": "transport_fault",
                "failure_code": failure_code,
                "latency_ms": (self._monotonic() - started) * 1000,
                "pre_send_backoff_seconds": cooldown_wait,
                "retry_after_seconds": backoff_seconds,
                "backoff_source": "exponential_fallback",
                "cost_usd": None,
                "unknown_charge_reservation_usd": str(reservation),
                **(metadata or {}),
            }
        )
        return TransportOutcome(
            "transport_fault",
            failure_code=failure_code,
            retry_after_seconds=backoff_seconds,
            backoff_source="exponential_fallback",
        )

    @property
    def wire_requests_sent(self) -> int:
        return self.ledger.wire_requests_sent

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        call_started = self._monotonic()
        if (
            request.get("model") != self.config.model
            or request.get("provider") != self.config.provider_parameters()
        ):
            return TransportOutcome("pre_send_failure", failure_code="request_identity_mismatch")
        cooldown_wait = max(0.0, self._cooldown_until - self._monotonic())
        if cooldown_wait >= deadline_seconds:
            return TransportOutcome(
                "pre_send_failure",
                failure_code="rate_limit_cooldown_exceeds_request_deadline",
            )
        if cooldown_wait:
            self._sleep(cooldown_wait)
        elapsed_before_wire = self._monotonic() - call_started
        deadline_margin = min(
            MAX_RUNNER_DEADLINE_SAFETY_MARGIN_SECONDS,
            deadline_seconds * 0.1,
        )
        remaining_deadline = deadline_seconds - elapsed_before_wire - deadline_margin
        if remaining_deadline <= 0:
            return TransportOutcome(
                "pre_send_failure",
                failure_code="rate_limit_cooldown_exhausted_request_deadline",
            )
        if not self.ledger.reserve_wire(self.config.request_maximum_usd):
            return TransportOutcome("pre_send_failure", failure_code="aggregate_spend_guard")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        if self.config.router_metadata:
            headers["X-OpenRouter-Metadata"] = "enabled"
        wire = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(request, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started = self._monotonic()
        try:
            with self._urlopen(
                wire, timeout=min(self.timeout_seconds, remaining_deadline)
            ) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                self._consecutive_rate_limits += 1
                retry_after_seconds, backoff_source = _rate_limit_backoff(
                    exc,
                    consecutive_rate_limits=self._consecutive_rate_limits,
                    base_seconds=self.config.rate_limit_backoff_base_seconds,
                    max_seconds=self.config.rate_limit_backoff_max_seconds,
                    wall_time=self._wall_time(),
                )
                self._cooldown_until = max(
                    self._cooldown_until,
                    self._monotonic() + retry_after_seconds,
                )
                self.records.append(
                    {
                        "idempotency_key": idempotency_key,
                        "status": "rate_limited",
                        "failure_code": type(exc).__name__,
                        "latency_ms": (self._monotonic() - started) * 1000,
                        "pre_send_backoff_seconds": cooldown_wait,
                        "retry_after_seconds": retry_after_seconds,
                        "backoff_source": backoff_source,
                        "cost_usd": "0",
                        **_safe_http_error_metadata(exc),
                    }
                )
                return TransportOutcome(
                    "rate_limited",
                    failure_code="http_429_rate_limit",
                    retry_after_seconds=retry_after_seconds,
                    backoff_source=backoff_source,
                )
            if exc.code in RETRYABLE_HTTP_STATUSES:
                return self._transport_fault(
                    idempotency_key=idempotency_key,
                    failure_code=f"retryable_http_{exc.code}",
                    started=started,
                    cooldown_wait=cooldown_wait,
                    metadata=_safe_http_error_metadata(exc),
                )
            # A non-retryable HTTP status is a request, route, or credential
            # defect. Retrying cannot fix it, so the ledger stays blocked.
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
            return self._transport_fault(
                idempotency_key=idempotency_key,
                failure_code=type(exc).__name__,
                started=started,
                cooldown_wait=cooldown_wait,
            )
        if not isinstance(body, dict):
            return self._transport_fault(
                idempotency_key=idempotency_key,
                failure_code="provider_envelope_invalid",
                started=started,
                cooldown_wait=cooldown_wait,
            )
        self._consecutive_rate_limits = 0
        self._consecutive_transport_faults = 0
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
            # The response arrived but its charge is unreadable, which is the same
            # unobservable-charge case as a dropped send: reserve the worst case
            # rather than blocking every request that follows. An envelope that
            # also lost its model identity is degenerate, not a billing anomaly,
            # so it retries like any other transient transport fault.
            cost = None
            usage["price_guard"] = "missing_or_invalid_cost"
            if body.get("model") is None and body.get("provider") is None:
                return self._transport_fault(
                    idempotency_key=idempotency_key,
                    failure_code="provider_response_envelope_incomplete",
                    started=started,
                    cooldown_wait=cooldown_wait,
                )
            self.ledger.reserve_unknown_charge(self.config.request_maximum_usd)
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
                "latency_ms": (self._monotonic() - started) * 1000,
                "pre_send_backoff_seconds": cooldown_wait,
                "response_id_digest": content_digest(str(body.get("id", ""))),
                "response_model": body.get("model"),
                "upstream_provider": body.get("provider"),
                "cost_usd": str(cost) if cost is not None else None,
            }
        )
        return TransportOutcome("response", canonical)

    def cancel(self, *, idempotency_key: str, mode: str) -> Literal["cancelled", "unknown"]:
        del idempotency_key, mode
        return "unknown"

    def reconcile(self, *, idempotency_key: str, deadline_seconds: float) -> TransportOutcome:
        del idempotency_key, deadline_seconds
        return TransportOutcome("unknown", failure_code="reconciliation_disabled")


def _rate_limit_backoff(
    exc: urllib.error.HTTPError,
    *,
    consecutive_rate_limits: int,
    base_seconds: float,
    max_seconds: float,
    wall_time: float,
) -> tuple[float, Literal["retry_after", "exponential_fallback"]]:
    """Parse standard Retry-After forms, falling back to capped exponential delay."""

    raw_retry_after = exc.headers.get("Retry-After") if exc.headers is not None else None
    parsed: float | None = None
    if raw_retry_after is not None:
        try:
            parsed = float(raw_retry_after)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw_retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                now = datetime.fromtimestamp(wall_time, tz=UTC)
                parsed = (retry_at - now).total_seconds()
            except (TypeError, ValueError, OverflowError):
                parsed = None
    if parsed is not None and math.isfinite(parsed) and parsed >= 0:
        return min(parsed, max_seconds), "retry_after"
    exponent = min(max(0, consecutive_rate_limits - 1), 63)
    fallback = base_seconds * (2**exponent)
    return min(fallback, max_seconds), "exponential_fallback"


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
        if isinstance(field, (int, float, bool)) or (isinstance(field, str) and len(field) <= 128):
            metadata[target] = field
    provider_metadata = error.get("metadata")
    if isinstance(provider_metadata, dict):
        provider_name = provider_metadata.get("provider_name")
        if isinstance(provider_name, str) and len(provider_name) <= 128:
            metadata["upstream_provider"] = provider_name
    router_metadata = _safe_router_metadata(value.get("openrouter_metadata"))
    if router_metadata:
        metadata["openrouter_metadata"] = router_metadata
    return metadata


def _safe_router_metadata(value: object) -> dict[str, Any]:
    """Allowlist credential-free routing facts from an opted-in error envelope."""

    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, maximum_length in (("requested", 256), ("strategy", 64)):
        item = value.get(key)
        if isinstance(item, str) and len(item) <= maximum_length:
            result[key] = item
    attempt = value.get("attempt")
    if type(attempt) is int and attempt >= 0:
        result["attempt"] = attempt
    endpoints = value.get("endpoints")
    if not isinstance(endpoints, dict):
        return result
    endpoint_record: dict[str, Any] = {}
    total = endpoints.get("total")
    if type(total) is int and total >= 0:
        endpoint_record["total"] = total
    available = endpoints.get("available")
    safe_available: list[dict[str, Any]] = []
    if isinstance(available, list):
        for endpoint in available[:32]:
            if not isinstance(endpoint, dict):
                continue
            safe_endpoint: dict[str, Any] = {}
            for key in ("provider", "model"):
                item = endpoint.get(key)
                if isinstance(item, str) and len(item) <= 256:
                    safe_endpoint[key] = item
            selected = endpoint.get("selected")
            if type(selected) is bool:
                safe_endpoint["selected"] = selected
            if safe_endpoint:
                safe_available.append(safe_endpoint)
    if safe_available:
        endpoint_record["available"] = safe_available
    if endpoint_record:
        result["endpoints"] = endpoint_record
    return result


def _safe_response_error_metadata(body: dict[str, Any], *, finish_reason: object) -> dict[str, Any]:
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
        ("response_schema_strict", str(config.strict_response_schema).lower()),
        ("seed", str(SEED)),
        (
            "max_rate_limit_retries_per_action",
            str(config.max_rate_limit_retries_per_action),
        ),
        ("rate_limit_backoff_base_seconds", str(config.rate_limit_backoff_base_seconds)),
        ("rate_limit_backoff_max_seconds", str(config.rate_limit_backoff_max_seconds)),
        (
            "runner_deadline_safety_margin_seconds",
            str(MAX_RUNNER_DEADLINE_SAFETY_MARGIN_SECONDS),
        ),
    ]
    if config.max_bounded_retries_per_action is not None:
        inference_parameters.append(
            (
                "max_bounded_retries_per_action",
                str(config.max_bounded_retries_per_action),
            )
        )
    if config.temperature is not None:
        inference_parameters.append(("temperature", str(config.temperature)))
    if config.quantizations:
        inference_parameters.append(("quantizations", ",".join(config.quantizations)))
    if config.response_format_type != "json_schema":
        inference_parameters.append(("response_format_type", config.response_format_type))
    if config.router_metadata:
        inference_parameters.append(("router_metadata", "enabled"))
    return PolicyManifest.build(
        provider=f"openrouter/{config.provider_route}",
        model=config.model,
        exact_snapshot=False,
        harness_digest=_file_digest(repository_root / "pixelgym/grounding/v5/runner.py"),
        dependency_lock_digest=_file_digest(repository_root / "requirements/platform-py312.lock"),
        system_prompt_digest=content_digest(system_prompt(config)),
        task_renderer_version=TASK_RENDERER_VERSION,
        response_schema_version=RESPONSE_SCHEMA_VERSION,
        state_reducer_version=config.state_reducer_version,
        parser_version=config.parser_version,
        memory_policy_version=config.memory_policy_version,
        coordinate_adapter=config.adapter.name,
        coordinate_adapter_digest=config.adapter.source_digest,
        coordinate_input_convention=config.coordinate_input_convention,
        max_model_attempts_per_action=config.max_model_attempts_per_action,
        max_cancellation_requests_per_attempt=0,
        max_reconciliation_requests_per_attempt=0,
        request_deadline_seconds=config.request_deadline_seconds,
        cancellation_mode="disabled",
        reconciliation_deadline_seconds=1.0,
        sandbox=sandbox,
        code_revision=code_revision,
        dirty_worktree_policy="reject-tracked-changes",
        inference_parameters=tuple(inference_parameters),
        context_limit=CONTEXT_LIMIT,
        transport_retry_rule=TRANSPORT_RETRY_RULE,
    )
