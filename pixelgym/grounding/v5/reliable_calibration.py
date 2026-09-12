"""Full reset episodes using the approved curl transport and bounded retries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pixelgym.grounding.v5.contracts import CallCaps, PolicyManifest, content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_calibration import episode_measurements
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.panel_policy import SpendLedger
from pixelgym.grounding.v5.reliable_memory import ReliableMemoryPolicy, ReliableMemoryRunner
from pixelgym.grounding.v5.runner import ProviderTransport


class ReliableCalibrationRunner(ReliableMemoryRunner):
    def __init__(
        self, *, ledger: SpendLedger, time_exhausted: Callable[[], bool], **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.ledger, self.time_exhausted = ledger, time_exhausted

    def _act(self, **kwargs: Any) -> dict[str, Any]:
        if self.time_exhausted():
            return {"classification": "phase_time_stop", "state": kwargs["state"]}
        if (
            self.ledger.blocked
            or self.ledger.budget_accounted_spend_usd >= self.ledger.maximum_spend_usd
        ):
            return {"classification": "budget_stop", "state": kwargs["state"]}
        # The transport checks the actual request's bound before every wire send.
        return super()._act(**kwargs)


def run_episode(
    journal: V5AttemptJournal,
    *,
    job: dict[str, Any],
    manifest: PolicyManifest,
    policy: ReliableMemoryPolicy,
    transport: ProviderTransport,
    ledger: SpendLedger,
    caps: CallCaps,
    plan_digest: str,
    boundary: Callable[[str], None] = lambda _: None,
    time_exhausted: Callable[[], bool] = lambda: False,
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
    binding = {
        "job": job,
        "policy_id": manifest.policy_id,
        "plan_digest": plan_digest,
        "backend_identity": FocusMemoryBackend.backend_identity,
    }
    started = journal.event(f"{trial}/full_started")
    completed = journal.event(f"{trial}/full_completed")
    if started is not None and started.payload != binding:
        raise ValueError("existing full assignment has a different binding")
    runner = ReliableCalibrationRunner(
        journal=journal,
        policy=policy,
        manifest=manifest,
        transport=transport,
        ledger=ledger,
        approved_caps=caps,
        boundary=boundary,
        time_exhausted=time_exhausted,
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
            result = runner.run(trial_id=trial, task=task, backend=FocusMemoryBackend()).to_dict()
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
