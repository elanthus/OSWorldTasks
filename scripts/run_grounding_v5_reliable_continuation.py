"""Run the 90 untouched assignments with curl and the shared USD 28 ceiling."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import time
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import CallCaps, content_digest, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.reliable_calibration import run_episode
from pixelgym.grounding.v5.reliable_memory import (
    ReliableMemoryPolicy,
    build_reliable_manifest,
    reliable_config,
)
from pixelgym.grounding.v5.reliable_transport import (
    RETRY_RULE,
    TRANSPORT_VERSION,
    ReliableTransport,
)
from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger
from scripts.run_grounding_v5_focus_calibration import config_from_snapshot
from scripts.run_grounding_v5_focus_continuation import phase_summary as previous_phase_summary
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

PHASE = "d58-reliable-memory-continuation-v1"
PUBLIC = ROOT / "artifacts/grounding-v5-d58-reliable-continuation"
PREVIOUS = ROOT / "artifacts/grounding-v5-d58-focus-continuation"
DIAGNOSTIC = ROOT / "artifacts/grounding-v5-d58-reliable-diagnostic"
CEILING = Decimal("28.00")
NEW_SOURCES = (
    "pixelgym/grounding/v5/reliable_calibration.py",
    "scripts/run_grounding_v5_reliable_continuation.py",
)


def continuation_assignments(
    old: dict[str, Any], previous: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if len(old["jobs"]) != len(previous["conditions"]) or any(
        any(row.get(k) != v for k, v in job.items())
        for job, row in zip(old["jobs"], previous["conditions"], strict=True)
    ):
        raise ValueError("previous assignment identities differ from stored outcomes")
    preserved = [r for r in previous["conditions"] if r["classification"] != "not_run"]
    if len(preserved) != 10 or any(
        r["classification"] != "infrastructure_failure" for r in preserved
    ):
        raise ValueError("expected the ten recorded infrastructure failures")
    kept = {r["trial_id"] for r in preserved}
    jobs = [
        j if j["trial_id"] in kept else {**j, "trial_id": f"{PHASE}-{j['seed']}-{j['mode']}"}
        for j in old["jobs"]
    ]
    execution = [j for j in jobs if j["trial_id"] not in kept]
    if (
        len(jobs) != 100
        or len(execution) != 90
        or any(sum(j["mode"] == mode for j in execution) != 45 for mode in ("history", "stateless"))
    ):
        raise ValueError("continuation must preserve 100 assignments and run 45 per mode")
    return jobs, execution, preserved


def curl_identity() -> dict[str, str]:
    return {
        "executable": "/usr/bin/curl",
        "executable_digest": "sha256:" + sha256_bytes(Path("/usr/bin/curl").read_bytes()),
        "version_output": subprocess.run(
            ["/usr/bin/curl", "--version"], capture_output=True, text=True, check=True
        ).stdout,
    }


def canonical_plan() -> dict[str, Any]:
    previous, old = read(PREVIOUS / "summary.json"), read(PREVIOUS / "execution-plan.json")
    diagnostic = read(DIAGNOSTIC / "summary.json")
    diagnostic_plan = read(DIAGNOSTIC / "execution-plan.json")
    if diagnostic_plan["previous_summary_digest"] != content_digest(previous):
        raise ValueError("previous cohort differs from the verified diagnostic carry-forward")
    if previous["execution_plan_digest"] != old["execution_plan_digest"]:
        raise ValueError("previous plan differs from its closed summary")
    if diagnostic["dispatched"] != 10 or not diagnostic["transport_idle_at_close"]:
        raise ValueError("completed reliability diagnostic required")
    sources = {
        name: "sha256:" + sha256_bytes((ROOT / name).read_bytes())
        for name in (*diagnostic_plan["source_digests"], *NEW_SOURCES)
    }
    changed = {
        name: {"executed_digest": digest, "current_digest": sources[name]}
        for name, digest in diagnostic_plan["source_digests"].items()
        if sources[name] != digest
    }
    if set(changed) - {"pyproject.toml"}:
        raise ValueError("executed runtime source changed")
    if not JOURNAL.exists():
        raise FileNotFoundError("original shared journal required")
    count = diagnostic["journal_integrity"]["event_count"]
    with closing(V5AttemptJournal(JOURNAL)) as journal:
        if prefix_digest(journal, count) != diagnostic["journal_integrity"]["event_chain_digest"]:
            raise ValueError("diagnostic journal prefix changed")
        events = journal.events()[:count]
        prior_actions = sum(e.kind == "dispatch_started" for e in events)
        prior_attempts = sum(e.kind == "attempt_started" for e in events)
    jobs, execution, preserved = continuation_assignments(old, previous)
    actions = sum(j["action_limit"] for j in execution)
    snapshot = read(PUBLIC / "price-recheck.json")
    config = reliable_config(config_from_snapshot(snapshot))
    prior_config = reliable_config(config_from_snapshot(read(DIAGNOSTIC / "price-recheck.json")))
    if config != prior_config:
        raise ValueError("model, routing, prices or retry configuration changed")
    revision = git("log", "-1", "--format=%H", "--", *NEW_SOURCES)
    policies = {
        mode: build_reliable_manifest(
            ROOT, config=config, code_revision=revision, retain_screenshots=mode == "history"
        ).to_dict()
        for mode in ("history", "stateless")
    }
    prior_spend = diagnostic["aggregate_spend"]
    value = {
        "schema_version": "pixelgym-d58-reliable-continuation-plan-v1",
        "phase_id": PHASE,
        "owner_approval": "please run the remaining 90",
        "approval_scope": "retain ten failed assignments and run only the 90 untouched assignments with the successfully diagnosed curl transport and bounded retries; existing USD 28 aggregate cap; no confirmatory tasks",
        "execution_enabled": True,
        "aggregate_ceiling_usd": str(CEILING),
        "phase_cap_usd": str(CEILING - Decimal(prior_spend["budget_accounted_spend_usd"])),
        "jobs": jobs,
        "execution_jobs": execution,
        "assigned_episodes": 100,
        "new_assigned_episodes": 90,
        "preserved_conditions": preserved,
        "ordering": old["ordering"],
        "previous_cohort_plan_digest": old["execution_plan_digest"],
        "previous_summary_digest": content_digest(previous),
        "diagnostic_summary_digest": content_digest(diagnostic),
        "diagnostic_plan_digest": diagnostic_plan["execution_plan_digest"],
        "prior_cohort_wire_requests": previous["cohort_wire_requests"],
        "prior_cohort_known_spend_usd": previous["cohort_known_spend_usd"],
        "admission_digest": old["admission_digest"],
        "prior_pilot_spend": prior_spend,
        "prior_integrity": diagnostic["journal_integrity"],
        "prior_event_count": count,
        "prior_event_prefix_digest": diagnostic["journal_integrity"]["event_chain_digest"],
        "source_digests": sources,
        "source_changes_since_diagnostic": changed,
        "source_change_reason": "pyproject adds the main-branch local HTTP test marker and a narrow frozen-analyzer import-order exception; executed runtime sources remain unchanged",
        "driver_code_revision": revision,
        "policy_manifests": policies,
        "backend_identity": FocusMemoryBackend.backend_identity,
        "transport_version": TRANSPORT_VERSION,
        "retry_rule": RETRY_RULE,
        "price_snapshot_digest": content_digest(snapshot),
        "curl_identity": read(PUBLIC / "curl-identity.json"),
        "phase_caps": CallCaps(actions, 3 * actions, 0, 3 * actions).to_dict(),
        "aggregate_caps": CallCaps(
            prior_actions + actions,
            prior_attempts + 3 * actions,
            0,
            prior_spend["wire_requests_sent"] + 3 * actions,
        ).to_dict(),
        "provider_retries_per_action": 2,
        "provider_control_call_cap": 0,
        "confirmatory_call_cap": 0,
        "phase_wall_clock_limit_seconds": 5400,
        "stop_rules": [
            "90 untouched assignments recorded; preserve all failures",
            "no consecutive-episode-failure stop; transient retries remain bounded to two per action",
            "retired transport or provider identity/price violation",
            "next actual request reservation exceeds USD 28 aggregate or phase wire cap",
            "90 minutes from durable phase start; honor server cooldowns in full",
        ],
        "interruption_rule": old["interruption_rule"],
        "unknown_charge_rule": old["unknown_charge_rule"],
        "analysis_rule": "retain all 100 assignments and report the 90-assignment repaired-transport subset separately; no reruns, scripted prefixes, or pooling with diagnostics or older renderers",
        "calibration_criteria": old["calibration_criteria"],
        "completion_within_budget_guaranteed": False,
    }
    return {**value, "execution_plan_digest": content_digest(value)}


def phase_summary(
    journal: V5AttemptJournal, ledger: ReboundedMemoryLedger, plan: dict[str, Any], stop: str
) -> dict[str, Any]:
    value = previous_phase_summary(journal, ledger, plan, stop)
    value.update(schema_version="pixelgym-d58-reliable-continuation-summary-v1", phase_id=PHASE)
    kept = {r["trial_id"] for r in plan["preserved_conditions"]}
    if [r for r in value["conditions"] if r["trial_id"] in kept] != plan["preserved_conditions"]:
        raise ValueError("preserved results differ from journal")
    value["new_phase_unknown_holds_usd"] = str(
        ledger.unknown_reservation_usd
        - Decimal(plan["prior_pilot_spend"]["unknown_reservation_usd"])
    )
    value["new_conditions"] = [r for r in value["conditions"] if r["trial_id"] not in kept]
    prefix = f"reliable/{content_digest(PHASE)}"
    value["transport_receipts"] = [
        e.payload for e in journal.events(prefix) if e.kind == "reliable_transport_receipt"
    ]
    closed = journal.event(f"{PHASE}/closed")
    value["transport_idle_at_close"] = closed.payload["transport_idle"] if closed else False
    return value


def render_report(value: dict[str, Any]) -> str:
    lines = [
        "# D5.8 calibration with repaired transport",
        "",
        f"Complete: **{value['complete']}**. Stop: `{value['stop_reason']}`.",
        "",
        "| Mode | Assigned | Attempted | Terminal successes | Reached both consumers | Correct first memory choices / attempted |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mode, r in value["scores"].items():
        lines.append(
            f"| {mode} | {r['assigned']} | {r['attempted_episodes']} | {r['terminal_successes']} | {r['reached_both_consumers']} | {r['correct_first_memory_attempts']} / {r['first_memory_attempts']} |"
        )
    spend = value["aggregate_spend"]
    lines += [
        "",
        f"New phase: {value['new_phase_wire_requests']} wire requests; USD {value['new_phase_known_spend_usd']} confirmed charges and USD {value['new_phase_unknown_holds_usd']} new unknown holds. Aggregate: USD {spend['spent_usd']} confirmed, USD {spend['unknown_reservation_usd']} held, USD {spend['in_flight_reservation_usd']} in flight; USD {spend['budget_accounted_spend_usd']} accounted against USD 28.",
        "",
        "The ten prior infrastructure failures remain unchanged. Only the 90 untouched assignments use the repaired curl transport, with an initial send and at most two same-request retries. All new episodes start from reset with model actions only. Seeds, order, screenshots, generator, focus cue and delayed correctness are preserved; the execution-version boundary is explicit. Raw attempts, failed episodes and unrun assignments remain in the evidence.",
        "",
        "Consumer exposure, first-choice correctness and terminal success are distinct measures. Supplied-state diagnostics add no episodes. No confirmatory tasks or final D5.8 verdict are included.",
        "",
        "[Summary](summary.json), [execution plan](execution-plan.json), and [prices](price-recheck.json). Response envelopes and checkpoints remain in the ignored authoritative journal.",
        "",
    ]
    return "\n".join(lines)


def publish(value: dict[str, Any]) -> None:
    write(PUBLIC / "summary.json", value)
    (PUBLIC / "report.md").write_text(render_report(value))


def execute(digest: str) -> None:
    plan = read(PUBLIC / "execution-plan.json")
    if plan != canonical_plan() or digest != plan["execution_plan_digest"]:
        raise ValueError("runtime differs from frozen plan")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean")
    for name in (*NEW_SOURCES, str((PUBLIC / "execution-plan.json").relative_to(ROOT))):
        git("ls-files", "--error-unmatch", name)
    if curl_identity() != plan["curl_identity"]:
        raise ValueError("curl executable changed")
    snapshot = read(PUBLIC / "price-recheck.json")
    age = (datetime.now(UTC) - datetime.fromisoformat(snapshot["observed_at"])).total_seconds()
    if not 0 <= age < 86400:
        raise ValueError("price snapshot expired")
    config = reliable_config(config_from_snapshot(snapshot))
    with (PRIVATE / "operator.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = V5AttemptJournal(JOURNAL)
        transport = None
        try:
            if journal.event(f"{PHASE}/started"):
                raise ValueError("closed or interrupted phase cannot restart")
            ledger = ReboundedMemoryLedger(CEILING, Decimal(0), journal=journal)
            if (
                ledger.to_dict() != plan["prior_pilot_spend"]
                or journal.integrity_report() != plan["prior_integrity"]
            ):
                raise ValueError("shared ledger differs from frozen carry-forward")
            started_at = time.time()
            deadline = started_at + plan["phase_wall_clock_limit_seconds"]
            journal.append_event(
                event_key=f"{PHASE}/started",
                kind="reliable_continuation_started",
                trial_id=PHASE,
                step_index=0,
                payload={"execution_plan_digest": digest, "started_at": started_at},
            )
            transport = ReliableTransport(
                config,
                lifecycle_id=PHASE,
                ledger=ledger,
                phase_deadline=deadline,
                phase_spend_limit=Decimal(plan["phase_cap_usd"]),
                phase_start_accounted=Decimal(
                    plan["prior_pilot_spend"]["budget_accounted_spend_usd"]
                ),
                phase_wire_limit=plan["phase_caps"]["provider_wire_request_cap"],
            )
            stop = "interrupted"
            try:
                for job in plan["execution_jobs"]:
                    if not transport.await_ready():
                        stop = "transport_retired" if transport.retired else "phase_time_stop"
                        break
                    if ledger.blocked:
                        stop = "aggregate_budget_stop"
                        break
                    mode = job["mode"]
                    manifest = build_reliable_manifest(
                        ROOT,
                        config=config,
                        code_revision=plan["driver_code_revision"],
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
                        policy=ReliableMemoryPolicy(config, retain_screenshots=mode == "history"),
                        transport=transport,
                        ledger=ledger,
                        caps=CallCaps(**plan["aggregate_caps"]),
                        plan_digest=digest,
                        boundary=progress,
                        time_exhausted=lambda: time.time() + 10 >= deadline,
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
                    if transport.retired or identity_failure(journal, job["trial_id"], config):
                        stop = "transport_or_identity_stop"
                        break
                    codes = {e.payload.get("failure_code") for e in journal.events(job["trial_id"])}
                    if (
                        ledger.blocked
                        or row["classification"] == "budget_stop"
                        or codes & {"phase_cap_stop", "aggregate_spend_guard"}
                    ):
                        stop = "aggregate_budget_or_wire_stop"
                        break
                    if time.time() + 10 >= deadline or codes & {
                        "phase_time_stop",
                        "phase_time_or_retirement_stop",
                        "cooldown_exceeds_remaining_time",
                    }:
                        stop = "phase_time_stop"
                        break
                else:
                    stop = "all_assignments_completed"
            finally:
                if not transport.wait_until_idle(5):
                    transport.wire.abort()
                idle = transport.wait_until_idle(5) and transport.wire.idle
                journal.append_event(
                    event_key=f"{PHASE}/closed",
                    kind="reliable_continuation_closed",
                    trial_id=PHASE,
                    step_index=0,
                    payload={"stop_reason": stop, "transport_idle": idle},
                )
                value = phase_summary(journal, ledger, plan, stop)
                publish(value)
                print(
                    json.dumps(
                        {
                            "stop_reason": stop,
                            "scores": value["scores"],
                            "aggregate_spend": ledger.to_dict(),
                        }
                    ),
                    flush=True,
                )
        finally:
            if transport is None or transport.wait_until_idle(5):
                journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "execute"))
    parser.add_argument("--approved-plan-digest")
    args = parser.parse_args()
    if args.command == "prepare":
        plan = canonical_plan()
        path = PUBLIC / "execution-plan.json"
        if path.exists() and read(path) != plan:
            raise ValueError("cannot overwrite frozen plan")
        write(path, plan)
        print(plan["execution_plan_digest"])
    elif args.approved_plan_digest:
        os.umask(0o077)
        execute(args.approved_plan_digest)
    else:
        parser.error("execute requires approved plan digest")


if __name__ == "__main__":
    main()
