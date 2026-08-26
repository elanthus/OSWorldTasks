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
    LLAMA_STATEFUL,
    PANEL,
    QWEN_STATEFUL,
    QWEN_STATELESS,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
    action_schema,
    build_panel_policy_manifest,
)
from pixelgym.serialization import canonical_json_bytes


class FakeHttpResponse:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.value).encode("utf-8")


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


def test_llama_uses_native_coordinates_and_fp8_route_filter() -> None:
    policy = OpenRouterPanelPolicy(LLAMA_STATEFUL)
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))
    schema = request["response_format"]["json_schema"]["schema"]

    assert request["provider"]["only"] == ["deepinfra"]
    assert request["provider"]["quantizations"] == ["fp8"]
    assert schema["properties"]["x"]["maximum"] == 1023
    assert schema["properties"]["y"]["maximum"] == 767
    assert OpenRouterPanelPolicy(LLAMA_STATEFUL).parse(
        canonical_response(
            config=LLAMA_STATEFUL,
            action='{"action_type":1,"x":1023,"y":767,"key":0}',
        ),
        policy.reset("task"),
    ) == {"action_type": 1, "x": 1023, "y": 767, "key": 0}


def test_gemini_uses_vertex_global_without_unsupported_temperature() -> None:
    policy = OpenRouterPanelPolicy(GEMINI_STATEFUL)
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))

    assert request["provider"]["only"] == ["google-vertex/global"]
    assert request["provider"]["data_collection"] == "deny"
    assert request["seed"] == 20260809
    assert "temperature" not in request


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
                        "message": {
                            "content": '{"action_type":0,"x":0,"y":0,"key":0}'
                        },
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
            }
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
        }
    ]
    assert "sensitive" not in json.dumps(transport.records)


def test_panel_transport_blocks_after_anomalous_response_cost() -> None:
    def urlopen(*_args: object, **_kwargs: object) -> FakeHttpResponse:
        return FakeHttpResponse(
            {
                "id": "response-overpriced",
                "model": QWEN_STATEFUL.model,
                "provider": QWEN_STATEFUL.response_provider,
                "choices": [
                    {
                        "message": {
                            "content": '{"action_type":0,"x":0,"y":0,"key":0}'
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "cost": str(QWEN_STATEFUL.request_maximum_usd + Decimal("0.000001"))
                },
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
