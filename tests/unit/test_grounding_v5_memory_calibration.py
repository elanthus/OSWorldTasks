"""Full-episode memory measurement, budget carry-forward, and fail-closed recovery."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_calibration import MemoryCalibrationLedger, run_episode, summarize
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.memory_plan import config_from_price_snapshot
from pixelgym.grounding.v5.panel_policy import SpendLedger
from pixelgym.grounding.v5.policies import _append_golden_stage, noop_action
from pixelgym.grounding.v5.runner import InjectedInterruption, ScriptedTransport, TransportOutcome
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    build_screenshot_policy_manifest,
)
from scripts.run_grounding_v5_memory_calibration import (
    PHASE,
    ordered_seeds,
    prefix_digest,
    render_report,
    verify_ledger,
)

ROOT = Path(__file__).parents[2]
CONFIG = config_from_price_snapshot(
    json.loads((ROOT / "artifacts/grounding-v5-d58-design/gemini-price-snapshot.json").read_text())
)
PLAN = "sha256:" + "a" * 64


def job(mode: str = "history") -> dict[str, Any]:
    task = generate_memory_task(5112)
    return {
        "trial_id": mode,
        "mode": mode,
        "seed": task.seed,
        "task_id": task.task_id,
        "task_digest": content_digest(task.canonical_dict()),
        "action_limit": task.max_episode_steps,
        "seed_record": task.seed_record.to_dict(),
    }


class GoldenTransport(ScriptedTransport):
    def __init__(
        self, ledger: SpendLedger, *, cost: str = "0.001", wrong_memory: bool = False
    ) -> None:
        backend = MemoryBackend()
        backend.reset(5112)
        actions: list[dict[str, int]] = []
        try:
            for stage in backend.task.stages:
                _append_golden_stage(backend, stage, actions)
            actions.extend(
                noop_action() for _ in range(backend.task.max_episode_steps - len(actions))
            )
        finally:
            backend.close()
        if wrong_memory:
            stage = generate_memory_task(5112).stages[5]
            rank = next(
                i for i, c in enumerate(stage.controls) if c.control_id != stage.target_control_id
            )
            actions[15] = {"action_type": 1, "x": 512, "y": 459 + 72 * rank, "key": 0}
        outcomes = []
        for action in actions:
            normalized = dict(action)
            if action["action_type"] == 1:
                normalized.update(x=int(action["x"] * 1000 / 1024), y=int(action["y"] * 1000 / 768))
            outcomes.append(
                TransportOutcome(
                    "response",
                    {
                        "response_id": "fake",
                        "model": CONFIG.model,
                        "content": json.dumps(normalized),
                        "finish_reason": "stop",
                        "usage": {
                            "upstream_provider": CONFIG.response_provider,
                            "price_guard": "ok",
                            "cost": float(cost),
                        },
                    },
                )
            )
        super().__init__(outcomes)
        self.ledger, self.cost = ledger, Decimal(cost)

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        assert self.ledger.reserve_wire(idempotency_key, CONFIG.request_maximum_usd)
        outcome = super().send(
            request, idempotency_key=idempotency_key, deadline_seconds=deadline_seconds
        )
        assert self.ledger.record_cost(idempotency_key, self.cost, CONFIG.request_maximum_usd)
        return outcome


def execute(
    journal: V5AttemptJournal, transport: GoldenTransport, *, mode: str = "history", **kwargs: Any
) -> dict[str, Any]:
    return run_episode(
        journal,
        job=job(mode),
        manifest=build_screenshot_policy_manifest(
            ROOT, config=CONFIG, code_revision="test", retain_screenshots=mode == "history"
        ),
        policy=ScreenshotMemoryPolicy(CONFIG, retain_screenshots=mode == "history"),
        transport=transport,
        ledger=transport.ledger,
        caps=CallCaps(100, 100, 0, 100),
        plan_digest=PLAN,
        **kwargs,
    )


def test_full_episode_uses_only_model_actions_and_measures_wrong_memory(tmp_path: Path) -> None:
    with closing(V5AttemptJournal(tmp_path / "shared.sqlite")) as journal:
        ledger = MemoryCalibrationLedger(Decimal(5), Decimal(0), journal=journal)
        good = execute(journal, GoldenTransport(ledger))
        bad = execute(journal, GoldenTransport(ledger, wrong_memory=True), mode="stateless")
        assert good["success"] and good["classification"] == "success_termination"
        assert good["reached_consumers"] == [5, 7]
        assert [x["correct"] for x in good["first_attempts"]] == [True, True]
        assert bad["classification"] == "step_limit_truncation" and not bad["success"]
        assert [x["correct"] for x in bad["first_attempts"]] == [False, True]
        assert all(
            row["model_attempts"] == row["environment_actions_dispatched"] for row in (good, bad)
        )
        assert not any(e.kind == "memory_prefix_started" for e in journal.events())
        plan = {
            "jobs": [job(), job("stateless")],
            "execution_plan_digest": PLAN,
            "driver_code_revision": "test",
            "prior_pilot_spend": {"spent_usd": "0"},
        }
        summary = summarize(journal, ledger, plan, stop_reason="all_assignments_completed")
        assert summary["scores"]["history"]["terminal_successes"] == 1
        assert summary["scores"]["stateless"]["correct_first_memory_attempts"] == 1
        assert summary["complete"]
        assert render_report(summary) == render_report(
            json.loads(json.dumps(summary, sort_keys=True))
        )


def test_budget_stops_before_next_attempt_and_survives_new_process(tmp_path: Path) -> None:
    path = tmp_path / "shared.sqlite"
    with closing(V5AttemptJournal(path)) as journal:
        ledger = SpendLedger(Decimal("1.50"), Decimal(0), journal=journal)
        transport = GoldenTransport(ledger, cost="0.10")
        result = execute(journal, transport)
        assert result["classification"] == "budget_stop"
        assert result["model_attempts"] == 1 and len(transport.model_requests) == 1
    with closing(V5AttemptJournal(path)) as journal:
        ledger = SpendLedger(Decimal("1.50"), Decimal(0), journal=journal)
        transport = GoldenTransport(ledger)
        assert execute(journal, transport) == result
        assert ledger.spent_usd == Decimal("0.10")
        assert not transport.model_requests


@pytest.mark.parametrize(
    "boundary,terminal",
    [
        ("full_started", False),
        ("provider_receipt", False),
        ("dispatch_committed", False),
        ("before_full_result", True),
    ],
)
def test_interrupted_full_episode_never_restarts_or_resends(
    tmp_path: Path, boundary: str, terminal: bool
) -> None:
    path = tmp_path / "shared.sqlite"

    def interrupt(name: str) -> None:
        if name == boundary:
            raise InjectedInterruption(name)

    with closing(V5AttemptJournal(path)) as journal:
        transport = GoldenTransport(SpendLedger(Decimal(5), Decimal(0), journal=journal))
        with pytest.raises(InjectedInterruption):
            execute(journal, transport, boundary=interrupt)
        before = journal.call_counts()
        prefix = prefix_digest(journal, len(journal.events()))
        assert prefix == content_digest([asdict(event) for event in journal.events()])
    with closing(V5AttemptJournal(path)) as journal:
        fresh = GoldenTransport(SpendLedger(Decimal(5), Decimal(0), journal=journal))
        result = execute(journal, fresh)
        assert result["success"] is terminal
        assert result["classification"] == (
            "success_termination" if terminal else "interrupted_episode"
        )
        assert not fresh.model_requests and journal.call_counts() == before
        assert result["model_attempts"] == before[0]


def test_calibration_order_retains_all_fifty_seeds_and_spreads_families() -> None:
    original = json.loads(
        (ROOT / "artifacts/grounding-v5-d56-gemini-v3-full-calibration-plan.json").read_text()
    )
    seeds = ordered_seeds()
    assert len(seeds) == len(set(seeds)) == 50
    assert set(seeds) == {row["seed"] for row in original["task_order"]}
    assert len({generate_memory_task(seed).family for seed in seeds[:6]}) == 6
    assert all(5100 <= seed <= 5167 for seed in seeds)


def test_full_phase_keeps_prior_spend_and_unknown_holds_on_restart(tmp_path: Path) -> None:
    path = tmp_path / "aggregate.sqlite"
    pilot_binding = json.loads(
        (ROOT / "artifacts/grounding-v5-d58-calibration-pilot/ledger-started.json").read_text()
    )
    with closing(V5AttemptJournal(path)) as journal:
        journal.append_event(
            event_key="d58-memory-pilot-v1/ledger_initialized",
            kind="memory_ledger_initialized",
            trial_id="d58-memory-pilot-v1",
            step_index=0,
            payload=pilot_binding,
        )
        ledger = MemoryCalibrationLedger(Decimal(5), Decimal(0), journal=journal)
        assert ledger.reserve_wire("prior-pilot", CONFIG.request_maximum_usd)
        assert ledger.record_cost("prior-pilot", Decimal("0.19"), CONFIG.request_maximum_usd)
        count = len(journal.events())
        plan = {
            "execution_plan_digest": PLAN,
            "pilot_ledger_event_count": count,
            "pilot_ledger_prefix_digest": prefix_digest(journal, count),
            "aggregate_ceiling_usd": "5.00",
            "prior_pilot_spend": ledger.to_dict(),
        }
        verify_ledger(journal, ledger, plan)
        assert journal.event(f"{PHASE}/started") is not None
        assert ledger.reserve_wire("full-unknown", CONFIG.request_maximum_usd)
        ledger.reserve_unknown_charge("full-unknown", CONFIG.request_maximum_usd)
    with closing(V5AttemptJournal(path)) as journal:
        ledger = MemoryCalibrationLedger(Decimal(5), Decimal(0), journal=journal)
        verify_ledger(journal, ledger, plan)
        assert ledger.budget_accounted_spend_usd == Decimal("0.19") + CONFIG.request_maximum_usd
        assert ledger.wire_requests_sent == 2
        with pytest.raises(ValueError, match="different approval"):
            verify_ledger(journal, ledger, {**plan, "execution_plan_digest": "sha256:" + "b" * 64})
        with pytest.raises(ValueError, match="lineage"):
            verify_ledger(
                journal, ledger, {**plan, "pilot_ledger_prefix_digest": "sha256:" + "c" * 64}
            )
