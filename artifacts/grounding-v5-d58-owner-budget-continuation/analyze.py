"""Read-only audit of the owner budget adjustment and continued calibration."""

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
from pixelgym.grounding.v5.owner_budget import (
    OWNER_AUTHORIZATION_KEY,
    OWNER_BUDGET_RULE,
    OwnerZeroHoldLedger,
)
from pixelgym.grounding.v5.reliable_memory import ReliableMemoryPolicy, reliable_config
from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger, request_bound
from scripts.run_grounding_v5_owner_budget_continuation import (
    DIAGNOSTIC,
    OWNER_APPROVAL,
    PHASE,
    PREVIOUS,
    PUBLIC,
    RECONCILIATION,
    ROOT,
    config_from_snapshot,
    read,
    render_report,
    write,
)


def verify_reconciliation(journal_path=None):
    approval, reconciliation = read(OWNER_APPROVAL), read(RECONCILIATION)
    previous = read(PREVIOUS / "summary.json")
    approval_digest = content_digest(approval)
    assert approval_digest == reconciliation["approval_digest"]
    assert approval["rule"] == OWNER_BUDGET_RULE
    assert approval["unresolved_budget_hold_usd"] == "0"
    assert approval["aggregate_ceiling_usd"] == "28.00"
    assert content_digest(previous) == reconciliation["previous_summary_digest"]
    for name, digest in reconciliation["source_digests"].items():
        assert "sha256:" + sha256_bytes((ROOT / name).read_bytes()) == digest, name
    before, after = (
        reconciliation["before_aggregate_spend"],
        reconciliation["after_aggregate_spend"],
    )
    assert before == previous["aggregate_spend"]
    assert reconciliation["before_integrity"] == previous["journal_integrity"]
    assert Decimal(before["spent_usd"]) == Decimal(after["spent_usd"])
    assert (
        Decimal(before["in_flight_reservation_usd"])
        == Decimal(after["in_flight_reservation_usd"])
        == 0
    )
    assert before["wire_requests_sent"] == after["wire_requests_sent"]
    assert before["unknown_charge_outcomes"] == after["unknown_charge_outcomes"]
    assert Decimal(after["unknown_reservation_usd"]) == 0
    assert Decimal(after["budget_accounted_spend_usd"]) == Decimal(after["spent_usd"])
    waivers = reconciliation["waivers"]
    assert len({w["reservation_id"] for w in waivers}) == len(waivers)
    total = sum((Decimal(w["previous_budget_hold_usd"]) for w in waivers), Decimal(0))
    assert (
        total
        == Decimal(reconciliation["waived_budget_holds_usd"])
        == Decimal(before["unknown_reservation_usd"])
    )
    assert all(
        w["approval_digest"] == approval_digest
        and w["rule"] == OWNER_BUDGET_RULE
        and w["budget_hold_usd"] == "0"
        and Decimal(w["previous_budget_hold_usd"]) > 0
        for w in waivers
    )
    receipt = {
        "schema_version": "pixelgym-d58-owner-reconciliation-verification-v1",
        "approval_digest": approval_digest,
        "reconciliation_digest": content_digest(reconciliation),
        "driver_code_revision": reconciliation["driver_code_revision"],
        "waivers_verified": len(waivers),
        "confirmed_charges_unchanged": True,
        "failed_outcome_count_unchanged": True,
        "unresolved_budget_holds_usd": "0",
        "private_journal_verified": False,
        "provider_calls": 0,
    }
    if journal_path is not None:
        with closing(
            sqlite3.connect(f"file:{journal_path.resolve()}?mode=ro", uri=True)
        ) as connection:
            connection.row_factory = sqlite3.Row
            events = [
                {**dict(r), "payload": json.loads(r["payload"])}
                for r in connection.execute(
                    "SELECT * FROM events ORDER BY sequence LIMIT ?",
                    (reconciliation["after_integrity"]["event_count"],),
                )
            ]
        count = reconciliation["before_integrity"]["event_count"]
        assert content_digest(events) == reconciliation["after_integrity"]["event_chain_digest"]
        assert (
            content_digest(events[:count])
            == reconciliation["before_integrity"]["event_chain_digest"]
        )
        delta = events[count:]
        assert len(delta) == 1 + len(waivers)
        assert delta[0]["event_key"] == OWNER_AUTHORIZATION_KEY
        assert delta[0]["kind"] == "owner_zero_hold_budget_authorized"
        assert delta[0]["payload"] == {
            "approval_digest": approval_digest,
            "rule": OWNER_BUDGET_RULE,
            "previous_summary_digest": content_digest(previous),
            "before_integrity": reconciliation["before_integrity"],
            "driver_code_revision": reconciliation["driver_code_revision"],
            "source_digests": reconciliation["source_digests"],
        }
        assert [e["payload"] for e in delta[1:]] == waivers
        assert all(e["kind"] == "spend_unknown_budget_waived" for e in delta[1:])

        class StoredLedgerJournal:
            def __init__(self, rows):
                self.rows = [SimpleNamespace(**row) for row in rows]

            def events(self):
                return self.rows

            def event(self, key):
                return next((e for e in self.rows if e.event_key == key), None)

        original = ReboundedMemoryLedger(
            Decimal(28), Decimal(0), journal=StoredLedgerJournal(events[:count])
        )
        adjusted = OwnerZeroHoldLedger(
            Decimal(28),
            Decimal(0),
            journal=StoredLedgerJournal(events),
            approval_digest=approval_digest,
        )
        assert original.to_dict() == before and adjusted.to_dict() == after
        receipt.update(private_journal_verified=True, prior_prefix_preserved=True)
    return receipt


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
    assert len(rows) == len(plan["jobs"]) == 100
    for row, job in zip(rows, plan["jobs"], strict=True):
        assert all(row[k] == v for k, v in job.items())
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
        "schema_version": "pixelgym-d58-owner-budget-continuation-analysis-v1",
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
        "current_phase_subset": score_rows(summary["new_conditions"]),
        "all_repaired_transport_assignments": score_rows(
            [
                r
                for r in rows
                if r["trial_id"].startswith(
                    ("d58-reliable-memory-", "d58-owner-budget-continuation-")
                )
            ]
        ),
        "current_phase_paired_outcomes": paired_counts(summary["new_conditions"]),
        "all_repaired_transport_paired_outcomes": paired_counts(
            [
                r
                for r in rows
                if r["trial_id"].startswith(
                    ("d58-reliable-memory-", "d58-owner-budget-continuation-")
                )
            ]
        ),
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
    assert digest == plan["execution_plan_digest"] == summary["execution_plan_digest"]
    for name, expected in plan["source_digests"].items():
        assert "sha256:" + sha256_bytes((ROOT / name).read_bytes()) == expected, name
    assert content_digest(read(PUBLIC / "price-recheck.json")) == plan["price_snapshot_digest"]
    previous = read(PREVIOUS / "summary.json")
    assert content_digest(previous) == plan["previous_summary_digest"]
    reconciliation_receipt = verify_reconciliation(journal_path)
    assert (
        reconciliation_receipt["approval_digest"]
        == plan["owner_budget_approval_digest"]
        == summary["owner_budget_approval_digest"]
    )
    assert (
        reconciliation_receipt["reconciliation_digest"]
        == plan["owner_reconciliation_digest"]
        == summary["owner_reconciliation_digest"]
    )
    assert plan["prior_pilot_spend"] == read(RECONCILIATION)["after_aggregate_spend"]
    assert content_digest(read(DIAGNOSTIC / "summary.json")) == plan["diagnostic_summary_digest"]
    preserved = [r for r in previous["conditions"] if r["classification"] != "not_run"]
    assert preserved == plan["preserved_conditions"] and 10 <= len(preserved) < 100
    assert summary["preserved_episode_count"] == len(preserved)
    assert all(r in summary["conditions"] for r in preserved)
    kept = {r["trial_id"] for r in preserved}
    assert summary["new_conditions"] == [
        r for r in summary["conditions"] if r["trial_id"] not in kept
    ]
    assert len(summary["new_conditions"]) == len(plan["execution_jobs"]) == 100 - len(preserved)
    assert score_rows(summary["conditions"]) == summary["scores"]
    assert (PUBLIC / "report.md").read_text() == render_report(summary)
    spend, prior = summary["aggregate_spend"], plan["prior_pilot_spend"]
    known = Decimal(spend["spent_usd"]) - Decimal(prior["spent_usd"])
    held = Decimal(spend["unknown_reservation_usd"]) - Decimal(prior["unknown_reservation_usd"])
    assert Decimal(spend["unknown_reservation_usd"]) == held == 0
    assert known == Decimal(summary["new_phase_known_spend_usd"])
    assert held == Decimal(summary["new_phase_unknown_holds_usd"])
    assert Decimal(spend["in_flight_reservation_usd"]) == 0 and summary["transport_idle_at_close"]
    assert Decimal(spend["budget_accounted_spend_usd"]) == Decimal(spend["spent_usd"]) + Decimal(
        spend["unknown_reservation_usd"]
    )
    assert Decimal(spend["budget_accounted_spend_usd"]) <= Decimal(28)
    assert known + held <= Decimal(plan["phase_cap_usd"])
    assert (
        summary["new_phase_wire_requests"]
        == spend["wire_requests_sent"] - prior["wire_requests_sent"]
    )
    assert summary["new_phase_wire_requests"] <= plan["phase_caps"]["provider_wire_request_cap"]
    assert (
        summary["cohort_wire_requests"]
        == summary["new_phase_wire_requests"] + plan["prior_cohort_wire_requests"]
    )
    assert Decimal(summary["cohort_known_spend_usd"]) == known + Decimal(
        plan["prior_cohort_known_spend_usd"]
    )
    approval = read(ROOT / "artifacts/grounding-v5-d58-runtime-amendment/approval.json")
    assert content_digest(approval) == plan["runtime_approval_digest"]
    assert plan["runtime_deadline"] == plan["runtime_started_at"] + 21600
    analysis = analyze(summary, plan)
    receipt = {
        "schema_version": "pixelgym-d58-owner-budget-continuation-verification-v1",
        "execution_plan_digest": digest,
        "summary_digest": content_digest(summary),
        "assigned_episodes_verified": 100,
        "preserved_conditions_verified": len(preserved),
        "private_journal_verified": False,
        "provider_calls": 0,
        "owner_budget_reconciliation": reconciliation_receipt,
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
        assert content_digest(events) == summary["journal_integrity"]["event_chain_digest"]
        assert (
            content_digest(events[: plan["prior_event_count"]]) == plan["prior_event_prefix_digest"]
        )
        by_key = {e["event_key"]: e for e in events}
        inherited = plan["inherited_transport_schedule"]
        if inherited:
            source = by_key[inherited["event_key"]]
            assert source["payload"] == inherited["payload"]
            predecessor_prefix = f"reliable/{content_digest(inherited['phase_id'])}"
            prior_schedules = [
                e
                for e in events[: plan["prior_event_count"]]
                if e["trial_id"] == predecessor_prefix
                and e["kind"] == "reliable_transport_schedule"
            ]
            assert source == prior_schedules[-1]
            carried = by_key[f"reliable/{content_digest(PHASE)}/inherited-schedule"]
            assert carried["payload"] == {
                **inherited["payload"],
                "inherited_from_event_key": inherited["event_key"],
            }
        by_trial = defaultdict(list)
        for event in events:
            by_trial[event["trial_id"]].append(event)
        assert by_key[f"{PHASE}/closed"]["payload"] == {
            "stop_reason": summary["stop_reason"],
            "transport_idle": True,
        }

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
                assert row is not None
                data = bytes(row["data"])
                assert "sha256:" + sha256_bytes(data) == digest
                if expected_kind and row["kind"] != expected_kind:
                    assert connection.execute(
                        "SELECT 1 FROM object_roles WHERE digest=? AND kind=?",
                        (digest, expected_kind),
                    ).fetchone()
                return data

        assert (
            by_key["d58-reliable-memory-continuation-v1/started"]["payload"]["started_at"]
            == plan["runtime_started_at"]
        )
        stored = StoredJournal()
        prefix = f"reliable/{content_digest(PHASE)}"
        wires_by_id = {
            e["payload"]["reservation_id"]: e["payload"]
            for e in by_trial[prefix]
            if e["kind"] == "reliable_transport_receipt"
        }
        assert list(wires_by_id.values()) == summary["transport_receipts"]
        attempts, wires, measured, retried_actions, retry_attempts = 0, 0, 0, 0, 0
        computed_known, computed_held = Decimal(0), Decimal(0)
        settlements, faults, failures = Counter(), Counter(), Counter()
        for row in summary["conditions"]:
            trial = row["trial_id"]
            started, completed = (
                by_key.get(f"{trial}/full_started"),
                by_key.get(f"{trial}/full_completed"),
            )
            if completed:
                assert completed["payload"] == row
            if not started:
                assert row["classification"] == "not_run" and row["model_attempts"] == 0
                continue
            measured += 1
            measurement = episode_measurements(stored, trial, row["seed"])
            assert all(row[k] == v for k, v in measurement.items())
            if trial in kept:
                continue
            assert started["payload"]["job"] == next(
                j for j in plan["execution_jobs"] if j["trial_id"] == trial
            )
            assert started["payload"]["plan_digest"] == digest
            assert (
                started["payload"]["policy_id"]
                == plan["policy_manifests"][row["mode"]]["policy_id"]
            )
            assert started["payload"]["backend_identity"] == plan["backend_identity"]
            trial_events = by_trial[trial]
            assert not any("prefix" in e["kind"] for e in trial_events)
            initial = by_key.get(f"{trial}/initial_screenshot")
            if initial:
                checkpoint = json.loads(
                    stored.get_object(initial["payload"]["environment_checkpoint_digest"])
                )
                assert checkpoint["stage_index"] == 0 and not checkpoint["deferred_choices"]
                assert (
                    plan["backend_identity"]
                    in stored.get_object(initial["payload"]["environment_resume_digest"]).decode()
                )
            commits = [e for e in trial_events if e["kind"] == "dispatch_committed"]
            assert row["success"] == bool(commits and commits[-1]["payload"]["terminated"])
            assert len({e["step_index"] for e in commits}) == len(commits)
            groups = defaultdict(list)
            for event in trial_events:
                if event["kind"] == "attempt_started":
                    groups[event["step_index"]].append(event)
                if event["payload"].get("failure_code") is not None:
                    failures[f"{event['kind']}:{event['payload']['failure_code']}"] += 1
            for step, group in groups.items():
                assert [e["attempt_index"] for e in group] == list(range(len(group)))
                assert 1 <= len(group) <= 3
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
                    assert requests[-1] == payload["request_digest"]
                    rid = content_digest(payload["idempotency_key"])
                    reserved = by_key.get(f"spend/{rid}/reserved")
                    if not reserved:
                        continue
                    wires += 1
                    proof = request_bound(request, config)
                    bound = by_key[f"spend/{rid}/request-bound"]["payload"]
                    assert all(bound[k] == v for k, v in proof.items())
                    assert bound["transport_version"] == plan["transport_version"]
                    maximum = bound["request_maximum_usd"]
                    assert reserved["payload"]["request_maximum_usd"] == maximum
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
                        assert cost == Decimal(charged["payload"]["cost_usd"])
                        assert charged["payload"]["request_maximum_usd"] == maximum
                        computed_known += cost
                        settlements["confirmed_zero" if cost == 0 else "confirmed_positive"] += 1
                    else:
                        assert unknown and cost is None
                        assert Decimal(unknown["payload"]["unknown_reservation_usd"]) == 0
                        computed_held += Decimal(unknown["payload"]["unknown_reservation_usd"])
                        settlements["unknown"] += 1
                assert len(set(requests)) == 1, "retry changed the model request"
        assert attempts == sum(r["model_attempts"] for r in summary["new_conditions"])
        assert wires == summary["new_phase_wire_requests"]
        assert wires == len(wires_by_id) + faults["missing_wire_receipt"]
        assert computed_known == known and computed_held == held
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
    parser.add_argument("--verify-reconciliation", action="store_true")
    args = parser.parse_args()
    if args.verify_reconciliation:
        receipt = verify_reconciliation(args.journal)
        public = OWNER_APPROVAL.parent
        if args.write:
            assert args.journal is not None
            if (public / "files.json").exists():
                raise FileExistsError("cannot overwrite frozen owner accounting evidence")
            write(public / "verification.json", receipt)
            write(
                public / "files.json",
                {
                    p.name: "sha256:" + sha256_bytes(p.read_bytes())
                    for p in sorted(public.iterdir())
                    if p.is_file() and p.name != "files.json"
                },
            )
        else:
            for name, digest in read(public / "files.json").items():
                assert "sha256:" + sha256_bytes((public / name).read_bytes()) == digest, name
            if args.journal:
                assert receipt == read(public / "verification.json")
        for path in public.glob("*.json"):
            validate_credential_free(read(path))
        print(json.dumps(receipt, sort_keys=True))
        return
    analysis, receipt = verify(args.journal)
    if args.write:
        if (PUBLIC / "files.json").exists():
            raise FileExistsError("cannot overwrite frozen evidence")
        assert args.journal is not None
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
        assert analysis == read(PUBLIC / "analysis.json")
        for name, digest in read(PUBLIC / "files.json").items():
            assert "sha256:" + sha256_bytes((PUBLIC / name).read_bytes()) == digest, name
        if args.journal:
            assert receipt == read(PUBLIC / "verification.json")
    for path in PUBLIC.glob("*.json"):
        validate_credential_free(read(path))
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
