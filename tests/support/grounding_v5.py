"""Shared v5 grounding test fixtures: golden transports, request builders, and configs."""

from __future__ import annotations

import copy
import io
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.memory_plan import config_from_price_snapshot
from pixelgym.grounding.v5.panel_policy import SpendLedger
from pixelgym.grounding.v5.policies import _append_golden_stage, noop_action
from pixelgym.grounding.v5.reliable_calibration import run_episode as reliable_run_episode
from pixelgym.grounding.v5.reliable_memory import (
    ReliableMemoryPolicy,
    build_reliable_manifest,
    reliable_config,
)
from pixelgym.grounding.v5.runner import ScriptedTransport, TransportOutcome
from pixelgym.grounding.v5.screenshot_memory import ScreenshotMemoryPolicy
from scripts.run_grounding_v5_gemini38_calibration import config_from_snapshot

ROOT = Path(__file__).parents[2]

# Memory-calibration configuration and golden transport.
CONFIG = config_from_price_snapshot(
    json.loads((ROOT / "artifacts/grounding-v5-d58-design/gemini-price-snapshot.json").read_text())
)
PLAN = "sha256:" + "a" * 64


def advance(backend: MemoryBackend, until: int) -> None:
    """Drive ``backend`` through the golden actions of every stage before ``until``."""
    actions: list[dict[str, int]] = []
    for stage in backend.task.stages[backend.stage_index : until]:
        _append_golden_stage(backend, stage, actions)


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


# Reliable-continuation runner bound to the golden memory task.
RELIABLE_CONFIG = reliable_config(CONFIG)


def reliable_execute(
    journal: V5AttemptJournal, transport: GoldenTransport, *, mode: str = "history", **kwargs: Any
) -> dict[str, Any]:
    return reliable_run_episode(
        journal,
        job=job(mode),
        manifest=build_reliable_manifest(
            ROOT, config=RELIABLE_CONFIG, code_revision="test", retain_screenshots=mode == "history"
        ),
        policy=ReliableMemoryPolicy(RELIABLE_CONFIG, retain_screenshots=mode == "history"),
        transport=transport,
        ledger=transport.ledger,
        caps=CallCaps(100, 300, 0, 300),
        plan_digest=PLAN,
        **kwargs,
    )


# Gemini 3.8 request-budget configuration and request/response builders.
BUDGET_SNAPSHOT = json.loads(
    (ROOT / "artifacts/grounding-v5-d58-gemini38-calibration/price-recheck.json").read_text()
)
BUDGET_CONFIG = config_from_snapshot(BUDGET_SNAPSHOT)


def budget_request(images: int = 1) -> dict[str, Any]:
    policy = ScreenshotMemoryPolicy(BUDGET_CONFIG, retain_screenshots=False)
    result = policy.build_request(policy.reset("Complete the workflow."), bytes(1024 * 768 * 3))
    picture = next(part for part in result["messages"][1]["content"] if part["type"] == "image_url")
    result["messages"][1]["content"] += [copy.deepcopy(picture) for _ in range(images - 1)]
    return result


def budget_response(cost: str = "0.01") -> io.BytesIO:
    return io.BytesIO(
        json.dumps(
            {
                "id": "fixture",
                "model": BUDGET_CONFIG.model,
                "provider": "Google",
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"cost": cost},
            }
        ).encode()
    )
