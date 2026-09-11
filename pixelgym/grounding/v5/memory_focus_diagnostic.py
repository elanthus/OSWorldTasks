"""Twenty single-action checks of text-entry usability and deferred choices."""

from __future__ import annotations

import json
from typing import Any

from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.v5.contracts import CallCaps, PolicyManifest, content_digest, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.memory_pilot import MemoryDiagnosticRunner, restore_prefix
from pixelgym.grounding.v5.policies import _append_golden_stage, click_action, key_action
from pixelgym.grounding.v5.runner import PolicyVisibleResult, ProviderTransport
from pixelgym.grounding.v5.screenshot_memory import ScreenshotMemoryPolicy, ScreenshotMemoryRunner
from pixelgym.serialization import canonical_json_bytes

# Fixed development cases, selected before any diagnostic response. Repeated
# seeds exercise different input states, not independent statistical samples.
CASE_SPECS = (
    (5000, "unfocused"),
    (5004, "unfocused"),
    (5000, "focused_empty"),
    (5004, "focused_empty"),
    (5008, "partial"),
    (5012, "complete"),
    (5000, "memory_5"),
    (5004, "memory_7"),
    (5008, "memory_5"),
    (5012, "memory_7"),
)


def prefix_actions(seed: int, state_name: str) -> list[dict[str, int]]:
    if (seed, state_name) not in CASE_SPECS:
        raise ValueError("undeclared diagnostic case")
    backend = FocusMemoryBackend()
    actions: list[dict[str, int]] = []
    try:
        backend.reset(seed)
        stage_index = int(state_name[-1]) if state_name.startswith("memory_") else 1
        for stage in backend.task.stages[:stage_index]:
            _append_golden_stage(backend, stage, actions)
        if not state_name.startswith("memory_") and state_name != "unfocused":
            actions.append(click_action(*backend.control_center("text_input")))
            if state_name in ("partial", "complete"):
                text = backend.task.stages[1].required_text
                actions.extend(
                    key_action(char) for char in (text[:1] if state_name == "partial" else text)
                )
        return actions
    finally:
        backend.close()


def cases() -> list[dict[str, Any]]:
    result = []
    for index, (seed, name) in enumerate(CASE_SPECS):
        task = generate_memory_task(seed)
        actions = prefix_actions(seed, name)
        result.append(
            {
                "case_id": f"case-{index:02d}",
                "seed": seed,
                "state_name": name,
                "stage_index": int(name[-1]) if name.startswith("memory_") else 1,
                "task_id": task.task_id,
                "task_digest": content_digest(task.canonical_dict()),
                "scripted_prefix_action_count": len(actions),
                "scripted_prefix_actions_digest": content_digest(actions),
            }
        )
    return result


def run_condition(
    journal: V5AttemptJournal,
    *,
    case: dict[str, Any],
    trial_id: str,
    mode: str,
    policy: ScreenshotMemoryPolicy,
    manifest: PolicyManifest,
    transport: ProviderTransport,
    caps: CallCaps,
    plan_digest: str,
) -> dict[str, Any]:
    if (
        case not in cases()
        or mode not in ("history", "stateless")
        or policy.retain_screenshots != (mode == "history")
    ):
        raise ValueError("diagnostic assignment differs from the fixed cases/policy")
    steps = case["scripted_prefix_action_count"]
    binding = {"case": case, "policy_id": manifest.policy_id, "execution_plan_digest": plan_digest}
    started = journal.event(f"{trial_id}/prefix_started")
    if started is not None and started.payload != binding:
        raise ValueError("diagnostic assignment has a different binding")
    prior = journal.event(f"{trial_id}/result")
    if prior is not None:
        if started is None:
            raise ValueError("result has no assignment")
        policy.close()
        return prior.payload
    backend = FocusMemoryBackend()
    task = generate_memory_task(case["seed"])
    env = PixelGuiEnv(
        backend, instruction=task.instruction, max_episode_steps=task.max_episode_steps
    )
    row = {
        **case,
        "trial_id": trial_id,
        "mode": mode,
        "policy_id": manifest.policy_id,
        "classification": "prefix_interrupted",
        "model_attempted": False,
        "action_dispatched": False,
        "desired_transition": False,
        "valid_memory_choice": False,
        "correct_memory_choice": False,
    }
    try:
        prepared = journal.event(f"{trial_id}/prefix_prepared")
        if started is not None and prepared is None:
            pass  # Interrupted prefixes are retained, never replayed or re-sent.
        else:
            if prepared is None:
                reserved = sum(
                    e.payload["case"]["scripted_prefix_action_count"]
                    for e in journal.events()
                    if e.kind == "focus_prefix_started"
                )
                maximum = 2 * sum(c["scripted_prefix_action_count"] for c in cases())
                if reserved + steps > maximum:
                    raise ValueError("diagnostic prefix allowance exhausted")
                journal.append_event(
                    event_key=f"{trial_id}/prefix_started",
                    kind="focus_prefix_started",
                    trial_id=trial_id,
                    step_index=0,
                    payload=binding,
                )
                observation, _ = env.reset(seed=task.seed)
                state = policy.reset(task.instruction)
                frames = []
                actions = prefix_actions(task.seed, case["state_name"])
                for index, action in enumerate(actions):
                    frames.append(journal.put_object("screenshot", observation.tobytes()))
                    state = policy.observe_screenshot(state, observation.tobytes())
                    observation, reward, terminated, truncated, _ = env.step(action)
                    if reward or terminated or truncated:
                        raise RuntimeError("diagnostic prefix ended the episode")
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
                    raise RuntimeError("prefix reached a different stage")
                frames.append(journal.put_object("screenshot", observation.tobytes()))
                payload = {
                    "prefix_receipt_digest": journal.put_object(
                        "scripted_prefix",
                        canonical_json_bytes({"actions": actions, "screenshot_digests": frames}),
                    ),
                    "policy_checkpoint_digest": journal.put_object("policy_checkpoint", state),
                    "environment_checkpoint_digest": journal.put_object(
                        "environment_checkpoint", backend.checkpoint()
                    ),
                    "environment_resume_digest": journal.put_object(
                        "environment_resume_record",
                        canonical_json_bytes(
                            backend.environment_resume_record(step_count=steps).to_dict()
                        ),
                    ),
                    "screenshot_digest": frames[-1],
                    "binding_digest": content_digest(binding),
                }
                journal.append_event(
                    event_key=f"{trial_id}/prefix_prepared",
                    kind="focus_prefix_prepared",
                    trial_id=trial_id,
                    step_index=steps,
                    payload=payload,
                )
            else:
                restore_prefix(journal, prepared.payload, backend, steps)
                env = ScreenshotMemoryRunner._restored_env(task, backend, step_count=steps)
                state = journal.get_object(
                    prepared.payload["policy_checkpoint_digest"], expected_kind="policy_checkpoint"
                )
            before = json.loads(backend.checkpoint())
            runner = MemoryDiagnosticRunner(
                journal=journal,
                manifest=manifest,
                policy=policy,
                transport=transport,
                approved_caps=caps,
            )
            runner._preflight(task, backend, required_action_limit=steps + 1)
            attempted = any(e.kind == "attempt_started" for e in journal.events(trial_id))
            outcome = (
                runner.recover_step(trial_id=trial_id, step_index=steps, task=task, backend=backend)
                if attempted
                else runner._act(
                    trial_id=trial_id,
                    step_index=steps,
                    env=env,
                    backend=backend,
                    state=state,
                    observation=backend.screenshot(),
                )
            )
            row.update(classification=outcome["classification"], model_attempted=True)
            committed = journal.event(f"{trial_id}/step-{steps:04d}/dispatch_committed")
            if committed is not None:
                runner._validate_committed_dispatch_evidence(committed)
                after = json.loads(
                    journal.get_object(
                        committed.payload["environment_checkpoint_digest"],
                        expected_kind="environment_checkpoint",
                    )
                )
                name = case["state_name"]
                desired = False
                if name == "unfocused":
                    desired = after["focused"] and not before["focused"]
                elif name in ("focused_empty", "partial"):
                    desired = (
                        after["text_value"]
                        == before["text_value"]
                        + task.stages[1].required_text[len(before["text_value"])]
                    )
                elif name == "complete":
                    desired = after["stage_index"] == 2
                else:
                    selected = after["deferred_choices"].get(str(case["stage_index"]))
                    row["valid_memory_choice"] = selected is not None
                    row["correct_memory_choice"] = (
                        selected == task.stages[case["stage_index"]].target_control_id
                    )
                    desired = row["valid_memory_choice"]
                row.update(
                    action_dispatched=True,
                    desired_transition=desired,
                    classification="action_dispatched",
                )
                row["dispatch_event_key"] = committed.event_key
    finally:
        env.close()
        policy.close()
    journal.append_event(
        event_key=f"{trial_id}/result",
        kind="focus_diagnostic_result",
        trial_id=trial_id,
        step_index=steps,
        payload=row,
    )
    return row
