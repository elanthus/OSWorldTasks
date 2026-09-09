"""Approved real-provider path for the frozen grounding flow (issue #164).

Every test runs without network or credentials: the OpenRouter transport is driven through
an injected ``urlopen`` and the credential is a dummy value in a scoped environment.
"""

from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, ClassVar, Self

import pytest

from flows import grounding_evaluation_flow as flow_module
from flows.grounding_evaluation_flow import GroundingEvaluationFlow
from pixelgym.platform.approved_providers import (
    REGISTRY_SCHEMA_VERSION,
    ApprovedGroundingProviderAdapter,
    ApprovedProviderError,
    ApprovedProviderPolicy,
    build_platform_provider,
    load_approved_providers,
    price_entry_for,
    resolve_approved_provider,
)
from tests.unit.platform.test_grounding_evaluation_flow import FlowHarness, _configure

RECORD: dict[str, Any] = {
    "reference": "openrouter-qwen3-vl-8b-raw-v2",
    "kind": "grounding",
    "transport": "openrouter",
    "provider": "openrouter",
    "model": "qwen/qwen3-vl-8b-instruct",
    "model_alias_disclosure": None,
    "prompt_version": 2,
    "condition": "raw",
    "call_cap": 100,
    "max_concurrency": 2,
    "price_catalog_version": "openrouter-test-v1",
    "credential_env": "PIXELGYM_TEST_OPENROUTER_KEY",
    "coordinate_adapter": "none",
    "request_parameters": {"temperature": 0, "seed": 7, "max_tokens": 64},
    "provider_routing": {"order": ["alibaba"], "allow_fallbacks": False},
}
PRICE_CATALOG: dict[str, Any] = {
    "schema_version": "pixelgym-price-catalog-v1",
    "catalog_version": "openrouter-test-v1",
    "entries": [
        {
            "provider": "openrouter",
            "model": "qwen/qwen3-vl-8b-instruct",
            "input_usd_per_million_tokens": 0.2,
            "output_usd_per_million_tokens": 0.8,
        }
    ],
}
ENVIRONMENT = {"PIXELGYM_TEST_OPENROUTER_KEY": "dummy-not-a-real-key"}


def _policy(**overrides: Any) -> ApprovedProviderPolicy:
    return ApprovedProviderPolicy.from_record({**RECORD, **overrides})


def _write_registry(root: Path, entries: list[dict[str, Any]]) -> Path:
    path = root / "config/approved-providers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": REGISTRY_SCHEMA_VERSION, "entries": entries}))
    return path


class _FakeHttpResponse(io.BytesIO):
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _fake_urlopen(bodies: list[object]) -> tuple[Any, list[dict[str, Any]]]:
    """Return an urlopen stand-in that replays bodies and records each request payload."""
    seen: list[dict[str, Any]] = []

    def urlopen(request: Any, timeout: float) -> _FakeHttpResponse:
        del timeout
        seen.append(json.loads(request.data))
        body = bodies.pop(0)
        if isinstance(body, BaseException):
            raise body
        return _FakeHttpResponse(json.dumps(body).encode())

    return urlopen, seen


def _openrouter_body(
    content: str, *, prompt_tokens: int = 1000, completion_tokens: int = 10
) -> dict:
    return {
        "id": "gen-1",
        "model": "qwen/qwen3-vl-8b-instruct",
        "provider": "Alibaba",
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost": 0.0,
        },
    }


# --- registry and record validation -------------------------------------------------------


def test_registry_loads_and_empty_registry_is_valid(tmp_path: Path) -> None:
    _write_registry(tmp_path, [RECORD])
    registry = load_approved_providers(tmp_path)
    assert list(registry) == [RECORD["reference"]]
    assert registry[RECORD["reference"]].request_parameters == RECORD["request_parameters"]

    _write_registry(tmp_path, [])
    assert load_approved_providers(tmp_path) == {}


def test_checked_in_registry_is_valid_and_ships_no_approved_provider(repository_root: Path) -> None:
    assert load_approved_providers(repository_root) == {}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"kind": "stateful-v5"}, "unsupported policy kind"),
        ({"transport": "python:pixelgym.evil"}, "unsupported transport"),
        ({"provider": "scripted-demo"}, "not an approved provider"),
        ({"reference": "../etc/passwd"}, "reference must be"),
        ({"reference": "Ref With Spaces"}, "reference must be"),
        ({"model": "qwen; rm -rf /"}, "plain identifiers"),
        ({"credential_env": "$(cat key)"}, "credential_env must name"),
        ({"price_catalog_version": "../price-catalog.demo-v1"}, "price_catalog_version"),
        ({"price_catalog_version": "pixelgym-demo-prices-v1"}, "demo catalog"),
        ({"condition": "marks"}, "unsupported condition"),
        ({"coordinate_adapter": "qwen-normalized-1000"}, "rewrite raw responses"),
        ({"call_cap": 0}, "call_cap must be"),
        ({"call_cap": True}, "call_cap must be"),
        ({"max_concurrency": 101}, "cannot exceed call_cap"),
        ({"prompt_version": "2"}, "prompt_version must be an integer"),
        ({"request_parameters": {"model": "other"}}, "reserved field"),
        ({"request_parameters": {"messages": []}}, "reserved field"),
        ({"request_parameters": {"tools": [{"type": "function"}]}}, "list entries"),
        ({"request_parameters": {"nested": {"a": 1}}}, "scalar or list"),
        ({"request_parameters": {"Bad-Key": 1}}, "non-identifier key"),
        ({"provider_routing": {"order": ["alibaba", "x y"]}}, "list entries"),
    ],
)
def test_record_rejects_unsafe_or_unsupported_values(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ApprovedProviderError, match=message):
        _policy(**overrides)


def test_record_rejects_unknown_and_missing_fields() -> None:
    with pytest.raises(ApprovedProviderError, match="unknown fields: \\['module'\\]"):
        ApprovedProviderPolicy.from_record({**RECORD, "module": "pixelgym.platform"})
    with pytest.raises(ApprovedProviderError, match="missing fields: \\['call_cap'\\]"):
        ApprovedProviderPolicy.from_record({k: v for k, v in RECORD.items() if k != "call_cap"})
    with pytest.raises(ApprovedProviderError, match="must be a JSON object"):
        ApprovedProviderPolicy.from_record(["not", "a", "record"])


def test_registry_rejects_duplicates_and_malformed_documents(tmp_path: Path) -> None:
    _write_registry(tmp_path, [RECORD, RECORD])
    with pytest.raises(ApprovedProviderError, match="duplicate"):
        load_approved_providers(tmp_path)
    path = _write_registry(tmp_path, [])
    path.write_text('{"schema_version": "pixelgym-approved-providers-v1", "entries": [], "x": 1}')
    with pytest.raises(ApprovedProviderError, match="exactly schema_version and entries"):
        load_approved_providers(tmp_path)
    path.write_text("[]")
    with pytest.raises(ApprovedProviderError, match="JSON object"):
        load_approved_providers(tmp_path)
    path.unlink()
    with pytest.raises(ApprovedProviderError, match="not readable"):
        load_approved_providers(tmp_path)


# --- approval digest ----------------------------------------------------------------------


def test_approval_digest_is_content_bound() -> None:
    base = _policy()
    assert base.approval_sha256.startswith("sha256:")
    assert _policy().approval_sha256 == base.approval_sha256
    for overrides in (
        {"model": "qwen/qwen3-vl-32b-instruct"},
        {"call_cap": 101},
        {"prompt_version": 1},
        {"price_catalog_version": "openrouter-test-v2"},
        {"credential_env": "OTHER_KEY"},
        {"request_parameters": {**RECORD["request_parameters"], "temperature": 0.5}},
        {"provider_routing": {"order": ["deepinfra"]}},
    ):
        assert _policy(**overrides).approval_sha256 != base.approval_sha256, overrides


def test_manifest_parameters_carry_identity_but_never_the_credential() -> None:
    parameters = _policy().manifest_parameters()
    assert parameters["approved_provider_sha256"] == _policy().approval_sha256
    assert parameters["credential_env"] == "PIXELGYM_TEST_OPENROUTER_KEY"
    assert parameters["hidden_retries"] == 0
    assert parameters["deterministic"] is False
    assert parameters["temperature"] == 0
    assert parameters["seed"] == 7
    assert parameters["max_output_tokens"] == 64
    assert "dummy-not-a-real-key" not in json.dumps(parameters)


# --- launch resolution ----------------------------------------------------------------------


def _resolve(**overrides: Any) -> ApprovedProviderPolicy:
    policy = _policy()
    arguments: dict[str, Any] = {
        "approved_sha256": policy.approval_sha256,
        "model": policy.model,
        "prompt_version": policy.prompt_version,
        "maximum_calls": policy.call_cap,
        "provider_concurrency": 1,
    }
    arguments.update(overrides)
    return resolve_approved_provider({policy.reference: policy}, policy.reference, **arguments)


def test_resolution_requires_exact_restatement_of_the_approved_record() -> None:
    assert _resolve() == _policy()
    with pytest.raises(ApprovedProviderError, match="not an approved provider reference"):
        resolve_approved_provider(
            {},
            "missing",
            approved_sha256="sha256:0",
            model="m",
            prompt_version=1,
            maximum_calls=1,
            provider_concurrency=1,
        )
    with pytest.raises(ApprovedProviderError, match="approval digest does not match"):
        _resolve(approved_sha256="sha256:" + "0" * 64)
    with pytest.raises(ApprovedProviderError, match="mismatched: \\['model'\\]"):
        _resolve(model="qwen/qwen3-vl-32b-instruct")
    with pytest.raises(ApprovedProviderError, match="mismatched: \\['prompt_version'\\]"):
        _resolve(prompt_version=1)
    with pytest.raises(ApprovedProviderError, match="mismatched: \\['maximum_calls'\\]"):
        _resolve(maximum_calls=99)
    with pytest.raises(ApprovedProviderError, match="concurrency exceeds"):
        _resolve(provider_concurrency=3)


def test_price_entry_requires_matching_catalog_and_identity() -> None:
    assert price_entry_for(PRICE_CATALOG, _policy()) == {
        "input_usd_per_million_tokens": 0.2,
        "output_usd_per_million_tokens": 0.8,
    }
    with pytest.raises(ApprovedProviderError, match="catalog version"):
        price_entry_for({**PRICE_CATALOG, "catalog_version": "other"}, _policy())
    with pytest.raises(ApprovedProviderError, match="no entry"):
        price_entry_for({**PRICE_CATALOG, "entries": []}, _policy())


# --- adapter behaviour ----------------------------------------------------------------------


def _adapter(
    bodies: list[object], **overrides: Any
) -> tuple[ApprovedGroundingProviderAdapter, list]:
    urlopen, seen = _fake_urlopen(bodies)
    adapter = build_platform_provider(
        _policy(**overrides), price_catalog=PRICE_CATALOG, environment=ENVIRONMENT, urlopen=urlopen
    )
    return adapter, seen


def _invoke(adapter: ApprovedGroundingProviderAdapter, image: Path, example: str = "ex-1"):
    return adapter.invoke(
        request_id="sha256:req",
        example_id=example,
        condition="raw",
        image_path=image,
        prompt="Where is the target?",
        schema={"type": "object"},
    )


@pytest.fixture
def image(tmp_path: Path) -> Path:
    from PIL import Image

    path = tmp_path / "shot.png"
    Image.new("RGB", (4, 4)).save(path)
    return path


def test_adapter_forwards_raw_text_and_prices_usage_from_the_frozen_catalog(image: Path) -> None:
    adapter, seen = _adapter([_openrouter_body(' {"x": 12, "y": 34} ')])
    assert adapter.synthetic is False
    assert (adapter.name, adapter.model) == ("openrouter", "qwen/qwen3-vl-8b-instruct")

    response = _invoke(adapter, image)

    assert response.raw_response == ' {"x": 12, "y": 34} '
    assert response.request_failure is None
    assert response.usage == {"prompt_tokens": 1000, "completion_tokens": 10, "cost": 0.0}
    assert response.cost_usd == pytest.approx((1000 * 0.2 + 10 * 0.8) / 1_000_000)
    assert response.latency_ms is not None and response.latency_ms >= 0
    request = seen[0]
    assert request["model"] == "qwen/qwen3-vl-8b-instruct"
    assert request["temperature"] == 0 and request["seed"] == 7 and request["max_tokens"] == 64
    assert request["provider"] == {
        "require_parameters": True,
        "order": ["alibaba"],
        "allow_fallbacks": False,
    }
    assert request["response_format"]["type"] == "json_schema"


def test_adapter_records_request_failure_once_without_retrying(image: Path) -> None:
    adapter, seen = _adapter([OSError("connection reset")])

    response = _invoke(adapter, image)

    assert response.raw_response is None
    assert response.request_failure == "OSError: provider request failed"
    assert response.usage is None and response.cost_usd is None
    assert len(seen) == 1
    assert adapter.attempts == 1


def test_adapter_reports_unpriced_response_when_usage_is_missing(image: Path) -> None:
    body = _openrouter_body('{"x": 1, "y": 1}')
    del body["usage"]
    adapter, _ = _adapter([body])
    response = _invoke(adapter, image)
    assert response.raw_response == '{"x": 1, "y": 1}'
    assert response.usage is None
    assert response.cost_usd is None


def test_adapter_enforces_the_call_cap_before_sending_even_under_concurrency(image: Path) -> None:
    bodies: list[object] = [_openrouter_body('{"x": 1, "y": 1}') for _ in range(10)]
    adapter, seen = _adapter(bodies, call_cap=3, max_concurrency=3)

    def attempt(index: int) -> str:
        try:
            _invoke(adapter, image, example=f"ex-{index}")
            return "sent"
        except ApprovedProviderError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=5) as executor:
        outcomes = list(executor.map(attempt, range(5)))

    assert outcomes.count("sent") == 3
    assert sum("call cap reached" in item for item in outcomes) == 2
    assert len(seen) == 3
    assert adapter.attempts == 3


def test_adapter_rejects_condition_outside_the_approved_record(image: Path) -> None:
    adapter, seen = _adapter([_openrouter_body("{}")])
    with pytest.raises(ApprovedProviderError, match="condition differs"):
        adapter.invoke(
            request_id="r",
            example_id="e",
            condition="marks",
            image_path=image,
            prompt="p",
            schema={},
        )
    assert seen == []


def test_build_fails_closed_without_the_named_credential() -> None:
    urlopen, seen = _fake_urlopen([])
    with pytest.raises(ApprovedProviderError, match="PIXELGYM_TEST_OPENROUTER_KEY is not set"):
        build_platform_provider(
            _policy(), price_catalog=PRICE_CATALOG, environment={}, urlopen=urlopen
        )
    with pytest.raises(ApprovedProviderError, match="is not set"):
        build_platform_provider(
            _policy(),
            price_catalog=PRICE_CATALOG,
            environment={"PIXELGYM_TEST_OPENROUTER_KEY": ""},
            urlopen=urlopen,
        )
    assert seen == []


def test_adapter_rejects_an_inner_provider_with_a_different_model() -> None:
    class Other:
        name = "openrouter"
        model = "different/model"
        parameters: ClassVar[dict[str, Any]] = {}

        def invoke(self, **kwargs: Any) -> Any:  # pragma: no cover - never reached
            raise AssertionError

    with pytest.raises(ApprovedProviderError, match="does not match the approved record"):
        ApprovedGroundingProviderAdapter(
            _policy(),
            inner=Other(),
            price_entry={"input_usd_per_million_tokens": 0, "output_usd_per_million_tokens": 0},
        )


# --- flow integration (step bodies, no Metaflow runtime) ------------------------------------


def _approved_flow(policy: ApprovedProviderPolicy, **updates: Any) -> FlowHarness:
    values: dict[str, Any] = {
        "submission_id": "submission-approved",
        "prompt_version": policy.prompt_version,
        "model": policy.model,
        "maximum_calls": policy.call_cap,
        "shard_size": 25,
        "provider_concurrency": 1,
        "approved_provider": policy.reference,
        "approved_provider_sha256": policy.approval_sha256,
    }
    values.update(updates)
    return FlowHarness(**values)


@pytest.fixture
def approved_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repository_root: Path) -> Path:
    """Registry plus priced catalog in a scratch root; the flow reads both from _root()."""
    _write_registry(tmp_path, [RECORD])
    catalog = tmp_path / "config/price-catalog.openrouter-test-v1.json"
    catalog.write_text(json.dumps(PRICE_CATALOG))
    monkeypatch.setattr(
        flow_module, "_approved_registry", lambda: load_approved_providers(tmp_path)
    )
    real_load = flow_module.load_price_catalog
    monkeypatch.setattr(
        flow_module,
        "load_price_catalog",
        lambda root, path=None: real_load(repository_root, catalog if path is not None else None),
    )
    return tmp_path


def test_start_admits_only_an_exactly_restated_record_with_its_credential(
    approved_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _policy()
    monkeypatch.delenv("PIXELGYM_TEST_OPENROUTER_KEY", raising=False)
    with pytest.raises(ApprovedProviderError, match="is not set"):
        GroundingEvaluationFlow.start(_approved_flow(policy))

    monkeypatch.setenv("PIXELGYM_TEST_OPENROUTER_KEY", "dummy-not-a-real-key")
    flow = _approved_flow(policy)
    GroundingEvaluationFlow.start(flow)
    assert flow.transition == ("validate_and_freeze_inputs", {})

    with pytest.raises(ApprovedProviderError, match="approval digest does not match"):
        GroundingEvaluationFlow.start(
            _approved_flow(policy, approved_provider_sha256="sha256:" + "f" * 64)
        )
    with pytest.raises(ApprovedProviderError, match="mismatched: \\['maximum_calls'\\]"):
        GroundingEvaluationFlow.start(_approved_flow(policy, maximum_calls=100_000))
    with pytest.raises(ApprovedProviderError, match="mismatched: \\['model'\\]"):
        GroundingEvaluationFlow.start(_approved_flow(policy, model="day3-replay-revised-v2"))
    with pytest.raises(ApprovedProviderError, match="not an approved provider reference"):
        GroundingEvaluationFlow.start(_approved_flow(policy, approved_provider="unlisted"))


def test_scripted_launch_cannot_carry_an_approval_digest() -> None:
    flow = FlowHarness(
        submission_id="s",
        prompt_version=2,
        model="day3-replay-revised-v2",
        maximum_calls=100,
        shard_size=25,
        provider_concurrency=1,
        approved_provider="",
        approved_provider_sha256="sha256:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="requires an approved provider reference"):
        GroundingEvaluationFlow.start(flow)


def test_scripted_default_still_rejects_unlisted_models_and_other_caps() -> None:
    flow = FlowHarness(
        submission_id="s",
        prompt_version=2,
        model="qwen/qwen3-vl-8b-instruct",
        maximum_calls=100,
        shard_size=25,
        provider_concurrency=1,
    )
    with pytest.raises(ValueError, match="outside the scripted allowlist"):
        GroundingEvaluationFlow.start(flow)


def test_provider_selection_builds_a_capped_non_synthetic_adapter(
    approved_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIXELGYM_TEST_OPENROUTER_KEY", "dummy-not-a-real-key")
    monkeypatch.delenv("PIXELGYM_TEST_PROVIDER_LEDGER", raising=False)
    provider = flow_module._provider(_approved_flow(_policy()))
    assert isinstance(provider, ApprovedGroundingProviderAdapter)
    assert provider.synthetic is False
    assert provider.policy == _policy()

    monkeypatch.setenv("PIXELGYM_TEST_PROVIDER_LEDGER", str(approved_root / "ledger"))
    with pytest.raises(RuntimeError, match="ledgered test provider cannot wrap"):
        flow_module._provider(_approved_flow(_policy()))


def test_frozen_inputs_bind_the_approved_identity_into_the_policy_manifest(
    approved_root: Path, monkeypatch: pytest.MonkeyPatch, repository_root: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv("PIXELGYM_TEST_OPENROUTER_KEY", "dummy-not-a-real-key")
    _configure(monkeypatch, repository_root, tmp_path / "state")
    policy = _policy()
    flow = _approved_flow(policy)

    GroundingEvaluationFlow.validate_and_freeze_inputs(flow)

    assert flow.price_catalog_version == "openrouter-test-v1"
    manifest = flow.policy
    assert manifest["provider"] == "openrouter"
    assert manifest["model"] == "qwen/qwen3-vl-8b-instruct"
    assert manifest["prompt_version"] == 2
    assert manifest["parameters"]["approved_provider_sha256"] == policy.approval_sha256
    assert manifest["parameters"]["hidden_retries"] == 0
    assert manifest["parameters"]["credential_env"] == "PIXELGYM_TEST_OPENROUTER_KEY"
    assert "dummy-not-a-real-key" not in json.dumps(manifest)
    assert flow.transition == ("create_or_recover_mlflow_run", {})

    scripted = _approved_flow(
        policy,
        approved_provider="",
        approved_provider_sha256="",
        model="day3-replay-revised-v2",
        maximum_calls=100,
    )
    GroundingEvaluationFlow.validate_and_freeze_inputs(scripted)
    assert scripted.policy["provider"] == "scripted-demo"
    assert scripted.price_catalog_version == "pixelgym-demo-prices-v1"
    assert scripted.policy["policy_id"] != manifest["policy_id"]
