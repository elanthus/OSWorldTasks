"""Read-only reconstruction of the bounded reliability diagnostic."""

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.openrouter_policy import _usage_cost
from pixelgym.grounding.v5.reliable_memory import ReliableMemoryPolicy, reliable_config
from pixelgym.grounding.v5.request_budget import request_bound
from scripts.run_grounding_v5_reliable_diagnostic import (
    PHASE, PREVIOUS, PUBLIC, ROOT, config_from_snapshot, read, write,
)


def verify(journal_path=None):
    plan, summary = read(PUBLIC / "execution-plan.json"), read(PUBLIC / "summary.json")
    digest = content_digest({k: v for k, v in plan.items() if k != "execution_plan_digest"})
    assert digest == plan["execution_plan_digest"] == summary["execution_plan_digest"]
    for name, expected in plan["source_digests"].items():
        assert "sha256:" + sha256_bytes((ROOT / name).read_bytes()) == expected, name
    assert content_digest(read(PUBLIC / "price-recheck.json")) == plan["price_snapshot_digest"]
    assert content_digest(read(PREVIOUS / "summary.json")) == plan["previous_summary_digest"]
    rows, jobs = summary["conditions"], plan["jobs"]
    assert len(rows) == len(jobs) == 10
    for row, job in zip(rows, jobs, strict=True):
        assert row["trial_id"] == job["trial_id"] and row["mode"] == job["mode"]
        assert row["case_id"] == job["case"]["case_id"]
        assert 0 <= row["model_attempts"] <= 3
    assert summary["dispatched"] == sum(r["action_dispatched"] for r in rows)
    spend, prior = summary["aggregate_spend"], plan["prior_spend"]
    assert Decimal(spend["in_flight_reservation_usd"]) == 0 and summary["transport_idle_at_close"]
    assert Decimal(spend["budget_accounted_spend_usd"]) == Decimal(spend["spent_usd"]) + Decimal(spend["unknown_reservation_usd"])
    assert Decimal(spend["budget_accounted_spend_usd"]) <= Decimal(28)
    known = Decimal(spend["spent_usd"]) - Decimal(prior["spent_usd"])
    held = Decimal(spend["unknown_reservation_usd"]) - Decimal(prior["unknown_reservation_usd"])
    assert known == Decimal(summary["new_known_spend_usd"])
    assert held == Decimal(summary["new_unknown_holds_usd"])
    assert 0 <= known + held <= Decimal(1)
    assert summary["new_wire_requests"] == spend["wire_requests_sent"] - prior["wire_requests_sent"] <= 20
    report = (PUBLIC / "report.md").read_text()
    assert f"Actions dispatched: {summary['dispatched']}/10. Wire calls: {summary['new_wire_requests']}/20." in report
    for row in rows:
        assert f"| {row['case_id']} | {row['mode']} | {row['classification']} | {row['model_attempts']} |" in report
    receipt = {"schema_version": "pixelgym-d58-reliable-diagnostic-verification-v1",
               "execution_plan_digest": digest, "summary_digest": content_digest(summary),
               "logical_conditions_verified": 10, "private_journal_verified": False, "provider_calls": 0}
    if journal_path is None:
        return receipt
    config = reliable_config(config_from_snapshot(read(PUBLIC / "price-recheck.json")))
    with closing(sqlite3.connect(f"file:{journal_path.resolve()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        events = [{**dict(r), "payload": json.loads(r["payload"])} for r in connection.execute(
            "SELECT * FROM events ORDER BY sequence LIMIT ?", (summary["journal_integrity"]["event_count"],))]
        assert content_digest(events) == summary["journal_integrity"]["event_chain_digest"]
        assert content_digest(events[:plan["prior_integrity"]["event_count"]]) == plan["prior_integrity"]["event_chain_digest"]
        by_key = {e["event_key"]: e for e in events}
        def obj(digest):
            result = connection.execute("SELECT data FROM objects WHERE digest=?", (digest,)).fetchone()
            assert result is not None
            raw = bytes(result["data"])
            assert "sha256:" + sha256_bytes(raw) == digest
            return raw
        assert by_key[f"{PHASE}/closed"]["payload"] == {"stop_reason": summary["stop_reason"], "transport_idle": True}
        prefix = f"reliable/{content_digest(PHASE)}"
        wire_receipts = {e["payload"]["reservation_id"]: e["payload"] for e in events if e["trial_id"] == prefix and e["kind"] == "reliable_transport_receipt"}
        assert list(wire_receipts.values()) == summary["transport_receipts"]
        reconstructed, wires, computed_known, computed_held = 0, 0, Decimal(0), Decimal(0)
        settlements, error_counts, images = Counter(), Counter(), []
        for row, job in zip(rows, jobs, strict=True):
            trial = job["trial_id"]
            result = by_key.get(f"{trial}/result")
            if result:
                assert result["payload"] == row
            trial_events = [e for e in events if e["trial_id"] == trial]
            attempts = [e for e in trial_events if e["kind"] == "attempt_started"]
            assert len(attempts) == row["model_attempts"]
            dispatches = [e for e in trial_events if e["kind"] == "dispatch_committed"]
            assert len(dispatches) == int(row["action_dispatched"])
            if not attempts:
                continue
            assert by_key[f"{trial}/started"]["payload"] == {"job": job, "policy_id": plan["policy_manifests"][row["mode"]]["policy_id"], "execution_plan_digest": digest}
            prepared = by_key[f"{trial}/prefix_prepared"]["payload"]
            requests = []
            for index, event in enumerate(attempts):
                assert event["attempt_index"] == index
                payload = event["payload"]
                policy = ReliableMemoryPolicy(config, retain_screenshots=row["mode"] == "history")
                request = policy.build_request(obj(payload["pre_call_checkpoint_digest"]), obj(prepared["screenshot_digest"]))
                requests.append(content_digest(request))
                assert requests[-1] == payload["request_digest"]
                reconstructed += 1
                rid = content_digest(payload["idempotency_key"])
                reserved = by_key.get(f"spend/{rid}/reserved")
                if not reserved:
                    continue
                wires += 1
                proof = request_bound(request, config)
                images.append(proof["images"])
                bound = by_key[f"spend/{rid}/request-bound"]["payload"]
                assert all(bound[k] == v for k, v in proof.items())
                maximum = bound["request_maximum_usd"]
                assert reserved["payload"]["request_maximum_usd"] == maximum
                wire = wire_receipts[rid]
                try:
                    body = json.loads(obj(wire["response_envelope_digest"]))
                    cost = _usage_cost(body.get("usage", {}))
                except (ValueError, AttributeError):
                    cost = None
                charged = by_key.get(f"spend/{rid}/charged")
                unknown = by_key.get(f"spend/{rid}/unknown")
                if charged:
                    assert cost == Decimal(charged["payload"]["cost_usd"])
                    assert charged["payload"]["request_maximum_usd"] == maximum
                    computed_known += cost
                    settlements["confirmed_zero" if cost == 0 else "confirmed_positive"] += 1
                else:
                    assert unknown and cost is None
                    assert unknown["payload"]["unknown_reservation_usd"] == maximum
                    computed_held += Decimal(maximum)
                    settlements["unknown"] += 1
                if wire["curl_exit_code"]:
                    error_counts[f"curl_{wire['curl_exit_code']}"] += 1
            assert len(set(requests)) == 1, "retry changed the request"
        assert reconstructed == sum(r["model_attempts"] for r in rows)
        assert wires == summary["new_wire_requests"] == len(wire_receipts)
        assert computed_known == known and computed_held == held
        receipt.update(private_journal_verified=True, prior_prefix_preserved=True,
                       request_bodies_reconstructed=reconstructed, wire_reservations_verified=wires,
                       settlement_counts=dict(settlements), curl_error_counts=dict(error_counts),
                       request_image_counts=images, exactly_one_dispatch_per_completed_condition=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = verify(args.journal)
    if args.write:
        assert args.journal is not None
        if (PUBLIC / "files.json").exists():
            raise FileExistsError("cannot replace frozen evidence")
        write(PUBLIC / "verification.json", result)
        write(PUBLIC / "files.json", {p.name: "sha256:" + sha256_bytes(p.read_bytes()) for p in sorted(PUBLIC.iterdir()) if p.is_file() and p.name != "files.json"})
    else:
        for name, digest in read(PUBLIC / "files.json").items():
            assert "sha256:" + sha256_bytes((PUBLIC / name).read_bytes()) == digest, name
        if args.journal:
            assert result == read(PUBLIC / "verification.json")
    for path in PUBLIC.glob("*.json"):
        validate_credential_free(read(path))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
