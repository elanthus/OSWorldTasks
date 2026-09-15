"""Successor with optimization-safe checks; the frozen original is reproduction-only.

Analyze stored outcomes and verify the closed journal without provider calls."""

import argparse
import json
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger
from scripts.run_grounding_v5_gemini38_calibration import (
    CEILING,
    JOURNAL,
    PHASE,
    canonical_plan,
    config_from_snapshot,
    phase_summary,
    render_report,
)
from scripts.run_grounding_v5_memory_calibration import identity_failure, prefix_digest

DIRECTORY = Path(__file__).resolve().parents[1] / "artifacts/grounding-v5-d58-gemini38-calibration"


def read(name):
    return json.loads((DIRECTORY / name).read_text())


def paired_counts(rows):
    counts = Counter()
    for seed in sorted({r["seed"] for r in rows}):
        pair = {r["mode"]: r for r in rows if r["seed"] == seed}
        if not (set(pair) == {"history", "stateless"}):
            raise ValueError("evidence check failed (gemini38-calibration:35)")
        if any(
            r["classification"] in ("not_run", "budget_stop", "interrupted_episode")
            for r in pair.values()
        ):
            counts["incomplete"] += 1
        else:
            h, s = pair["history"]["success"], pair["stateless"]["success"]
            counts[
                "both_success"
                if h and s
                else "history_only"
                if h
                else "stateless_only"
                if s
                else "neither_success"
            ] += 1
    return {
        key: counts[key]
        for key in (
            "both_success",
            "history_only",
            "stateless_only",
            "neither_success",
            "incomplete",
        )
    }


def analyze(summary, plan):
    rows = summary["conditions"]
    if not (len(rows) == len(plan["jobs"]) == 100):
        raise ValueError("evidence check failed (gemini38-calibration:66)")
    for row, job in zip(rows, plan["jobs"], strict=True):
        if not (all(row[k] == v for k, v in job.items())):
            raise ValueError("evidence check failed (gemini38-calibration:68)")
    logical = {}
    for row in rows:
        logical.setdefault(row["seed_record"]["logical_id"], set()).add(row["seed"])
    # The smallest assigned seed in each logical cluster is chosen without outcomes.
    representatives = {min(seeds) for seeds in logical.values()}
    scores = summary["scores"]
    missing_history_exposure = sum(
        r["mode"] == "history"
        and not r["reached_both_consumers"]
        and r["classification"] in ("not_run", "budget_stop", "interrupted_episode")
        for r in rows
    )
    return {
        "schema_version": "pixelgym-d58-gemini38-descriptive-analysis-v1",
        "summary_digest": content_digest(summary),
        "execution_plan_digest": plan["execution_plan_digest"],
        "complete": summary["complete"],
        "paired_terminal_outcomes": paired_counts(rows),
        "logical_clusters": len(logical),
        "representative_seeds": sorted(representatives),
        "representative_terminal_outcomes": paired_counts(
            [r for r in rows if r["seed"] in representatives]
        ),
        "first_attempt_and_exposure_counts": scores,
        "started_final_stages": {
            mode: dict(
                sorted(
                    Counter(
                        str(r["final_stage_index"])
                        for r in rows
                        if r["mode"] == mode and r["model_attempts"]
                    ).items()
                )
            )
            for mode in ("history", "stateless")
        },
        "new_phase_wire_requests": summary["new_phase_wire_requests"],
        "new_phase_known_spend_usd": summary["new_phase_known_spend_usd"],
        "aggregate_spend": summary["aggregate_spend"],
        "planned_exposure_threshold_count": 40,
        "history_exposure_upper_bound_including_missing": (
            scores["history"]["reached_both_consumers"] + missing_history_exposure
        ),
        "planned_mixed_terminal_outcome_range": [10, 40],
        "calibration_threshold_scope": "Compare history consumer exposure with 40/50 and history terminal success with 10..40/50; these are planning thresholds, not a milestone verdict. Missing episodes do not count as observed model failures.",
        "inferential_test": None,
        "confirmatory_power_estimate": None,
        "interpretation": "Descriptive Gemini 3.8 cohort only; do not pool with 3.7 or its scripted-prefix pilot. Infrastructure failures are retained. Final confirmation remains unapproved.",
        "provider_calls": 0,
    }


def verify_journal(summary, plan):
    if not (canonical_plan() == plan):
        raise ValueError("evidence check failed (gemini38-calibration:122)")
    config = config_from_snapshot(read("price-recheck.json"))
    if not JOURNAL.exists():
        raise FileNotFoundError("original private journal is required")
    with closing(V5AttemptJournal(JOURNAL)) as journal:
        closed = journal.event(f"{PHASE}/closed")
        if not (closed is not None):
            raise ValueError("evidence check failed (gemini38-calibration:128)")
        ledger = ReboundedMemoryLedger(CEILING, Decimal(0), journal=journal)
        if not (summary == phase_summary(journal, ledger, plan, closed.payload["stop_reason"])):
            raise ValueError("evidence check failed (gemini38-calibration:130)")
        if not (
            prefix_digest(journal, plan["prior_event_count"]) == plan["prior_event_prefix_digest"]
        ):
            raise ValueError("evidence check failed (gemini38-calibration:131)")
        attempts = [
            e
            for e in journal.events()
            if e.trial_id.startswith(PHASE + "-") and e.kind == "attempt_started"
        ]
        if not (all(e.attempt_index == 0 for e in attempts)):
            raise ValueError("evidence check failed (gemini38-calibration:139)")
        if not (len({(e.trial_id, e.step_index) for e in attempts}) == len(attempts)):
            raise ValueError("evidence check failed (gemini38-calibration:140)")
        bounded = []
        charges = []
        settlements = Counter()
        for attempt in attempts:
            rid = content_digest(attempt.payload["idempotency_key"])
            bound = journal.event(f"spend/{rid}/request-bound")
            reservation = journal.event(f"spend/{rid}/reserved")
            if reservation:
                if not (
                    bound and bound.payload["request_digest"] == attempt.payload["request_digest"]
                ):
                    raise ValueError("evidence check failed (gemini38-calibration:149)")
                if not (
                    bound.payload["request_maximum_usd"]
                    == reservation.payload["request_maximum_usd"]
                ):
                    raise ValueError("evidence check failed (gemini38-calibration:152)")
                bounded.append(bound.payload)
                charged = journal.event(f"spend/{rid}/charged")
                released = journal.event(f"spend/{rid}/released")
                unknown = journal.event(f"spend/{rid}/unknown")
                if charged:
                    cost = Decimal(charged.payload["cost_usd"])
                    charges.append(cost)
                    settlements["confirmed_zero" if cost == 0 else "confirmed_positive"] += 1
                elif released:
                    settlements["released_zero_charge"] += 1
                else:
                    if not (unknown is not None):
                        raise ValueError("evidence check failed (gemini38-calibration:167)")
                    settlements["unknown"] += 1
        if not (len(bounded) == summary["new_phase_wire_requests"]):
            raise ValueError("evidence check failed (gemini38-calibration:169)")
        if not (sum(settlements.values()) == len(bounded)):
            raise ValueError("evidence check failed (gemini38-calibration:170)")
        if not (sum(charges, Decimal(0)) == Decimal(summary["new_phase_known_spend_usd"])):
            raise ValueError("evidence check failed (gemini38-calibration:171)")
        if not (len(attempts) == sum(r["model_attempts"] for r in summary["conditions"])):
            raise ValueError("evidence check failed (gemini38-calibration:172)")
        if not (journal.call_counts()[1] == 0):
            raise ValueError("evidence check failed (gemini38-calibration:173)")
        if not (ledger.in_flight_reservation_usd == 0):
            raise ValueError("evidence check failed (gemini38-calibration:174)")
        if not (ledger.budget_accounted_spend_usd <= CEILING):
            raise ValueError("evidence check failed (gemini38-calibration:175)")
        for job in plan["jobs"]:
            if identity_failure(journal, job["trial_id"], config):
                raise ValueError("evidence check failed (gemini38-calibration:177)")
        phase_events = [e for e in journal.events() if e.trial_id.startswith(PHASE + "-")]
        failure_counts = {
            kind: dict(
                sorted(
                    Counter(
                        e.payload.get("failure_code", "unspecified")
                        for e in phase_events
                        if e.kind == kind
                    ).items()
                )
            )
            for kind in (
                "sealed_unsuccessful_result",
                "retryable_provider_response",
                "unknown_outcome_infrastructure_failure",
            )
        }
        return {
            "schema_version": "pixelgym-d58-gemini38-evidence-verification-v1",
            "summary_digest": content_digest(summary),
            "execution_plan_digest": plan["execution_plan_digest"],
            "journal_integrity": journal.integrity_report(),
            "previous_event_prefix_unchanged": True,
            "summary_reconstructed_from_journal": True,
            "request_bounds_match_wire_reservations": True,
            "request_bound_count": len(bounded),
            "model_attempt_count": len(attempts),
            "new_phase_charge_status_counts": dict(sorted(settlements.items())),
            "new_phase_known_spend_usd": str(sum(charges, Decimal(0))),
            "largest_confirmed_charge_usd": str(max(charges, default=Decimal(0))),
            "largest_request_reservation_usd": str(
                max((Decimal(b["request_maximum_usd"]) for b in bounded), default=Decimal(0))
            ),
            "no_retried_steps": True,
            "recorded_failure_codes": failure_counts,
            "phase_closed": True,
            "aggregate_spend": ledger.to_dict(),
            "provider_calls": 0,
        }


def hashes():
    return {
        p.name: "sha256:" + sha256_bytes(p.read_bytes())
        for p in sorted(DIRECTORY.iterdir())
        if p.is_file() and p.name != "files.json"
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--journal", action="store_true")
    args = parser.parse_args()
    summary, plan = read("summary.json"), read("execution-plan.json")
    if not ((DIRECTORY / "report.md").read_text() == render_report(summary)):
        raise ValueError("evidence check failed (gemini38-calibration:233)")
    analysis = analyze(summary, plan)
    if args.record:
        if any(
            (DIRECTORY / name).exists()
            for name in ("analysis.json", "verification.json", "files.json")
        ):
            raise FileExistsError("refusing to replace frozen evidence")
        verification = verify_journal(summary, plan)
        for name, value in (("analysis.json", analysis), ("verification.json", verification)):
            (DIRECTORY / name).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        (DIRECTORY / "files.json").write_text(json.dumps(hashes(), sort_keys=True, indent=2) + "\n")
    else:
        if not (hashes() == read("files.json")):
            raise ValueError("evidence check failed (gemini38-calibration:243)")
        if not (analysis == read("analysis.json")):
            raise ValueError("evidence check failed (gemini38-calibration:244)")
        if args.journal and verify_journal(summary, plan) != read("verification.json"):
            raise ValueError("private journal differs from verification receipt")
    print(json.dumps({"verified": True, "provider_calls": 0}))


if __name__ == "__main__":
    main()
