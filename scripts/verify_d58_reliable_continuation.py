"""Successor with optimization-safe checks; the frozen original is reproduction-only.

Read-only audit of the ninety-assignment repaired-transport continuation."""

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.memory_calibration import episode_measurements
from pixelgym.grounding.v5.openrouter_policy import _usage_cost
from pixelgym.grounding.v5.reliable_memory import ReliableMemoryPolicy, reliable_config
from pixelgym.grounding.v5.request_budget import request_bound
from scripts.run_grounding_v5_reliable_continuation import (
    DIAGNOSTIC,
    PHASE,
    PREVIOUS,
    PUBLIC,
    ROOT,
    config_from_snapshot,
    read,
    render_report,
    write,
)


def paired_counts(rows):
    counts = Counter()
    for seed in sorted({r["seed"] for r in rows}):
        pair = {r["mode"]: r for r in rows if r["seed"] == seed}
        if set(pair) != {"history", "stateless"}:
            counts["unpaired_in_subset"] += 1
            continue
        if any(
            r["classification"]
            in ("not_run", "budget_stop", "interrupted_episode", "phase_time_stop")
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
            "unpaired_in_subset",
        )
    }


def analyze(summary, plan):
    rows = summary["conditions"]
    if not (len(rows) == len(plan["jobs"]) == 100):
        raise ValueError("evidence check failed (reliable-continuation:70)")
    for row, job in zip(rows, plan["jobs"], strict=True):
        if not (all(row[k] == v for k, v in job.items())):
            raise ValueError("evidence check failed (reliable-continuation:72)")
    logical = {}
    for row in rows:
        logical.setdefault(row["seed_record"]["logical_id"], set()).add(row["seed"])
    # The smallest assigned seed in each logical cluster is chosen without outcomes.
    representatives = {min(seeds) for seeds in logical.values()}
    scores = summary["scores"]
    missing_history_exposure = sum(
        r["mode"] == "history"
        and not r["reached_both_consumers"]
        and r["classification"]
        in ("not_run", "budget_stop", "interrupted_episode", "phase_time_stop")
        for r in rows
    )
    return {
        "schema_version": "pixelgym-d58-reliable-continuation-analysis-v1",
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
        "repaired_transport_subset": score_rows(summary["new_conditions"]),
        "repaired_transport_paired_outcomes": paired_counts(summary["new_conditions"]),
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
        "interpretation": "Continued focus-repaired Gemini 3.8 cohort including ten preserved failures and an explicit transport-version boundary; do not pool with earlier renderers or scripted-prefix diagnostics. Infrastructure failures are retained. Final confirmation remains unapproved.",
        "provider_calls": 0,
    }


def score_rows(rows):
    scores = {}
    for mode in ("history", "stateless"):
        selected = [r for r in rows if r["mode"] == mode]
        scores[mode] = {
            "assigned": len(selected),
            "attempted_episodes": sum(r["model_attempts"] > 0 for r in selected),
            "terminal_successes": sum(r["success"] for r in selected),
            "reached_both_consumers": sum(r["reached_both_consumers"] for r in selected),
            "first_memory_attempts": sum(len(r["first_attempts"]) for r in selected),
            "correct_first_memory_attempts": sum(
                c["correct"] for r in selected for c in r["first_attempts"]
            ),
            "classifications": dict(sorted(Counter(r["classification"] for r in selected).items())),
        }
    return scores


def verify(journal_path=None):
    plan, summary = read(PUBLIC / "execution-plan.json"), read(PUBLIC / "summary.json")
    digest = content_digest({k: v for k, v in plan.items() if k != "execution_plan_digest"})
    if not (digest == plan["execution_plan_digest"] == summary["execution_plan_digest"]):
        raise ValueError("evidence check failed (reliable-continuation:149)")
    for name, expected in plan["source_digests"].items():
        if not ("sha256:" + sha256_bytes((ROOT / name).read_bytes()) == expected):
            raise ValueError(name)
    if not (content_digest(read(PUBLIC / "price-recheck.json")) == plan["price_snapshot_digest"]):
        raise ValueError("evidence check failed (reliable-continuation:152)")
    previous = read(PREVIOUS / "summary.json")
    if not (content_digest(previous) == plan["previous_summary_digest"]):
        raise ValueError("evidence check failed (reliable-continuation:154)")
    if not (content_digest(read(DIAGNOSTIC / "summary.json")) == plan["diagnostic_summary_digest"]):
        raise ValueError("evidence check failed (reliable-continuation:155)")
    preserved = [r for r in previous["conditions"] if r["classification"] != "not_run"]
    if not (preserved == plan["preserved_conditions"] and len(preserved) == 10):
        raise ValueError("evidence check failed (reliable-continuation:157)")
    if not (summary["preserved_episode_count"] == 10):
        raise ValueError("evidence check failed (reliable-continuation:158)")
    if not (all(r in summary["conditions"] for r in preserved)):
        raise ValueError("evidence check failed (reliable-continuation:159)")
    kept = {r["trial_id"] for r in preserved}
    if not (
        summary["new_conditions"] == [r for r in summary["conditions"] if r["trial_id"] not in kept]
    ):
        raise ValueError("evidence check failed (reliable-continuation:161)")
    if not (len(summary["new_conditions"]) == len(plan["execution_jobs"]) == 90):
        raise ValueError("evidence check failed (reliable-continuation:164)")
    if not (score_rows(summary["conditions"]) == summary["scores"]):
        raise ValueError("evidence check failed (reliable-continuation:165)")
    if not ((PUBLIC / "report.md").read_text() == render_report(summary)):
        raise ValueError("evidence check failed (reliable-continuation:166)")
    spend, prior = summary["aggregate_spend"], plan["prior_pilot_spend"]
    known = Decimal(spend["spent_usd"]) - Decimal(prior["spent_usd"])
    held = Decimal(spend["unknown_reservation_usd"]) - Decimal(prior["unknown_reservation_usd"])
    if not (known == Decimal(summary["new_phase_known_spend_usd"])):
        raise ValueError("evidence check failed (reliable-continuation:170)")
    if not (held == Decimal(summary["new_phase_unknown_holds_usd"])):
        raise ValueError("evidence check failed (reliable-continuation:171)")
    if not (
        Decimal(spend["in_flight_reservation_usd"]) == 0 and summary["transport_idle_at_close"]
    ):
        raise ValueError("evidence check failed (reliable-continuation:172)")
    if not (
        Decimal(spend["budget_accounted_spend_usd"])
        == Decimal(spend["spent_usd"]) + Decimal(spend["unknown_reservation_usd"])
    ):
        raise ValueError("evidence check failed (reliable-continuation:173)")
    if not (Decimal(spend["budget_accounted_spend_usd"]) <= Decimal(28)):
        raise ValueError("evidence check failed (reliable-continuation:176)")
    if not (known + held <= Decimal(plan["phase_cap_usd"])):
        raise ValueError("evidence check failed (reliable-continuation:177)")
    if not (
        summary["new_phase_wire_requests"]
        == spend["wire_requests_sent"] - prior["wire_requests_sent"]
    ):
        raise ValueError("evidence check failed (reliable-continuation:178)")
    if not (summary["new_phase_wire_requests"] <= plan["phase_caps"]["provider_wire_request_cap"]):
        raise ValueError("evidence check failed (reliable-continuation:182)")
    if not (
        summary["cohort_wire_requests"]
        == summary["new_phase_wire_requests"] + plan["prior_cohort_wire_requests"]
    ):
        raise ValueError("evidence check failed (reliable-continuation:183)")
    if not (
        Decimal(summary["cohort_known_spend_usd"])
        == known + Decimal(plan["prior_cohort_known_spend_usd"])
    ):
        raise ValueError("evidence check failed (reliable-continuation:187)")
    analysis = analyze(summary, plan)
    receipt = {
        "schema_version": "pixelgym-d58-reliable-continuation-verification-v1",
        "execution_plan_digest": digest,
        "summary_digest": content_digest(summary),
        "assigned_episodes_verified": 100,
        "preserved_conditions_verified": 10,
        "private_journal_verified": False,
        "provider_calls": 0,
    }
    if journal_path is None:
        return analysis, receipt
    config = reliable_config(config_from_snapshot(read(PUBLIC / "price-recheck.json")))
    with closing(sqlite3.connect(f"file:{journal_path.resolve()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        events = [
            {**dict(r), "payload": json.loads(r["payload"])}
            for r in connection.execute(
                "SELECT * FROM events ORDER BY sequence LIMIT ?",
                (summary["journal_integrity"]["event_count"],),
            )
        ]
        if not (content_digest(events) == summary["journal_integrity"]["event_chain_digest"]):
            raise ValueError("evidence check failed (reliable-continuation:212)")
        if not (
            content_digest(events[: plan["prior_event_count"]]) == plan["prior_event_prefix_digest"]
        ):
            raise ValueError("evidence check failed (reliable-continuation:213)")
        by_key = {e["event_key"]: e for e in events}
        by_trial = defaultdict(list)
        for event in events:
            by_trial[event["trial_id"]].append(event)
        if not (
            by_key[f"{PHASE}/closed"]["payload"]
            == {
                "stop_reason": summary["stop_reason"],
                "transport_idle": True,
            }
        ):
            raise ValueError("evidence check failed (reliable-continuation:220)")

        class StoredJournal:
            def events(self, trial_id=None):
                return [
                    SimpleNamespace(**e)
                    for e in (events if trial_id is None else by_trial[trial_id])
                ]

            def event(self, key):
                return SimpleNamespace(**by_key[key]) if key in by_key else None

            def get_object(self, digest, *, expected_kind=None):
                row = connection.execute(
                    "SELECT kind,data FROM objects WHERE digest=?", (digest,)
                ).fetchone()
                if not (row is not None):
                    raise ValueError("evidence check failed (reliable-continuation:239)")
                data = bytes(row["data"])
                if not ("sha256:" + sha256_bytes(data) == digest):
                    raise ValueError("evidence check failed (reliable-continuation:241)")
                if (
                    expected_kind
                    and row["kind"] != expected_kind
                    and not (
                        connection.execute(
                            "SELECT 1 FROM object_roles WHERE digest=? AND kind=?",
                            (digest, expected_kind),
                        ).fetchone()
                    )
                ):
                    raise ValueError("evidence check failed (reliable-continuation:243)")
                return data

        stored = StoredJournal()
        prefix = f"reliable/{content_digest(PHASE)}"
        wires_by_id = {
            e["payload"]["reservation_id"]: e["payload"]
            for e in by_trial[prefix]
            if e["kind"] == "reliable_transport_receipt"
        }
        if not (list(wires_by_id.values()) == summary["transport_receipts"]):
            raise ValueError("evidence check failed (reliable-continuation:256)")
        attempts, wires, measured, retried_actions, retry_attempts = 0, 0, 0, 0, 0
        computed_known, computed_held = Decimal(0), Decimal(0)
        settlements, faults, failures = Counter(), Counter(), Counter()
        for row in summary["conditions"]:
            trial = row["trial_id"]
            started, completed = (
                by_key.get(f"{trial}/full_started"),
                by_key.get(f"{trial}/full_completed"),
            )
            if completed and not (completed["payload"] == row):
                raise ValueError("evidence check failed (reliable-continuation:267)")
            if not started:
                if not (row["classification"] == "not_run" and row["model_attempts"] == 0):
                    raise ValueError("evidence check failed (reliable-continuation:269)")
                continue
            measured += 1
            measurement = episode_measurements(stored, trial, row["seed"])
            if not (all(row[k] == v for k, v in measurement.items())):
                raise ValueError("evidence check failed (reliable-continuation:273)")
            if trial in kept:
                continue
            if not (
                started["payload"]["job"]
                == next(j for j in plan["execution_jobs"] if j["trial_id"] == trial)
            ):
                raise ValueError("evidence check failed (reliable-continuation:276)")
            if not (started["payload"]["plan_digest"] == digest):
                raise ValueError("evidence check failed (reliable-continuation:279)")
            if not (
                started["payload"]["policy_id"]
                == plan["policy_manifests"][row["mode"]]["policy_id"]
            ):
                raise ValueError("evidence check failed (reliable-continuation:280)")
            if not (started["payload"]["backend_identity"] == plan["backend_identity"]):
                raise ValueError("evidence check failed (reliable-continuation:284)")
            trial_events = by_trial[trial]
            if any("prefix" in e["kind"] for e in trial_events):
                raise ValueError("evidence check failed (reliable-continuation:286)")
            initial = by_key.get(f"{trial}/initial_screenshot")
            if initial:
                checkpoint = json.loads(
                    stored.get_object(initial["payload"]["environment_checkpoint_digest"])
                )
                if not (checkpoint["stage_index"] == 0 and not checkpoint["deferred_choices"]):
                    raise ValueError("evidence check failed (reliable-continuation:292)")
                if not (
                    plan["backend_identity"]
                    in stored.get_object(initial["payload"]["environment_resume_digest"]).decode()
                ):
                    raise ValueError("evidence check failed (reliable-continuation:293)")
            commits = [e for e in trial_events if e["kind"] == "dispatch_committed"]
            if not (row["success"] == bool(commits and commits[-1]["payload"]["terminated"])):
                raise ValueError("evidence check failed (reliable-continuation:298)")
            if not (len({e["step_index"] for e in commits}) == len(commits)):
                raise ValueError("evidence check failed (reliable-continuation:299)")
            groups = defaultdict(list)
            for event in trial_events:
                if event["kind"] == "attempt_started":
                    groups[event["step_index"]].append(event)
                if event["payload"].get("failure_code") is not None:
                    failures[f"{event['kind']}:{event['payload']['failure_code']}"] += 1
            for step, group in groups.items():
                if not ([e["attempt_index"] for e in group] == list(range(len(group)))):
                    raise ValueError("evidence check failed (reliable-continuation:307)")
                if not (1 <= len(group) <= 3):
                    raise ValueError("evidence check failed (reliable-continuation:308)")
                retried_actions += int(len(group) > 1)
                retry_attempts += len(group) - 1
                requests = []
                for event in group:
                    attempts += 1
                    payload = event["payload"]
                    pre = by_key[
                        f"{trial}/initial_screenshot"
                        if step == 0
                        else f"{trial}/step-{step - 1:04d}/dispatch_committed"
                    ]["payload"]
                    policy = ReliableMemoryPolicy(
                        config, retain_screenshots=row["mode"] == "history"
                    )
                    try:
                        request = policy.build_request(
                            stored.get_object(payload["pre_call_checkpoint_digest"]),
                            stored.get_object(pre["screenshot_digest"]),
                        )
                    finally:
                        policy.close()
                    requests.append(content_digest(request))
                    if not (requests[-1] == payload["request_digest"]):
                        raise ValueError("evidence check failed (reliable-continuation:331)")
                    rid = content_digest(payload["idempotency_key"])
                    reserved = by_key.get(f"spend/{rid}/reserved")
                    if not reserved:
                        continue
                    wires += 1
                    proof = request_bound(request, config)
                    bound = by_key[f"spend/{rid}/request-bound"]["payload"]
                    if not (all(bound[k] == v for k, v in proof.items())):
                        raise ValueError("evidence check failed (reliable-continuation:339)")
                    if not (bound["transport_version"] == plan["transport_version"]):
                        raise ValueError("evidence check failed (reliable-continuation:340)")
                    maximum = bound["request_maximum_usd"]
                    if not (reserved["payload"]["request_maximum_usd"] == maximum):
                        raise ValueError("evidence check failed (reliable-continuation:342)")
                    wire = wires_by_id.get(rid)
                    cost = None
                    if wire:
                        try:
                            body = json.loads(stored.get_object(wire["response_envelope_digest"]))
                            cost = _usage_cost(body.get("usage", {}))
                        except (ValueError, AttributeError):
                            pass
                        if wire["curl_exit_code"]:
                            faults[f"curl_{wire['curl_exit_code']}"] += 1
                    else:
                        faults["missing_wire_receipt"] += 1
                    charged = by_key.get(f"spend/{rid}/charged")
                    unknown = by_key.get(f"spend/{rid}/unknown")
                    if charged:
                        if not (cost == Decimal(charged["payload"]["cost_usd"])):
                            raise ValueError("evidence check failed (reliable-continuation:358)")
                        if not (charged["payload"]["request_maximum_usd"] == maximum):
                            raise ValueError("evidence check failed (reliable-continuation:359)")
                        computed_known += cost
                        settlements["confirmed_zero" if cost == 0 else "confirmed_positive"] += 1
                    else:
                        if not (unknown and cost is None):
                            raise ValueError("evidence check failed (reliable-continuation:363)")
                        if not (unknown["payload"]["unknown_reservation_usd"] == maximum):
                            raise ValueError("evidence check failed (reliable-continuation:364)")
                        computed_held += Decimal(maximum)
                        settlements["unknown"] += 1
                if not (len(set(requests)) == 1):
                    raise ValueError("retry changed the model request")
        if not (attempts == sum(r["model_attempts"] for r in summary["new_conditions"])):
            raise ValueError("evidence check failed (reliable-continuation:368)")
        if not (wires == summary["new_phase_wire_requests"]):
            raise ValueError("evidence check failed (reliable-continuation:369)")
        if not (wires == len(wires_by_id) + faults["missing_wire_receipt"]):
            raise ValueError("evidence check failed (reliable-continuation:370)")
        if not (computed_known == known and computed_held == held):
            raise ValueError("evidence check failed (reliable-continuation:371)")
        receipt.update(
            private_journal_verified=True,
            prior_prefix_preserved=True,
            episodes_remeasured=measured,
            new_request_bodies_reconstructed=attempts,
            wire_reservations_verified=wires,
            retried_logical_actions=retried_actions,
            additional_retry_attempts=retry_attempts,
            identical_requests_within_retries=True,
            settlement_counts=dict(sorted(settlements.items())),
            transport_fault_counts=dict(sorted(faults.items())),
            recorded_failure_codes=dict(sorted(failures.items())),
        )
    return analysis, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    analysis, receipt = verify(args.journal)
    if args.write:
        if any(
            (PUBLIC / name).exists()
            for name in ("analysis.json", "verification.json", "files.json")
        ):
            raise FileExistsError("cannot overwrite frozen evidence")
        if not (args.journal is not None):
            raise ValueError("evidence check failed (reliable-continuation:397)")
        write(PUBLIC / "analysis.json", analysis)
        write(PUBLIC / "verification.json", receipt)
        write(
            PUBLIC / "files.json",
            {
                p.name: "sha256:" + sha256_bytes(p.read_bytes())
                for p in sorted(PUBLIC.iterdir())
                if p.is_file() and p.name != "files.json"
            },
        )
    else:
        if not (analysis == read(PUBLIC / "analysis.json")):
            raise ValueError("evidence check failed (reliable-continuation:409)")
        manifest = read(PUBLIC / "files.json")
        if set(manifest) != {
            p.name for p in PUBLIC.iterdir() if p.is_file() and p.name != "files.json"
        }:
            raise ValueError("evidence file set differs from manifest")
        for name, digest in manifest.items():
            if not ("sha256:" + sha256_bytes((PUBLIC / name).read_bytes()) == digest):
                raise ValueError(name)
        if args.journal and not (receipt == read(PUBLIC / "verification.json")):
            raise ValueError("evidence check failed (reliable-continuation:413)")
    for path in PUBLIC.glob("*.json"):
        validate_credential_free(read(path))
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
