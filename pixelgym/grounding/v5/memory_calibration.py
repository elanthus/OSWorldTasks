"""Budgeted full episodes and host-only memory measurements for the frozen pair."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from pixelgym.grounding.v5.contracts import CallCaps, PolicyManifest, content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.panel_policy import SpendLedger
from pixelgym.grounding.v5.runner import ProviderTransport
from pixelgym.grounding.v5.screenshot_memory import ScreenshotMemoryPolicy, ScreenshotMemoryRunner


class MemoryCalibrationLedger(SpendLedger):
    """Retain the full approved request bound even after observing cheaper calls."""

    def _unknown_charge_reservation(self, request_maximum_usd: Decimal) -> Decimal:
        return request_maximum_usd


class BudgetedMemoryRunner(ScreenshotMemoryRunner):
    def __init__(
        self,
        *,
        ledger: SpendLedger,
        boundary: Callable[[str], None] = lambda _: None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.ledger = ledger
        self.boundary = boundary

    def _boundary(self, name: str) -> None:
        self.boundary(name)

    def _act(self, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(self.policy, ScreenshotMemoryPolicy):
            raise TypeError("full calibration requires the frozen screenshot policy")
        if (
            self.ledger.blocked
            or self.ledger.budget_accounted_spend_usd + self.policy.config.request_maximum_usd
            > self.ledger.maximum_spend_usd
        ):
            return {"classification": "budget_stop", "state": kwargs["state"]}
        return super()._act(**kwargs)


def episode_measurements(journal: V5AttemptJournal, trial_id: str, seed: int) -> dict[str, Any]:
    """Score only stored dispatches; privileged choices never enter policy context."""
    task = generate_memory_task(seed)
    events = journal.events(trial_id)
    reached: set[int] = set()
    first: dict[int, dict[str, Any]] = {}
    final_stage = 0
    for event in events:
        if event.kind not in ("initial_screenshot", "dispatch_committed"):
            continue
        checkpoint = json.loads(
            journal.get_object(
                event.payload["environment_checkpoint_digest"],
                expected_kind="environment_checkpoint",
            )
        )
        final_stage = checkpoint["stage_index"]
        if final_stage in (5, 7):
            reached.add(final_stage)
    for event in events:
        if event.kind != "attempt_started":
            continue
        step = event.step_index
        previous = journal.event(
            f"{trial_id}/initial_screenshot"
            if step == 0
            else f"{trial_id}/step-{step - 1:04d}/dispatch_committed"
        )
        if previous is None:
            raise ValueError("model attempt has no preceding environment checkpoint")
        checkpoint = json.loads(
            journal.get_object(
                previous.payload["environment_checkpoint_digest"],
                expected_kind="environment_checkpoint",
            )
        )
        consumer = checkpoint["stage_index"]
        if consumer not in (5, 7) or consumer in first or checkpoint["repair_pending"]:
            continue
        selected = None
        committed = journal.event(f"{trial_id}/step-{step:04d}/dispatch_committed")
        if committed is not None:
            post = json.loads(
                journal.get_object(
                    committed.payload["environment_checkpoint_digest"],
                    expected_kind="environment_checkpoint",
                )
            )
            selected = post["deferred_choices"].get(str(consumer))
        first[consumer] = {
            "consumer_index": consumer,
            "step_index": step,
            "valid_choice": selected is not None,
            "correct": selected == task.stages[consumer].target_control_id,
        }
    return {
        "reached_consumers": sorted(reached),
        "reached_both_consumers": reached == {5, 7},
        "first_attempts": [first[key] for key in sorted(first)],
        "final_stage_index": final_stage,
        "model_attempts": sum(event.kind == "attempt_started" for event in events),
        "environment_actions_dispatched": sum(
            event.kind == "dispatch_committed" for event in events
        ),
        "environment_dispatch_reservations": sum(
            event.kind == "dispatch_started" for event in events
        ),
    }


def run_episode(
    journal: V5AttemptJournal,
    *,
    job: dict[str, Any],
    manifest: PolicyManifest,
    policy: ScreenshotMemoryPolicy,
    transport: ProviderTransport,
    ledger: SpendLedger,
    caps: CallCaps,
    plan_digest: str,
    boundary: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    task = generate_memory_task(job["seed"])
    if (
        task.task_id != job["task_id"]
        or content_digest(task.canonical_dict()) != job["task_digest"]
        or task.max_episode_steps != job["action_limit"]
    ):
        raise ValueError("task differs from approved full calibration assignment")
    if job["mode"] not in ("history", "stateless") or policy.retain_screenshots != (
        job["mode"] == "history"
    ):
        raise ValueError("policy mode differs from assignment")
    trial = job["trial_id"]
    binding = {"job": job, "policy_id": manifest.policy_id, "plan_digest": plan_digest}
    started = journal.event(f"{trial}/full_started")
    completed = journal.event(f"{trial}/full_completed")
    if started is not None and started.payload != binding:
        raise ValueError("existing full assignment has a different binding")
    runner = BudgetedMemoryRunner(
        journal=journal,
        policy=policy,
        manifest=manifest,
        transport=transport,
        ledger=ledger,
        approved_caps=caps,
        boundary=boundary,
    )
    try:
        if completed is not None:
            if started is None:
                raise ValueError("completed assignment lacks its start binding")
            return completed.payload
        if started is None:
            journal.append_event(
                event_key=f"{trial}/full_started",
                kind="memory_full_started",
                trial_id=trial,
                step_index=0,
                payload=binding,
            )
            boundary("full_started")
            result = runner.run(trial_id=trial, task=task, backend=MemoryBackend()).to_dict()
            classification = result["classification"]
            success = result["success"]
        else:
            # A partly executed episode is never restarted or re-sent. A stored
            # terminal dispatch retains its result even if summary publication crashed.
            classification, success = "interrupted_episode", False
            commits = [
                event for event in journal.events(trial) if event.kind == "dispatch_committed"
            ]
            for commit in commits:
                runner._validate_committed_dispatch_evidence(commit)
            if commits and commits[-1].payload["terminated"]:
                classification, success = "success_termination", True
            elif commits and commits[-1].payload["truncated"]:
                classification = "step_limit_truncation"
        row = {
            **job,
            "classification": classification,
            "success": success,
            "policy_id": manifest.policy_id,
            **episode_measurements(journal, trial, job["seed"]),
        }
        boundary("before_full_result")
        journal.append_event(
            event_key=f"{trial}/full_completed",
            kind="memory_full_completed",
            trial_id=trial,
            step_index=row["environment_actions_dispatched"],
            payload=row,
        )
        return row
    finally:
        policy.close()


def summarize(
    journal: V5AttemptJournal, ledger: SpendLedger, plan: dict[str, Any], *, stop_reason: str
) -> dict[str, Any]:
    rows = []
    for job in plan["jobs"]:
        event = journal.event(f"{job['trial_id']}/full_completed")
        if event is None and journal.event(f"{job['trial_id']}/full_started") is not None:
            rows.append(
                {
                    **job,
                    "classification": "interrupted_episode",
                    "success": False,
                    **episode_measurements(journal, job["trial_id"], job["seed"]),
                }
            )
            continue
        rows.append(
            event.payload
            if event
            else {
                **job,
                "classification": "not_run",
                "success": False,
                "model_attempts": 0,
                "environment_actions_dispatched": 0,
                "reached_consumers": [],
                "reached_both_consumers": False,
                "first_attempts": [],
            }
        )
    scores = {}
    for mode in ("history", "stateless"):
        selected = [row for row in rows if row["mode"] == mode]
        scores[mode] = {
            "assigned": len(selected),
            "attempted_episodes": sum(row["model_attempts"] > 0 for row in selected),
            "terminal_successes": sum(row["success"] for row in selected),
            "reached_both_consumers": sum(row["reached_both_consumers"] for row in selected),
            "first_memory_attempts": sum(len(row["first_attempts"]) for row in selected),
            "correct_first_memory_attempts": sum(
                choice["correct"] for row in selected for choice in row["first_attempts"]
            ),
            "classifications": dict(
                sorted(Counter(row["classification"] for row in selected).items())
            ),
        }
    return {
        "schema_version": "pixelgym-d58-full-memory-calibration-summary-v1",
        "execution_plan_digest": plan["execution_plan_digest"],
        "driver_code_revision": plan["driver_code_revision"],
        "conditions": rows,
        "scores": scores,
        "stop_reason": stop_reason,
        "complete": all(
            row["classification"] not in ("not_run", "budget_stop", "interrupted_episode")
            for row in rows
        ),
        "aggregate_spend": ledger.to_dict(),
        "prior_pilot_spend_usd": plan["prior_pilot_spend"]["spent_usd"],
        "model_attempt_reservations": journal.call_counts()[0],
        "provider_control_requests": journal.call_counts()[1],
        "journal_integrity": journal.integrity_report(),
        "confirmatory_tasks_evaluated": 0,
        "publication_policy": "provider text, screenshots and checkpoints remain in the ignored authoritative journal",
    }
