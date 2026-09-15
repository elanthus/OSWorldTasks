"""Prepare and execute the approved 20-call focus/timeout repair diagnostic."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import numpy as np
from PIL import Image

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.grounding.v5.contracts import CallCaps, content_digest, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_focus_diagnostic import cases, prefix_actions, run_condition
from pixelgym.grounding.v5.memory_generator import COUNTERFACTUAL_SEEDS
from pixelgym.grounding.v5.policies import _append_golden_stage
from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger
from pixelgym.grounding.v5.request_budget_v2 import TRANSPORT_VERSION, IsolatedRequestBoundTransport
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    build_screenshot_policy_manifest,
)
from scripts.prepare_grounding_v5_memory import SOURCE_PATHS
from scripts.prepare_grounding_v5_memory import verify as verify_admission
from scripts.run_grounding_v5_gemini38_calibration import config_from_snapshot
from scripts.run_grounding_v5_memory_calibration import (
    JOURNAL,
    PRIVATE,
    ROOT,
    git,
    identity_failure,
    prefix_digest,
    read,
    write,
)

PUBLIC = ROOT / "artifacts/grounding-v5-d58-focus-diagnostic"
PREVIOUS = ROOT / "artifacts/grounding-v5-d58-gemini38-calibration"
PHASE = "d58-focus-timeout-diagnostic-v1"
CEILING = Decimal("20.00")
NEW_SOURCES = (
    "pixelgym/grounding/v5/request_budget_v2.py",
    "pixelgym/grounding/v5/memory_focus_backend.py",
    "pixelgym/grounding/v5/memory_focus_diagnostic.py",
    "scripts/run_grounding_v5_focus_diagnostic.py",
)


def apply(backend: MemoryBackend, action: dict[str, int]) -> None:
    if action["action_type"] == 1:
        backend.click(action["x"], action["y"])
    elif action["action_type"] == 2:
        backend.key(KEY_ALLOWLIST[action["key"]])


def diagnose() -> dict[str, Any]:
    """Read existing dispatch evidence; never expose provider text or task answers."""
    summary = read(PREVIOUS / "summary.json")
    rows = []
    with closing(sqlite3.connect(f"file:{JOURNAL}?mode=ro", uri=True)) as connection:

        def obj(digest: str) -> Any:
            return json.loads(
                connection.execute("SELECT data FROM objects WHERE digest=?", (digest,)).fetchone()[
                    0
                ]
            )

        for row in summary["conditions"]:
            if row["mode"] != "stateless" or row["classification"] != "step_limit_truncation":
                continue
            counts = {"text_stage_actions": 0, "clicks_on_focused_empty_input": 0, "key_actions": 0}
            keys = []
            for key, payload in connection.execute(
                "SELECT event_key,payload FROM events WHERE trial_id=? AND kind='sealed_action_intent' ORDER BY sequence",
                (row["trial_id"],),
            ):
                event = json.loads(payload)
                before = obj(event["environment_checkpoint_digest"])
                if before["stage_index"] != 1:
                    continue
                backend = MemoryBackend()
                try:
                    backend.restore(json.dumps(before).encode())
                    box = next(
                        c.bbox for c in backend.visible_controls() if c.control_id == "text_input"
                    )
                finally:
                    backend.close()
                action = obj(event["action_digest"])
                counts["text_stage_actions"] += 1
                counts["key_actions"] += int(action["action_type"] == 2)
                if (
                    action["action_type"] == 1
                    and before["focused"]
                    and before["text_value"] == ""
                    and box[0] <= action["x"] < box[2]
                    and box[1] <= action["y"] < box[3]
                ):
                    counts["clicks_on_focused_empty_input"] += 1
                    keys.append(key)
            rows.append(
                {
                    "trial_id": row["trial_id"],
                    "seed": row["seed"],
                    **counts,
                    "first_repeated_click_event": keys[0] if keys else None,
                }
            )
    value = {
        "schema_version": "pixelgym-d58-text-entry-diagnosis-v1",
        "source_summary_digest": content_digest(summary),
        "source_journal_prefix": summary["journal_integrity"],
        "episodes": rows,
        "totals": {
            name: sum(row[name] for row in rows)
            for name in ("text_stage_actions", "clicks_on_focused_empty_input", "key_actions")
        },
        "observation": "All stateless action-limit episodes repeatedly clicked an already-focused empty field. Focus changed only the fill; the old empty placeholder continued to instruct clicking.",
        "inference": "A clear visible focus cue may break the loop. This causal explanation requires the separate diagnostic; the traces alone do not prove it.",
        "provider_calls": 0,
    }
    write(PUBLIC / "diagnosis.json", value)
    return value


def admission() -> dict[str, Any]:
    """Check all old development golden states against the successor renderer."""
    rows = []
    for seed in (*range(5000, 5024), *COUNTERFACTUAL_SEEDS):
        old, new = MemoryBackend(), FocusMemoryBackend()
        try:
            old.reset(seed)
            new.reset(seed)
            planner = MemoryBackend()
            actions: list[dict[str, int]] = []
            try:
                planner.reset(seed)
                for stage in planner.task.stages:
                    _append_golden_stage(planner, stage, actions)
            finally:
                planner.close()
            changed_states = 0
            differing_pixels = 0
            maximum_channel_delta = 0
            outside_pixels = 0
            checkpoints_equal = True
            consumer_equal = True
            for action in actions:
                checkpoints_equal &= old.checkpoint() == new.checkpoint()
                if not checkpoints_equal:
                    raise ValueError("state checkpoints diverged")
                first, second = old.screenshot(), new.screenshot()
                delta = np.abs(first.astype(np.int16) - second.astype(np.int16))
                mask = np.any(delta != 0, axis=2)
                differing_pixels += int(mask.sum())
                maximum_channel_delta = max(maximum_channel_delta, int(delta.max()))
                if mask.any():
                    changed_states += 1
                    state = json.loads(old.checkpoint())
                    if not state["focused"]:
                        raise ValueError("renderer changed an unfocused state")
                    box = next(
                        c.bbox for c in old.visible_controls() if c.control_id == "text_input"
                    )
                    mask[box[1] : box[3] + 1, box[0] : box[2] + 1] = False
                    outside_pixels += int(mask.sum())
                    if outside_pixels:
                        raise ValueError("renderer changed pixels outside the input")
                if old.stage_index in (5, 7):
                    consumer_equal &= np.array_equal(first, second)
                    if not consumer_equal:
                        raise ValueError("consumer pixels changed")
                apply(old, action)
                apply(new, action)
            checkpoints_equal &= old.checkpoint() == new.checkpoint()
            submissions_equal = old.read_submissions() == new.read_submissions()
            if not checkpoints_equal or not submissions_equal:
                raise ValueError("final state or submissions diverged")
            rows.append(
                {
                    "seed": seed,
                    "actions": len(actions),
                    "changed_input_states": changed_states,
                    "summed_differing_pixels_before_confinement_check": differing_pixels,
                    "max_per_channel_delta_before_confinement_check": maximum_channel_delta,
                    "differing_pixels_outside_focused_input": outside_pixels,
                    "state_checkpoints_equal": checkpoints_equal,
                    "consumer_pixels_equal": consumer_equal,
                    "submissions_equal": submissions_equal,
                }
            )
        finally:
            old.close()
            new.close()
    for backend_type, name in ((MemoryBackend, "before"), (FocusMemoryBackend, "after")):
        backend = backend_type()
        try:
            backend.reset(5000)
            for action in prefix_actions(5000, "focused_empty"):
                apply(backend, action)
            Image.fromarray(backend.screenshot()).save(PUBLIC / f"focused-{name}.png")
        finally:
            backend.close()
    value = {
        "schema_version": "pixelgym-d58-focus-admission-v1",
        "backend_identity": FocusMemoryBackend.backend_identity,
        "tasks": rows,
        "provider_calls": 0,
        "confirmatory_tasks": 0,
        "scope": "72 development golden replays: exact state/submission equality; changed pixels confined to focused input; all consumer pixels identical to the admitted predecessor. Existing generator and deferred-feedback semantics are inherited unchanged.",
    }
    write(PUBLIC / "admission.json", value)
    return value


def source_binding() -> dict[str, str]:
    return {
        name: "sha256:" + sha256_bytes((ROOT / name).read_bytes())
        for name in (
            *SOURCE_PATHS,
            *NEW_SOURCES,
            "pixelgym/grounding/v5/memory_pilot.py",
            "pixelgym/grounding/v5/request_budget.py",
            "scripts/run_grounding_v5_gemini38_calibration.py",
            "scripts/run_grounding_v5_memory_calibration.py",
        )
    }


def canonical_plan() -> dict[str, Any]:
    verify_admission()
    previous = read(PREVIOUS / "summary.json")
    config = config_from_snapshot(read(PUBLIC / "price-recheck.json"))
    if not JOURNAL.exists():
        raise FileNotFoundError("original shared ledger required")
    with closing(V5AttemptJournal(JOURNAL)) as journal:
        count = previous["journal_integrity"]["event_count"]
        if prefix_digest(journal, count) != previous["journal_integrity"]["event_chain_digest"]:
            raise ValueError("closed cohort's journal prefix changed")
        prior_actions = sum(e.kind == "dispatch_started" for e in journal.events()[:count])
    jobs = [
        {"trial_id": f"{PHASE}-{case['case_id']}-{mode}", "case": case, "mode": mode}
        for index, case in enumerate(cases())
        for mode in (("history", "stateless") if index % 2 == 0 else ("stateless", "history"))
    ]
    revision = git("log", "-1", "--format=%H", "--", *NEW_SOURCES)
    value = {
        "schema_version": "pixelgym-d58-focus-diagnostic-plan-v1",
        "phase_id": PHASE,
        "owner_approval": "go ahead with the repair and diagnosis, the run the diagnostic",
        "execution_enabled": True,
        "aggregate_ceiling_usd": str(CEILING),
        "purpose": "10 scripted-prefix states x 2 matched modes, one model action per condition; 6 text-entry states and 4 deferred-choice states; no end-to-end or confirmatory execution",
        "jobs": jobs,
        "model_call_cap": 20,
        "provider_wire_call_cap": 20,
        "provider_retries": 0,
        "provider_control_call_cap": 0,
        "confirmatory_call_cap": 0,
        "scripted_action_cap": 2 * sum(c["scripted_prefix_action_count"] for c in cases()),
        "model_action_cap": 20,
        "maximum_new_request_reservations_usd": str(20 * config.request_maximum_usd),
        "prior_event_count": count,
        "prior_event_prefix_digest": previous["journal_integrity"]["event_chain_digest"],
        "prior_spend": previous["aggregate_spend"],
        "prior_model_attempts": previous["model_attempt_reservations"],
        "aggregate_caps": CallCaps(
            prior_actions + 20,
            previous["model_attempt_reservations"] + 20,
            0,
            previous["aggregate_spend"]["wire_requests_sent"] + 20,
        ).to_dict(),
        "source_digests": source_binding(),
        "driver_code_revision": revision,
        "policy_manifests": {
            mode: build_screenshot_policy_manifest(
                ROOT, config=config, code_revision=revision, retain_screenshots=mode == "history"
            ).to_dict()
            for mode in ("history", "stateless")
        },
        "transport_version": TRANSPORT_VERSION,
        "backend_identity": FocusMemoryBackend.backend_identity,
        "price_snapshot_digest": content_digest(read(PUBLIC / "price-recheck.json")),
        "diagnosis_digest": content_digest(read(PUBLIC / "diagnosis.json")),
        "admission_digest": content_digest(read(PUBLIC / "admission.json")),
        "stop_rules": [
            "20 assigned calls completed",
            "any runner-abandoned active send retires the phase transport and stops the phase",
            "five consecutive conditions without a dispatched model action",
            "provider identity or price violation",
            "next reservation would exceed USD 20 aggregate",
        ],
        "interruption_rule": "never resend a started condition; preserve prefix, response, failed and unrun conditions; closed phases cannot restart",
        "unknown_charge_rule": "retain request-sized holds until billing evidence resolves them; no zero-charge assumption",
    }
    return {**value, "execution_plan_digest": content_digest(value)}


def summary(
    journal: V5AttemptJournal, ledger: ReboundedMemoryLedger, plan: dict[str, Any], stop: str
) -> dict[str, Any]:
    rows = []
    for job in plan["jobs"]:
        event = journal.event(f"{job['trial_id']}/result")
        rows.append(
            event.payload
            if event
            else {
                **job["case"],
                "trial_id": job["trial_id"],
                "mode": job["mode"],
                "classification": "not_run",
                "model_attempted": any(
                    e.kind == "attempt_started" for e in journal.events(job["trial_id"])
                ),
                "action_dispatched": False,
                "desired_transition": False,
                "valid_memory_choice": False,
                "correct_memory_choice": False,
            }
        )
    scores = {}
    for mode in ("history", "stateless"):
        selected = [r for r in rows if r["mode"] == mode]
        text = [r for r in selected if not r["state_name"].startswith("memory_")]
        memory = [r for r in selected if r["state_name"].startswith("memory_")]
        scores[mode] = {
            "assigned": 10,
            "attempted": sum(r["model_attempted"] for r in selected),
            "text_transitions": sum(r["desired_transition"] for r in text),
            "text_assigned": 6,
            "memory_valid": sum(r["valid_memory_choice"] for r in memory),
            "memory_correct": sum(r["correct_memory_choice"] for r in memory),
            "memory_assigned": 4,
        }
    return {
        "schema_version": "pixelgym-d58-focus-diagnostic-summary-v1",
        "execution_plan_digest": plan["execution_plan_digest"],
        "conditions": rows,
        "scores": scores,
        "stop_reason": stop,
        "aggregate_spend": ledger.to_dict(),
        "new_known_spend_usd": str(ledger.spent_usd - Decimal(plan["prior_spend"]["spent_usd"])),
        "new_wire_requests": ledger.wire_requests_sent - plan["prior_spend"]["wire_requests_sent"],
        "journal_integrity": journal.integrity_report(),
        "confirmatory_tasks_evaluated": 0,
    }


def publish(value: dict[str, Any]) -> None:
    write(PUBLIC / "summary.json", value)
    lines = [
        "# D5.8 focus and timeout repair diagnostic",
        "",
        "Scripted prefixes supplied every test state. These single-action checks do not measure end-to-end memory exposure or terminal success.",
        "",
        "| Mode | Attempted / 10 | Desired text transition / 6 | Valid memory choices / 4 | Correct memory choices / 4 |",
        "|---|---:|---:|---:|---:|",
    ]
    for mode, score in value["scores"].items():
        lines.append(
            f"| {mode} | {score['attempted']} | {score['text_transitions']} | {score['memory_valid']} | {score['memory_correct']} |"
        )
    lines += [
        "",
        f"Stop: `{value['stop_reason']}`. New requests: {value['new_wire_requests']}. New known charges: USD {value['new_known_spend_usd']}.",
        "",
        f"Aggregate accounting: `{json.dumps(value['aggregate_spend'], sort_keys=True)}`. The shared ceiling remains USD 20; holds are not confirmed charges.",
        "",
        "All failures and unrun assignments remain in [the summary](summary.json). [Execution plan](execution-plan.json), [historical diagnosis](diagnosis.json), [development renderer checks](admission.json), [before](focused-before.png), [after](focused-after.png).",
        "",
        "Repeated development seeds and supplied prefixes make this a diagnostic, not an independent memory-effect estimate. The old stopped cohort is unchanged. Full calibration and final D5.8 approval remain open.",
        "",
    ]
    (PUBLIC / "report.md").write_text("\n".join(lines))


def execute(digest: str) -> None:
    plan = read(PUBLIC / "execution-plan.json")
    if plan != canonical_plan() or digest != plan["execution_plan_digest"]:
        raise ValueError("diagnostic differs from its frozen execution plan")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before calls")
    for name in (*NEW_SOURCES, str((PUBLIC / "execution-plan.json").relative_to(ROOT))):
        git("ls-files", "--error-unmatch", name)
    snapshot = read(PUBLIC / "price-recheck.json")
    age = (datetime.now(UTC) - datetime.fromisoformat(snapshot["observed_at"])).total_seconds()
    if not 0 <= age <= 86400:
        raise ValueError("price snapshot must be less than 24 hours old")
    config = config_from_snapshot(snapshot)
    with (PRIVATE / "operator.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = V5AttemptJournal(JOURNAL)
        transport = None
        try:
            ledger = ReboundedMemoryLedger(CEILING, Decimal(0), journal=journal)
            started = journal.event(f"{PHASE}/started")
            binding = {"execution_plan_digest": digest}
            if started is None:
                if (
                    ledger.to_dict() != plan["prior_spend"]
                    or len(journal.events()) != plan["prior_event_count"]
                ):
                    raise ValueError("shared ledger differs from carry-forward")
                journal.append_event(
                    event_key=f"{PHASE}/started",
                    kind="focus_phase_started",
                    trial_id=PHASE,
                    step_index=0,
                    payload=binding,
                )
            elif started.payload != binding:
                raise ValueError("phase has another binding")
            closed = journal.event(f"{PHASE}/closed")
            stop = closed.payload["stop_reason"] if closed else "interrupted"
            if closed is None:
                transport = IsolatedRequestBoundTransport(config, lifecycle_id=PHASE, ledger=ledger)
                stop, failures = "all_assignments_completed", 0
                try:
                    for job in plan["jobs"]:
                        if transport.retired:
                            stop = "transport_retired_after_deadline"
                            break
                        if (
                            ledger.blocked
                            or ledger.budget_accounted_spend_usd + config.request_maximum_usd
                            > CEILING
                        ):
                            stop = "aggregate_budget_stop"
                            break
                        mode = job["mode"]
                        manifest = build_screenshot_policy_manifest(
                            ROOT,
                            config=config,
                            code_revision=plan["driver_code_revision"],
                            retain_screenshots=mode == "history",
                        )
                        row = run_condition(
                            journal,
                            case=job["case"],
                            trial_id=job["trial_id"],
                            mode=mode,
                            policy=ScreenshotMemoryPolicy(
                                config, retain_screenshots=mode == "history"
                            ),
                            manifest=manifest,
                            transport=transport,
                            caps=CallCaps(**plan["aggregate_caps"]),
                            plan_digest=digest,
                        )
                        print(
                            json.dumps(
                                {
                                    "event": "diagnostic_condition",
                                    "case": job["case"]["state_name"],
                                    "mode": mode,
                                    "classification": row["classification"],
                                    "desired_transition": row["desired_transition"],
                                    "known_spend_usd": str(ledger.spent_usd),
                                }
                            ),
                            flush=True,
                        )
                        if identity_failure(journal, job["trial_id"], config) or ledger.blocked:
                            stop = "provider_identity_or_price_guard_failure"
                            break
                        if transport.retired:
                            stop = "transport_retired_after_deadline"
                            break
                        failures = 0 if row["action_dispatched"] else failures + 1
                        if failures >= 5:
                            stop = "five_consecutive_non_normal_conditions"
                            break
                    journal.append_event(
                        event_key=f"{PHASE}/closed",
                        kind="focus_phase_closed",
                        trial_id=PHASE,
                        step_index=0,
                        payload={"stop_reason": stop},
                    )
                except BaseException:
                    stop = "interrupted"
                    raise
                finally:
                    # Never close a journal underneath a still-running worker.
                    # After a bounded drain, process exit terminates the daemon;
                    # the provider charge remains unknown unless a receipt arrived.
                    transport.wait_until_idle(5)
                    publish(summary(journal, ledger, plan, stop))
            else:
                publish(summary(journal, ledger, plan, stop))
        finally:
            if transport is None or transport.wait_until_idle():
                journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("diagnose", "admission", "prepare", "execute", "report")
    )
    parser.add_argument("--approved-plan-digest")
    args = parser.parse_args()
    PUBLIC.mkdir(exist_ok=True)
    if args.command == "diagnose":
        print(json.dumps(diagnose()["totals"]))
    elif args.command == "admission":
        print(json.dumps({"development_tasks": len(admission()["tasks"])}))
    elif args.command == "prepare":
        plan = canonical_plan()
        path = PUBLIC / "execution-plan.json"
        if path.exists() and read(path) != plan:
            raise ValueError("refusing to overwrite frozen diagnostic plan")
        write(path, plan)
        print(
            json.dumps(
                {
                    k: plan[k]
                    for k in (
                        "execution_plan_digest",
                        "model_call_cap",
                        "maximum_new_request_reservations_usd",
                    )
                }
            )
        )
    elif args.command == "report":
        publish(read(PUBLIC / "summary.json"))
    elif args.approved_plan_digest:
        os.umask(0o077)
        execute(args.approved_plan_digest)
    else:
        parser.error("execute requires --approved-plan-digest")


if __name__ == "__main__":
    main()
