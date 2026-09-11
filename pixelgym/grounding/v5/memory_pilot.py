"""One-attempt memory diagnostics with scripted prefixes and durable shared accounting.

This module never changes the admitted task or policy. Scripted prefixes are
explicitly separate from model-selected actions and cannot be benchmark scores.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.v5.contracts import CallCaps, PolicyManifest, content_digest, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.panel_policy import SpendLedger
from pixelgym.grounding.v5.policies import _append_golden_stage
from pixelgym.grounding.v5.resume import decode_resume_record
from pixelgym.grounding.v5.runner import PolicyVisibleResult, ProviderTransport
from pixelgym.grounding.v5.screenshot_memory import ScreenshotMemoryPolicy, ScreenshotMemoryRunner
from pixelgym.serialization import canonical_json_bytes

PILOT_CAPS = CallCaps(340, 20, 0, 20)


class MemoryDiagnosticRunner(ScreenshotMemoryRunner):
    """Recover the consumer boundary from its declared scripted prefix."""

    def __init__(self, *, boundary: Callable[[str], None] = lambda _: None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.diagnostic_boundary = boundary

    def _boundary(self, name: str) -> None:
        self.diagnostic_boundary(name)

    def _restore_current_environment(
        self,
        *,
        trial_id: str,
        step_index: int,
        backend: Any,
    ) -> None:
        event = self.journal.event(f"{trial_id}/prefix_prepared")
        if event is None or event.step_index != step_index:
            raise RuntimeError("the diagnostic prefix boundary is missing")
        restore_prefix(self.journal, event.payload, backend, step_index)


def restore_prefix(
    journal: V5AttemptJournal,
    payload: dict[str, Any],
    backend: MemoryBackend,
    steps: int,
) -> None:
    backend.restore(
        journal.get_object(
            payload["environment_checkpoint_digest"], expected_kind="environment_checkpoint"
        )
    )
    record = decode_resume_record(
        journal.get_object(
            payload["environment_resume_digest"], expected_kind="environment_resume_record"
        )
    )
    backend.verify_resume_record(record, step_count=steps)


def prepare_prefix(
    journal: V5AttemptJournal,
    *,
    trial_id: str,
    case: dict[str, Any],
    policy: ScreenshotMemoryPolicy,
    manifest: PolicyManifest,
    plan_digest: str,
) -> tuple[PixelGuiEnv, MemoryBackend, bytes] | None:
    task = generate_memory_task(case["seed"])
    if (
        task.task_id != case["task_id"]
        or content_digest(task.canonical_dict()) != case["task_digest"]
    ):
        raise ValueError("diagnostic task differs from the approved case")
    steps = case["scripted_prefix_action_count"]
    binding = {"case": case, "policy_id": manifest.policy_id, "execution_plan_digest": plan_digest}
    started = journal.event(f"{trial_id}/prefix_started")
    prepared = journal.event(f"{trial_id}/prefix_prepared")
    if started is not None:
        if started.payload != binding:
            raise ValueError("existing diagnostic assignment has a different binding")
        if prepared is None:
            # Its whole prefix allowance was reserved before the first action.
            # An interrupted prefix is retained as infrastructure failure, never replayed.
            return None
        backend = MemoryBackend()
        try:
            restore_prefix(journal, prepared.payload, backend, steps)
            env = ScreenshotMemoryRunner._restored_env(task, backend, step_count=steps)
            state = journal.get_object(
                prepared.payload["policy_checkpoint_digest"], expected_kind="policy_checkpoint"
            )
            return env, backend, state
        except BaseException:
            backend.close()
            raise
    reserved = sum(
        event.payload["case"]["scripted_prefix_action_count"]
        for event in journal.events()
        if event.kind == "memory_prefix_started"
    )
    if reserved + steps > PILOT_CAPS.environment_action_cap - PILOT_CAPS.model_attempt_cap:
        raise RuntimeError("scripted-prefix action cap exceeded")
    journal.append_event(
        event_key=f"{trial_id}/prefix_started",
        kind="memory_prefix_started",
        trial_id=trial_id,
        step_index=0,
        payload=binding,
    )
    planner = MemoryBackend()
    actions: list[dict[str, int]] = []
    try:
        planner.reset(task.seed)
        for stage in task.stages[: case["consumer_index"]]:
            _append_golden_stage(planner, stage, actions)
    finally:
        planner.close()
    if len(actions) != steps or content_digest(actions) != case["scripted_prefix_actions_digest"]:
        raise ValueError("scripted prefix differs from the approved trace")
    backend = MemoryBackend()
    env = PixelGuiEnv(
        backend, instruction=task.instruction, max_episode_steps=task.max_episode_steps
    )
    state = policy.reset(task.instruction)
    frames = []
    try:
        observation, _ = env.reset(seed=task.seed)
        for index, action in enumerate(actions):
            frames.append(journal.put_object("screenshot", observation.tobytes()))
            state = policy.observe_screenshot(state, observation.tobytes())
            observation, reward, terminated, truncated, _ = env.step(action)
            if reward or terminated or truncated:
                raise RuntimeError("scripted prefix unexpectedly ended the episode")
            visible = PolicyVisibleResult(
                "sha256:" + sha256_bytes(observation.tobytes()),
                reward,
                terminated,
                truncated,
                index,
            )
            state = policy.post_dispatch_state(state, action, visible)
        if backend.stage_index != case["consumer_index"]:
            raise RuntimeError("scripted prefix did not reach the assigned consumer")
        frames.append(journal.put_object("screenshot", observation.tobytes()))
        controls = [
            {"control_id": control.control_id, "bbox": list(control.bbox)}
            for control in backend.visible_controls()
        ]
        receipt = {
            "actions": actions,
            "screenshot_digests": frames,
            "consumer_controls": controls,
            "expected_control": task.stages[case["consumer_index"]].target_control_id,
        }
        payload = {
            "prefix_receipt_digest": journal.put_object(
                "scripted_prefix", canonical_json_bytes(receipt)
            ),
            "policy_checkpoint_digest": journal.put_object("policy_checkpoint", state),
            "environment_checkpoint_digest": journal.put_object(
                "environment_checkpoint", backend.checkpoint()
            ),
            "environment_resume_digest": journal.put_object(
                "environment_resume_record",
                canonical_json_bytes(backend.environment_resume_record(step_count=steps).to_dict()),
            ),
            "screenshot_digest": frames[-1],
            "binding_digest": content_digest(binding),
        }
        journal.append_event(
            event_key=f"{trial_id}/prefix_prepared",
            kind="memory_prefix_prepared",
            trial_id=trial_id,
            step_index=steps,
            payload=payload,
        )
        return env, backend, state
    except BaseException:
        env.close()
        raise


def run_condition(
    journal: V5AttemptJournal,
    *,
    case: dict[str, Any],
    mode: str,
    trial_id: str,
    plan_digest: str,
    policy: ScreenshotMemoryPolicy,
    manifest: PolicyManifest,
    transport: ProviderTransport,
    boundary: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    if mode not in ("history", "stateless") or policy.retain_screenshots != (mode == "history"):
        raise ValueError("diagnostic mode does not match the policy")
    previous = journal.event(f"{trial_id}/result")
    if previous is not None:
        assignment = journal.event(f"{trial_id}/prefix_started")
        if assignment is None or assignment.payload != {
            "case": case,
            "policy_id": manifest.policy_id,
            "execution_plan_digest": plan_digest,
        }:
            raise ValueError("completed condition differs from the requested assignment")
        policy.close()
        return previous.payload
    try:
        prepared = prepare_prefix(
            journal,
            trial_id=trial_id,
            case=case,
            policy=policy,
            manifest=manifest,
            plan_digest=plan_digest,
        )
    except BaseException:
        policy.close()
        raise
    result: dict[str, Any] = {
        "trial_id": trial_id,
        "mode": mode,
        "seed": case["seed"],
        "task_id": case["task_id"],
        "consumer_index": case["consumer_index"],
        "policy_id": manifest.policy_id,
        "first_attempt_correct": False,
        "valid_consumer_choice": False,
        "classification": "prefix_interrupted",
        "model_attempted": False,
    }
    if prepared is not None:
        env, backend, state = prepared
        steps = case["scripted_prefix_action_count"]
        try:
            runner = MemoryDiagnosticRunner(
                journal=journal,
                manifest=manifest,
                policy=policy,
                transport=transport,
                approved_caps=PILOT_CAPS,
                boundary=boundary,
            )
            runner._preflight(backend.task, backend, required_action_limit=steps + 1)
            boundary("prefix_prepared")
            attempted = any(event.kind == "attempt_started" for event in journal.events(trial_id))
            if attempted:
                outcome = runner.recover_step(
                    trial_id=trial_id, step_index=steps, task=backend.task, backend=backend
                )
            else:
                outcome = runner._act(
                    trial_id=trial_id,
                    step_index=steps,
                    env=env,
                    backend=backend,
                    state=state,
                    observation=backend.screenshot(),
                )
            result["classification"] = outcome["classification"]
            result["model_attempted"] = True
            commit = journal.event(f"{trial_id}/step-{steps:04d}/dispatch_committed")
            if commit is not None:
                runner._validate_committed_dispatch_evidence(commit)
                intent = journal.event(f"{trial_id}/step-{steps:04d}/sealed_action_intent")
                prefix = journal.event(f"{trial_id}/prefix_prepared")
                if intent is None or prefix is None:
                    raise RuntimeError("committed diagnostic lacks authoritative lineage")
                action = json.loads(journal.get_object(intent.payload["action_digest"]))
                receipt = json.loads(
                    journal.get_object(
                        prefix.payload["prefix_receipt_digest"], expected_kind="scripted_prefix"
                    )
                )
                selected = next(
                    (
                        control["control_id"]
                        for control in receipt["consumer_controls"]
                        if action["action_type"] == 1
                        and control["bbox"][0] <= action["x"] < control["bbox"][2]
                        and control["bbox"][1] <= action["y"] < control["bbox"][3]
                    ),
                    None,
                )
                result.update(
                    classification="choice_dispatched",
                    valid_consumer_choice=selected is not None,
                    first_attempt_correct=selected == receipt["expected_control"],
                    action_digest=intent.payload["action_digest"],
                )
            response = next(
                (
                    event
                    for event in journal.events(trial_id)
                    if event.kind == "canonical_response_persisted"
                ),
                None,
            )
            if response is not None:
                result["canonical_response_digest"] = response.payload["canonical_response_digest"]
        finally:
            policy.close()
            env.close()
    else:
        policy.close()
    boundary("before_result")
    journal.append_event(
        event_key=f"{trial_id}/result",
        kind="memory_diagnostic_result",
        trial_id=trial_id,
        step_index=case["scripted_prefix_action_count"],
        payload=result,
    )
    return result


def summarize(
    journal: V5AttemptJournal,
    ledger: SpendLedger,
    *,
    jobs: list[dict[str, Any]],
    plan_digest: str,
    stop_reason: str,
) -> dict[str, Any]:
    rows = []
    for job in jobs:
        event = journal.event(f"{job['trial_id']}/result")
        rows.append(
            event.payload
            if event is not None
            else {
                **job,
                "classification": "not_run",
                "first_attempt_correct": False,
                "valid_consumer_choice": False,
                "model_attempted": False,
            }
        )
    scores = {}
    for mode in ("history", "stateless"):
        assigned = [row for row in rows if row["mode"] == mode]
        scores[mode] = {
            "assigned": len(assigned),
            "attempted": sum(row["model_attempted"] for row in assigned),
            "first_attempt_correct": sum(row["first_attempt_correct"] for row in assigned),
            "valid_consumer_choices": sum(row["valid_consumer_choice"] for row in assigned),
        }
    return {
        "schema_version": "pixelgym-v5-d58-memory-pilot-result-v1",
        "purpose": "scripted-prefix memory diagnostic; not an end-to-end benchmark score",
        "execution_plan_digest": plan_digest,
        "conditions": rows,
        "scores": scores,
        "stop_reason": stop_reason,
        "spend": ledger.to_dict(),
        "maximum_aggregate_spend_usd": str(ledger.maximum_spend_usd),
        "model_attempt_reservations": journal.call_counts()[0],
        "provider_control_requests": journal.call_counts()[1],
        "scripted_prefix_action_reservations": sum(
            event.payload["case"]["scripted_prefix_action_count"]
            for event in journal.events()
            if event.kind == "memory_prefix_started"
        ),
        "model_actions_dispatched": sum(
            event.kind == "dispatch_committed" for event in journal.events()
        ),
        "journal_integrity": journal.integrity_report(),
        "confirmatory_tasks_evaluated": 0,
        "publication_policy": "provider text, images and private checkpoints remain in the ignored authoritative journal",
    }
