"""Verify public evidence and, locally, its closed authoritative journal; no paid calls."""

import argparse
import fcntl
import json
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_calibration import MemoryCalibrationLedger, summarize
from pixelgym.grounding.v5.memory_plan import config_from_price_snapshot
from pixelgym.grounding.v5.screenshot_memory import build_screenshot_policy_manifest
from scripts.run_grounding_v5_memory_calibration import (
    JOURNAL,
    PHASE,
    PRIVATE,
    ROOT,
    canonical_plan,
    execution_amendment,
    identity_failure,
    prefix_digest,
    render_report,
)

DIRECTORY = Path(__file__).parent


def read(name: str) -> dict:
    return json.loads((DIRECTORY / name).read_text())


def hashes() -> dict:
    return {
        p.name: "sha256:" + sha256_bytes(p.read_bytes())
        for p in sorted(DIRECTORY.iterdir())
        if p.is_file() and p.name != "files.json"
    }


def audit_journal() -> dict:
    if not JOURNAL.exists():
        raise FileNotFoundError(
            "Original private aggregate journal required; no replacement allowed"
        )
    plan, summary = read("execution-plan.json"), read("summary.json")
    current = canonical_plan()
    amendment = execution_amendment(plan, current)
    assert amendment == read("execution-amendment-1.json")
    config = config_from_price_snapshot(read("price-recheck.json"))
    with (PRIVATE / "operator.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(V5AttemptJournal(JOURNAL)) as journal:
            closed = journal.event(f"{PHASE}/closed")
            assert closed is not None
            ledger = MemoryCalibrationLedger(Decimal("5.00"), Decimal(0), journal=journal)
            reconstructed = summarize(
                journal, ledger, plan, stop_reason=closed.payload["stop_reason"]
            )
            reconstructed["execution_amendments"] = [amendment]
            reconstructed["effective_driver_code_revision"] = current["driver_code_revision"]
            assert summary == reconstructed
            assert (DIRECTORY / "report.md").read_text() == render_report(summary)
            assert (
                prefix_digest(journal, plan["pilot_ledger_event_count"])
                == plan["pilot_ledger_prefix_digest"]
            )
            full_events = [e for e in journal.events() if e.trial_id.startswith(PHASE + "-")]
            attempts = [e for e in full_events if e.kind == "attempt_started"]
            assert all(e.attempt_index == 0 for e in attempts)
            assert len({(e.trial_id, e.step_index) for e in attempts}) == len(attempts)
            assert not any(e.kind.startswith("memory_prefix") for e in full_events)
            assert len(attempts) == sum(row["model_attempts"] for row in summary["conditions"])
            assert journal.call_counts()[1] == 0
            assert ledger.in_flight_reservation_usd == 0
            assert ledger.budget_accounted_spend_usd <= Decimal("5.00")
            if summary["stop_reason"] == "aggregate_budget_stop":
                assert (
                    ledger.blocked
                    or ledger.budget_accounted_spend_usd + config.request_maximum_usd
                    > Decimal("5.00")
                )
            for job in plan["jobs"]:
                assert not identity_failure(journal, job["trial_id"], config)
            for mode, manifest in plan["policy_manifests"].items():
                assert (
                    build_screenshot_policy_manifest(
                        ROOT,
                        config=config,
                        code_revision=manifest["code_revision"],
                        retain_screenshots=mode == "history",
                    ).to_dict()
                    == manifest
                )
            return {
                "schema_version": "pixelgym-d58-full-memory-evidence-verification-v1",
                "summary_digest": content_digest(summary),
                "execution_plan_digest": plan["execution_plan_digest"],
                "effective_driver_code_revision": current["driver_code_revision"],
                "journal_integrity": journal.integrity_report(),
                "pilot_prefix_unchanged": True,
                "summary_reconstructed_from_journal": True,
                "frozen_policies_and_task_admission_verified": True,
                "full_phase_model_attempts": len(attempts),
                "no_retried_steps_or_scripted_prefixes": True,
                "provider_identity_and_price_guards_verified": True,
                "phase_closed": True,
                "aggregate_spend": ledger.to_dict(),
                "provider_calls_by_verifier": 0,
            }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--journal", action="store_true")
    args = parser.parse_args()
    if args.record:
        if (DIRECTORY / "files.json").exists():
            raise FileExistsError("Refusing to replace frozen evidence hashes")
        result = audit_journal()
        (DIRECTORY / "verification.json").write_text(
            json.dumps(result, sort_keys=True, indent=2) + "\n"
        )
        (DIRECTORY / "files.json").write_text(json.dumps(hashes(), sort_keys=True, indent=2) + "\n")
    else:
        assert read("files.json") == hashes()
        assert (DIRECTORY / "report.md").read_text() == render_report(read("summary.json"))
        if args.journal:
            assert read("verification.json") == audit_journal()
    print(
        json.dumps(
            {
                "verified": True,
                "private_journal_checked": args.record or args.journal,
                "provider_calls": 0,
            }
        )
    )
