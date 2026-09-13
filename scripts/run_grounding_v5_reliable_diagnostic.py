"""Source-bound reliability diagnostic: at most 20 sends and USD 1 within USD 28."""

from __future__ import annotations

import argparse
import fcntl
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
from pixelgym.grounding.v5.reliable_diagnostic import diagnostic_jobs, run_condition
from pixelgym.grounding.v5.reliable_memory import (
    REPAIR_SOURCES,
    build_reliable_manifest,
    reliable_config,
)
from pixelgym.grounding.v5.reliable_transport import ReliableTransport
from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger
from scripts.run_grounding_v5_focus_calibration import config_from_snapshot
from scripts.run_grounding_v5_focus_continuation import source_binding as prior_sources
from scripts.run_grounding_v5_memory_calibration import (
    JOURNAL,
    PRIVATE,
    ROOT,
    git,
    prefix_digest,
    read,
    write,
)

PHASE = "d58-curl-reliability-diagnostic-v1"
PUBLIC = ROOT / "artifacts/grounding-v5-d58-reliable-diagnostic"
PREVIOUS = ROOT / "artifacts/grounding-v5-d58-focus-continuation"
NEW_SOURCES = (
    *REPAIR_SOURCES,
    "pixelgym/grounding/v5/reliable_diagnostic.py",
    "scripts/run_grounding_v5_reliable_diagnostic.py",
)


def canonical_plan() -> dict[str, Any]:
    previous, old = read(PREVIOUS / "summary.json"), read(PREVIOUS / "execution-plan.json")
    sources = {
        **prior_sources(),
        **{name: "sha256:" + sha256_bytes((ROOT / name).read_bytes()) for name in NEW_SOURCES},
    }
    if any(sources[name] != digest for name, digest in old["source_digests"].items()):
        raise ValueError("executed source changed")
    revision = git("log", "-1", "--format=%H", "--", *NEW_SOURCES)
    snapshot = read(PUBLIC / "price-recheck.json")
    config = reliable_config(config_from_snapshot(snapshot))
    with closing(V5AttemptJournal(JOURNAL)) as journal:
        if (
            prefix_digest(journal, previous["journal_integrity"]["event_count"])
            != previous["journal_integrity"]["event_chain_digest"]
        ):
            raise ValueError("previous journal prefix changed")
        prefix_events = journal.events()[: previous["journal_integrity"]["event_count"]]
        prior_actions = sum(e.kind == "dispatch_started" for e in prefix_events)
        prior_attempts = sum(e.kind == "attempt_started" for e in prefix_events)
    value = {
        "schema_version": "pixelgym-d58-reliable-diagnostic-plan-v1",
        "phase_id": PHASE,
        "owner_approval": "go ahead with the repairs. I don't want the tests stopping incessantly from random, retryable network issues.",
        "approval_scope": "approved proposed repair and bounded reliability diagnostic; 20 wire calls or USD 1 within existing USD 28 aggregate; preserve prior ten failures; no confirmatory evaluation",
        "execution_enabled": True,
        "aggregate_cap_usd": "28",
        "phase_cap_usd": "1",
        "wire_call_cap": 20,
        "phase_caps": CallCaps(10, 20, 0, 20).to_dict(),
        "aggregate_caps": CallCaps(
            prior_actions + 10,
            prior_attempts + 20,
            0,
            previous["aggregate_spend"]["wire_requests_sent"] + 20,
        ).to_dict(),
        "scripted_prefix_actions": sum(
            j["case"]["scripted_prefix_action_count"] for j in diagnostic_jobs(PHASE)
        ),
        "phase_time_limit_seconds": 5400,
        "jobs": diagnostic_jobs(PHASE),
        "source_digests": sources,
        "driver_code_revision": revision,
        "price_snapshot_digest": content_digest(snapshot),
        "previous_summary_digest": content_digest(previous),
        "prior_spend": previous["aggregate_spend"],
        "prior_integrity": previous["journal_integrity"],
        "policy_manifests": {
            m: build_reliable_manifest(
                ROOT, config=config, code_revision=revision, retain_screenshots=m == "history"
            ).to_dict()
            for m in ("history", "stateless")
        },
        "curl_identity": read(PUBLIC / "curl-identity.json"),
        "acceptance": "all ten logical actions dispatch once, caps honored, no active child or in-flight hold; report every recovered failure; not a full-run reliability guarantee",
        "interruption": "close phase and retain every attempt; never restart diagnostic assignments",
    }
    return {**value, "execution_plan_digest": content_digest(value)}


def summarize(
    journal: V5AttemptJournal, ledger: ReboundedMemoryLedger, plan: dict[str, Any], stop: str
) -> dict[str, Any]:
    rows = []
    for job in plan["jobs"]:
        event = journal.event(f"{job['trial_id']}/result")
        rows.append(
            event.payload
            if event
            else {
                "trial_id": job["trial_id"],
                "mode": job["mode"],
                "case_id": job["case"]["case_id"],
                "classification": "interrupted" if journal.events(job["trial_id"]) else "not_run",
                "action_dispatched": False,
                "model_attempts": sum(
                    e.kind == "attempt_started" for e in journal.events(job["trial_id"])
                ),
            }
        )
    prefix = f"reliable/{content_digest(PHASE)}"
    receipts = [e.payload for e in journal.events(prefix) if e.kind == "reliable_transport_receipt"]
    closed = journal.event(f"{PHASE}/closed")
    return {
        "schema_version": "pixelgym-d58-reliable-diagnostic-summary-v1",
        "execution_plan_digest": plan["execution_plan_digest"],
        "conditions": rows,
        "stop_reason": stop,
        "dispatched": sum(r["action_dispatched"] for r in rows),
        "new_wire_requests": ledger.wire_requests_sent - plan["prior_spend"]["wire_requests_sent"],
        "new_known_spend_usd": str(ledger.spent_usd - Decimal(plan["prior_spend"]["spent_usd"])),
        "new_unknown_holds_usd": str(
            ledger.unknown_reservation_usd - Decimal(plan["prior_spend"]["unknown_reservation_usd"])
        ),
        "aggregate_spend": ledger.to_dict(),
        "transport_receipts": receipts,
        "journal_integrity": journal.integrity_report(),
        "transport_idle_at_close": closed.payload["transport_idle"] if closed else False,
    }


def publish(summary: dict[str, Any]) -> None:
    write(PUBLIC / "summary.json", summary)
    lines = [
        "# D5.8 transport reliability diagnostic",
        "",
        "Ten supplied-state logical actions; retries keep the same request. These are not end-to-end calibration episodes.",
        "",
        f"Stop: `{summary['stop_reason']}`. Actions dispatched: {summary['dispatched']}/10. Wire calls: {summary['new_wire_requests']}/20.",
        "",
        f"New known charges: USD {summary['new_known_spend_usd']}; new unknown holds: USD {summary['new_unknown_holds_usd']}.",
        "",
        "| Case | Mode | Classification | Attempts |",
        "|---|---|---|---:|",
    ]
    for row in summary["conditions"]:
        lines.append(
            f"| {row['case_id']} | {row['mode']} | {row['classification']} | {row['model_attempts']} |"
        )
    lines += [
        "",
        "All raw attempts and response envelopes remain in the private journal. [Summary](summary.json) and [plan](execution-plan.json) preserve caps and provenance. The ten prior failed calibration assignments remain unchanged; 90 remain unrun.",
        "",
    ]
    (PUBLIC / "report.md").write_text("\n".join(lines))


def execute(digest: str) -> None:
    plan = read(PUBLIC / "execution-plan.json")
    if plan != canonical_plan() or digest != plan["execution_plan_digest"]:
        raise ValueError("execution differs from frozen plan")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean")
    for name in (*NEW_SOURCES, str((PUBLIC / "execution-plan.json").relative_to(ROOT))):
        git("ls-files", "--error-unmatch", name)
    actual = subprocess.run(
        ["/usr/bin/curl", "--version"], capture_output=True, text=True, check=True
    ).stdout
    if actual != plan["curl_identity"]["version_output"]:
        raise ValueError("curl build changed")
    if (
        "sha256:" + sha256_bytes(Path("/usr/bin/curl").read_bytes())
        != plan["curl_identity"]["executable_digest"]
    ):
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
                raise ValueError("closed or interrupted diagnostic cannot restart")
            ledger = ReboundedMemoryLedger(Decimal(28), Decimal(0), journal=journal)
            if (
                ledger.to_dict() != plan["prior_spend"]
                or journal.integrity_report() != plan["prior_integrity"]
            ):
                raise ValueError("aggregate journal changed before diagnostic")
            journal.append_event(
                event_key=f"{PHASE}/started",
                kind="reliable_diagnostic_phase_started",
                trial_id=PHASE,
                step_index=0,
                payload={"execution_plan_digest": digest, "started_at": time.time()},
            )
            transport = ReliableTransport(
                config,
                lifecycle_id=PHASE,
                ledger=ledger,
                phase_deadline=time.time() + 5400,
                phase_spend_limit=Decimal(plan["phase_cap_usd"]),
            )
            caps = CallCaps(**plan["aggregate_caps"])
            stop = "interrupted"
            try:
                for job in plan["jobs"]:
                    row = run_condition(
                        journal,
                        job=job,
                        transport=transport,
                        revision=plan["driver_code_revision"],
                        plan_digest=digest,
                        caps=caps,
                    )
                    print(row, flush=True)
                    if transport.retired or ledger.blocked:
                        stop = "transport_or_identity_stop"
                        break
                    if (
                        ledger.budget_accounted_spend_usd - transport.phase_start_accounted
                        >= transport.phase_spend_limit
                    ):
                        stop = "phase_cap_stop"
                        break
                    if ledger.wire_requests_sent - plan["prior_spend"]["wire_requests_sent"] >= 20:
                        stop = "wire_cap_stop"
                        break
                else:
                    stop = "all_conditions_recorded"
            finally:
                if not transport.wait_until_idle(5):
                    transport.wire.abort()
                idle = transport.wait_until_idle(5) and transport.wire.idle
                journal.append_event(
                    event_key=f"{PHASE}/closed",
                    kind="reliable_diagnostic_phase_closed",
                    trial_id=PHASE,
                    step_index=0,
                    payload={"stop_reason": stop, "transport_idle": idle},
                )
                summary = summarize(journal, ledger, plan, stop)
                publish(summary)
                print(
                    {
                        k: summary[k]
                        for k in (
                            "stop_reason",
                            "dispatched",
                            "new_wire_requests",
                            "new_known_spend_usd",
                            "aggregate_spend",
                        )
                    },
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
