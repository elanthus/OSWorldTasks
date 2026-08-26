from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest

from pixelgym.grounding.v5 import calibration_pilot
from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.openrouter_policy import MODEL, UPSTREAM_PROVIDER
from pixelgym.grounding.v5.policies import golden_actions
from pixelgym.grounding.v5.runner import TransportOutcome

ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def _stable_smoke_evidence_digests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        calibration_pilot,
        "_file_digest",
        lambda _path: "sha256:" + "a" * 64,
    )


def _normalized(action: dict[str, int]) -> dict[str, int]:
    value = dict(action)
    if value["action_type"] == 1:
        value["x"] = round(value["x"] * 999 / 1023)
        value["y"] = round(value["y"] * 999 / 767)
    return value


class GoldenPilotTransport:
    def __init__(self, actions: list[dict[str, int]]) -> None:
        self.actions = list(actions)
        self.records: list[dict[str, object]] = []
        self.wire_requests_sent = 0
        self.spent_usd = calibration_pilot.PRIOR_DIAGNOSTIC_SPEND_USD

    def send(
        self, request: dict[str, object], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        del request, deadline_seconds
        self.wire_requests_sent += 1
        action = self.actions.pop(0)
        cost = Decimal("0.0001")
        self.spent_usd += cost
        self.records.append(
            {"idempotency_key": idempotency_key, "status": "response", "cost_usd": str(cost)}
        )
        return TransportOutcome(
            "response",
            {
                "response_id": f"fake-{len(self.records)}",
                "model": MODEL,
                "content": json.dumps(action, separators=(",", ":")),
                "finish_reason": "stop",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "cost": float(cost),
                    "upstream_provider": "Alibaba",
                    "price_guard": "ok",
                },
            },
        )

    def cancel(
        self, *, idempotency_key: str, mode: str
    ) -> Literal["cancelled", "unknown"]:
        del idempotency_key, mode
        return "unknown"

    def reconcile(
        self, *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        del idempotency_key, deadline_seconds
        return TransportOutcome("unknown", failure_code="disabled")


def _golden_pilot_actions(plan: dict[str, object]) -> list[dict[str, int]]:
    actions: list[dict[str, int]] = []
    for record in plan["tasks"]:  # type: ignore[index]
        seed = record["seed"]
        task = generate_task(seed)
        backend = V5FakeBackend()
        backend.reset(seed)
        actions.extend(_normalized(action) for action in golden_actions(task, backend)[:2])
        backend.close()
    return actions


def test_calibration_pilot_plan_is_ten_tasks_twenty_calls_and_under_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calibration_pilot, "_git", lambda *_args: "revision-1")
    plan = calibration_pilot.build_plan(ROOT)

    assert plan["provider_calls_made"] == 0
    assert plan["model"] == MODEL
    assert plan["provider"]["upstream_provider"] == UPSTREAM_PROVIDER
    assert len(plan["tasks"]) == 10
    assert plan["caps"]["model_attempt_cap"] == 20
    assert plan["caps"]["provider_control_request_cap"] == 0
    assert Decimal(plan["caps"]["aggregate_upper_bound_usd"]) < Decimal(5)
    families = [record["family"] for record in plan["tasks"]]
    assert len(set(families)) == 6


def test_calibration_pilot_rejects_wrong_digest_before_any_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calibration_pilot, "_git", lambda *_args: "revision-1")
    plan = calibration_pilot.build_plan(ROOT)
    transport = GoldenPilotTransport(_golden_pilot_actions(plan))

    with pytest.raises(ValueError, match="approved pilot plan digest"):
        calibration_pilot.execute_pilot(
            ROOT,
            plan=plan,
            approved_plan_sha256="sha256:" + "0" * 64,
            output_directory=tmp_path / "pilot",
            transport=transport,  # type: ignore[arg-type]
        )

    assert transport.records == []


def test_calibration_pilot_runs_two_actions_on_each_frozen_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def git(_root: Path, *args: str) -> str:
        return "" if args == ("status", "--porcelain", "--untracked-files=no") else "revision-1"

    monkeypatch.setattr(calibration_pilot, "_git", git)
    plan = calibration_pilot.build_plan(ROOT)
    transport = GoldenPilotTransport(_golden_pilot_actions(plan))

    result = calibration_pilot.execute_pilot(
        ROOT,
        plan=plan,
        approved_plan_sha256=calibration_pilot.plan_digest(plan),
        output_directory=tmp_path / "pilot",
        transport=transport,  # type: ignore[arg-type]
    )

    assert result["provider_calls_made"] == 20
    assert result["model_attempt_reservations"] == 20
    assert result["provider_control_requests"] == 0
    assert len(result["episode_results"]) == 10
    assert {episode["classification"] for episode in result["episode_results"]} == {
        "pilot_action_limit"
    }
    assert result["journal_integrity"]["event_count"] > 0
    assert result["journal_integrity"]["object_count"] > 0
    assert (tmp_path / "pilot/summary.json").is_file()
