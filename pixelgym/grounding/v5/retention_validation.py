"""A fixed, no-network campaign proving capture, restore, replay, and failure retention.

The golden policy is privileged scripted test data. This campaign measures engineering
controls, never model quality, and is separate from the frozen D5.9 evidence and human gate.
"""

from __future__ import annotations

import json
import shutil
from contextlib import closing
from pathlib import Path
from typing import Any

from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import CallCaps, TransportOutcome, content_digest
from pixelgym.grounding.v5.fixtures import scripted_policy_manifest
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import CallCapExceededError, V5AttemptJournal
from pixelgym.grounding.v5.policies import Mutation, golden_actions, mutation_trace
from pixelgym.grounding.v5.retention import _files, _hash, write_private
from pixelgym.grounding.v5.runner import (
    InjectedInterruption,
    ScriptedStatefulPolicy,
    ScriptedTransport,
    V5Runner,
)
from pixelgym.serialization import canonical_json_bytes

SCHEMA = "pixelgym-retention-validation-v1"
SEED = 5000
CASES = (
    "golden",
    "premature_commit",
    "step_limit",
    "malformed",
    "invalid_action",
    "call_cap",
    "resume",
)
SOURCE_SUFFIXES = frozenset(
    {".py", ".json", ".html", ".css", ".js", ".ttf", ".woff", ".woff2", ".svg", ".png"}
)
SOURCE_EXTRAS = ("scripts/validate_evidence_retention.py", "pyproject.toml")


def _policy(case: str) -> ScriptedStatefulPolicy:
    task = generate_task(SEED)
    if case in {"premature_commit", "step_limit"}:
        mutation = (
            Mutation.PREMATURE_COMMIT
            if case == "premature_commit"
            else Mutation.STEP_BUDGET_EXHAUSTION
        )
        actions = mutation_trace(task, mutation).actions
    else:
        with closing(V5FakeBackend()) as backend:
            backend.reset(SEED)
            actions = golden_actions(task, backend)
    return ScriptedStatefulPolicy(actions)


def _caps(case: str) -> CallCaps:
    horizon = generate_task(SEED).max_episode_steps
    attempts = 1 if case == "call_cap" else horizon
    return CallCaps(horizon, attempts, 2 * attempts, 3 * attempts)


def capture_campaign(run: Path) -> None:
    """Run the seven fixed cases using only in-process fakes; close every resource."""
    run.mkdir(mode=0o700)
    observations = []
    for case in CASES:
        directory = run / "cases" / case
        directory.mkdir(parents=True, mode=0o700)
        transport = ScriptedTransport()
        if case in {"malformed", "invalid_action"}:
            transport = ScriptedTransport(
                [
                    TransportOutcome(
                        "response",
                        {
                            "response_id": "fixture",
                            "model": "no-cost-scripted-policy",
                            "content": "not-json"
                            if case == "malformed"
                            else '{"action_type":1,"x":-1,"y":0,"key":0}',
                            "finish_reason": "stop",
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                        },
                    )
                ]
            )
        record: dict[str, Any] = {"case": case, "seed": SEED, "trial_id": "retention-" + case}
        path = directory / "attempts.sqlite"
        with closing(V5AttemptJournal(path)) as journal:
            runner = V5Runner(
                journal=journal,
                manifest=scripted_policy_manifest(),
                transport=transport,
                policy=_policy(case),
                approved_caps=_caps(case),
                interrupt_after="sealed_action_intent" if case == "resume" else None,
            )
            try:
                record["episode"] = runner.run(
                    trial_id=record["trial_id"], task=generate_task(SEED)
                ).to_dict()
            except InjectedInterruption as exc:
                if case != "resume":
                    raise
                record["interruption_boundary"] = exc.boundary
                record["interrupted_integrity"] = journal.integrity_report()
            except CallCapExceededError:
                if case != "call_cap":
                    raise
                record["classification"] = "call_cap_exceeded"
        if case == "resume":
            # Preserve the pre-recovery journal, then reopen the durable database with new
            # policy/backend/transport instances. The prior transport cannot serve recovery.
            shutil.copyfile(path, directory / "interrupted.sqlite")
            with closing(V5AttemptJournal(path)) as journal, closing(V5FakeBackend()) as backend:
                policy = _policy(case)
                recovery_transport = ScriptedTransport()
                try:
                    runner = V5Runner(
                        journal=journal,
                        manifest=scripted_policy_manifest(),
                        transport=recovery_transport,
                        policy=policy,
                        approved_caps=_caps(case),
                    )
                    recovered = runner.recover_step(
                        trial_id=record["trial_id"],
                        step_index=0,
                        task=generate_task(SEED),
                        backend=backend,
                    )
                    second = runner.recover_step(
                        trial_id=record["trial_id"],
                        step_index=0,
                        task=generate_task(SEED),
                        backend=backend,
                    )
                    record["recovery"] = {
                        "classification": recovered["classification"],
                        "second_classification": second["classification"],
                        "environment_actions": backend.action_count,
                        "additional_sends": len(recovery_transport.model_requests),
                        "additional_control_requests": len(recovery_transport.control_requests),
                    }
                finally:
                    policy.close()
        record["scripted_sends"] = len(transport.model_requests)
        record["scripted_control_requests"] = len(transport.control_requests)
        observations.append(record)
    write_private(
        run / "observations.json",
        canonical_json_bytes({"schema_version": SCHEMA, "cases": observations}),
    )


def _replay(journal: V5AttemptJournal, trial: str) -> dict[str, Any]:
    """Replay stored actions and compare every corresponding screenshot and host reward."""
    task = generate_task(SEED)
    backend = V5FakeBackend()
    env = PixelGuiEnv(
        backend, instruction=task.instruction, max_episode_steps=task.max_episode_steps
    )
    checked = 0
    rewards = []
    try:
        observation, _info = env.reset(seed=SEED)
        initial = journal.event(trial + "/initial_screenshot")
        if initial is None or observation.tobytes() != journal.get_object(
            initial.payload["screenshot_digest"]
        ):
            raise ValueError("initial screenshot replay mismatch")
        for event in journal.events(trial):
            if event.kind != "dispatch_committed":
                continue
            intent = journal.event(f"{trial}/step-{event.step_index:04d}/sealed_action_intent")
            if intent is None:
                raise ValueError("dispatch has no sealed action intent")
            payload = {
                key: value for key, value in intent.payload.items() if key != "sealed_intent_digest"
            }
            if content_digest(payload) != event.payload["sealed_intent_digest"]:
                raise ValueError("dispatch intent digest mismatch")
            action = json.loads(
                journal.get_object(intent.payload["action_digest"], expected_kind="sealed_action")
            )
            observation, reward, terminated, truncated, _info = env.step(action)
            if (
                observation.tobytes() != journal.get_object(event.payload["screenshot_digest"])
                or reward != event.payload["reward"]
                or terminated != event.payload["terminated"]
                or truncated != event.payload["truncated"]
                or backend.checkpoint()
                != journal.get_object(event.payload["environment_checkpoint_digest"])
            ):
                raise ValueError("stored dispatch does not match replay")
            rewards.append(reward)
            checked += 1
    finally:
        env.close()
    return {
        "replayed_dispatches": checked,
        "reward_sum": sum(rewards),
        "reward_one_count": rewards.count(1.0),
    }


def replay_campaign(run: Path) -> dict[str, Any]:
    """Verify stored actions against the fake environment; never run policies or transports."""
    value = json.loads((run / "observations.json").read_bytes())
    if value["schema_version"] != SCHEMA or [r["case"] for r in value["cases"]] != list(CASES):
        raise ValueError("validation case allocation changed")
    rows = []
    for record in value["cases"]:
        case, trial = record["case"], record["trial_id"]
        if trial != "retention-" + case or record["seed"] != SEED:
            raise ValueError("validation identity changed")
        with closing(
            V5AttemptJournal(run / "cases" / case / "attempts.sqlite", read_only=True)
        ) as journal:
            integrity = journal.integrity_report()
            replay = _replay(journal, trial)
            attempts, controls = journal.call_counts()
            if (
                attempts != record["scripted_sends"]
                or controls != 0
                or record["scripted_control_requests"] != 0
            ):
                raise ValueError("scripted request accounting mismatch")
            classification = record.get(
                "classification", record.get("episode", {}).get("classification")
            )
            if case == "golden":
                if classification != "success_termination" or replay["reward_one_count"] != 1:
                    raise ValueError("golden episode did not earn reward exactly once")
            elif case in {"premature_commit", "step_limit"}:
                if classification != "step_limit_truncation" or replay["reward_sum"] != 0:
                    raise ValueError("adversarial episode unexpectedly succeeded")
            elif case in {"malformed", "invalid_action"}:
                if classification != "invalid_output" or replay["replayed_dispatches"] != 0:
                    raise ValueError("invalid output reached dispatch")
            elif case == "call_cap":
                if (
                    classification != "call_cap_exceeded"
                    or attempts != 1
                    or replay["replayed_dispatches"] != 1
                ):
                    raise ValueError("call cap was not enforced before the second send")
            else:
                with closing(
                    V5AttemptJournal(run / "cases" / case / "interrupted.sqlite", read_only=True)
                ) as before:
                    if before.integrity_report() != record["interrupted_integrity"]:
                        raise ValueError("interrupted evidence changed")
                    if any(e.kind == "dispatch_started" for e in before.events(trial)):
                        raise ValueError("interruption fixture already dispatched")
                    if before.call_counts() != (attempts, controls):
                        raise ValueError("recovery duplicated a reserved request")
                if (
                    record["recovery"]
                    != {
                        "classification": "dispatched",
                        "second_classification": "already_committed",
                        "environment_actions": 1,
                        "additional_sends": 0,
                        "additional_control_requests": 0,
                    }
                    or replay["replayed_dispatches"] != 1
                ):
                    raise ValueError("recovery did not preserve exactly-once dispatch")
                classification = "interrupted_step_recovered"
            if "episode" in record:
                episode = record["episode"]
                if (
                    episode["environment_actions"] != replay["replayed_dispatches"]
                    or episode["model_attempts"] != attempts
                    or episode["success"] != (replay["reward_one_count"] == 1)
                ):
                    raise ValueError("stored episode projection disagrees with journal replay")
            rows.append(
                {
                    "case": case,
                    "classification": classification,
                    "scripted_sends": attempts,
                    "journal_integrity": integrity,
                    **replay,
                }
            )
    return {
        "schema_version": SCHEMA,
        "evidence_class": "credential-free-scripted-engineering-validation",
        "real_provider_requests": 0,
        "case_count": len(rows),
        "cases": rows,
        "observations_digest": content_digest(value),
        "milestone_verdict": None,
    }


def source_files(root: Path) -> dict[str, Path]:
    """Select only executable source and required assets, excluding caches and old evidence."""
    return {
        **{
            "pixelgym/" + name: path
            for name, path in _files(root / "pixelgym").items()
            if path.suffix in SOURCE_SUFFIXES
        },
        **{name: root / name for name in SOURCE_EXTRAS},
    }


def source_snapshot(root: Path, run: Path) -> dict[str, str]:
    """Retain executable source and required assets, excluding caches and old evidence."""
    source = run / "source"
    for name, path in source_files(root).items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(path, target)
    return {name: _hash(path) for name, path in _files(source).items()}


def verify_source(run: Path) -> dict[str, Any]:
    binding: dict[str, Any] = json.loads((run / "source-binding.json").read_bytes())
    actual = {name: _hash(path) for name, path in _files(run / "source").items()}
    if actual != binding["files"]:
        raise ValueError("retained source snapshot differs from binding")
    return binding


def regenerate_report(run: Path) -> dict[str, Any]:
    """Generate a report strictly from stored observations; no environment or test runs."""
    observations = json.loads((run / "observations.json").read_bytes())
    replay: dict[str, Any] = json.loads((run / "replay.json").read_bytes())
    binding = verify_source(run)
    if (
        replay["schema_version"] != SCHEMA
        or replay["observations_digest"] != content_digest(observations)
        or replay["case_count"] != len(CASES)
        or [r["case"] for r in replay["cases"]] != list(CASES)
    ):
        raise ValueError("stored replay observations do not match the campaign")
    return {**replay, "source_binding_digest": content_digest(binding)}


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Evidence-retention engineering validation",
        "",
        (
            "Generated from stored scripted observations. No model was called. This is not D5.9 "
            "replication, a model-quality measurement, or a milestone verdict."
        ),
        "",
        "| Case | Stored outcome | Scripted sends | Replayed dispatches | Reward sum |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in report["cases"]:
        lines.append(
            f"| {row['case']} | {row['classification']} | {row['scripted_sends']} | "
            f"{row['replayed_dispatches']} | {row['reward_sum']} |"
        )
    lines.extend(
        [
            "",
            (
                "The resume case verifies one interrupted step, recovery with fresh instances, "
                "and a second recovery without another dispatch. It is not a completed episode."
            ),
            "",
            (
                "Every replay compares stored screenshot bytes, semantic backend checkpoints, "
                "reward, termination, and truncation. Coverage is limited to these scripted "
                "fake-backend cases."
            ),
            "",
            (
                "Restricted journals, screenshots, and captured source are archived outside Git. "
                "The public bundle contains projections and digests; regenerate the "
                "credential-free "
                "campaign to inspect its complete evidence locally. Neither archive replaces the "
                "missing historical D5.9 journals."
            ),
            "",
        ]
    )
    return "\n".join(lines)
