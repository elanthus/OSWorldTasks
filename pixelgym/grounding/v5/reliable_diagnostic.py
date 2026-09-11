"""Ten supplied-state logical actions; network retries never advance the prefix."""

from __future__ import annotations

from typing import Any

from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.v5.contracts import CallCaps, content_digest, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_focus_diagnostic import cases, prefix_actions
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.memory_pilot import restore_prefix
from pixelgym.grounding.v5.reliable_memory import (
    ROOT,
    ReliableMemoryPolicy,
    ReliableMemoryRunner,
    build_reliable_manifest,
)
from pixelgym.grounding.v5.reliable_transport import ReliableTransport
from pixelgym.grounding.v5.runner import PolicyVisibleResult
from pixelgym.serialization import canonical_json_bytes


def diagnostic_jobs(phase: str) -> list[dict[str, Any]]:
    selected = [cases()[i] for i in (0, 2, 6, 7, 9)]
    return [
        {"case": case, "mode": mode, "trial_id": f"{phase}-{case['case_id']}-{mode}"}
        for index, case in enumerate(selected)
        for mode in (("history", "stateless") if index % 2 == 0 else ("stateless", "history"))
    ]


class ReliableDiagnosticRunner(ReliableMemoryRunner):
    def _restore_current_environment(self, *, trial_id: str, step_index: int, backend: Any) -> None:
        event = self.journal.event(f"{trial_id}/prefix_prepared")
        if event is None or event.step_index != step_index:
            raise ValueError("diagnostic prefix is missing")
        restore_prefix(self.journal, event.payload, backend, step_index)


def run_condition(
    journal: V5AttemptJournal,
    *,
    job: dict[str, Any],
    transport: ReliableTransport,
    revision: str,
    plan_digest: str,
    caps: CallCaps,
) -> dict[str, Any]:
    case, mode, trial = job["case"], job["mode"], job["trial_id"]
    if case not in cases() or mode not in ("history", "stateless"):
        raise ValueError("invalid diagnostic assignment")
    if journal.events(trial):
        raise ValueError("diagnostic assignments cannot be restarted")
    config = transport.config
    manifest = build_reliable_manifest(
        ROOT, config=config, code_revision=revision, retain_screenshots=mode == "history"
    )
    policy = ReliableMemoryPolicy(config, retain_screenshots=mode == "history")
    backend = FocusMemoryBackend()
    task = generate_memory_task(case["seed"])
    env = PixelGuiEnv(
        backend, instruction=task.instruction, max_episode_steps=task.max_episode_steps
    )
    steps = case["scripted_prefix_action_count"]
    try:
        journal.append_event(
            event_key=f"{trial}/started",
            kind="reliable_diagnostic_started",
            trial_id=trial,
            step_index=0,
            payload={
                "job": job,
                "policy_id": manifest.policy_id,
                "execution_plan_digest": plan_digest,
            },
        )
        observation, _ = env.reset(seed=task.seed)
        state = policy.reset(task.instruction)
        actions = prefix_actions(task.seed, case["state_name"])
        if content_digest(actions) != case["scripted_prefix_actions_digest"]:
            raise ValueError("prefix changed")
        for index, action in enumerate(actions):
            state = policy.observe_screenshot(state, observation.tobytes())
            observation, reward, terminated, truncated, _ = env.step(action)
            if reward or terminated or truncated:
                raise ValueError("diagnostic prefix ended the episode")
            state = policy.post_dispatch_state(
                state,
                action,
                PolicyVisibleResult(
                    "sha256:" + sha256_bytes(observation.tobytes()),
                    reward,
                    terminated,
                    truncated,
                    index,
                ),
            )
        if backend.stage_index != case["stage_index"]:
            raise ValueError("diagnostic prefix reached wrong stage")
        journal.append_event(
            event_key=f"{trial}/prefix_prepared",
            kind="reliable_prefix_prepared",
            trial_id=trial,
            step_index=steps,
            payload={
                "environment_checkpoint_digest": journal.put_object(
                    "environment_checkpoint", backend.checkpoint()
                ),
                "environment_resume_digest": journal.put_object(
                    "environment_resume_record",
                    canonical_json_bytes(
                        backend.environment_resume_record(step_count=steps).to_dict()
                    ),
                ),
                "policy_checkpoint_digest": journal.put_object("policy_checkpoint", state),
                "screenshot_digest": journal.put_object("screenshot", observation.tobytes()),
            },
        )
        runner = ReliableDiagnosticRunner(
            journal=journal,
            policy=policy,
            manifest=manifest,
            transport=transport,
            approved_caps=caps,
        )
        runner._preflight(task, backend, required_action_limit=steps + 1)
        outcome = runner._act(
            trial_id=trial,
            step_index=steps,
            env=env,
            backend=backend,
            state=state,
            observation=observation,
        )
        dispatches = [e for e in journal.events(trial) if e.kind == "dispatch_committed"]
        for event in dispatches:
            runner._validate_committed_dispatch_evidence(event)
        row = {
            "trial_id": trial,
            "mode": mode,
            "case_id": case["case_id"],
            "classification": outcome["classification"],
            "action_dispatched": len(dispatches) == 1,
            "model_attempts": sum(e.kind == "attempt_started" for e in journal.events(trial)),
        }
        journal.append_event(
            event_key=f"{trial}/result",
            kind="reliable_diagnostic_result",
            trial_id=trial,
            step_index=steps,
            payload=row,
        )
        return row
    finally:
        env.close()
        policy.close()
