"""Admit, freeze, execute, and report the approved full D5.8 matched calibration."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
from collections import defaultdict
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.admission import validate_task_admission
from pixelgym.grounding.v5.contracts import CallCaps, content_digest, sha256_bytes
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_calibration import MemoryCalibrationLedger, run_episode, summarize
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.memory_plan import TOTAL_REPAIR_BUDGET_USD, config_from_price_snapshot
from pixelgym.grounding.v5.panel_policy import OpenRouterPanelTransport, SpendLedger
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    build_screenshot_policy_manifest,
)
from scripts.prepare_grounding_v5_memory import source_digests
from scripts.prepare_grounding_v5_memory import verify as verify_admission

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "artifacts/grounding-v5-d58-full-calibration"
PILOT = ROOT / "artifacts/grounding-v5-d58-calibration-pilot"
CANDIDATE = ROOT / "artifacts/grounding-v5-d58-design/memory-repair/pilot-plan.json"
SEED_SOURCE = ROOT / "artifacts/grounding-v5-d56-gemini-v3-full-calibration-plan.json"
PRIVATE = ROOT / ".cache/d58-memory-calibration"
JOURNAL = PRIVATE / "aggregate.sqlite"
PHASE = "d58-full-memory-calibration-v1"
DRIVERS = (
    "pixelgym/grounding/v5/memory_calibration.py",
    "scripts/run_grounding_v5_memory_calibration.py",
)


def read(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text()))


def write(path: Path, value: dict[str, Any]) -> None:
    validate_credential_free(value)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def ordered_seeds() -> list[int]:
    # Reuse every seed in the prior full calibration. Only scheduling changes:
    # round-robin families prevent a budget stop from selecting one family alone.
    groups: dict[str, list[int]] = defaultdict(list)
    for row in read(SEED_SOURCE)["task_order"]:
        groups[row["family"]].append(row["seed"])
    return [
        group[index]
        for index in range(max(map(len, groups.values())))
        for group in groups.values()
        if index < len(group)
    ]


def admit() -> None:
    verify_admission()
    path = PUBLIC / "admission.json"
    if path.exists():
        verify_full_admission()
        return
    binding = content_digest(source_digests())
    records = []
    for index, seed in enumerate(ordered_seeds()):
        task = generate_memory_task(seed)
        result = validate_task_admission(task, backend_factory=MemoryBackend)
        records.append(
            {
                "seed": seed,
                "task_id": task.task_id,
                "task_digest": content_digest(task.canonical_dict()),
                "action_limit": task.max_episode_steps,
                "seed_record": task.seed_record.to_dict(),
                "admission": result,
            }
        )
        if (index + 1) % 5 == 0:
            print(
                json.dumps({"admitted": index + 1, "assigned_tasks": 50, "provider_calls": 0}),
                flush=True,
            )
    if binding != content_digest(source_digests()):
        raise ValueError("frozen source changed during full admission")
    value = {
        "schema_version": "pixelgym-d58-full-memory-admission-v1",
        "source_binding_digest": binding,
        "seed_source_digest": content_digest(read(SEED_SOURCE)),
        "tasks": records,
        "provider_calls": 0,
        "confirmatory_tasks_generated": 0,
    }
    write(path, {**value, "admission_digest": content_digest(value)})


def verify_full_admission() -> dict[str, Any]:
    value = read(PUBLIC / "admission.json")
    if value["admission_digest"] != content_digest(
        {k: v for k, v in value.items() if k != "admission_digest"}
    ):
        raise ValueError("full admission artifact changed")
    if value["source_binding_digest"] != content_digest(source_digests()):
        raise ValueError("full admission source binding changed")
    if [row["seed"] for row in value["tasks"]] != ordered_seeds():
        raise ValueError("full admission seed order changed")
    for row in value["tasks"]:
        task = generate_memory_task(row["seed"])
        if (
            row["task_digest"] != content_digest(task.canonical_dict())
            or row["action_limit"] != task.max_episode_steps
        ):
            raise ValueError("full admission task changed")
    return value


def prefix_digest(journal: V5AttemptJournal, count: int) -> str:
    events = journal.events()
    if len(events) < count:
        raise ValueError("aggregate journal lost its pilot prefix")
    return content_digest([asdict(event) for event in events[:count]])


def canonical_plan() -> dict[str, Any]:
    verify_admission()
    admission = verify_full_admission()
    candidate = read(CANDIDATE)
    pilot = read(PILOT / "summary.json")
    recheck = read(PUBLIC / "price-recheck.json")
    config = config_from_price_snapshot(recheck)
    old_config = config_from_price_snapshot(
        read(ROOT / "artifacts/grounding-v5-d58-design/gemini-price-snapshot.json")
    )
    if config != old_config:
        raise ValueError("provider prices or context bounds differ from frozen policies")
    if not JOURNAL.exists():
        raise RuntimeError(
            "the existing aggregate pilot ledger is required; no new budget is permitted"
        )
    with closing(V5AttemptJournal(JOURNAL)) as journal:
        prefix = prefix_digest(journal, pilot["journal_integrity"]["event_count"])
        if (
            journal.event(f"{PHASE}/started") is None
            and journal.integrity_report() != pilot["journal_integrity"]
        ):
            raise ValueError("aggregate ledger differs from completed pilot evidence")
    jobs = []
    for index, row in enumerate(admission["tasks"]):
        for mode in ("history", "stateless") if index % 2 == 0 else ("stateless", "history"):
            jobs.append(
                {
                    "trial_id": f"{PHASE}-{row['seed']}-{mode}",
                    "mode": mode,
                    **{
                        key: row[key]
                        for key in ("seed", "task_id", "task_digest", "action_limit", "seed_record")
                    },
                }
            )
    actions = sum(job["action_limit"] for job in jobs)
    prior_calls = pilot["model_attempt_reservations"]
    pilot_actions = read(PILOT / "execution-plan.json")["caps"]["environment_action_cap"]
    value = {
        "schema_version": "pixelgym-d58-full-memory-calibration-plan-v1",
        "phase_id": PHASE,
        "owner_approval": "ok, please proceed with the full run",
        "approval_scope": "full end-to-end matched Gemini calibration using the remaining existing USD 5 aggregate ceiling; no Qwen, Mistral, reliability or confirmatory calls",
        "execution_enabled": True,
        "jobs": jobs,
        "assigned_episodes": len(jobs),
        "admission_digest": admission["admission_digest"],
        "candidate_plan_digest": candidate["plan_digest"],
        "policy_manifests": candidate["policy_manifests"],
        "driver_code_revision": git("log", "-1", "--format=%H", "--", *DRIVERS),
        "driver_source_digest": content_digest(
            {name: "sha256:" + sha256_bytes((ROOT / name).read_bytes()) for name in DRIVERS}
        ),
        "price_recheck_digest": content_digest(recheck),
        "phase_caps": CallCaps(actions, actions, 0, actions).to_dict(),
        "aggregate_caps": CallCaps(
            pilot_actions + actions, prior_calls + actions, 0, prior_calls + actions
        ).to_dict(),
        "aggregate_ceiling_usd": str(TOTAL_REPAIR_BUDGET_USD),
        "prior_pilot_spend": pilot["spend"],
        "pilot_summary_digest": content_digest(pilot),
        "pilot_ledger_prefix_digest": prefix,
        "pilot_ledger_event_count": pilot["journal_integrity"]["event_count"],
        "per_request_reservation_usd": str(config.request_maximum_usd),
        "unknown_charge_reservation_rule": "full request maximum, irrespective of prior observed charges",
        "maximum_phase_cost_without_aggregate_guard_usd": str(actions * config.request_maximum_usd),
        "pilot_average_request_cost_projection_usd": str(
            Decimal(pilot["spend"]["spent_usd"]) * actions / pilot["spend"]["wire_requests_sent"]
        ),
        "completion_within_budget_guaranteed": False,
        "measurements": "host evaluator terminal success; reached both consumers; first model attempt at each consumer including misses/invalid outputs as incorrect; all assignments retained",
        "analysis_rule": "report incomplete coverage explicitly; do not estimate confirmatory power from a budget-truncated calibration; twins remain logical clusters",
        "ordering": "round-robin families from the previous fifty-seed calibration; alternate first policy by task index; sequential requests",
        "stop_rules": [
            "before any request whose full reservation exceeds shared USD 5 ceiling",
            "phase call/action caps",
            "five consecutive non-normal episode results",
            "provider identity or price guard mismatch",
            "evidence integrity failure",
        ],
        "interruption_rule": "retain terminal committed outcomes; classify other interrupted episodes as failed without restarting or resending; retain unknown/in-flight holds",
        "provider_retries": 0,
        "confirmatory_call_cap": 0,
    }
    return {**value, "execution_plan_digest": content_digest(value)}


def verify_ledger(journal: V5AttemptJournal, ledger: SpendLedger, plan: dict[str, Any]) -> None:
    if (
        prefix_digest(journal, plan["pilot_ledger_event_count"])
        != plan["pilot_ledger_prefix_digest"]
    ):
        raise ValueError("pilot ledger prefix does not match approved lineage")
    pilot_binding = read(PILOT / "ledger-started.json")
    original = journal.event("d58-memory-pilot-v1/ledger_initialized")
    if original is None or original.payload != pilot_binding:
        raise ValueError("aggregate pilot ledger identity mismatch")
    started = journal.event(f"{PHASE}/started")
    binding = {
        "execution_plan_digest": plan["execution_plan_digest"],
        "pilot_ledger_prefix_digest": plan["pilot_ledger_prefix_digest"],
        "aggregate_ceiling_usd": plan["aggregate_ceiling_usd"],
    }
    if started is None:
        if ledger.to_dict() != plan["prior_pilot_spend"]:
            raise ValueError("unrecognized spending before the approved full phase")
        journal.append_event(
            event_key=f"{PHASE}/started",
            kind="memory_full_phase_started",
            trial_id=PHASE,
            step_index=0,
            payload=binding,
        )
    elif started.payload != binding:
        raise ValueError("full calibration phase has a different approval binding")


def identity_failure(journal: V5AttemptJournal, trial_id: str, config: Any) -> bool:
    for event in journal.events(trial_id):
        if event.kind != "canonical_response_persisted":
            continue
        response = json.loads(
            journal.get_object(
                event.payload["canonical_response_digest"],
                expected_kind="canonical_provider_response",
            )
        )
        usage = response.get("usage", {})
        if (
            response.get("model") != config.model
            or str(usage.get("upstream_provider", "")).lower() != config.response_provider.lower()
            or usage.get("price_guard") != "ok"
        ):
            return True
    return False


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# D5.8 full end-to-end memory calibration",
        "",
        f"Run complete: **{summary['complete']}**. Stop reason: `{summary['stop_reason']}`.",
        "",
        "| Mode | Assigned | Attempted episodes | Terminal successes | Reached both consumers | Correct first memory attempts / attempted |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mode, row in sorted(summary["scores"].items()):
        lines.append(
            f"| {mode} | {row['assigned']} | {row['attempted_episodes']} | {row['terminal_successes']} | {row['reached_both_consumers']} | {row['correct_first_memory_attempts']} / {row['first_memory_attempts']} |"
        )
    spend = summary["aggregate_spend"]
    lines += [
        "",
        f"Aggregate known spend (including the prior pilot): **USD {spend['spent_usd']}**. Unknown reservations: **USD {spend['unknown_reservation_usd']}**. In-flight reservations: **USD {spend['in_flight_reservation_usd']}**. Total wire requests including the pilot: **{spend['wire_requests_sent']}**. Shared ceiling: **USD 5.00**.",
        "",
        "Every assignment remains in the denominator; unrun assignments are explicit. Failures, missed clicks and invalid responses are retained. This phase starts each episode at reset and uses no scripted prefix. The frozen policies share all settings except screenshot retention.",
        "",
        "A budget-truncated campaign cannot establish complete calibration rates, paired terminal discordance, or confirmatory power. D5.8 final approval remains open; no confirmatory task was evaluated.",
        "",
        "[Stored summary](summary.json), [execution approval](execution-plan.json), [task admission](admission.json), [price recheck](price-recheck.json). Provider response text, screenshots and checkpoints remain in the existing ignored authoritative aggregate journal.",
        "",
    ]
    return "\n".join(lines)


def publish(summary: dict[str, Any]) -> None:
    write(PUBLIC / "summary.json", summary)
    (PUBLIC / "report.md").write_text(render_report(read(PUBLIC / "summary.json")))


def execution_amendment(original: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    mutable = {"driver_source_digest", "driver_code_revision", "execution_plan_digest"}
    if {k: v for k, v in original.items() if k not in mutable} != {
        k: v for k, v in current.items() if k not in mutable
    }:
        raise ValueError("an execution amendment cannot change tasks, policies, prices or caps")
    value = {
        "schema_version": "pixelgym-d58-execution-amendment-v1",
        "original_execution_plan_digest": original["execution_plan_digest"],
        "original_driver_source_digest": original["driver_source_digest"],
        "driver_source_digest": current["driver_source_digest"],
        "driver_code_revision": current["driver_code_revision"],
        "reason": "Correct the canonical_provider_response object-role check in post-episode auditing; preserve every request, outcome and unknown-charge hold already recorded",
        "approval_scope": "implementation repair within the owner's approved full run; no change to model policies, task assignments, retry rules or shared USD 5 cap",
    }
    return {**value, "amendment_digest": content_digest(value)}


def execute(digest: str) -> None:
    plan = read(PUBLIC / "execution-plan.json")
    current = canonical_plan()
    if digest != plan["execution_plan_digest"]:
        raise ValueError("full execution plan differs from its approval")
    amendment = None
    if current != plan:
        amendment = execution_amendment(plan, current)
        if read(PUBLIC / "execution-amendment-1.json") != amendment:
            raise ValueError("runtime driver differs from its recorded execution amendment")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before requests")
    for name in (*DRIVERS, "artifacts/grounding-v5-d58-full-calibration/execution-plan.json"):
        git("ls-files", "--error-unmatch", name)
    age = (
        datetime.now(UTC)
        - datetime.fromisoformat(read(PUBLIC / "price-recheck.json")["observed_at"])
    ).total_seconds()
    if not 0 <= age <= 86400:
        raise ValueError("a price recheck within 24 hours is required")
    if not JOURNAL.exists():
        raise RuntimeError("authoritative aggregate ledger missing; no replacement allowed")
    with (PRIVATE / "operator.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(V5AttemptJournal(JOURNAL)) as journal:
            ledger = MemoryCalibrationLedger(TOTAL_REPAIR_BUDGET_USD, Decimal(0), journal=journal)
            verify_ledger(journal, ledger, plan)
            if amendment is not None:
                journal.append_event(
                    event_key=f"{PHASE}/execution-amendment-1",
                    kind="memory_execution_amendment",
                    trial_id=PHASE,
                    step_index=0,
                    payload=amendment,
                )
            caps = CallCaps(**plan["aggregate_caps"])
            config = config_from_price_snapshot(read(PUBLIC / "price-recheck.json"))
            closed = journal.event(f"{PHASE}/closed")
            stop = closed.payload["stop_reason"] if closed else "interrupted"
            failures = 0
            transports: dict[str, OpenRouterPanelTransport] = {}
            try:
                if closed is None:
                    stop = "all_assignments_completed"
                    for job in plan["jobs"]:
                        completed = journal.event(f"{job['trial_id']}/full_completed")
                        if (
                            completed is None
                            and journal.event(f"{job['trial_id']}/full_started") is None
                            and (
                                ledger.blocked
                                or ledger.budget_accounted_spend_usd + config.request_maximum_usd
                                > ledger.maximum_spend_usd
                            )
                        ):
                            stop = "aggregate_budget_stop"
                            break
                        mode = job["mode"]
                        manifest = build_screenshot_policy_manifest(
                            ROOT,
                            config=config,
                            code_revision=plan["policy_manifests"][mode]["code_revision"],
                            retain_screenshots=mode == "history",
                        )
                        if manifest.to_dict() != plan["policy_manifests"][mode]:
                            raise ValueError("frozen policy changed")
                        if mode not in transports:
                            transports[mode] = OpenRouterPanelTransport(config, ledger=ledger)

                        def progress(name: str, trial_id: str = job["trial_id"]) -> None:
                            if name == "attempt_terminal":
                                print(
                                    json.dumps(
                                        {
                                            "event": "request_settled",
                                            "trial_id": trial_id,
                                            "aggregate_requests": ledger.wire_requests_sent,
                                            "known_spend_usd": str(ledger.spent_usd),
                                        }
                                    ),
                                    flush=True,
                                )

                        row = run_episode(
                            journal,
                            job=job,
                            manifest=manifest,
                            policy=ScreenshotMemoryPolicy(
                                config, retain_screenshots=mode == "history"
                            ),
                            transport=transports[mode],
                            ledger=ledger,
                            caps=caps,
                            plan_digest=digest,
                            boundary=progress,
                        )
                        print(
                            json.dumps(
                                {
                                    "event": "episode_recorded",
                                    "seed": row["seed"],
                                    "mode": mode,
                                    "classification": row["classification"],
                                    "success": row["success"],
                                    "reached_consumers": row["reached_consumers"],
                                    "spend": ledger.to_dict(),
                                }
                            ),
                            flush=True,
                        )
                        if identity_failure(journal, job["trial_id"], config):
                            stop = "provider_identity_or_price_guard_failure"
                            break
                        if ledger.blocked or row["classification"] == "budget_stop":
                            stop = "aggregate_budget_stop"
                            break
                        failures = (
                            0
                            if row["classification"]
                            in ("success_termination", "step_limit_truncation")
                            else failures + 1
                        )
                        if failures >= 5:
                            stop = "five_consecutive_non_normal_episodes"
                            break
                    journal.append_event(
                        event_key=f"{PHASE}/closed",
                        kind="memory_full_phase_closed",
                        trial_id=PHASE,
                        step_index=0,
                        payload={"stop_reason": stop},
                    )
            except BaseException:
                stop = "interrupted"
                raise
            finally:
                summary = summarize(journal, ledger, plan, stop_reason=stop)
                summary["execution_amendments"] = [] if amendment is None else [amendment]
                summary["effective_driver_code_revision"] = current["driver_code_revision"]
                publish(summary)
                print(
                    json.dumps(
                        {
                            "scores": summary["scores"],
                            "stop_reason": stop,
                            "aggregate_spend": ledger.to_dict(),
                        }
                    ),
                    flush=True,
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("admit", "prepare", "prepare-amendment", "execute", "report")
    )
    parser.add_argument("--approved-plan-digest")
    args = parser.parse_args()
    if args.command == "admit":
        admit()
    elif args.command == "prepare":
        plan = canonical_plan()
        path = PUBLIC / "execution-plan.json"
        if path.exists() and read(path) != plan:
            raise ValueError("refusing to replace a frozen full calibration plan")
        write(path, plan)
        print(
            json.dumps(
                {
                    key: plan[key]
                    for key in (
                        "execution_plan_digest",
                        "assigned_episodes",
                        "phase_caps",
                        "aggregate_ceiling_usd",
                        "pilot_average_request_cost_projection_usd",
                    )
                }
            )
        )
    elif args.command == "prepare-amendment":
        amendment = execution_amendment(read(PUBLIC / "execution-plan.json"), canonical_plan())
        path = PUBLIC / "execution-amendment-1.json"
        if path.exists() and read(path) != amendment:
            raise ValueError("refusing to replace the recorded execution amendment")
        write(path, amendment)
        print(json.dumps(amendment))
    elif args.command == "report":
        publish(read(PUBLIC / "summary.json"))
    elif args.approved_plan_digest:
        old_umask = os.umask(0o077)
        try:
            execute(args.approved_plan_digest)
        finally:
            os.umask(old_umask)
    else:
        parser.error("execute requires --approved-plan-digest")


if __name__ == "__main__":
    main()
