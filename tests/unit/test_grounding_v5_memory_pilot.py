"""Pilot isolation, shared spend, and interruption recovery without provider calls."""

from __future__ import annotations

import json
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.memory_pilot import run_condition, summarize
from pixelgym.grounding.v5.memory_plan import config_from_price_snapshot
from pixelgym.grounding.v5.panel_policy import SpendLedger
from pixelgym.grounding.v5.runner import InjectedInterruption, ScriptedTransport, TransportOutcome
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    build_screenshot_policy_manifest,
)
from scripts.run_grounding_v5_memory_pilot import bind_ledger, render_report

ROOT = Path(__file__).parents[2]
CANDIDATE = json.loads(
    (ROOT / "artifacts/grounding-v5-d58-design/memory-repair/pilot-plan.json").read_text()
)
CONFIG = config_from_price_snapshot(
    json.loads((ROOT / "artifacts/grounding-v5-d58-design/gemini-price-snapshot.json").read_text())
)
CASE = CANDIDATE["cases"][0]
PLAN_DIGEST = "sha256:" + "a" * 64


class CostedTransport(ScriptedTransport):
    def __init__(self, ledger: SpendLedger, *, correct: bool = True) -> None:
        stage = generate_memory_task(CASE["seed"]).stages[CASE["consumer_index"]]
        rank = next(
            i for i, c in enumerate(stage.controls) if c.control_id == stage.target_control_id
        )
        if not correct:
            rank = (rank + 1) % 3
        response = {
            "response_id": "fake",
            "model": CONFIG.model,
            "content": json.dumps(
                {"action_type": 1, "x": 500, "y": int((459 + 72 * rank) * 1000 / 768), "key": 0}
            ),
            "usage": {
                "upstream_provider": CONFIG.response_provider,
                "price_guard": "ok",
                "cost": 0.01,
            },
            "finish_reason": "stop",
        }
        super().__init__([TransportOutcome("response", response)])
        self.ledger = ledger

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        assert self.ledger.reserve_wire(idempotency_key, CONFIG.request_maximum_usd)
        outcome = super().send(
            request, idempotency_key=idempotency_key, deadline_seconds=deadline_seconds
        )
        assert self.ledger.record_cost(idempotency_key, Decimal("0.01"), CONFIG.request_maximum_usd)
        return outcome


def condition(
    journal: V5AttemptJournal, transport: CostedTransport, *, mode: str = "history", **kwargs: Any
) -> dict[str, Any]:
    manifest = build_screenshot_policy_manifest(
        ROOT, config=CONFIG, code_revision="test", retain_screenshots=mode == "history"
    )
    return run_condition(
        journal,
        case=CASE,
        mode=mode,
        trial_id=mode,
        plan_digest=PLAN_DIGEST,
        policy=ScreenshotMemoryPolicy(CONFIG, retain_screenshots=mode == "history"),
        manifest=manifest,
        transport=transport,
        **kwargs,
    )


def test_paired_diagnostic_has_same_consumer_and_one_shared_ledger(tmp_path: Path) -> None:
    with closing(V5AttemptJournal(tmp_path / "aggregate.sqlite")) as journal:
        ledger = SpendLedger(Decimal(5), Decimal(0), journal=journal)
        history = CostedTransport(ledger)
        stateless = CostedTransport(ledger, correct=False)
        good = condition(journal, history)
        bad = condition(journal, stateless, mode="stateless")
        assert good["first_attempt_correct"] is True
        assert bad["first_attempt_correct"] is False
        assert good["valid_consumer_choice"] and bad["valid_consumer_choice"]
        requests = [t.model_requests[0]["request"] for t in (history, stateless)]
        assert requests[0]["messages"][0] == requests[1]["messages"][0]
        images = [
            [i for i in r["messages"][1]["content"] if i["type"] == "image_url"] for r in requests
        ]
        assert list(map(len, images)) == [16, 1]
        assert images[0][-1] == images[1][-1]
        jobs = [
            {"trial_id": mode, "seed": CASE["seed"], "mode": mode}
            for mode in ("history", "stateless")
        ]
        summary = summarize(journal, ledger, jobs=jobs, plan_digest=PLAN_DIGEST, stop_reason="test")
        assert summary["scripted_prefix_action_reservations"] == 30
        assert summary["model_actions_dispatched"] == 2
        assert summary["spend"]["spent_usd"] == "0.02"
        assert summary["provider_control_requests"] == 0
        assert "expected_control" not in json.dumps(summary)
        assert "image_url" not in json.dumps(summary)
        assert render_report(summary) == render_report(
            json.loads(json.dumps(summary, sort_keys=True))
        )
        before = len(journal.events())
        assert condition(journal, history) == good
        assert len(journal.events()) == before
        assert len(history.model_requests) == 1


@pytest.mark.parametrize(
    "boundary,expected_wires,expected_dispatches,correct",
    [
        ("prefix_prepared", 1, 1, True),
        ("attempt_started", 0, 0, False),
        ("provider_receipt", 1, 0, False),
        ("canonical_response_persisted", 1, 1, True),
        ("dispatch_started", 1, 0, False),
        ("backend_accepted", 1, 0, False),
        ("dispatch_committed", 1, 1, True),
        ("before_result", 1, 1, True),
    ],
)
def test_fresh_process_recovery_never_resends_uncertain_request(
    tmp_path: Path, boundary: str, expected_wires: int, expected_dispatches: int, correct: bool
) -> None:
    path = tmp_path / "aggregate.sqlite"

    def interrupt(name: str) -> None:
        if name == boundary:
            raise InjectedInterruption(name)

    with closing(V5AttemptJournal(path)) as journal:
        original = CostedTransport(SpendLedger(Decimal(5), Decimal(0), journal=journal))
        with pytest.raises(InjectedInterruption):
            condition(journal, original, boundary=interrupt)
    with closing(V5AttemptJournal(path)) as journal:
        ledger = SpendLedger(Decimal(5), Decimal(0), journal=journal)
        fresh = CostedTransport(ledger)
        result = condition(journal, fresh)
        assert result["first_attempt_correct"] is correct
        assert len(original.model_requests) + len(fresh.model_requests) == expected_wires
        assert ledger.wire_requests_sent == expected_wires
        assert ledger.spent_usd == Decimal("0.01") * expected_wires
        assert sum(e.kind == "dispatch_committed" for e in journal.events()) == expected_dispatches
        assert sum(e.kind == "memory_prefix_started" for e in journal.events()) == 1
        assert journal.call_counts() == (1, 0)
        journal.integrity_report()


def test_incomplete_prefix_is_retained_without_replaying_actions(tmp_path: Path) -> None:
    with closing(V5AttemptJournal(tmp_path / "aggregate.sqlite")) as journal:
        manifest = build_screenshot_policy_manifest(
            ROOT, config=CONFIG, code_revision="test", retain_screenshots=True
        )
        journal.append_event(
            event_key="history/prefix_started",
            kind="memory_prefix_started",
            trial_id="history",
            step_index=0,
            payload={
                "case": CASE,
                "policy_id": manifest.policy_id,
                "execution_plan_digest": PLAN_DIGEST,
            },
        )
        transport = CostedTransport(SpendLedger(Decimal(5), Decimal(0), journal=journal))
        result = condition(journal, transport)
        assert result["classification"] == "prefix_interrupted"
        assert not result["model_attempted"]
        assert not transport.model_requests
        assert journal.event("history/prefix_prepared") is None


def test_shared_unknown_holds_survive_restart_and_stop_both_arms(tmp_path: Path) -> None:
    path = tmp_path / "aggregate.sqlite"
    for index in range(3):
        with closing(V5AttemptJournal(path)) as journal:
            ledger = SpendLedger(Decimal(5), Decimal(0), journal=journal)
            assert ledger.reserve_wire(f"condition-{index}", CONFIG.request_maximum_usd)
            ledger.reserve_unknown_charge(f"condition-{index}", CONFIG.request_maximum_usd)
    with closing(V5AttemptJournal(path)) as journal:
        ledger = SpendLedger(Decimal(5), Decimal(0), journal=journal)
        assert ledger.budget_accounted_spend_usd == Decimal("4.32967680")
        assert not ledger.reserve_wire("other-arm", CONFIG.request_maximum_usd)
        assert ledger.wire_requests_sent == 3


def test_wrong_assignment_rejected_without_another_request(tmp_path: Path) -> None:
    with closing(V5AttemptJournal(tmp_path / "aggregate.sqlite")) as journal:
        transport = CostedTransport(SpendLedger(Decimal(5), Decimal(0), journal=journal))
        condition(journal, transport)
        manifest = build_screenshot_policy_manifest(
            ROOT, config=CONFIG, code_revision="test", retain_screenshots=True
        )
        with pytest.raises(ValueError, match="assignment"):
            run_condition(
                journal,
                case={**CASE, "seed": 5004},
                mode="history",
                trial_id="history",
                plan_digest=PLAN_DIGEST,
                manifest=manifest,
                transport=transport,
                policy=ScreenshotMemoryPolicy(CONFIG, retain_screenshots=True),
            )
        assert len(transport.model_requests) == 1


def test_missing_or_mismatched_ledger_never_creates_fresh_budget(tmp_path: Path) -> None:
    marker, journal = tmp_path / "marker.json", tmp_path / "aggregate.sqlite"
    binding = {"execution_plan_digest": PLAN_DIGEST, "maximum_aggregate_spend_usd": "5.00"}
    bind_ledger(marker, journal, binding)
    with pytest.raises(RuntimeError, match="new allowance is forbidden"):
        bind_ledger(marker, journal, binding)
    journal.touch()
    bind_ledger(marker, journal, binding)
    with pytest.raises(ValueError, match="different approved execution"):
        bind_ledger(marker, journal, {**binding, "maximum_aggregate_spend_usd": "10.00"})
    marker.unlink()
    with pytest.raises(RuntimeError, match="durable ledger marker"):
        bind_ledger(marker, journal, binding)


def test_unrun_assignments_remain_in_budget_stopped_report(tmp_path: Path) -> None:
    with closing(V5AttemptJournal(tmp_path / "aggregate.sqlite")) as journal:
        ledger = SpendLedger(Decimal(5), Decimal(0), journal=journal)
        summary = summarize(
            journal,
            ledger,
            jobs=[
                {"trial_id": mode, "seed": 5000, "mode": mode} for mode in ("history", "stateless")
            ],
            plan_digest=PLAN_DIGEST,
            stop_reason="aggregate_budget_or_call_cap_stop",
        )
        assert len(summary["conditions"]) == 2
        assert all(row["classification"] == "not_run" for row in summary["conditions"])
        assert all(
            row["assigned"] == 1 and row["attempted"] == 0 for row in summary["scores"].values()
        )
        assert summary["model_attempt_reservations"] == 0
        assert "aggregate_budget_or_call_cap_stop" in render_report(summary)
