"""Prepare, execute once, or report the approved twenty-call D5.8 memory pilot."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_pilot import PILOT_CAPS, run_condition, summarize
from pixelgym.grounding.v5.memory_plan import (
    TOTAL_REPAIR_BUDGET_USD,
    config_from_price_snapshot,
    pilot_plan,
)
from pixelgym.grounding.v5.panel_policy import OpenRouterPanelTransport, SpendLedger
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    build_screenshot_policy_manifest,
)
from scripts.prepare_grounding_v5_memory import verify as verify_admission

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "artifacts/grounding-v5-d58-calibration-pilot"
PRIVATE = ROOT / ".cache/d58-memory-calibration"
CANDIDATE = ROOT / "artifacts/grounding-v5-d58-design/memory-repair/pilot-plan.json"
OLD_PRICES = ROOT / "artifacts/grounding-v5-d58-design/gemini-price-snapshot.json"
PLAN = PUBLIC / "execution-plan.json"
LEDGER_MARKER = PUBLIC / "ledger-started.json"
JOURNAL = PRIVATE / "aggregate.sqlite"
PHASE_ID = "d58-memory-pilot-v1"
OWNER_APPROVAL = "OK, unit tests passed. Lets do the calbration"


def read_json(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text()))


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def driver_digest() -> str:
    return content_digest(
        {
            name: "sha256:" + sha256_bytes((ROOT / name).read_bytes())
            for name in (
                "pixelgym/grounding/v5/memory_pilot.py",
                "scripts/run_grounding_v5_memory_pilot.py",
            )
        }
    )


def canonical_plan() -> dict[str, Any]:
    verify_admission()
    candidate = read_json(CANDIDATE)
    source_binding = candidate["source_binding_digest"]
    revision = candidate["policy_manifests"]["history"]["code_revision"]
    expected = pilot_plan(ROOT, snapshot=read_json(OLD_PRICES), code_revision=revision)
    expected["source_binding_digest"] = source_binding
    expected["plan_digest"] = content_digest(
        {key: value for key, value in expected.items() if key != "plan_digest"}
    )
    if candidate != expected:
        raise ValueError("candidate task/policy plan has drifted")
    recheck = read_json(PUBLIC / "price-recheck.json")
    if config_from_price_snapshot(recheck) != config_from_price_snapshot(read_json(OLD_PRICES)):
        raise ValueError("provider bounds or prices changed from the approved candidate")
    jobs = []
    for index, case in enumerate(candidate["cases"]):
        for mode in ("history", "stateless") if index % 2 == 0 else ("stateless", "history"):
            jobs.append(
                {
                    "trial_id": f"{PHASE_ID}-{case['seed']}-{mode}",
                    "seed": case["seed"],
                    "mode": mode,
                }
            )
    value = {
        "schema_version": "pixelgym-v5-d58-memory-pilot-execution-v1",
        "phase_id": PHASE_ID,
        "candidate_plan_digest": candidate["plan_digest"],
        "driver_source_digest": driver_digest(),
        "driver_code_revision": git(
            "log",
            "-1",
            "--format=%H",
            "--",
            "pixelgym/grounding/v5/memory_pilot.py",
            "scripts/run_grounding_v5_memory_pilot.py",
        ),
        "price_recheck_digest": content_digest(recheck),
        "execution_enabled": True,
        "owner_approval": OWNER_APPROVAL,
        "approval_scope": "the previously presented ten-example, twenty-condition-call diagnostic under the existing shared USD 5 cap; no full episode or confirmatory run",
        "jobs": jobs,
        "caps": PILOT_CAPS.to_dict(),
        "aggregate_ceiling_usd": str(TOTAL_REPAIR_BUDGET_USD),
        "ledger_location": ".cache/d58-memory-calibration/aggregate.sqlite",
        "provider_retries": 0,
        "confirmatory_call_cap": 0,
        "stop_rule": "complete twenty assigned conditions or stop before the aggregate reservation exceeds USD 5; never resend an uncertain request; retain all errors and unrun assignments",
    }
    return {**value, "execution_plan_digest": content_digest(value)}


def render_report(summary: dict[str, Any]) -> str:
    """Render recorded outcomes only; no task execution or model output parsing."""
    lines = [
        "# D5.8 revised memory calibration pilot",
        "",
        "This is a scripted-prefix, first-attempt memory diagnostic. It is not an end-to-end episode score.",
        "",
        "| Condition | Assigned | Attempted | Correct first choices | Valid consumer choices |",
        "|---|---:|---:|---:|---:|",
    ]
    for mode in ("history", "stateless"):
        row = summary["scores"][mode]
        lines.append(
            f"| {mode} | {row['assigned']} | {row['attempted']} | {row['first_attempt_correct']} | {row['valid_consumer_choices']} |"
        )
    spend = summary["spend"]
    lines += [
        "",
        f"Stop reason: `{summary['stop_reason']}`.",
        "",
        (
            f"Provider wire requests: **{spend['wire_requests_sent']}**. "
            f"Known spend: **USD {spend['spent_usd']}**. "
            f"Unknown-charge reservations: **USD {spend['unknown_reservation_usd']}**. "
            f"In-flight reservations: **USD {spend['in_flight_reservation_usd']}**. "
            f"Aggregate ceiling: **USD {summary['maximum_aggregate_spend_usd']}**."
        ),
        "",
        (
            "Every assigned condition remains in the stored summary, including invalid outputs, "
            "infrastructure failures and assignments not run. No retry or response-dependent task selection is permitted."
        ),
        "",
        (
            "Both conditions use the same admitted consumer screen and scripted lead-in. The history condition "
            "receives the chronologically observed screenshots and executed actions; the stateless condition "
            "receives only the consumer screenshot. Condition order alternates by the frozen case index."
        ),
        "",
        (
            "[Stored summary](summary.json), [execution approval and caps](execution-plan.json), "
            "[price recheck](price-recheck.json). Provider text and private checkpoints remain in the ignored "
            "authoritative journal. The ledger is shared with future D5.8 phases and must be preserved."
        ),
        "",
        (
            "These ten paired diagnostic cases cannot estimate end-to-end consumer reachability, terminal "
            "success, or confirmatory power. A full calibration requires the next explicit approval; "
            "the final D5.8 freeze and confirmatory execution remain open."
        ),
        "",
    ]
    return "\n".join(lines)


def publish_summary(summary: dict[str, Any]) -> None:
    destination = PUBLIC / "summary.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    temporary.replace(destination)
    (PUBLIC / "report.md").write_text(render_report(read_json(destination)))


def bind_ledger(marker: Path, journal_path: Path, binding: dict[str, Any]) -> None:
    """A missing or mismatched authoritative ledger can never create a fresh budget."""
    if marker.exists() and not journal_path.exists():
        raise RuntimeError(
            "authoritative aggregate ledger is missing; a new allowance is forbidden"
        )
    if marker.exists() and read_json(marker) != binding:
        raise ValueError("ledger marker belongs to a different approved execution")
    if not marker.exists():
        if journal_path.exists():
            raise RuntimeError("existing journal lacks its durable ledger marker")
        marker.write_text(json.dumps(binding, sort_keys=True, indent=2) + "\n")


def execute(approved_digest: str) -> dict[str, Any]:
    plan = read_json(PLAN)
    if approved_digest != plan["execution_plan_digest"] or plan != canonical_plan():
        raise ValueError("execution approval does not match the frozen plan")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before provider requests")
    for path in (
        "pixelgym/grounding/v5/memory_pilot.py",
        "scripts/run_grounding_v5_memory_pilot.py",
    ):
        git("ls-files", "--error-unmatch", path)
    recheck_age = (
        datetime.now(UTC)
        - datetime.fromisoformat(read_json(PUBLIC / "price-recheck.json")["observed_at"])
    ).total_seconds()
    if not 0 <= recheck_age <= 86400:
        raise ValueError("the endpoint price recheck must be within the preceding 24 hours")
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (PRIVATE / "operator.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        binding = {
            "schema_version": "pixelgym-d58-aggregate-ledger-binding-v1",
            "phase_id": PHASE_ID,
            "execution_plan_digest": approved_digest,
            "maximum_aggregate_spend_usd": str(TOTAL_REPAIR_BUDGET_USD),
        }
        bind_ledger(LEDGER_MARKER, JOURNAL, binding)
        with closing(V5AttemptJournal(JOURNAL)) as journal:
            initialized = journal.event(f"{PHASE_ID}/ledger_initialized")
            if initialized is None:
                if journal.events():
                    raise ValueError("unrecognized prior ledger events")
                journal.append_event(
                    event_key=f"{PHASE_ID}/ledger_initialized",
                    kind="memory_ledger_initialized",
                    trial_id=PHASE_ID,
                    step_index=0,
                    payload=binding,
                )
            elif initialized.payload != binding:
                raise ValueError("journal belongs to a different approved execution")
            ledger = SpendLedger(TOTAL_REPAIR_BUDGET_USD, Decimal(0), journal=journal)
            candidate = read_json(CANDIDATE)
            config = config_from_price_snapshot(read_json(OLD_PRICES))
            cases = {case["seed"]: case for case in candidate["cases"]}
            closed = journal.event(f"{PHASE_ID}/closed")
            stop_reason = (
                closed.payload["stop_reason"] if closed is not None else "all_conditions_completed"
            )
            transports: dict[str, OpenRouterPanelTransport] = {}
            if closed is None:
                for job in plan["jobs"]:
                    trial_id, mode = job["trial_id"], job["mode"]
                    if journal.event(f"{trial_id}/result") is not None:
                        continue
                    in_progress = any(
                        event.kind == "attempt_started" for event in journal.events(trial_id)
                    )
                    if not in_progress and (
                        ledger.blocked
                        or ledger.budget_accounted_spend_usd + config.request_maximum_usd
                        > TOTAL_REPAIR_BUDGET_USD
                        or journal.call_counts()[0] >= PILOT_CAPS.model_attempt_cap
                    ):
                        stop_reason = "aggregate_budget_or_call_cap_stop"
                        break
                    manifest = build_screenshot_policy_manifest(
                        ROOT,
                        config=config,
                        code_revision=candidate["policy_manifests"][mode]["code_revision"],
                        retain_screenshots=mode == "history",
                    )
                    if manifest.to_dict() != candidate["policy_manifests"][mode]:
                        raise ValueError(
                            "runtime candidate policy differs from its approved manifest"
                        )
                    if mode not in transports:
                        transports[mode] = OpenRouterPanelTransport(config, ledger=ledger)
                    row = run_condition(
                        journal,
                        case=cases[job["seed"]],
                        mode=mode,
                        trial_id=trial_id,
                        plan_digest=approved_digest,
                        policy=ScreenshotMemoryPolicy(config, retain_screenshots=mode == "history"),
                        manifest=manifest,
                        transport=transports[mode],
                    )
                    print(
                        json.dumps(
                            {
                                "seed": row["seed"],
                                "mode": mode,
                                "classification": row["classification"],
                                "first_attempt_correct": row["first_attempt_correct"],
                                "spend": ledger.to_dict(),
                            }
                        ),
                        flush=True,
                    )
                journal.append_event(
                    event_key=f"{PHASE_ID}/closed",
                    kind="memory_pilot_closed",
                    trial_id=PHASE_ID,
                    step_index=0,
                    payload={"stop_reason": stop_reason},
                )
            summary = summarize(
                journal,
                ledger,
                jobs=plan["jobs"],
                plan_digest=approved_digest,
                stop_reason=stop_reason,
            )
            summary["execution_code_revision"] = plan["driver_code_revision"]
            summary["cleanup"] = {
                "journal_closed_on_return": True,
                "policy_and_environments_closed": True,
                "http_responses_closed_by_transport": True,
            }
        publish_summary(summary)
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "execute", "report"))
    parser.add_argument("--approved-plan-digest")
    args = parser.parse_args()
    if args.command == "prepare":
        value = canonical_plan()
        if PLAN.exists() and read_json(PLAN) != value:
            raise ValueError("refusing to replace a frozen execution plan")
        PLAN.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        print(
            json.dumps(
                {
                    "execution_plan_digest": value["execution_plan_digest"],
                    "caps": value["caps"],
                    "aggregate_ceiling_usd": value["aggregate_ceiling_usd"],
                }
            )
        )
    elif args.command == "report":
        (PUBLIC / "report.md").write_text(render_report(read_json(PUBLIC / "summary.json")))
    elif not args.approved_plan_digest:
        parser.error("execute requires --approved-plan-digest")
    else:
        old_umask = os.umask(0o077)
        try:
            summary = execute(args.approved_plan_digest)
            print(
                json.dumps(
                    {
                        "scores": summary["scores"],
                        "spend": summary["spend"],
                        "stop_reason": summary["stop_reason"],
                    }
                )
            )
        finally:
            os.umask(old_umask)


if __name__ == "__main__":
    main()
