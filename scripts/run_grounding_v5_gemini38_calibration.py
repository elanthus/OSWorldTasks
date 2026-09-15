"""Freeze and execute the owner-approved Gemini 3.8 comparison under USD 20 total."""

from __future__ import annotations

import argparse
import base64
import fcntl
import io
import json
import os
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from PIL import Image

from pixelgym.grounding.v5.contracts import CallCaps, content_digest, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_calibration import run_episode, summarize
from pixelgym.grounding.v5.memory_plan import ScreenshotPriceConfig, config_from_price_snapshot
from pixelgym.grounding.v5.request_budget import (
    BUDGET_VERSION,
    MAX_WORKLOAD_INPUT_TOKENS,
    ReboundedMemoryLedger,
    RequestBoundTransport,
    request_bound,
)
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    build_screenshot_policy_manifest,
)
from scripts.prepare_grounding_v5_memory import verify as verify_admission
from scripts.run_grounding_v5_memory_calibration import (
    JOURNAL,
    PRIVATE,
    ROOT,
    git,
    identity_failure,
    prefix_digest,
    read,
    verify_full_admission,
    write,
)

PUBLIC = ROOT / "artifacts/grounding-v5-d58-gemini38-calibration"
PREVIOUS = ROOT / "artifacts/grounding-v5-d58-full-calibration"
PHASE = "d58-gemini38-memory-calibration-v1"
MODEL = "google/gemini-3.8-flash"
CEILING = Decimal("20.00")
DRIVERS = (
    "pixelgym/grounding/v5/request_budget.py",
    "scripts/run_grounding_v5_gemini38_calibration.py",
    "pixelgym/grounding/v5/memory_calibration.py",
    "scripts/run_grounding_v5_memory_calibration.py",
)


def config_from_snapshot(snapshot: dict[str, Any]) -> ScreenshotPriceConfig:
    if snapshot.get("model") != MODEL:
        raise ValueError("price snapshot identifies a different model")
    base = config_from_price_snapshot(snapshot)
    endpoint = next(row for row in snapshot["endpoints"] if row["tag"] == "google-vertex/global")
    return replace(
        base,
        model=MODEL,
        slot="d58-gemini38-screenshot-v1",
        price_source=snapshot["source_url"],
        upstream_context_length=MAX_WORKLOAD_INPUT_TOKENS,
        prompt_price_per_token_usd=Decimal(endpoint["pricing"]["prompt"]),
        completion_price_per_token_usd=Decimal(endpoint["pricing"]["completion"]),
    )


def prior_hold_proofs(journal: V5AttemptJournal) -> list[dict[str, Any]]:
    """Reconstruct the two old failed requests, never interpret their charge as zero."""
    old_plan = read(PREVIOUS / "execution-plan.json")
    old_config = config_from_price_snapshot(read(PREVIOUS / "price-recheck.json"))
    old_summary = read(PREVIOUS / "summary.json")
    proofs = []
    for row in old_summary["conditions"]:
        if row["classification"] != "infrastructure_failure":
            continue
        attempts = [e for e in journal.events(row["trial_id"]) if e.kind == "attempt_started"]
        event = attempts[-1]
        terminals = [
            e
            for e in journal.events(row["trial_id"])
            if e.step_index == event.step_index
            and e.kind == "unknown_outcome_infrastructure_failure"
        ]
        if len(terminals) != 1 or terminals[0].payload.get("response_digest") is not None:
            raise ValueError("legacy failure is not the recorded unknown transport outcome")
        # The transport returned no response ID or cost; the frozen summary remains failed.
        state = journal.get_object(
            event.payload["pre_call_checkpoint_digest"], expected_kind="policy_checkpoint"
        )
        value = json.loads(state)
        if row["mode"] != "history":
            raise ValueError("legacy hold reconstruction expects stored screenshot history")
        png = base64.b64decode(value["frames"][-1]["image_url"].split(",", 1)[1])
        with Image.open(io.BytesIO(png)) as image:
            pixels = image.convert("RGB").tobytes()
        policy = ScreenshotMemoryPolicy(old_config, retain_screenshots=True)
        request = policy.build_request(state, pixels)
        if content_digest(request) != event.payload["request_digest"]:
            raise ValueError("legacy request reconstruction differs from the stored digest")
        bound = request_bound(request, old_config)
        rid = content_digest(event.payload["idempotency_key"])
        original = journal.event(f"spend/{rid}/unknown")
        if original is None:
            raise ValueError("legacy failure lacks its original unknown hold")
        proofs.append(
            {
                "reservation_id": rid,
                "trial_id": row["trial_id"],
                "old_bound_usd": original.payload["unknown_reservation_usd"],
                "new_bound_usd": bound["request_maximum_usd"],
                "request_bound": bound,
                "original_plan_digest": old_plan["execution_plan_digest"],
                "cost_knowledge": "unknown",
                "provider_response_id_available": False,
            }
        )
    if len(proofs) != 2:
        raise ValueError("legacy hold inventory differs from the reviewed two failures")
    return proofs


def canonical_plan() -> dict[str, Any]:
    verify_admission()
    admission = verify_full_admission()
    old_plan, old_summary = read(PREVIOUS / "execution-plan.json"), read(PREVIOUS / "summary.json")
    snapshot = read(PUBLIC / "price-recheck.json")
    config = config_from_snapshot(snapshot)
    if not JOURNAL.exists():
        raise FileNotFoundError("original aggregate journal required")
    count = old_summary["journal_integrity"]["event_count"]
    with closing(V5AttemptJournal(JOURNAL)) as journal:
        prefix = prefix_digest(journal, count)
        if prefix != old_summary["journal_integrity"]["event_chain_digest"]:
            raise ValueError("previous campaign prefix changed")
        proofs = prior_hold_proofs(journal)
    jobs = [{**job, "trial_id": f"{PHASE}-{job['seed']}-{job['mode']}"} for job in old_plan["jobs"]]
    actions = sum(job["action_limit"] for job in jobs)
    code_revision = git("log", "-1", "--format=%H", "--", *DRIVERS)
    policy_revision = old_plan["policy_manifests"]["history"]["code_revision"]
    value = {
        "schema_version": "pixelgym-d58-gemini38-calibration-plan-v1",
        "phase_id": PHASE,
        "owner_approval": "switch to gemini 3.8, increase the budget up to 20 dollars",
        "assignment_approval": "All 50 tasks, 100 episodes (recommended)",
        "approval_scope": "fresh matched Gemini 3.8 full calibration; USD 20 aggregate including all previous D5.8 charges and bounded unknown outcomes; no confirmatory calls",
        "execution_enabled": True,
        "aggregate_ceiling_usd": str(CEILING),
        "jobs": jobs,
        "assigned_episodes": len(jobs),
        "admission_digest": admission["admission_digest"],
        "previous_execution_plan_digest": old_plan["execution_plan_digest"],
        "previous_summary_digest": content_digest(old_summary),
        "prior_pilot_spend": old_summary["aggregate_spend"],
        "prior_event_count": count,
        "prior_event_prefix_digest": prefix,
        "prior_unknown_bound_revisions": proofs,
        "policy_manifests": {
            mode: build_screenshot_policy_manifest(
                ROOT,
                config=config,
                code_revision=policy_revision,
                retain_screenshots=mode == "history",
            ).to_dict()
            for mode in ("history", "stateless")
        },
        "driver_code_revision": code_revision,
        "driver_source_digest": content_digest(
            {p: "sha256:" + sha256_bytes((ROOT / p).read_bytes()) for p in DRIVERS}
        ),
        "price_recheck_digest": content_digest(snapshot),
        "phase_caps": CallCaps(actions, actions, 0, actions).to_dict(),
        "aggregate_caps": CallCaps(
            old_plan["aggregate_caps"]["environment_action_cap"] + actions,
            old_summary["model_attempt_reservations"] + actions,
            0,
            old_summary["aggregate_spend"]["wire_requests_sent"] + actions,
        ).to_dict(),
        "budget_rule": BUDGET_VERSION,
        "budget_assumptions": "4096 tokens per unchanged 1024x768 PNG (Google documents 2240 at ultra-high), UTF-8 byte count of remaining request metadata, 8192 framing tokens, at most 163840 input allowance and 4096 output; cached-input discounts ignored; only declared Vertex route and explicit max-price cap; stop on a returned charge above its reservation",
        "budget_source": "https://ai.google.dev/gemini-api/docs/media-resolution",
        "maximum_request_reservation_usd": str(config.request_maximum_usd),
        "unknown_charge_rule": "retain each request-sized reservation unless a confirmed charge replaces it; no assumption that a failed request costs zero",
        "provider_retries": 0,
        "provider_control_call_cap": 0,
        "confirmatory_call_cap": 0,
        "ordering": old_plan["ordering"],
        "interruption_rule": old_plan["interruption_rule"],
        "stop_rules": old_plan["stop_rules"][1:]
        + ["before the next worst-case workload reservation exceeds USD 20 total"],
        "analysis_rule": "standalone Gemini 3.8 cohort; never pool terminal results with Gemini 3.7 or its scripted-prefix pilot; retain all failures and unrun assignments",
        "completion_within_budget_guaranteed": False,
    }
    return {**value, "execution_plan_digest": content_digest(value)}


def phase_summary(
    journal: V5AttemptJournal, ledger: ReboundedMemoryLedger, plan: dict[str, Any], stop: str
) -> dict[str, Any]:
    result = summarize(journal, ledger, plan, stop_reason=stop)
    result["schema_version"] = "pixelgym-d58-gemini38-calibration-summary-v1"
    result["model"] = MODEL
    result["prior_phase_known_spend_usd"] = result.pop("prior_pilot_spend_usd")
    result["new_phase_known_spend_usd"] = str(
        ledger.spent_usd - Decimal(result["prior_phase_known_spend_usd"])
    )
    result["new_phase_wire_requests"] = (
        ledger.wire_requests_sent - plan["prior_pilot_spend"]["wire_requests_sent"]
    )
    result["aggregate_ceiling_usd"] = str(CEILING)
    result["unknown_bound_revisions"] = plan["prior_unknown_bound_revisions"]
    return result


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# D5.8 Gemini 3.8 Flash calibration",
        "",
        f"Complete: **{summary['complete']}**. Stop: `{summary['stop_reason']}`.",
        "",
        "| Mode | Assigned | Attempted | Terminal successes | Reached both consumers | Correct first memory attempts / attempted |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mode, row in sorted(summary["scores"].items()):
        lines.append(
            f"| {mode} | {row['assigned']} | {row['attempted_episodes']} | {row['terminal_successes']} | {row['reached_both_consumers']} | {row['correct_first_memory_attempts']} / {row['first_memory_attempts']} |"
        )
    spend = summary["aggregate_spend"]
    lines += [
        "",
        f"New phase: {summary['new_phase_wire_requests']} requests and USD {summary['new_phase_known_spend_usd']} known charges. Aggregate known charges: USD {spend['spent_usd']}; unknown holds: USD {spend['unknown_reservation_usd']}; in-flight holds: USD {spend['in_flight_reservation_usd']}. Shared ceiling: USD 20.00.",
        "",
        "The fresh Gemini 3.8 cohort starts all episodes at reset with no scripted prefixes. All assignments and failures remain explicit. Gemini 3.7 results stay separate. Unknown holds are conservative request-size estimates, not billed charges; they are not set to zero. No retries, provider control calls, or confirmatory calls are authorized by this phase.",
        "",
        "D5.8 final approval remains an owner decision. Incomplete coverage cannot establish complete calibration rates or confirmatory power.",
        "",
        "[Stored summary](summary.json), [approved execution plan](execution-plan.json), [prices](price-recheck.json). Original private responses and checkpoints remain in the ignored aggregate journal.",
        "",
    ]
    return "\n".join(lines)


def publish(summary: dict[str, Any]) -> None:
    write(PUBLIC / "summary.json", summary)
    (PUBLIC / "report.md").write_text(render_report(summary))


def execute(digest: str) -> None:
    plan = read(PUBLIC / "execution-plan.json")
    if plan != canonical_plan() or digest != plan["execution_plan_digest"]:
        raise ValueError("runtime differs from the approved frozen plan")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before execution")
    for name in (*DRIVERS, str((PUBLIC / "execution-plan.json").relative_to(ROOT))):
        git("ls-files", "--error-unmatch", name)
    age = (
        datetime.now(UTC)
        - datetime.fromisoformat(read(PUBLIC / "price-recheck.json")["observed_at"])
    ).total_seconds()
    if not 0 <= age <= 86400:
        raise ValueError("price recheck must be less than 24 hours old")
    config = config_from_snapshot(read(PUBLIC / "price-recheck.json"))
    with (PRIVATE / "operator.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(V5AttemptJournal(JOURNAL)) as journal:
            ledger = ReboundedMemoryLedger(CEILING, Decimal(0), journal=journal)
            binding = {"execution_plan_digest": digest, "aggregate_ceiling_usd": str(CEILING)}
            started = journal.event(f"{PHASE}/started")
            if started is None:
                if (
                    ledger.to_dict() != plan["prior_pilot_spend"]
                    or len(journal.events()) != plan["prior_event_count"]
                ):
                    raise ValueError("aggregate ledger differs from the approved carry-forward")
                journal.append_event(
                    event_key=f"{PHASE}/started",
                    kind="memory_gemini38_phase_started",
                    trial_id=PHASE,
                    step_index=0,
                    payload=binding,
                )
            elif started.payload != binding:
                raise ValueError("phase has a different plan binding")
            for proof in plan["prior_unknown_bound_revisions"]:
                ledger.revise_unknown_bound(proof, plan_digest=digest)
            closed = journal.event(f"{PHASE}/closed")
            stop = closed.payload["stop_reason"] if closed else "interrupted"
            caps = CallCaps(**plan["aggregate_caps"])
            failures = 0
            transports: dict[str, RequestBoundTransport] = {}
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
                                > CEILING
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
                            raise ValueError("policy manifest changed")
                        if mode not in transports:
                            transports[mode] = RequestBoundTransport(config, ledger=ledger)

                        def progress(name: str, trial: str = job["trial_id"]) -> None:
                            if name == "attempt_terminal":
                                print(
                                    json.dumps(
                                        {
                                            "event": "request_settled",
                                            "trial_id": trial,
                                            "aggregate_requests": ledger.wire_requests_sent,
                                            "known_spend_usd": str(ledger.spent_usd),
                                            "unknown_holds_usd": str(
                                                ledger.unknown_reservation_usd
                                            ),
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
                        kind="memory_gemini38_phase_closed",
                        trial_id=PHASE,
                        step_index=0,
                        payload={"stop_reason": stop},
                    )
            except BaseException:
                stop = "interrupted"
                raise
            finally:
                summary = phase_summary(journal, ledger, plan, stop)
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
    parser.add_argument("command", choices=("prepare", "execute", "report"))
    parser.add_argument("--approved-plan-digest")
    args = parser.parse_args()
    if args.command == "prepare":
        plan = canonical_plan()
        path = PUBLIC / "execution-plan.json"
        if path.exists() and read(path) != plan:
            raise ValueError("refusing to overwrite a frozen execution plan")
        write(path, plan)
        print(
            json.dumps(
                {
                    k: plan[k]
                    for k in (
                        "execution_plan_digest",
                        "assigned_episodes",
                        "aggregate_ceiling_usd",
                        "maximum_request_reservation_usd",
                        "prior_unknown_bound_revisions",
                    )
                },
                indent=2,
            )
        )
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
