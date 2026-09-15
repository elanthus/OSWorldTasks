"""Verify the closed calibration from stored evidence without provider calls."""

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.memory_calibration import episode_measurements
from pixelgym.grounding.v5.request_budget import request_bound
from pixelgym.grounding.v5.screenshot_memory import ScreenshotMemoryPolicy
from scripts.run_grounding_v5_focus_continuation import (
    PHASE,
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
        assert set(pair) == {"history", "stateless"}
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
        "schema_version": "pixelgym-d58-focus-continuation-analysis-v1",
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
        "interpretation": "Continued focus-repaired Gemini 3.8 cohort including five preserved failures; do not pool with earlier renderers or scripted-prefix diagnostics. Infrastructure failures are retained. Final confirmation remains unapproved.",
        "provider_calls": 0,
    }


def verify(journal_path):
    plan, summary = read(PUBLIC / "execution-plan.json"), read(PUBLIC / "summary.json")
    assert (
        content_digest({k: v for k, v in plan.items() if k != "execution_plan_digest"})
        == plan["execution_plan_digest"]
        == summary["execution_plan_digest"]
    )
    for name, digest in plan["source_digests"].items():
        assert "sha256:" + sha256_bytes((ROOT / name).read_bytes()) == digest, name
    assert content_digest(read(PUBLIC / "price-recheck.json")) == plan["price_snapshot_digest"]
    assert (PUBLIC / "report.md").read_text() == render_report(summary)
    analysis = analyze(summary, plan)
    for mode, score in summary["scores"].items():
        rows = [r for r in summary["conditions"] if r["mode"] == mode]
        assert score == {
            "assigned": len(rows),
            "attempted_episodes": sum(r["model_attempts"] > 0 for r in rows),
            "terminal_successes": sum(r["success"] for r in rows),
            "reached_both_consumers": sum(r["reached_both_consumers"] for r in rows),
            "first_memory_attempts": sum(len(r["first_attempts"]) for r in rows),
            "correct_first_memory_attempts": sum(
                c["correct"] for r in rows for c in r["first_attempts"]
            ),
            "classifications": dict(sorted(Counter(r["classification"] for r in rows).items())),
        }
    spend = summary["aggregate_spend"]
    assert Decimal(spend["budget_accounted_spend_usd"]) <= Decimal(28)
    assert Decimal(summary["new_phase_known_spend_usd"]) == Decimal(spend["spent_usd"]) - Decimal(
        plan["prior_pilot_spend"]["spent_usd"]
    )
    assert (
        summary["new_phase_wire_requests"]
        == spend["wire_requests_sent"] - plan["prior_pilot_spend"]["wire_requests_sent"]
    )
    receipt = {
        "schema_version": "pixelgym-d58-focus-continuation-verification-v1",
        "execution_plan_digest": plan["execution_plan_digest"],
        "summary_digest": content_digest(summary),
        "assigned_episodes_verified": 100,
        "private_journal_verified": False,
        "provider_calls": 0,
    }
    if journal_path is None:
        return analysis, receipt
    config = config_from_snapshot(read(PUBLIC / "price-recheck.json"))
    with closing(sqlite3.connect(f"file:{journal_path.resolve()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        count = summary["journal_integrity"]["event_count"]
        events = [
            {**dict(r), "payload": json.loads(r["payload"])}
            for r in connection.execute("SELECT * FROM events ORDER BY sequence LIMIT ?", (count,))
        ]
        assert content_digest(events) == summary["journal_integrity"]["event_chain_digest"]
        assert (
            content_digest(events[: plan["prior_event_count"]]) == plan["prior_event_prefix_digest"]
        )
        by_key = {e["event_key"]: e for e in events}
        assert by_key[f"{PHASE}/closed"]["payload"]["stop_reason"] == summary["stop_reason"]

        class StoredJournal:
            def events(self, trial_id=None):
                return [
                    SimpleNamespace(**e)
                    for e in events
                    if trial_id is None or e["trial_id"] == trial_id
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

        stored = StoredJournal()
        attempts, wires, known, measured = 0, 0, Decimal(0), 0
        unresolved = Decimal(0)
        settlements, failure_codes = Counter(), Counter()
        for row in summary["conditions"]:
            trial = row["trial_id"]
            is_new = trial.startswith(PHASE + "-")
            completed = by_key.get(f"{trial}/full_completed")
            started = by_key.get(f"{trial}/full_started")
            if completed:
                assert completed["payload"] == row
            if not started:
                assert row["classification"] == "not_run" and row["model_attempts"] == 0
                continue
            assert started["payload"]["plan_digest"] == (
                plan["execution_plan_digest"] if is_new else plan["previous_cohort_plan_digest"]
            )
            if not is_new:
                assert row in plan["preserved_conditions"]
            assert started["payload"]["backend_identity"] == plan["backend_identity"]
            measurement = episode_measurements(stored, trial, row["seed"])
            assert all(row[k] == v for k, v in measurement.items())
            measured += 1
            trial_events = [e for e in events if e["trial_id"] == trial]
            assert not any("prefix" in e["kind"] for e in trial_events)
            initial = by_key.get(f"{trial}/initial_screenshot")
            if initial:
                checkpoint = json.loads(
                    stored.get_object(initial["payload"]["environment_checkpoint_digest"])
                )
                assert checkpoint["stage_index"] == 0 and not checkpoint["deferred_choices"]
                resume = stored.get_object(initial["payload"]["environment_resume_digest"])
                assert plan["backend_identity"] in resume.decode()
            commits = [e for e in trial_events if e["kind"] == "dispatch_committed"]
            assert row["success"] == bool(commits and commits[-1]["payload"]["terminated"])
            attempted = [e for e in trial_events if e["kind"] == "attempt_started"]
            assert len({e["step_index"] for e in attempted}) == len(attempted)
            for event in attempted:
                attempts += 1
                assert event["attempt_index"] == 0
                step, payload = event["step_index"], event["payload"]
                previous = by_key[
                    f"{trial}/initial_screenshot"
                    if step == 0
                    else f"{trial}/step-{step - 1:04d}/dispatch_committed"
                ]["payload"]
                policy = ScreenshotMemoryPolicy(config, retain_screenshots=row["mode"] == "history")
                try:
                    request = policy.build_request(
                        stored.get_object(payload["pre_call_checkpoint_digest"]),
                        stored.get_object(previous["screenshot_digest"]),
                    )
                finally:
                    policy.close()
                assert content_digest(request) == payload["request_digest"]
                rid = content_digest(payload["idempotency_key"])
                bound = by_key.get(f"spend/{rid}/request-bound")
                reservation = by_key.get(f"spend/{rid}/reserved")
                if not reservation:
                    continue
                wires += int(is_new)
                proof = request_bound(request, config)
                assert bound and all(bound["payload"][k] == v for k, v in proof.items())
                maximum = bound["payload"]["request_maximum_usd"]
                assert bound["payload"]["transport_version"] == plan["transport_version"]
                assert reservation["payload"]["request_maximum_usd"] == maximum
                charged = by_key.get(f"spend/{rid}/charged")
                unknown = by_key.get(f"spend/{rid}/unknown")
                released = by_key.get(f"spend/{rid}/released")
                if unknown:
                    assert unknown["payload"]["unknown_reservation_usd"] == maximum
                if charged:
                    assert charged["payload"]["request_maximum_usd"] == maximum
                    cost = Decimal(charged["payload"]["cost_usd"])
                    known += cost if is_new else Decimal(0)
                    settlements["confirmed_zero" if cost == 0 else "confirmed_positive"] += int(
                        is_new
                    )
                elif released:
                    settlements["released"] += int(is_new)
                else:
                    assert unknown
                    settlements["unknown"] += int(is_new)
                    unresolved += Decimal(maximum) if is_new else Decimal(0)
            for event in trial_events:
                if is_new and event["payload"].get("failure_code") is not None:
                    failure_codes[f"{event['kind']}:{event['payload']['failure_code']}"] += 1
        assert wires == summary["new_phase_wire_requests"]
        assert attempts == sum(r["model_attempts"] for r in summary["conditions"])
        assert known == Decimal(summary["new_phase_known_spend_usd"])
        assert Decimal(spend["unknown_reservation_usd"]) == (
            Decimal(plan["prior_pilot_spend"]["unknown_reservation_usd"]) + unresolved
        )
        assert Decimal(spend["in_flight_reservation_usd"]) == 0
        assert Decimal(spend["budget_accounted_spend_usd"]) == (
            Decimal(spend["spent_usd"]) + Decimal(spend["unknown_reservation_usd"])
        )
        diagnostics = [
            e["payload"]
            for e in events
            if e["trial_id"] == PHASE and e["kind"] == "transport_error_diagnostic"
        ]
        provider_errors = []
        for event in events:
            if (
                not event["trial_id"].startswith(PHASE + "-")
                or event["kind"] != "retryable_provider_response"
            ):
                continue
            digest = event["payload"].get("response_digest")
            if digest:
                response = json.loads(stored.get_object(digest))
                usage = response.get("usage", {})
                provider_errors.append(
                    {
                        "trial_id": event["trial_id"],
                        "step_index": event["step_index"],
                        "finish_reason": response.get("finish_reason"),
                        "provider_error_code": usage.get("provider_error_code"),
                        "confirmed_cost_usd": usage.get("cost"),
                        "completion_tokens": usage.get("completion_tokens"),
                    }
                )
        assert summary["preserved_episode_count"] == len(plan["preserved_conditions"]) == 5
        assert all(r in summary["conditions"] for r in plan["preserved_conditions"])
        assert len(plan["execution_jobs"]) == 95
        receipt.update(
            preserved_conditions_verified=5,
            transport_error_diagnostics=diagnostics,
            provider_error_envelopes=provider_errors,
            private_journal_verified=True,
            prior_prefix_preserved=True,
            episodes_remeasured=measured,
            request_bodies_reconstructed=attempts,
            wire_reservations_verified=wires,
            settlement_counts=dict(sorted(settlements.items())),
            recorded_failure_codes=dict(sorted(failure_codes.items())),
            request_reservations_and_settlements_match=True,
        )
    return analysis, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    analysis, receipt = verify(args.journal)
    if args.write:
        if (PUBLIC / "files.json").exists():
            raise FileExistsError("refusing to replace frozen evidence")
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
