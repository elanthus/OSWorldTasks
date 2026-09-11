"""Verify stored diagnostic evidence; never call a model or replay an episode."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.screenshot_memory import ScreenshotMemoryPolicy
from scripts.run_grounding_v5_focus_diagnostic import PHASE, PUBLIC, ROOT, read, write
from scripts.run_grounding_v5_gemini38_calibration import config_from_snapshot


def verify(journal_path: Path | None) -> dict:
    plan, summary = read(PUBLIC / "execution-plan.json"), read(PUBLIC / "summary.json")
    assert (
        content_digest({k: v for k, v in plan.items() if k != "execution_plan_digest"})
        == plan["execution_plan_digest"]
        == summary["execution_plan_digest"]
    )
    for name, digest in plan["source_digests"].items():
        assert "sha256:" + sha256_bytes((ROOT / name).read_bytes()) == digest, name
    assert len(plan["jobs"]) == len(summary["conditions"]) == 20
    for job, row in zip(plan["jobs"], summary["conditions"]):
        assert row["trial_id"] == job["trial_id"] and row["mode"] == job["mode"]
        assert all(row[k] == value for k, value in job["case"].items())
        assert not row["desired_transition"] or row["action_dispatched"]
        assert not row["correct_memory_choice"] or row["valid_memory_choice"]
    for mode, score in summary["scores"].items():
        selected = [r for r in summary["conditions"] if r["mode"] == mode]
        text = [r for r in selected if not r["state_name"].startswith("memory_")]
        memory = [r for r in selected if r["state_name"].startswith("memory_")]
        assert score == {
            "assigned": 10,
            "attempted": sum(r["model_attempted"] for r in selected),
            "text_transitions": sum(r["desired_transition"] for r in text),
            "text_assigned": 6,
            "memory_valid": sum(r["valid_memory_choice"] for r in memory),
            "memory_correct": sum(r["correct_memory_choice"] for r in memory),
            "memory_assigned": 4,
        }
    assert 0 <= summary["new_wire_requests"] <= 20
    assert Decimal(summary["aggregate_spend"]["budget_accounted_spend_usd"]) <= Decimal(20)
    assert Decimal(summary["new_known_spend_usd"]) == Decimal(
        summary["aggregate_spend"]["spent_usd"]
    ) - Decimal(plan["prior_spend"]["spent_usd"])
    receipt = {
        "schema_version": "pixelgym-d58-focus-verification-v1",
        "execution_plan_digest": plan["execution_plan_digest"],
        "summary_digest": content_digest(summary),
        "conditions_verified": 20,
        "provider_calls": 0,
        "private_journal_verified": False,
    }
    if journal_path is None:
        return receipt
    config = config_from_snapshot(read(PUBLIC / "price-recheck.json"))
    with closing(sqlite3.connect(f"file:{journal_path.resolve()}?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row

        def obj(digest):
            data = bytes(
                c.execute("SELECT data FROM objects WHERE digest=?", (digest,)).fetchone()[0]
            )
            assert "sha256:" + sha256_bytes(data) == digest
            return data

        events = []
        for record in c.execute("SELECT * FROM events ORDER BY sequence"):
            value = dict(record)
            value["payload"] = json.loads(value["payload"])
            events.append(value)
        by_key = {e["event_key"]: e for e in events}
        assert (
            content_digest(events[: plan["prior_event_count"]]) == plan["prior_event_prefix_digest"]
        )
        count = summary["journal_integrity"]["event_count"]
        assert content_digest(events[:count]) == summary["journal_integrity"]["event_chain_digest"]
        assert by_key[f"{PHASE}/closed"]["payload"]["stop_reason"] == summary["stop_reason"]
        reconstructed, requests, known = 0, 0, Decimal(0)
        failure_receipts = []
        for row in summary["conditions"]:
            trial = row["trial_id"]
            result = by_key.get(f"{trial}/result")
            if result is None:
                assert not row["action_dispatched"]
                continue
            assert result["payload"] == row
            prefix = by_key[f"{trial}/prefix_prepared"]["payload"]
            trial_events = [e for e in events if e["trial_id"] == trial]
            attempted = [e for e in trial_events if e["kind"] == "attempt_started"]
            assert len(attempted) <= 1 and bool(attempted) == row["model_attempted"]
            for event in attempted:
                p = event["payload"]
                rid = content_digest(p["idempotency_key"])
                policy = ScreenshotMemoryPolicy(config, retain_screenshots=row["mode"] == "history")
                try:
                    request = policy.build_request(
                        obj(p["pre_call_checkpoint_digest"]), obj(prefix["screenshot_digest"])
                    )
                    assert content_digest(request) == p["request_digest"]
                finally:
                    policy.close()
                bound = by_key[f"spend/{rid}/request-bound"]["payload"]["request_maximum_usd"]
                reserved = by_key.get(f"spend/{rid}/reserved")
                if reserved:
                    requests += 1
                    assert reserved["payload"]["request_maximum_usd"] == bound
                charged = by_key.get(f"spend/{rid}/charged")
                if charged:
                    assert charged["payload"]["request_maximum_usd"] == bound
                    known += Decimal(charged["payload"]["cost_usd"])
                unknown = by_key.get(f"spend/{rid}/unknown")
                if unknown:
                    assert unknown["payload"]["unknown_reservation_usd"] == bound
            commit = by_key.get(row.get("dispatch_event_key"))
            assert bool(commit) == row["action_dispatched"]
            if not commit:
                charges = [
                    by_key.get(f"spend/{content_digest(e['payload']['idempotency_key'])}/charged")
                    for e in attempted
                ]
                failure_receipts.append(
                    {
                        "trial_id": trial,
                        "failure_codes": sorted(
                            {
                                e["payload"]["failure_code"]
                                for e in trial_events
                                if isinstance(e["payload"].get("failure_code"), str)
                            }
                        ),
                        "known_charge_usd": str(
                            sum(
                                (Decimal(e["payload"]["cost_usd"]) for e in charges if e),
                                Decimal(0),
                            )
                        )
                        if charges and all(charges)
                        else None,
                    }
                )
            if commit:
                before = json.loads(obj(prefix["environment_checkpoint_digest"]))
                after = json.loads(obj(commit["payload"]["environment_checkpoint_digest"]))
                task = generate_memory_task(row["seed"])
                name = row["state_name"]
                valid, correct = False, False
                if name == "unfocused":
                    desired = after["focused"] and not before["focused"]
                elif name in ("focused_empty", "partial"):
                    desired = (
                        after["text_value"]
                        == before["text_value"]
                        + task.stages[1].required_text[len(before["text_value"])]
                    )
                elif name == "complete":
                    desired = after["stage_index"] == 2
                else:
                    selection = after["deferred_choices"].get(str(row["stage_index"]))
                    valid = selection is not None
                    correct = selection == task.stages[row["stage_index"]].target_control_id
                    desired = valid
                assert (desired, valid, correct) == (
                    row["desired_transition"],
                    row["valid_memory_choice"],
                    row["correct_memory_choice"],
                )
                reconstructed += 1
        assert requests == summary["new_wire_requests"]
        assert known == Decimal(summary["new_known_spend_usd"])
        receipt.update(
            private_journal_verified=True,
            stored_dispatches_reconstructed=reconstructed,
            request_bodies_reconstructed=requests,
            prior_prefix_preserved=True,
            request_reservations_and_settlements_match=True,
            failure_receipts=failure_receipts,
        )
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    receipt = verify(args.journal)
    if args.write:
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
        for name, digest in read(PUBLIC / "files.json").items():
            assert "sha256:" + sha256_bytes((PUBLIC / name).read_bytes()) == digest, name
    for path in PUBLIC.glob("*.json"):
        validate_credential_free(read(path))
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
