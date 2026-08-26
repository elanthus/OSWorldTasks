from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

from PIL import Image

from pixelgym.grounding.providers import ProviderResponse
from pixelgym.grounding.v5 import provider_smoke
from pixelgym.grounding.v5.backend import V5FakeBackend


class FakeProvider:
    name = "fake-openrouter"
    model = provider_smoke.MODEL
    parameters: ClassVar[dict[str, object]] = {}

    def __init__(self, raw_response: str, *, usage: dict[str, object] | None = None) -> None:
        self.raw_response = raw_response
        self.usage = usage or {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.00001}
        self.calls = 0

    def invoke(
        self, *, image_path: Path, prompt: str, schema: dict[str, object]
    ) -> ProviderResponse:
        self.calls += 1
        assert image_path.is_file()
        assert prompt == provider_smoke.smoke_prompt()
        assert schema == provider_smoke.ACTION_SCHEMA
        return ProviderResponse(
            timestamp_utc="2026-08-26T00:00:00+00:00",
            latency_ms=12.5,
            raw_response=self.raw_response,
            usage=self.usage,
            provider_metadata={
                "response_id": "generation-1",
                "response_model": provider_smoke.MODEL,
                "upstream_provider": "Alibaba",
            },
            provider_trace=[],
        )


def _repository(tmp_path: Path) -> Path:
    (tmp_path / "requirements").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (tmp_path / "requirements/platform-py312.lock").write_text("locked\n", encoding="utf-8")
    screenshot = tmp_path / provider_smoke.SCREENSHOT_PATH
    screenshot.parent.mkdir(parents=True)
    backend = V5FakeBackend()
    backend.reset(provider_smoke.DEVELOPMENT_SEED)
    Image.fromarray(backend.screenshot()).save(screenshot)
    backend.close()
    return tmp_path


def test_checked_in_smoke_input_matches_current_fake_backend_renderer() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    backend = V5FakeBackend()
    backend.reset(provider_smoke.DEVELOPMENT_SEED)
    try:
        with Image.open(repository_root / provider_smoke.SCREENSHOT_PATH) as image:
            assert image.convert("RGB").tobytes() == backend.screenshot().tobytes()
    finally:
        backend.close()


def test_plan_is_one_call_development_only_and_under_approved_cap(
    tmp_path: Path, monkeypatch
) -> None:
    root = _repository(tmp_path)
    monkeypatch.setattr(provider_smoke, "_git", lambda *_args: "revision-1")

    plan = provider_smoke.build_plan(
        root,
        maximum_spend_usd=Decimal("2.00"),
        price_observed_at_utc="2026-08-26T00:00:00+00:00",
    )

    assert plan["provider_calls_made"] == 0
    assert plan["purpose"].startswith("development-only")
    assert plan["task"]["partition"] == "development"
    assert plan["task"]["seed"] == 5000
    assert plan["provider"]["require_parameters"] is True
    assert plan["provider"]["data_collection"] == "deny"
    assert plan["caps"]["model_attempts"] == 1
    assert plan["caps"]["provider_wire_requests"] == 1
    assert Decimal(plan["caps"]["theoretical_request_maximum_usd"]) < Decimal("2.00")


def test_execute_smoke_requires_exact_approval_before_provider_call(
    tmp_path: Path, monkeypatch
) -> None:
    root = _repository(tmp_path)
    monkeypatch.setattr(provider_smoke, "_git", lambda *_args: "revision-1")
    plan = provider_smoke.build_plan(root, maximum_spend_usd=Decimal("2.00"))
    provider = FakeProvider('{"action_type":1,"x":512,"y":459,"key":0}')

    try:
        provider_smoke.execute_smoke(
            root,
            plan=plan,
            approved_plan_sha256="sha256:" + "0" * 64,
            provider=provider,
        )
    except ValueError as exc:
        assert "approved plan digest" in str(exc)
    else:  # pragma: no cover - assertion helper
        raise AssertionError("mismatched approval digest was accepted")
    assert provider.calls == 0


def test_execute_smoke_rejects_non_development_seed_even_when_digest_matches(
    tmp_path: Path, monkeypatch
) -> None:
    root = _repository(tmp_path)
    monkeypatch.setattr(provider_smoke, "_git", lambda *_args: "revision-1")
    plan = provider_smoke.build_plan(root, maximum_spend_usd=Decimal("2.00"))
    plan["task"]["partition"] = "calibration"
    plan["task"]["seed"] = 6000
    provider = FakeProvider('{"action_type":1,"x":512,"y":459,"key":0}')

    try:
        provider_smoke.execute_smoke(
            root,
            plan=plan,
            approved_plan_sha256=provider_smoke.plan_digest(plan),
            provider=provider,
        )
    except ValueError as exc:
        assert "development seed" in str(exc)
    else:  # pragma: no cover - assertion helper
        raise AssertionError("non-development smoke seed was accepted")
    assert provider.calls == 0


def test_execute_smoke_makes_one_call_validates_and_dispatches(tmp_path: Path, monkeypatch) -> None:
    root = _repository(tmp_path)

    def git(_root: Path, *args: str) -> str:
        return (
            ""
            if args == ("status", "--porcelain", "--untracked-files=no")
            else "revision-1"
        )

    monkeypatch.setattr(provider_smoke, "_git", git)
    plan = provider_smoke.build_plan(root, maximum_spend_usd=Decimal("2.00"))
    provider = FakeProvider('{"action_type":1,"x":512,"y":459,"key":0}')

    result = provider_smoke.execute_smoke(
        root,
        plan=plan,
        approved_plan_sha256=provider_smoke.plan_digest(plan),
        provider=provider,
    )

    assert provider.calls == 1
    assert result["provider_calls_made"] == 1
    assert result["environment_actions"] == 1
    assert result["publishable_response"] == {
        "action_type": 1,
        "x": 512,
        "y": 459,
        "key": 0,
    }
    assert result["dispatch"]["backend_diagnostic"] == "correct_transition"
    assert result["classification"] == "dispatched"
    assert result["cost_usd"] == "0.00001"
    assert "generation-1" not in json.dumps(result)


def test_execute_smoke_retains_attempt_when_usage_cost_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    root = _repository(tmp_path)

    def git(_root: Path, *args: str) -> str:
        return (
            ""
            if args == ("status", "--porcelain", "--untracked-files=no")
            else "revision-1"
        )

    monkeypatch.setattr(provider_smoke, "_git", git)
    plan = provider_smoke.build_plan(root, maximum_spend_usd=Decimal("2.00"))
    provider = FakeProvider(
        '{"action_type":1,"x":512,"y":459,"key":0}',
        usage={"prompt_tokens": 100, "completion_tokens": 10},
    )

    result = provider_smoke.execute_smoke(
        root,
        plan=plan,
        approved_plan_sha256=provider_smoke.plan_digest(plan),
        provider=provider,
    )

    assert provider.calls == 1
    assert result["provider_calls_made"] == 1
    assert result["environment_actions"] == 0
    assert result["classification"] == "evidence_integrity_failure"
    assert result["failure_code"] == "missing_or_invalid_usage_cost"


def test_command_refuses_existing_output_before_loading_credentials(tmp_path: Path) -> None:
    output = tmp_path / "already-exists.json"
    output.write_text("preserve me", encoding="utf-8")
    repository_root = Path(__file__).resolve().parents[2]

    completed = subprocess.run(
        [
            sys.executable,
            str(repository_root / "scripts/run_grounding_v5_provider_smoke.py"),
            "--execute",
            "--plan",
            "does-not-exist.json",
            "--approved-plan-sha256",
            "sha256:" + "0" * 64,
            "--output",
            str(output),
        ],
        cwd=repository_root,
        env={},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "output already exists" in completed.stderr
    assert output.read_text(encoding="utf-8") == "preserve me"
