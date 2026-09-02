from __future__ import annotations

import io
import json
import urllib.error
from decimal import Decimal
from pathlib import Path
from typing import Any, Self

import pytest

from pixelgym.grounding.v5.contracts import sha256_bytes
from pixelgym.grounding.v5.panel_policy import (
    GEMINI_STATEFUL,
    GEMINI_STATEFUL_FULL_CALIBRATION,
    GEMINI_STATEFUL_ONE_CALL_SMOKE,
    GLM_STATEFUL_CANDIDATE,
    GLM_STATEFUL_JSON_OBJECT_SMOKE_CANDIDATE,
    GLM_STATEFUL_RELAXED_SCHEMA_CANDIDATE,
    LLAMA_STATEFUL,
    PANEL,
    QWEN_STATEFUL,
    QWEN_STATELESS,
    TRANSPORT_RETRY_RULE,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
    action_schema,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.runner import TransportOutcome
from pixelgym.serialization import canonical_json_bytes

ROOT = Path(__file__).parents[2]


class FakeHttpResponse:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.value).encode("utf-8")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def canonical_response(
    *, config: Any, action: str = '{"action_type":0,"x":0,"y":0,"key":0}'
) -> bytes:
    return canonical_json_bytes(
        {
            "response_id": "response-1",
            "model": config.model,
            "content": action,
            "finish_reason": "stop",
            "usage": {
                "cost": "0.0001",
                "upstream_provider": config.response_provider,
                "price_guard": "ok",
            },
        }
    )


def test_panel_has_four_slots_and_intentional_qwen_memory_ablation() -> None:
    assert [config.slot for config in PANEL] == [
        "A-gemini-stateful",
        "B-qwen-stateful",
        "C-llama-stateful",
        "D-qwen-stateless",
    ]
    assert QWEN_STATEFUL.model == QWEN_STATELESS.model
    assert QWEN_STATEFUL.provider_route == QWEN_STATELESS.provider_route
    assert QWEN_STATEFUL.adapter == QWEN_STATELESS.adapter
    assert QWEN_STATEFUL.stateful
    assert not QWEN_STATELESS.stateful
    assert QWEN_STATEFUL.memory_policy_version != QWEN_STATELESS.memory_policy_version


@pytest.mark.parametrize("config", PANEL)
def test_panel_requests_pin_provider_and_are_credential_free(config: Any) -> None:
    policy = OpenRouterPanelPolicy(config)
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))

    assert request["model"] == config.model
    assert request["provider"] == config.provider_parameters()
    assert request["max_tokens"] == 4096
    if config.temperature is None:
        assert "temperature" not in request
    else:
        assert request["temperature"] == config.temperature
    assert request["response_format"]["json_schema"]["schema"] == action_schema(config)
    assert "api_key" not in json.dumps(request).lower()


def test_llama_uses_normalized_coordinates_and_fp8_route_filter() -> None:
    policy = OpenRouterPanelPolicy(LLAMA_STATEFUL)
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))
    schema = request["response_format"]["json_schema"]["schema"]

    assert request["provider"]["only"] == ["deepinfra"]
    assert request["provider"]["quantizations"] == ["fp8"]
    assert schema["properties"]["x"]["maximum"] == 999
    assert schema["properties"]["y"]["maximum"] == 999
    assert OpenRouterPanelPolicy(LLAMA_STATEFUL).parse(
        canonical_response(
            config=LLAMA_STATEFUL,
            action='{"action_type":1,"x":999,"y":999,"key":0}',
        ),
        policy.reset("task"),
    ) == {"action_type": 1, "x": 1023, "y": 767, "key": 0}


def test_glm_candidate_uses_novita_fp8_and_normalized_coordinates() -> None:
    policy = OpenRouterPanelPolicy(GLM_STATEFUL_CANDIDATE)
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))
    schema = request["response_format"]["json_schema"]["schema"]

    assert GLM_STATEFUL_CANDIDATE not in PANEL
    assert request["model"] == "z-ai/glm-5.3-flash"
    assert request["provider"] == {
        "only": ["novita"],
        "allow_fallbacks": False,
        "data_collection": "deny",
        "require_parameters": True,
        "quantizations": ["fp8"],
    }
    assert request["seed"] == 20260809
    assert schema["properties"]["x"]["maximum"] == 999
    assert schema["properties"]["y"]["maximum"] == 999
    assert GLM_STATEFUL_CANDIDATE.request_maximum_usd == Decimal("0.0105472")
    assert request["response_format"]["json_schema"]["strict"] is True


def test_glm_relaxed_schema_candidate_changes_only_schema_mode_and_identity() -> None:
    strict_policy = OpenRouterPanelPolicy(GLM_STATEFUL_CANDIDATE)
    relaxed_policy = OpenRouterPanelPolicy(GLM_STATEFUL_RELAXED_SCHEMA_CANDIDATE)
    strict_request = strict_policy.build_request(strict_policy.reset("task"), bytes(1024 * 768 * 3))
    relaxed_request = relaxed_policy.build_request(
        relaxed_policy.reset("task"), bytes(1024 * 768 * 3)
    )

    assert GLM_STATEFUL_RELAXED_SCHEMA_CANDIDATE not in PANEL
    assert relaxed_request["response_format"]["json_schema"]["strict"] is False
    strict_request["response_format"]["json_schema"]["strict"] = False
    assert strict_request == relaxed_request
    strict_manifest = build_panel_policy_manifest(
        ROOT, config=GLM_STATEFUL_CANDIDATE, code_revision="revision"
    )
    relaxed_manifest = build_panel_policy_manifest(
        ROOT,
        config=GLM_STATEFUL_RELAXED_SCHEMA_CANDIDATE,
        code_revision="revision",
    )
    assert strict_manifest.policy_id != relaxed_manifest.policy_id
    assert dict(strict_manifest.inference_parameters)["response_schema_strict"] == "true"
    assert dict(relaxed_manifest.inference_parameters)["response_schema_strict"] == "false"

    with pytest.raises((json.JSONDecodeError, ValueError, KeyError, TypeError)):
        relaxed_policy.parse(
            canonical_response(config=GLM_STATEFUL_RELAXED_SCHEMA_CANDIDATE, action="not json"),
            relaxed_policy.reset("task"),
        )


def test_glm_json_object_smoke_pins_exact_novita_endpoint_without_structured_outputs() -> None:
    config = GLM_STATEFUL_JSON_OBJECT_SMOKE_CANDIDATE
    policy = OpenRouterPanelPolicy(config)
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))

    assert config not in PANEL
    assert request["model"] == "z-ai/glm-5.3-flash"
    assert request["provider"] == {
        "only": ["novita/fp8"],
        "allow_fallbacks": False,
        "data_collection": "deny",
        "require_parameters": True,
        "quantizations": ["fp8"],
    }
    assert request["response_format"] == {"type": "json_object"}
    assert config.max_model_attempts_per_action == 1
    manifest = build_panel_policy_manifest(ROOT, config=config, code_revision="revision")
    assert manifest.max_model_attempts_per_action == 1
    assert dict(manifest.inference_parameters)["response_format_type"] == "json_object"
    assert dict(manifest.inference_parameters)["router_metadata"] == "enabled"


def test_glm_json_object_smoke_requests_safe_router_metadata() -> None:
    config = GLM_STATEFUL_JSON_OBJECT_SMOKE_CANDIDATE
    captured: dict[str, Any] = {}

    def urlopen(request: Any, *, timeout: float) -> FakeHttpResponse:
        del timeout
        captured["metadata_header"] = request.get_header("X-openrouter-metadata")
        return FakeHttpResponse(
            {
                "id": "response-1",
                "model": config.model,
                "provider": config.response_provider,
                "choices": [{"message": {"content": '{"action_type":0,"x":0,"y":0,"key":0}'}}],
                "usage": {"cost": "0.0001", "completion_tokens": 1},
            }
        )

    ledger = SpendLedger(Decimal(10), Decimal(0))
    policy = OpenRouterPanelPolicy(config)
    outcome = OpenRouterPanelTransport(
        config,
        ledger=ledger,
        environment={"OPENROUTER_API_KEY": "secret"},
        urlopen=urlopen,
    ).send(
        policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3)),
        idempotency_key="attempt-json-object",
        deadline_seconds=1.0,
    )

    assert outcome.status == "response"
    assert captured["metadata_header"] == "enabled"


def test_gemini_uses_vertex_global_without_unsupported_temperature() -> None:
    policy = OpenRouterPanelPolicy(GEMINI_STATEFUL)
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))

    assert request["provider"]["only"] == ["google-vertex/global"]
    assert request["provider"]["data_collection"] == "deny"
    assert request["seed"] == 20260809
    assert "temperature" not in request


def test_gemini_one_call_smoke_is_strict_no_retry_and_reserves_priority_price() -> None:
    config = GEMINI_STATEFUL_ONE_CALL_SMOKE
    policy = OpenRouterPanelPolicy(config)
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))

    assert config not in PANEL
    assert request["model"] == "google/gemini-3.7-flash"
    assert request["provider"] == {
        "only": ["google-vertex/global"],
        "allow_fallbacks": False,
        "data_collection": "deny",
        "require_parameters": True,
    }
    assert request["response_format"]["json_schema"]["strict"] is True
    assert "temperature" not in request
    assert config.max_model_attempts_per_action == 1
    assert config.request_maximum_usd == Decimal("0.099532800")
    manifest = build_panel_policy_manifest(ROOT, config=config, code_revision="revision")
    assert manifest.max_model_attempts_per_action == 1
    assert dict(manifest.inference_parameters)["router_metadata"] == "enabled"


def test_gemini_full_calibration_matches_smoke_inference_with_deadline_margin() -> None:
    config = GEMINI_STATEFUL_FULL_CALIBRATION
    policy = OpenRouterPanelPolicy(config)
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))

    assert config not in PANEL
    assert request["model"] == GEMINI_STATEFUL_ONE_CALL_SMOKE.model
    assert request["provider"] == GEMINI_STATEFUL_ONE_CALL_SMOKE.provider_parameters()
    assert request["response_format"]["json_schema"]["strict"] is True
    assert config.request_maximum_usd == GEMINI_STATEFUL_ONE_CALL_SMOKE.request_maximum_usd
    assert config.max_model_attempts_per_action == 4
    assert config.max_rate_limit_retries_per_action == 3
    assert config.bounded_retry_budget == 3
    manifest = build_panel_policy_manifest(ROOT, config=config, code_revision="revision")
    assert manifest.request_deadline_seconds == 210.0
    assert manifest.max_model_attempts_per_action == 4
    assert dict(manifest.inference_parameters)["max_rate_limit_retries_per_action"] == "3"
    assert dict(manifest.inference_parameters)["max_bounded_retries_per_action"] == "3"


def test_normalized_panel_policy_maps_grid_to_native_pixels() -> None:
    policy = OpenRouterPanelPolicy(GEMINI_STATEFUL)
    candidate = policy.parse(
        canonical_response(
            config=GEMINI_STATEFUL,
            action='{"action_type":1,"x":999,"y":999,"key":0}',
        ),
        policy.reset("task"),
    )
    assert candidate == {"action_type": 1, "x": 1023, "y": 767, "key": 0}


def test_panel_policy_rejects_wrong_response_model_or_provider() -> None:
    policy = OpenRouterPanelPolicy(GEMINI_STATEFUL)
    state = policy.reset("task")
    wrong_model = json.loads(canonical_response(config=GEMINI_STATEFUL))
    wrong_model["model"] = "wrong/model"
    with pytest.raises(ValueError, match="model"):
        policy.parse(canonical_json_bytes(wrong_model), state)

    wrong_provider = json.loads(canonical_response(config=GEMINI_STATEFUL))
    wrong_provider["usage"]["upstream_provider"] = "Google Vertex"
    with pytest.raises(ValueError, match="route"):
        policy.parse(canonical_json_bytes(wrong_provider), state)


def test_panel_policy_retries_only_exact_zero_completion_error_envelope() -> None:
    policy = OpenRouterPanelPolicy(GEMINI_STATEFUL)
    response = json.loads(canonical_response(config=GEMINI_STATEFUL))
    response.update({"content": "", "finish_reason": "error"})
    response["usage"].update({"completion_tokens": 0, "cost": "0"})

    assert policy.retryable_response_code(canonical_json_bytes(response)) == "zero_completion_error"
    for field, value in (
        ("content", "not empty"),
        ("finish_reason", "stop"),
    ):
        changed = json.loads(canonical_json_bytes(response))
        changed[field] = value
        assert policy.retryable_response_code(canonical_json_bytes(changed)) is None
    for field, value in (("completion_tokens", 1), ("cost", "0.0001")):
        changed = json.loads(canonical_json_bytes(response))
        changed["usage"][field] = value
        assert policy.retryable_response_code(canonical_json_bytes(changed)) is None


def test_stateless_policy_does_not_retain_response_candidate_or_outcome() -> None:
    policy = OpenRouterPanelPolicy(QWEN_STATELESS)
    initial = policy.reset("task")
    response = canonical_response(config=QWEN_STATELESS)
    candidate = policy.parse(response, initial)

    assert policy.reduce_state(initial, response) == initial
    assert policy.failure_state(initial, "failure") == initial
    assert policy.post_parse_state(initial, candidate) == initial
    assert (
        policy.post_dispatch_state(
            initial,
            candidate,
            {"reward": 0.0, "terminated": False, "truncated": False},
        )
        == initial
    )
    assert "Visible-action history" not in json.dumps(
        policy.build_request(initial, bytes(1024 * 768 * 3))
    )


def test_stateful_policy_retains_only_visible_outcome_fields() -> None:
    policy = OpenRouterPanelPolicy(QWEN_STATEFUL)
    initial = policy.reset("task")
    response = canonical_response(config=QWEN_STATEFUL)
    candidate = policy.parse(response, initial)
    reduced = policy.reduce_state(initial, response)
    dispatched = policy.post_dispatch_state(
        policy.post_parse_state(reduced, candidate),
        candidate,
        {
            "reward": 0.0,
            "terminated": False,
            "truncated": False,
            "diagnostic": {"expected_answer": "secret"},
        },
    )

    assert b"Visible-action" not in dispatched
    assert b"diagnostic" not in dispatched
    assert b"expected_answer" not in dispatched
    assert b'"action"' in dispatched


def test_shared_spend_ledger_accounts_across_policy_transports() -> None:
    responses = {
        GEMINI_STATEFUL.model: Decimal("0.01"),
        QWEN_STATEFUL.model: Decimal("0.001"),
    }

    def urlopen(request: Any, *, timeout: float) -> FakeHttpResponse:
        del timeout
        body = json.loads(request.data)
        config = GEMINI_STATEFUL if body["model"] == GEMINI_STATEFUL.model else QWEN_STATEFUL
        return FakeHttpResponse(
            {
                "id": f"response-{config.slot}",
                "model": config.model,
                "provider": config.response_provider,
                "choices": [
                    {
                        "message": {"content": '{"action_type":0,"x":0,"y":0,"key":0}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"cost": str(responses[config.model])},
            }
        )

    ledger = SpendLedger(Decimal(10), Decimal("0.25"))
    for config in (GEMINI_STATEFUL, QWEN_STATEFUL):
        policy = OpenRouterPanelPolicy(config)
        transport = OpenRouterPanelTransport(
            config,
            ledger=ledger,
            environment={"OPENROUTER_API_KEY": "secret"},
            urlopen=urlopen,
        )
        outcome = transport.send(
            policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3)),
            idempotency_key=config.slot,
            deadline_seconds=1.0,
        )
        assert outcome.status == "response"

    assert ledger.wire_requests_sent == 2
    assert ledger.spent_usd == Decimal("0.261")


def test_shared_spend_ledger_blocks_before_wire_using_slot_maximum() -> None:
    called = False

    def urlopen(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True

    ledger = SpendLedger(
        Decimal(10),
        Decimal(10) - GEMINI_STATEFUL.request_maximum_usd + Decimal("0.000000001"),
    )
    policy = OpenRouterPanelPolicy(GEMINI_STATEFUL)
    transport = OpenRouterPanelTransport(
        GEMINI_STATEFUL,
        ledger=ledger,
        environment={"OPENROUTER_API_KEY": "secret"},
        urlopen=urlopen,
    )
    outcome = transport.send(
        policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3)),
        idempotency_key="attempt-1",
        deadline_seconds=1.0,
    )

    assert outcome.status == "pre_send_failure"
    assert outcome.failure_code == "aggregate_spend_guard"
    assert not called
    assert ledger.wire_requests_sent == 0


def test_panel_transport_records_safe_bounded_http_error_metadata() -> None:
    error_body = json.dumps(
        {
            "error": {
                "code": 404,
                "type": "no_available_provider",
                "message": "sensitive provider response must not be retained",
                "metadata": {
                    "provider_name": "Google AI Studio",
                    "raw": "sensitive upstream response must not be retained",
                },
            },
            "openrouter_metadata": {
                "requested": "z-ai/glm-5.3-flash",
                "strategy": "direct",
                "attempt": 0,
                "endpoints": {
                    "total": 1,
                    "available": [
                        {
                            "provider": "Novita",
                            "model": "z-ai/glm-5.3-flash",
                            "selected": False,
                            "private_detail": "must not be retained",
                        }
                    ],
                },
                "private_detail": "must not be retained",
            },
        }
    ).encode("utf-8")

    def urlopen(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            404,
            "Not Found",
            None,
            io.BytesIO(error_body),
        )

    ledger = SpendLedger(Decimal(10), Decimal(0))
    policy = OpenRouterPanelPolicy(GEMINI_STATEFUL)
    transport = OpenRouterPanelTransport(
        GEMINI_STATEFUL,
        ledger=ledger,
        environment={"OPENROUTER_API_KEY": "secret"},
        urlopen=urlopen,
    )
    outcome = transport.send(
        policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3)),
        idempotency_key="attempt-http-error",
        deadline_seconds=1.0,
    )

    assert outcome.status == "unknown"
    assert outcome.failure_code == "provider_request_unknown"
    assert ledger.blocked
    assert transport.records == [
        {
            "idempotency_key": "attempt-http-error",
            "status": "unknown",
            "failure_code": "HTTPError",
            "http_status": 404,
            "error_body_prefix_digest": "sha256:" + sha256_bytes(error_body),
            "error_body_bytes_read": len(error_body),
            "error_body_truncated": False,
            "provider_error_code": 404,
            "provider_error_type": "no_available_provider",
            "upstream_provider": "Google AI Studio",
            "openrouter_metadata": {
                "requested": "z-ai/glm-5.3-flash",
                "strategy": "direct",
                "attempt": 0,
                "endpoints": {
                    "total": 1,
                    "available": [
                        {
                            "provider": "Novita",
                            "model": "z-ai/glm-5.3-flash",
                            "selected": False,
                        }
                    ],
                },
            },
        }
    ]
    assert "sensitive" not in json.dumps(transport.records)


def test_panel_transport_honors_retry_after_before_the_next_wire_send() -> None:
    config = GEMINI_STATEFUL_FULL_CALIBRATION
    clock = FakeClock()
    timeouts: list[float] = []
    calls = 0

    def urlopen(_request: Any, *, timeout: float) -> FakeHttpResponse:
        nonlocal calls
        calls += 1
        timeouts.append(timeout)
        if calls == 1:
            raise urllib.error.HTTPError(
                "https://openrouter.ai/api/v1/chat/completions",
                429,
                "Too Many Requests",
                {"Retry-After": "3"},
                io.BytesIO(b'{"error":{"type":"rate_limit"}}'),
            )
        return FakeHttpResponse(
            {
                "id": "response-after-backoff",
                "model": config.model,
                "provider": config.response_provider,
                "choices": [
                    {
                        "message": {
                            "content": '{"action_type":0,"x":0,"y":0,"key":0}'
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"cost": "0.001"},
            }
        )

    ledger = SpendLedger(Decimal(10), Decimal(0))
    policy = OpenRouterPanelPolicy(config)
    transport = OpenRouterPanelTransport(
        config,
        ledger=ledger,
        environment={"OPENROUTER_API_KEY": "secret"},
        urlopen=urlopen,
        monotonic=clock.monotonic,
        wall_time=lambda: 0.0,
        sleep=clock.sleep,
    )
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))

    first = transport.send(
        request, idempotency_key="attempt-1", deadline_seconds=10.0
    )
    second = transport.send(
        request, idempotency_key="attempt-2", deadline_seconds=10.0
    )

    assert first == TransportOutcome(
        "rate_limited",
        failure_code="http_429_rate_limit",
        retry_after_seconds=3.0,
        backoff_source="retry_after",
    )
    assert second.status == "response"
    assert clock.sleeps == [3.0]
    assert timeouts == [9.0, 6.0]
    assert ledger.wire_requests_sent == 2
    assert not ledger.blocked
    assert transport.records[0]["status"] == "rate_limited"
    assert transport.records[0]["cost_usd"] == "0"
    assert transport.records[1]["pre_send_backoff_seconds"] == 3.0


def test_panel_transport_uses_exponential_429_fallback() -> None:
    config = GEMINI_STATEFUL_FULL_CALIBRATION
    clock = FakeClock()

    def urlopen(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            429,
            "Too Many Requests",
            {"Retry-After": "invalid"},
            io.BytesIO(b"{}"),
        )

    policy = OpenRouterPanelPolicy(config)
    transport = OpenRouterPanelTransport(
        config,
        ledger=SpendLedger(Decimal(10), Decimal(0)),
        environment={"OPENROUTER_API_KEY": "secret"},
        urlopen=urlopen,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))

    first = transport.send(request, idempotency_key="one", deadline_seconds=100.0)
    second = transport.send(request, idempotency_key="two", deadline_seconds=100.0)
    third = transport.send(request, idempotency_key="three", deadline_seconds=100.0)

    assert first.retry_after_seconds == 2.0
    assert second.retry_after_seconds == 4.0
    assert third.retry_after_seconds == 8.0
    assert {
        first.backoff_source,
        second.backoff_source,
        third.backoff_source,
    } == {"exponential_fallback"}
    assert clock.sleeps == [2.0, 4.0]


def test_panel_transport_retains_safe_successful_error_envelope_metadata() -> None:
    def urlopen(*_args: object, **_kwargs: object) -> FakeHttpResponse:
        return FakeHttpResponse(
            {
                "id": "response-empty",
                "model": GEMINI_STATEFUL.model,
                "provider": GEMINI_STATEFUL.response_provider,
                "choices": [
                    {
                        "message": {"content": ""},
                        "finish_reason": "error",
                        "error": {
                            "code": 503,
                            "type": "provider_unavailable",
                            "message": "sensitive provider detail",
                        },
                    }
                ],
                "usage": {"cost": "0", "completion_tokens": 0},
            }
        )

    ledger = SpendLedger(Decimal(10), Decimal(0))
    policy = OpenRouterPanelPolicy(GEMINI_STATEFUL)
    outcome = OpenRouterPanelTransport(
        GEMINI_STATEFUL,
        ledger=ledger,
        environment={"OPENROUTER_API_KEY": "secret"},
        urlopen=urlopen,
    ).send(
        policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3)),
        idempotency_key="attempt-error-envelope",
        deadline_seconds=1.0,
    )

    assert outcome.status == "response" and outcome.response is not None
    usage = outcome.response["usage"]
    assert usage["provider_error_signal"] == "finish_reason_error"
    assert usage["provider_error_code"] == 503
    assert usage["provider_error_type"] == "provider_unavailable"
    assert usage["provider_error_envelope_digest"].startswith("sha256:")
    assert "sensitive" not in json.dumps(outcome.response)
    assert (
        policy.retryable_response_code(canonical_json_bytes(outcome.response))
        == "zero_completion_error"
    )


def test_panel_transport_blocks_after_anomalous_response_cost() -> None:
    def urlopen(*_args: object, **_kwargs: object) -> FakeHttpResponse:
        return FakeHttpResponse(
            {
                "id": "response-overpriced",
                "model": QWEN_STATEFUL.model,
                "provider": QWEN_STATEFUL.response_provider,
                "choices": [
                    {
                        "message": {"content": '{"action_type":0,"x":0,"y":0,"key":0}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"cost": str(QWEN_STATEFUL.request_maximum_usd + Decimal("0.000001"))},
            }
        )

    ledger = SpendLedger(Decimal(10), Decimal(0))
    policy = OpenRouterPanelPolicy(QWEN_STATEFUL)
    transport = OpenRouterPanelTransport(
        QWEN_STATEFUL,
        ledger=ledger,
        environment={"OPENROUTER_API_KEY": "secret"},
        urlopen=urlopen,
    )
    outcome = transport.send(
        policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3)),
        idempotency_key="attempt-overpriced",
        deadline_seconds=1.0,
    )

    assert outcome.status == "response"
    assert outcome.response is not None
    assert outcome.response["usage"]["price_guard"] == "exceeded"
    assert ledger.blocked
    with pytest.raises(ValueError, match="price guard"):
        policy.parse(canonical_json_bytes(outcome.response), policy.reset("task"))


def test_panel_manifest_ids_bind_model_route_adapter_and_memory(tmp_path: Path) -> None:
    (tmp_path / "pixelgym/grounding/v5").mkdir(parents=True)
    (tmp_path / "requirements").mkdir()
    root = Path(__file__).parents[2]
    for name in ("panel_policy.py", "openrouter_policy.py", "runner.py"):
        (tmp_path / f"pixelgym/grounding/v5/{name}").write_bytes(
            (root / f"pixelgym/grounding/v5/{name}").read_bytes()
        )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (tmp_path / "requirements/platform-py312.lock").write_text("locked\n", encoding="utf-8")

    manifests = [
        build_panel_policy_manifest(tmp_path, config=config, code_revision="revision-1")
        for config in PANEL
    ]

    assert len({manifest.policy_id for manifest in manifests}) == 4
    assert manifests[1].model == manifests[3].model
    assert manifests[1].coordinate_adapter == manifests[3].coordinate_adapter
    assert manifests[1].memory_policy_version != manifests[3].memory_policy_version
    assert manifests[2].inference_parameters[-1] == ("quantizations", "fp8")
    assert all(manifest.max_model_attempts_per_action == 2 for manifest in manifests)
    assert all(manifest.transport_retry_rule == TRANSPORT_RETRY_RULE for manifest in manifests)


def gemini_transport(
    urlopen: Any, *, ledger: SpendLedger, sleeps: list[float] | None = None
) -> OpenRouterPanelTransport:
    return OpenRouterPanelTransport(
        GEMINI_STATEFUL_FULL_CALIBRATION,
        ledger=ledger,
        environment={"OPENROUTER_API_KEY": "secret"},
        urlopen=urlopen,
        sleep=(sleeps.append if sleeps is not None else lambda _seconds: None),
    )


def one_send(transport: OpenRouterPanelTransport, key: str) -> TransportOutcome:
    policy = OpenRouterPanelPolicy(GEMINI_STATEFUL_FULL_CALIBRATION)
    return transport.send(
        policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3)),
        idempotency_key=key,
        deadline_seconds=60.0,
    )


@pytest.mark.parametrize(
    ("raise_error", "failure_code"),
    [
        (lambda: (_ for _ in ()).throw(urllib.error.URLError("connection reset")), "URLError"),
        (lambda: (_ for _ in ()).throw(TimeoutError("read timed out")), "TimeoutError"),
        (
            lambda: (_ for _ in ()).throw(
                urllib.error.HTTPError(
                    "https://openrouter.ai/api/v1/chat/completions",
                    503,
                    "Service Unavailable",
                    None,
                    io.BytesIO(b"{}"),
                )
            ),
            "retryable_http_503",
        ),
    ],
)
def test_dropped_request_is_retryable_and_does_not_block_the_ledger(
    raise_error: Any, failure_code: str
) -> None:
    def urlopen(*_args: object, **_kwargs: object) -> None:
        raise_error()

    ledger = SpendLedger(Decimal(10), Decimal(0))
    outcome = one_send(gemini_transport(urlopen, ledger=ledger), "attempt-dropped")

    assert outcome.status == "transport_fault"
    assert outcome.failure_code == failure_code
    assert outcome.retry_after_seconds == 2.0
    # The run must survive the fault, so the ledger stays open ...
    assert not ledger.blocked
    # ... while the possibly-billed send is charged at its worst case.
    assert ledger.unknown_charge_outcomes == 1
    assert ledger.unknown_reservation_usd == (
        GEMINI_STATEFUL_FULL_CALIBRATION.request_maximum_usd
    )
    assert ledger.budget_accounted_spend_usd == ledger.unknown_reservation_usd


def test_unreadable_envelope_is_a_retryable_transport_fault() -> None:
    def urlopen(*_args: object, **_kwargs: object) -> FakeHttpResponse:
        return FakeHttpResponse(["not", "an", "object"])  # type: ignore[arg-type]

    ledger = SpendLedger(Decimal(10), Decimal(0))
    outcome = one_send(gemini_transport(urlopen, ledger=ledger), "attempt-envelope")

    assert outcome.status == "transport_fault"
    assert outcome.failure_code == "provider_envelope_invalid"
    assert not ledger.blocked
    assert ledger.unknown_charge_outcomes == 1


def test_non_retryable_http_status_still_blocks_the_ledger() -> None:
    def urlopen(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            401,
            "Unauthorized",
            None,
            io.BytesIO(b"{}"),
        )

    ledger = SpendLedger(Decimal(10), Decimal(0))
    outcome = one_send(gemini_transport(urlopen, ledger=ledger), "attempt-auth")

    # A credential or route defect cannot be retried away; it must stop the run.
    assert outcome.status == "unknown"
    assert ledger.blocked
    assert ledger.unknown_charge_outcomes == 0


def test_a_dropped_request_does_not_poison_the_requests_that_follow() -> None:
    calls: list[int] = []

    def urlopen(*_args: object, **_kwargs: object) -> FakeHttpResponse:
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.URLError("connection reset")
        return FakeHttpResponse(
            {
                "id": "resp-2",
                "model": GEMINI_STATEFUL_FULL_CALIBRATION.model,
                "provider": "Google",
                "choices": [
                    {
                        "message": {"content": '{"action_type":0,"x":0,"y":0,"key":0}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "cost": "0.002"},
            }
        )

    ledger = SpendLedger(Decimal(10), Decimal(0))
    sleeps: list[float] = []
    transport = gemini_transport(urlopen, ledger=ledger, sleeps=sleeps)

    first = one_send(transport, "attempt-1")
    second = one_send(transport, "attempt-2")

    assert first.status == "transport_fault"
    assert second.status == "response"
    assert second.response is not None
    assert second.response["usage"]["price_guard"] == "ok"
    # The retry waits out the fault's exponential backoff before reaching the wire.
    assert sleeps and sleeps[0] == pytest.approx(2.0, abs=0.5)
    assert ledger.spent_usd == Decimal("0.002")
    assert ledger.unknown_charge_outcomes == 1


def test_transport_fault_backoff_grows_and_resets_after_a_response() -> None:
    faulty = [True]

    def urlopen(*_args: object, **_kwargs: object) -> FakeHttpResponse:
        if faulty[0]:
            raise urllib.error.URLError("connection reset")
        return FakeHttpResponse(
            {
                "id": "resp",
                "model": GEMINI_STATEFUL_FULL_CALIBRATION.model,
                "provider": "Google",
                "choices": [
                    {
                        "message": {"content": '{"action_type":0,"x":0,"y":0,"key":0}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "cost": "0.002"},
            }
        )

    transport = gemini_transport(urlopen, ledger=SpendLedger(Decimal(10), Decimal(0)))
    delays = [one_send(transport, f"attempt-{index}").retry_after_seconds for index in range(3)]
    assert delays == [2.0, 4.0, 8.0]

    faulty[0] = False
    assert one_send(transport, "attempt-ok").status == "response"
    # A clean response clears the streak, so the next fault starts at the base delay.
    faulty[0] = True
    assert one_send(transport, "attempt-after").retry_after_seconds == 2.0


def test_unknown_charge_reservations_fail_closed_at_the_aggregate_cap() -> None:
    ledger = SpendLedger(Decimal(10), Decimal(0))

    ledger.reserve_unknown_charge(Decimal("9.99"))
    assert not ledger.blocked
    # A reservation consumes budget, so it must gate the next send.
    assert not ledger.reserve_wire(Decimal("0.02"))
    assert ledger.reserve_wire(Decimal("0.005"))

    ledger.reserve_unknown_charge(Decimal("1.00"))
    assert ledger.blocked
    assert not ledger.reserve_wire(Decimal("0.001"))
