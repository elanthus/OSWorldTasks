from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Self

import pytest

from pixelgym.grounding.v5.openrouter_policy import (
    ACTION_SCHEMA,
    MODEL,
    REQUEST_MAXIMUM_USD,
    UPSTREAM_PROVIDER,
    OpenRouterV5Transport,
    QwenV5StatefulPolicy,
    build_policy_manifest,
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


def test_qwen_policy_request_is_credential_free_and_binds_normalized_schema() -> None:
    policy = QwenV5StatefulPolicy()
    state = policy.reset("Open the matching request")
    request = policy.build_request(state, bytes(1024 * 768 * 3))

    assert request["model"] == MODEL
    assert request["provider"] == {
        "only": [UPSTREAM_PROVIDER],
        "allow_fallbacks": False,
        "data_collection": "deny",
        "require_parameters": True,
    }
    assert request["response_format"]["json_schema"]["schema"] == ACTION_SCHEMA
    image_url = request["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")
    assert "api_key" not in json.dumps(request).lower()


def test_qwen_policy_maps_normalized_click_and_excludes_privileged_diagnostic() -> None:
    policy = QwenV5StatefulPolicy()
    state = policy.reset("Open the matching request")
    response = canonical_json_bytes(
        {
            "response_id": "response-1",
            "model": MODEL,
            "content": '{"action_type":1,"x":499,"y":607,"key":0}',
            "finish_reason": "stop",
            "usage": {
                "cost": 0.001,
                "upstream_provider": "Alibaba",
                "price_guard": "ok",
            },
        }
    )

    candidate = policy.parse(response, state)
    assert candidate == {"action_type": 1, "x": 511, "y": 466, "key": 0}
    reduced = policy.post_dispatch_state(
        policy.post_parse_state(state, candidate),
        candidate,
        {
            "reward": 0.0,
            "terminated": False,
            "truncated": False,
            "diagnostic": {"secret": "privileged"},
        },
    )
    assert b"privileged" not in reduced
    assert b"diagnostic" not in reduced


def test_qwen_policy_rejects_wrong_model_or_provider() -> None:
    policy = QwenV5StatefulPolicy()
    state = policy.reset("task")
    base = {
        "response_id": "response-1",
        "model": MODEL,
        "content": '{"action_type":0,"x":0,"y":0,"key":0}',
        "finish_reason": "stop",
        "usage": {
            "cost": 0.001,
            "upstream_provider": "Alibaba",
            "price_guard": "ok",
        },
    }
    with pytest.raises(ValueError, match="model"):
        policy.parse(canonical_json_bytes({**base, "model": "wrong"}), state)
    with pytest.raises(ValueError, match="route"):
        policy.parse(
            canonical_json_bytes(
                {
                    **base,
                    "usage": {
                        "cost": 0.001,
                        "upstream_provider": "Parasail",
                        "price_guard": "ok",
                    },
                }
            ),
            state,
        )


def test_openrouter_transport_injects_secret_only_at_wire_and_accounts_cost() -> None:
    seen: dict[str, Any] = {}

    def urlopen(request: Any, *, timeout: float) -> FakeHttpResponse:
        seen["request"] = request
        seen["timeout"] = timeout
        return FakeHttpResponse(
            {
                "id": "generation-1",
                "model": MODEL,
                "provider": "Alibaba",
                "choices": [
                    {
                        "message": {
                            "content": '{"action_type":0,"x":0,"y":0,"key":0}'
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.0001},
            }
        )

    policy = QwenV5StatefulPolicy()
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))
    transport = OpenRouterV5Transport(
        environment={"OPENROUTER_API_KEY": "secret"},
        maximum_spend_usd=Decimal(5),
        prior_spend_usd=Decimal("0.25"),
        urlopen=urlopen,
    )
    outcome = transport.send(request, idempotency_key="attempt-1", deadline_seconds=1.5)

    assert outcome.status == "response"
    assert outcome.response is not None
    assert outcome.response["usage"]["upstream_provider"] == "Alibaba"
    assert outcome.response["usage"]["price_guard"] == "ok"
    assert transport.spent_usd == Decimal("0.2501")
    assert transport.wire_requests_sent == 1
    assert seen["request"].get_header("Authorization") == "Bearer secret"
    assert b"secret" not in seen["request"].data


def test_openrouter_transport_spend_guard_blocks_before_wire() -> None:
    called = False

    def urlopen(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True

    policy = QwenV5StatefulPolicy()
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))
    transport = OpenRouterV5Transport(
        environment={"OPENROUTER_API_KEY": "secret"},
        maximum_spend_usd=Decimal(5),
        prior_spend_usd=Decimal(5) - REQUEST_MAXIMUM_USD + Decimal("0.000000001"),
        urlopen=urlopen,
    )

    outcome = transport.send(request, idempotency_key="attempt-1", deadline_seconds=1.0)
    assert outcome.status == "pre_send_failure"
    assert outcome.failure_code == "aggregate_spend_guard"
    assert not called
    assert transport.wire_requests_sent == 0


def test_openrouter_transport_preserves_but_blocks_anomalous_response_cost() -> None:
    def urlopen(*_args: object, **_kwargs: object) -> FakeHttpResponse:
        return FakeHttpResponse(
            {
                "id": "generation-overpriced",
                "model": MODEL,
                "provider": "Alibaba",
                "choices": [
                    {
                        "message": {
                            "content": '{"action_type":0,"x":0,"y":0,"key":0}'
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"cost": str(REQUEST_MAXIMUM_USD + Decimal("0.000001"))},
            }
        )

    policy = QwenV5StatefulPolicy()
    request = policy.build_request(policy.reset("task"), bytes(1024 * 768 * 3))
    transport = OpenRouterV5Transport(
        environment={"OPENROUTER_API_KEY": "secret"},
        maximum_spend_usd=Decimal(5),
        prior_spend_usd=Decimal(0),
        urlopen=urlopen,
    )

    outcome = transport.send(request, idempotency_key="attempt-1", deadline_seconds=1.0)
    assert outcome.status == "response"
    assert outcome.response is not None
    assert outcome.response["usage"]["price_guard"] == "exceeded"
    assert transport.blocked
    with pytest.raises(ValueError, match="price guard"):
        policy.parse(canonical_json_bytes(outcome.response), policy.reset("task"))


def test_real_policy_manifest_binds_alias_adapter_and_no_retry(tmp_path: Path) -> None:
    (tmp_path / "pixelgym/grounding/v5").mkdir(parents=True)
    (tmp_path / "requirements").mkdir()
    source = Path(__file__).parents[2] / "pixelgym/grounding/v5/openrouter_policy.py"
    runner = Path(__file__).parents[2] / "pixelgym/grounding/v5/runner.py"
    (tmp_path / "pixelgym/grounding/v5/openrouter_policy.py").write_bytes(source.read_bytes())
    (tmp_path / "pixelgym/grounding/v5/runner.py").write_bytes(runner.read_bytes())
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (tmp_path / "requirements/platform-py312.lock").write_text("locked\n", encoding="utf-8")

    manifest = build_policy_manifest(tmp_path, code_revision="revision-1")

    assert manifest.model == MODEL
    assert manifest.exact_snapshot is False
    assert manifest.coordinate_adapter == "normalized-1000x1000"
    assert manifest.max_cancellation_requests_per_attempt == 0
    assert manifest.max_reconciliation_requests_per_attempt == 0
    assert manifest.transport_retry_rule == "no-retry-after-send-v1"
