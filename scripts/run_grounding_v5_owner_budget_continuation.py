"""Continue untouched assignments with the owner-approved zero unresolved budget holds."""

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
from pixelgym.grounding.v5.owner_budget import (
    OWNER_AUTHORIZATION_KEY,
    OWNER_BUDGET_RULE,
    OwnerZeroHoldLedger,
)
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

PHASE = "d58-owner-budget-continuation-v1"
PUBLIC = ROOT / "artifacts/grounding-v5-d58-owner-budget-continuation"
PREVIOUS = ROOT / "artifacts/grounding-v5-d58-reliable-extension"
APPROVAL = ROOT / "artifacts/grounding-v5-d58-runtime-amendment/approval.json"
OWNER_APPROVAL = ROOT / "artifacts/grounding-v5-d58-owner-budget/approval.json"
RECONCILIATION = ROOT / "artifacts/grounding-v5-d58-owner-budget/reconciliation.json"
ORIGIN_PHASE = "d58-reliable-memory-continuation-v1"
DIAGNOSTIC = ROOT / "artifacts/grounding-v5-d58-reliable-diagnostic"
CEILING = Decimal("28.00")
NEW_SOURCES = (
    "pixelgym/grounding/v5/owner_budget.py",
    "scripts/run_grounding_v5_owner_budget_continuation.py",
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
    if len(preserved) < 10 or len(preserved) >= 100:
        raise ValueError("extension requires prior outcomes and untouched assignments")
    kept = {r["trial_id"] for r in preserved}
    jobs = [
        j if j["trial_id"] in kept else {**j, "trial_id": f"{PHASE}-{j['seed']}-{j['mode']}"}
        for j in old["jobs"]
    ]
    execution = [j for j in jobs if j["trial_id"] not in kept]
    if len(jobs) != 100 or len(execution) + len(preserved) != 100:
        raise ValueError("extension assignment count changed")
    if len({(j["seed"], j["mode"]) for j in jobs}) != 100:
        raise ValueError("duplicate assignments")
    return jobs, execution, preserved


def curl_identity() -> dict[str, str]:
    return {
        "executable": "/usr/bin/curl",
        "executable_digest": "sha256:" + sha256_bytes(Path("/usr/bin/curl").read_bytes()),
        "version_output": subprocess.run(
            ["/usr/bin/curl", "--version"], capture_output=True, text=True, check=True
        ).stdout,
    }


def owner_approval() -> dict[str, Any]:
    value = read(OWNER_APPROVAL)
    if (
        value["aggregate_ceiling_usd"] != str(CEILING)
        or value["unresolved_budget_hold_usd"] != "0"
        or value["rule"] != OWNER_BUDGET_RULE
        or value["maximum_total_runtime_seconds"] != 21600
        or value["runtime_origin_phase"] != ORIGIN_PHASE
    ):
        raise ValueError("owner budget instruction differs from the authorized scope")
    return value


def reconcile_owner_budget() -> dict[str, Any]:
    """Apply the owner's accounting decision after the active phase is idle."""
    approval = owner_approval()
    approval_digest = content_digest(approval)
    previous = read(PREVIOUS / "summary.json")
    previous_plan = read(PREVIOUS / "execution-plan.json")
    if previous["execution_plan_digest"] != previous_plan["execution_plan_digest"]:
        raise ValueError("closed accounting snapshot differs from its frozen plan")
    revision = git("log", "-1", "--format=%H", "--", *NEW_SOURCES)
    git("ls-files", "--error-unmatch", *NEW_SOURCES)
    git("diff", "--exit-code", revision, "--", *NEW_SOURCES)
    sources = {
        name: "sha256:" + sha256_bytes((ROOT / name).read_bytes())
        for name in (*previous_plan["source_digests"], *NEW_SOURCES)
    }
    if any(sources[name] != expected for name, expected in previous_plan["source_digests"].items()):
        raise ValueError("executed predecessor source changed before owner reconciliation")
    with (PRIVATE / "operator.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(V5AttemptJournal(JOURNAL)) as journal:
            closed = journal.event(f"{previous['phase_id']}/closed")
            if (
                closed is None
                or not closed.payload["transport_idle"]
                or not previous["transport_idle_at_close"]
            ):
                raise ValueError(
                    "owner reconciliation requires the previous transport to close idle"
                )
            if journal.event(OWNER_AUTHORIZATION_KEY) and RECONCILIATION.exists():
                receipt = read(RECONCILIATION)
                ledger = OwnerZeroHoldLedger(
                    CEILING, Decimal(0), journal=journal, approval_digest=approval_digest
                )
                if (
                    receipt["approval_digest"] != approval_digest
                    or ledger.to_dict() != receipt["after_aggregate_spend"]
                    or journal.integrity_report() != receipt["after_integrity"]
                ):
                    raise ValueError("owner reconciliation already exists with a different state")
                return receipt
            before, integrity = previous["aggregate_spend"], previous["journal_integrity"]
            binding = {
                "approval_digest": approval_digest,
                "rule": OWNER_BUDGET_RULE,
                "previous_summary_digest": content_digest(previous),
                "before_integrity": integrity,
                "driver_code_revision": revision,
                "source_digests": sources,
            }
            existing = journal.event(OWNER_AUTHORIZATION_KEY)
            if existing is None:
                original = ReboundedMemoryLedger(CEILING, Decimal(0), journal=journal)
                if (
                    original.to_dict() != before
                    or journal.integrity_report() != integrity
                    or original.in_flight_reservation_usd != 0
                ):
                    raise ValueError("shared journal differs from the closed accounting snapshot")
                journal.append_event(
                    event_key=OWNER_AUTHORIZATION_KEY,
                    kind="owner_zero_hold_budget_authorized",
                    trial_id="__spend_ledger__",
                    step_index=0,
                    payload=binding,
                )
            elif existing.payload != binding or any(
                event.kind
                not in ("owner_zero_hold_budget_authorized", "spend_unknown_budget_waived")
                for event in journal.events()[integrity["event_count"] :]
            ):
                raise ValueError("interrupted reconciliation has unexpected later events")
            ledger = OwnerZeroHoldLedger(
                CEILING, Decimal(0), journal=journal, approval_digest=approval_digest
            )
            ledger.waive_existing_unknown_holds()
            waivers = [
                event.payload
                for event in journal.events()
                if event.kind == "spend_unknown_budget_waived"
            ]
            receipt = {
                "schema_version": "pixelgym-d58-owner-budget-reconciliation-v1",
                "approval_digest": approval_digest,
                "previous_summary_digest": content_digest(previous),
                "driver_code_revision": revision,
                "source_digests": sources,
                "before_aggregate_spend": before,
                "after_aggregate_spend": ledger.to_dict(),
                "before_integrity": integrity,
                "after_integrity": journal.integrity_report(),
                "waivers": waivers,
                "waived_budget_holds_usd": str(
                    sum((Decimal(w["previous_budget_hold_usd"]) for w in waivers), Decimal(0))
                ),
                "basis": approval["billing_evidence_basis"],
                "provider_calls": 0,
            }
            write(RECONCILIATION, receipt)
            return receipt


def canonical_plan() -> dict[str, Any]:
    previous, old = read(PREVIOUS / "summary.json"), read(PREVIOUS / "execution-plan.json")
    diagnostic = read(DIAGNOSTIC / "summary.json")
    if previous["execution_plan_digest"] != old["execution_plan_digest"]:
        raise ValueError("previous plan differs from its closed summary")
    if (
        previous["stop_reason"] not in ("aggregate_budget_or_wire_stop", "phase_time_stop")
        or not previous["transport_idle_at_close"]
    ):
        raise ValueError("continuation requires a closed budget/time boundary and idle transport")
    approval = read(APPROVAL)
    budget_approval = owner_approval()
    reconciliation = read(RECONCILIATION)
    if (
        reconciliation["approval_digest"] != content_digest(budget_approval)
        or reconciliation["previous_summary_digest"] != content_digest(previous)
        or reconciliation["before_aggregate_spend"] != previous["aggregate_spend"]
    ):
        raise ValueError("owner reconciliation differs from the closed phase")
    if (
        approval["owner_selection"] != "Allow up to 6 hours; keep $28 cap"
        or approval["aggregate_ceiling_usd"] != str(CEILING)
        or approval["maximum_total_runtime_seconds"] != 21600
        or approval["runtime_origin_phase"] != ORIGIN_PHASE
    ):
        raise ValueError("runtime extension differs from owner authorization")
    sources = {
        name: "sha256:" + sha256_bytes((ROOT / name).read_bytes())
        for name in (*old["source_digests"], *NEW_SOURCES)
    }
    if any(sources[name] != expected for name, expected in old["source_digests"].items()):
        raise ValueError("executed predecessor source changed")
    if not JOURNAL.exists():
        raise FileNotFoundError("original shared journal required")
    count = reconciliation["after_integrity"]["event_count"]
    with closing(V5AttemptJournal(JOURNAL)) as journal:
        if (
            prefix_digest(journal, previous["journal_integrity"]["event_count"])
            != previous["journal_integrity"]["event_chain_digest"]
        ):
            raise ValueError("preceding journal prefix changed")
        if prefix_digest(journal, count) != reconciliation["after_integrity"]["event_chain_digest"]:
            raise ValueError("owner reconciliation journal prefix changed")
        origin = journal.event(f"{ORIGIN_PHASE}/started")
        if origin is None:
            raise ValueError("original runtime clock missing")
        runtime_start = origin.payload["started_at"]
        events = journal.events()[:count]
        prior_actions = sum(e.kind == "dispatch_started" for e in events)
        prior_attempts = sum(e.kind == "attempt_started" for e in events)
    jobs, execution, preserved = continuation_assignments(old, previous)
    actions = sum(j["action_limit"] for j in execution)
    snapshot = read(PUBLIC / "price-recheck.json")
    config = reliable_config(config_from_snapshot(snapshot))
    prior_config = reliable_config(config_from_snapshot(read(PREVIOUS / "price-recheck.json")))
    if config != prior_config:
        raise ValueError("model, routing, prices or retry configuration changed")
    revision = git("log", "-1", "--format=%H", "--", *NEW_SOURCES)
    policies = {
        mode: build_reliable_manifest(
            ROOT, config=config, code_revision=revision, retain_screenshots=mode == "history"
        ).to_dict()
        for mode in ("history", "stateless")
    }
    prior_spend = reconciliation["after_aggregate_spend"]
    if prior_spend["blocked"] or Decimal(prior_spend["in_flight_reservation_usd"]) != 0:
        raise ValueError("owner accounting does not override a blocked or active ledger")
    value = {
        "schema_version": "pixelgym-d58-owner-budget-continuation-plan-v1",
        "phase_id": PHASE,
        "owner_approval": budget_approval["owner_statement"],
        "owner_budget_approval_digest": content_digest(budget_approval),
        "owner_reconciliation_digest": content_digest(reconciliation),
        "runtime_approval_digest": content_digest(approval),
        "runtime_started_at": runtime_start,
        "runtime_deadline": runtime_start + 21600,
        "approval_scope": "continue only untouched assignments after the prior closed phase; owner assigns zero budget weight to unresolved outcomes; six hours total from the original start, unchanged USD 28 cap on confirmed charges plus active request bounds; no episode replay or confirmatory tasks",
        "execution_enabled": True,
        "aggregate_ceiling_usd": str(CEILING),
        "phase_cap_usd": str(CEILING - Decimal(prior_spend["budget_accounted_spend_usd"])),
        "jobs": jobs,
        "execution_jobs": execution,
        "assigned_episodes": 100,
        "new_assigned_episodes": len(execution),
        "preserved_conditions": preserved,
        "ordering": old["ordering"],
        "previous_cohort_plan_digest": old["execution_plan_digest"],
        "previous_summary_digest": content_digest(previous),
        "diagnostic_summary_digest": content_digest(diagnostic),
        "diagnostic_plan_digest": read(DIAGNOSTIC / "execution-plan.json")["execution_plan_digest"],
        "prior_cohort_wire_requests": previous["cohort_wire_requests"],
        "prior_cohort_known_spend_usd": previous["cohort_known_spend_usd"],
        "admission_digest": old["admission_digest"],
        "prior_pilot_spend": prior_spend,
        "prior_integrity": reconciliation["after_integrity"],
        "prior_event_count": count,
        "prior_event_prefix_digest": reconciliation["after_integrity"]["event_chain_digest"],
        "source_digests": sources,
        "source_changes_since_previous": {},
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
        "maximum_total_runtime_seconds": 21600,
        "stop_rules": [
            "all remaining untouched assignments recorded; preserve every prior result",
            "no consecutive-episode-failure stop; transient retries remain bounded to two per action",
            "retired transport or provider identity/price violation",
            "confirmed charges plus the next actual in-flight request bound exceed USD 28 aggregate or phase wire cap; unresolved outcomes have owner-authorized zero budget weight",
            "six hours from the original reliable-continuation start, including setup time; honor server cooldowns in full",
        ],
        "interruption_rule": old["interruption_rule"],
        "unknown_charge_rule": "Retain all unpriced outcomes and original request bounds, with zero unresolved budget hold under the owner's activity check and explicit instruction. This does not create a provider-reported zero-cost receipt. Active requests retain their request-sized bounds; any later actual charge is counted.",
        "analysis_rule": "retain all 100 assignments and report execution phases separately; no reruns, scripted prefixes, or pooling with diagnostics or older renderers",
        "calibration_criteria": old["calibration_criteria"],
        "completion_within_budget_guaranteed": False,
    }
    return {**value, "execution_plan_digest": content_digest(value)}


def phase_summary(
    journal: V5AttemptJournal, ledger: OwnerZeroHoldLedger, plan: dict[str, Any], stop: str
) -> dict[str, Any]:
    value = previous_phase_summary(journal, ledger, plan, stop)
    value.update(schema_version="pixelgym-d58-owner-budget-continuation-summary-v1", phase_id=PHASE)
    value["owner_budget_approval_digest"] = plan["owner_budget_approval_digest"]
    value["owner_reconciliation_digest"] = plan["owner_reconciliation_digest"]
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
        "# D5.8 calibration with owner-authorized zero unresolved budget holds",
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
        f"New phase: {value['new_phase_wire_requests']} wire requests; USD {value['new_phase_known_spend_usd']} confirmed charges. Aggregate: USD {spend['spent_usd']} confirmed and USD {spend['in_flight_reservation_usd']} reserved for active requests; USD {spend['budget_accounted_spend_usd']} accounted against USD 28. Unresolved outcomes carry USD 0 budget weight under the owner's instruction.",
        "",
        "The owner checked OpenRouter activity and reports that failed calls are not billed. This accounting rule retains all unknown outcomes and original request bounds; it does not create provider-reported zero-cost receipts. Confirmed charges and active request bounds still count toward the cap.",
        "",
        "All prior outcomes remain unchanged. This phase continues only untouched assignments with the same repaired curl transport, with an initial send and at most two same-request retries. Episodes start from reset with model actions only. Seeds, order, screenshots, generator, focus cue and delayed correctness are preserved. The six-hour limit starts at the original reliable continuation, including intervening setup time. Every failed and unrun assignment remains in the evidence.",
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
            ledger = OwnerZeroHoldLedger(
                CEILING,
                Decimal(0),
                journal=journal,
                approval_digest=plan["owner_budget_approval_digest"],
            )
            if (
                ledger.to_dict() != plan["prior_pilot_spend"]
                or journal.integrity_report() != plan["prior_integrity"]
            ):
                raise ValueError("shared ledger differs from frozen carry-forward")
            started_at = time.time()
            deadline = plan["runtime_deadline"]
            if started_at + 10 >= deadline:
                raise ValueError("approved six-hour window is exhausted")
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
    parser.add_argument("command", choices=("reconcile", "prepare", "execute"))
    parser.add_argument("--approved-plan-digest")
    args = parser.parse_args()
    if args.command == "reconcile":
        receipt = reconcile_owner_budget()
        print(
            json.dumps(
                {
                    "approval_digest": receipt["approval_digest"],
                    "waived_budget_holds_usd": receipt["waived_budget_holds_usd"],
                    "after_aggregate_spend": receipt["after_aggregate_spend"],
                    "provider_calls": 0,
                }
            )
        )
    elif args.command == "prepare":
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
