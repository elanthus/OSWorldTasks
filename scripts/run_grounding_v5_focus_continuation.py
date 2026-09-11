"""Continue the 95 untouched focus-calibration assignments under USD 28 total."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pixelgym.grounding.v5.contracts import CallCaps, content_digest, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_calibration import summarize
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_focus_calibration import run_episode
from pixelgym.grounding.v5.memory_plan import ScreenshotPriceConfig
from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger
from pixelgym.grounding.v5.request_budget_v2 import TRANSPORT_VERSION, IsolatedRequestBoundTransport
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    build_screenshot_policy_manifest,
)
from pixelgym.grounding.v5.transport_diagnostics import DIAGNOSTICS_VERSION, DiagnosticUrlopen
from scripts.run_grounding_v5_focus_calibration import source_binding as previous_source_binding
from scripts.run_grounding_v5_gemini38_calibration import config_from_snapshot as vertex_config
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

PUBLIC = ROOT / "artifacts/grounding-v5-d58-focus-continuation"
PREVIOUS = ROOT / "artifacts/grounding-v5-d58-focus-calibration"
APPROVAL = ROOT / "artifacts/grounding-v5-d58-budget-amendment/approval.json"
PHASE = "d58-focus-memory-continuation-v1"
CEILING = Decimal("28.00")
NEW_SOURCES = (
    "pixelgym/grounding/v5/transport_diagnostics.py",
    "scripts/run_grounding_v5_focus_continuation.py",
)


def config_from_snapshot(snapshot: dict[str, Any]) -> ScreenshotPriceConfig:
    selected = [row for row in snapshot["endpoints"] if row["tag"] == "google-vertex/global"]
    if len(selected) != 1:
        raise ValueError("exactly one approved standard Vertex endpoint required")
    return vertex_config({**snapshot, "endpoints": selected})


def source_binding() -> dict[str, str]:
    return {
        **previous_source_binding(),
        **{
            name: "sha256:" + sha256_bytes((ROOT / name).read_bytes())
            for name in (*NEW_SOURCES, "pixelgym/grounding/v5/memory_calibration.py")
        },
    }


def continuation_assignments(
    old: dict[str, Any], previous: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if len(old["jobs"]) != len(previous["conditions"]) or any(
        any(row.get(k) != v for k, v in job.items())
        for job, row in zip(old["jobs"], previous["conditions"], strict=True)
    ):
        raise ValueError("previous assignment identities differ from stored outcomes")
    preserved = [row for row in previous["conditions"] if row["classification"] != "not_run"]
    if len(preserved) != 5 or any(
        row["classification"] != "infrastructure_failure" for row in preserved
    ):
        raise ValueError("expected exactly the five preserved failed episodes")
    preserved_ids = {row["trial_id"] for row in preserved}
    jobs = [
        job
        if job["trial_id"] in preserved_ids
        else {**job, "trial_id": f"{PHASE}-{job['seed']}-{job['mode']}"}
        for job in old["jobs"]
    ]
    execution_jobs = [job for job in jobs if job["trial_id"] not in preserved_ids]
    if len(jobs) != 100 or len(execution_jobs) != 95:
        raise ValueError("continuation assignment count changed")
    return jobs, execution_jobs, preserved


def canonical_plan() -> dict[str, Any]:
    previous = read(PREVIOUS / "summary.json")
    old = read(PREVIOUS / "execution-plan.json")
    approval = read(APPROVAL)
    if (
        approval["new_aggregate_cap_usd"] != str(CEILING)
        or approval["assignment_selection"] != "Retain five results; run remaining 95 (recommended)"
    ):
        raise ValueError("continuation differs from owner authorization")
    if approval["closed_source_summary_digest"] != content_digest(previous):
        raise ValueError("authorization identifies a different closed cohort")
    sources = source_binding()
    for name, digest in old["source_digests"].items():
        if sources[name] != digest:
            raise ValueError(f"executed predecessor source changed: {name}")
    if not JOURNAL.exists():
        raise FileNotFoundError("original shared ledger required")
    count = previous["journal_integrity"]["event_count"]
    with closing(V5AttemptJournal(JOURNAL)) as journal:
        prefix = prefix_digest(journal, count)
        if prefix != previous["journal_integrity"]["event_chain_digest"]:
            raise ValueError("closed calibration prefix changed")
        events = journal.events()[:count]
        prior_actions = sum(e.kind == "dispatch_started" for e in events)
        prior_attempts = sum(e.kind == "attempt_started" for e in events)
    jobs, execution_jobs, preserved = continuation_assignments(old, previous)
    actions = sum(job["action_limit"] for job in execution_jobs)
    revision = git("log", "-1", "--format=%H", "--", *NEW_SOURCES)
    snapshot = read(PUBLIC / "price-recheck.json")
    config = config_from_snapshot(snapshot)
    policies = {
        mode: build_screenshot_policy_manifest(
            ROOT,
            config=config,
            code_revision=old["policy_manifests"][mode]["code_revision"],
            retain_screenshots=mode == "history",
        ).to_dict()
        for mode in ("history", "stateless")
    }
    if policies != old["policy_manifests"]:
        raise ValueError("continued cohort requires identical candidate policy manifests")
    value = {
        "schema_version": "pixelgym-d58-focus-continuation-plan-v1",
        "phase_id": PHASE,
        "owner_approval": approval["owner_approval"],
        "assignment_approval": approval["assignment_selection"],
        "approval_digest": content_digest(approval),
        "approval_scope": "retain five failed episodes and execute only 95 untouched assignments; USD 28 aggregate including all previous D5.8 charges and holds; no confirmatory calls or final D5.8 verdict",
        "execution_enabled": True,
        "aggregate_ceiling_usd": str(CEILING),
        "jobs": jobs,
        "execution_jobs": execution_jobs,
        "assigned_episodes": 100,
        "new_assigned_episodes": 95,
        "preserved_conditions": preserved,
        "ordering": old["ordering"],
        "previous_cohort_plan_digest": old["execution_plan_digest"],
        "previous_summary_digest": content_digest(previous),
        "prior_cohort_wire_requests": previous["new_phase_wire_requests"],
        "prior_cohort_known_spend_usd": previous["new_phase_known_spend_usd"],
        "admission_digest": old["admission_digest"],
        "prior_pilot_spend": previous["aggregate_spend"],
        "prior_event_count": count,
        "prior_event_prefix_digest": prefix,
        "source_digests": sources,
        "driver_code_revision": revision,
        "policy_manifests": policies,
        "backend_identity": FocusMemoryBackend.backend_identity,
        "transport_version": TRANSPORT_VERSION,
        "diagnostics_version": DIAGNOSTICS_VERSION,
        "price_snapshot_digest": content_digest(snapshot),
        "phase_caps": CallCaps(actions, actions, 0, actions).to_dict(),
        "aggregate_caps": CallCaps(
            prior_actions + actions,
            prior_attempts + actions,
            0,
            previous["aggregate_spend"]["wire_requests_sent"] + actions,
        ).to_dict(),
        "maximum_request_reservation_usd": str(config.request_maximum_usd),
        "provider_retries": 0,
        "provider_control_call_cap": 0,
        "confirmatory_call_cap": 0,
        "phase_wall_clock_limit_seconds": 5400,
        "stop_rules": [
            "95 untouched assignments recorded",
            "any runner-abandoned active send retires the single continuation transport",
            "five consecutive non-normal episodes in this explicitly approved continuation",
            "provider identity or price violation",
            "next reservation would exceed USD 28 aggregate",
            "90 minutes since durable continuation start; stop before next model action",
        ],
        "interruption_rule": old["interruption_rule"],
        "unknown_charge_rule": old["unknown_charge_rule"],
        "analysis_rule": "retain all five original failures and 95 untouched assignments in one 100-assignment cohort; disclose the continuation boundary and transport diagnostics; no reruns or pooling with earlier renderers or supplied-state diagnostics",
        "calibration_criteria": old["calibration_criteria"],
        "completion_within_budget_guaranteed": False,
        "connectivity_check_digest": content_digest(read(PUBLIC / "connectivity-check.json")),
    }
    return {**value, "execution_plan_digest": content_digest(value)}


def phase_summary(
    journal: V5AttemptJournal, ledger: ReboundedMemoryLedger, plan: dict[str, Any], stop: str
) -> dict[str, Any]:
    value = summarize(journal, ledger, plan, stop_reason=stop)
    value["schema_version"] = "pixelgym-d58-focus-continuation-summary-v1"
    value["phase_id"] = PHASE
    value["model"] = "google/gemini-3.8-flash"
    value["backend_identity"] = plan["backend_identity"]
    value["transport_version"] = plan["transport_version"]
    value["prior_phase_known_spend_usd"] = value.pop("prior_pilot_spend_usd")
    value["new_phase_known_spend_usd"] = str(
        ledger.spent_usd - Decimal(value["prior_phase_known_spend_usd"])
    )
    value["new_phase_wire_requests"] = (
        ledger.wire_requests_sent - plan["prior_pilot_spend"]["wire_requests_sent"]
    )
    value["complete"] = value["complete"] and not any(
        row["classification"] == "phase_time_stop" for row in value["conditions"]
    )
    value["preserved_episode_count"] = len(plan["preserved_conditions"])
    value["cohort_wire_requests"] = (
        value["new_phase_wire_requests"] + plan["prior_cohort_wire_requests"]
    )
    value["cohort_known_spend_usd"] = str(
        Decimal(value["new_phase_known_spend_usd"]) + Decimal(plan["prior_cohort_known_spend_usd"])
    )
    return value


def render_report(value: dict[str, Any]) -> str:
    lines = [
        "# D5.8 focus calibration continuation",
        "",
        f"Complete: **{value['complete']}**. Stop: `{value['stop_reason']}`.",
        "",
        "| Mode | Assigned | Attempted | Terminal successes | Reached both consumers | Correct first memory attempts / attempted |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mode, row in value["scores"].items():
        lines.append(
            f"| {mode} | {row['assigned']} | {row['attempted_episodes']} | {row['terminal_successes']} | {row['reached_both_consumers']} | {row['correct_first_memory_attempts']} / {row['first_memory_attempts']} |"
        )
    spend = value["aggregate_spend"]
    lines += [
        "",
        f"New phase: {value['new_phase_wire_requests']} wire requests; USD {value['new_phase_known_spend_usd']} known charges. Aggregate known charges: USD {spend['spent_usd']}; unknown holds: USD {spend['unknown_reservation_usd']}; in-flight holds: USD {spend['in_flight_reservation_usd']}. Shared ceiling: USD 28.00.",
        "",
        "The cohort retains five original infrastructure failures; this continuation executes only the 95 untouched assignments. All episodes start at reset with model actions only. The task seeds, order, generator, delayed feedback and matched screenshot policies remain fixed. The focus cue and request-local transport match the completed diagnostic. Credential-free exception diagnostics add no retries or request changes. Earlier renderer cohorts remain separate; every failed and unrun assignment remains in the denominator.",
        "",
        "Exposure means reaching memory consumers, separately from correctness and terminal success. Proposed calibration criteria are history exposure of at least 40/50 and terminal success between 20% and 80%; a positive or significant memory effect is not required. Incomplete observations cannot establish complete-cohort rates or confirmatory power. D5.8 final approval remains an owner decision.",
        "",
        "[Stored summary](summary.json), [execution plan](execution-plan.json), [price snapshot](price-recheck.json). Provider responses and checkpoints remain in the ignored aggregate journal.",
        "",
    ]
    return "\n".join(lines)


def publish(value: dict[str, Any]) -> None:
    write(PUBLIC / "summary.json", value)
    (PUBLIC / "report.md").write_text(render_report(value))


def execute(digest: str) -> None:
    plan = read(PUBLIC / "execution-plan.json")
    if plan != canonical_plan() or digest != plan["execution_plan_digest"]:
        raise ValueError("runtime differs from frozen calibration plan")
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
            if started is None:
                if (
                    ledger.to_dict() != plan["prior_pilot_spend"]
                    or len(journal.events()) != plan["prior_event_count"]
                ):
                    raise ValueError("shared ledger differs from carry-forward")
                started = journal.append_event(
                    event_key=f"{PHASE}/started",
                    kind="focus_continuation_started",
                    trial_id=PHASE,
                    step_index=0,
                    payload={
                        "execution_plan_digest": digest,
                        "started_at": datetime.now(UTC).isoformat(),
                    },
                )
            if started.payload["execution_plan_digest"] != digest:
                raise ValueError("phase has another binding")
            start_time = datetime.fromisoformat(started.payload["started_at"])

            def time_exhausted() -> bool:
                return (datetime.now(UTC) - start_time).total_seconds() >= int(
                    plan["phase_wall_clock_limit_seconds"]
                )

            closed = journal.event(f"{PHASE}/closed")
            if closed:
                publish(phase_summary(journal, ledger, plan, closed.payload["stop_reason"]))
                return
            transport = IsolatedRequestBoundTransport(
                config,
                lifecycle_id=PHASE,
                ledger=ledger,
                urlopen=DiagnosticUrlopen(journal, phase_id=PHASE),
            )
            stop, failures = "interrupted", 0
            try:
                stop = "all_assignments_completed"
                for job in plan["execution_jobs"]:
                    if transport.retired:
                        stop = "transport_retired_after_deadline"
                        break
                    if time_exhausted():
                        stop = "phase_time_stop"
                        break
                    if (
                        ledger.blocked
                        or ledger.budget_accounted_spend_usd + config.request_maximum_usd > CEILING
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
                        raise ValueError("policy manifest changed")

                    def progress(name: str, trial: str = job["trial_id"]) -> None:
                        if name == "attempt_terminal":
                            print(
                                json.dumps(
                                    {
                                        "event": "request_settled",
                                        "trial_id": trial,
                                        "aggregate_requests": ledger.wire_requests_sent,
                                        "known_spend_usd": str(ledger.spent_usd),
                                        "unknown_holds_usd": str(ledger.unknown_reservation_usd),
                                    }
                                ),
                                flush=True,
                            )

                    row = run_episode(
                        journal,
                        job=job,
                        manifest=manifest,
                        policy=ScreenshotMemoryPolicy(config, retain_screenshots=mode == "history"),
                        transport=transport,
                        ledger=ledger,
                        caps=CallCaps(**plan["aggregate_caps"]),
                        plan_digest=digest,
                        boundary=progress,
                        time_exhausted=time_exhausted,
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
                    if transport.retired:
                        stop = "transport_retired_after_deadline"
                        break
                    if identity_failure(journal, job["trial_id"], config):
                        stop = "provider_identity_or_price_guard_failure"
                        break
                    if ledger.blocked or row["classification"] == "budget_stop":
                        stop = "aggregate_budget_stop"
                        break
                    if row["classification"] == "phase_time_stop":
                        stop = "phase_time_stop"
                        break
                    failures = (
                        0
                        if row["classification"] in ("success_termination", "step_limit_truncation")
                        else failures + 1
                    )
                    if failures >= 5:
                        stop = "five_consecutive_non_normal_episodes"
                        break
            except BaseException:
                stop = "interrupted"
                raise
            finally:
                transport.wait_until_idle(5)
                journal.append_event(
                    event_key=f"{PHASE}/closed",
                    kind="focus_continuation_closed",
                    trial_id=PHASE,
                    step_index=0,
                    payload={"stop_reason": stop},
                )
                value = phase_summary(journal, ledger, plan, stop)
                publish(value)
                print(
                    json.dumps(
                        {
                            "scores": value["scores"],
                            "stop_reason": stop,
                            "aggregate_spend": ledger.to_dict(),
                        }
                    ),
                    flush=True,
                )
        finally:
            if transport is None or transport.wait_until_idle():
                journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "execute", "report"))
    parser.add_argument("--approved-plan-digest")
    args = parser.parse_args()
    if args.command == "prepare":
        plan = canonical_plan()
        path = PUBLIC / "execution-plan.json"
        if path.exists() and read(path) != plan:
            raise ValueError("refusing to overwrite frozen calibration plan")
        write(path, plan)
        print(
            json.dumps(
                {
                    k: plan[k]
                    for k in ("execution_plan_digest", "assigned_episodes", "aggregate_ceiling_usd")
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
