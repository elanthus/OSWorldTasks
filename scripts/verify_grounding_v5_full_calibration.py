"""Hardened successor to the frozen D5.8 full-calibration verifier.

Checks remain active under Python optimization. Original evidence and the
original verifier are retained byte-for-byte for historical reproduction.
"""

import argparse
import fcntl
import json
from contextlib import closing
from decimal import Decimal
from typing import Any

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

DIRECTORY = ROOT / "artifacts/grounding-v5-d58-full-calibration"


def read(name: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((DIRECTORY / name).read_text())
    return value


def hashes() -> dict[str, str]:
    return {
        p.name: "sha256:" + sha256_bytes(p.read_bytes())
        for p in sorted(DIRECTORY.iterdir())
        if p.is_file() and p.name != "files.json"
    }


def audit_journal() -> dict[str, Any]:
    if not JOURNAL.exists():
        raise FileNotFoundError(
            "Original private aggregate journal required; no replacement allowed"
        )
    plan, summary = (read("execution-plan.json"), read("summary.json"))
    current = canonical_plan()
    amendment = execution_amendment(plan, current)
    if amendment != read("execution-amendment-1.json"):
        raise ValueError("execution amendment differs from the approved record")
    config = config_from_price_snapshot(read("price-recheck.json"))
    with (PRIVATE / "operator.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(V5AttemptJournal(JOURNAL)) as journal:
            closed = journal.event(f"{PHASE}/closed")
            if closed is None:
                raise ValueError("full phase has no closure record")
            ledger = MemoryCalibrationLedger(Decimal("5.00"), Decimal(0), journal=journal)
            reconstructed = summarize(
                journal, ledger, plan, stop_reason=closed.payload["stop_reason"]
            )
            reconstructed["execution_amendments"] = [amendment]
            reconstructed["effective_driver_code_revision"] = current["driver_code_revision"]
            if summary != reconstructed:
                raise ValueError("summary does not match the journal reconstruction")
            if (DIRECTORY / "report.md").read_text() != render_report(summary):
                raise ValueError("report does not match the stored summary")
            if (
                prefix_digest(journal, plan["pilot_ledger_event_count"])
                != plan["pilot_ledger_prefix_digest"]
            ):
                raise ValueError("pilot ledger prefix differs from approved lineage")
            full_events = [e for e in journal.events() if e.trial_id.startswith(PHASE + "-")]
            attempts = [e for e in full_events if e.kind == "attempt_started"]
            if not all(e.attempt_index == 0 for e in attempts):
                raise ValueError("a trial step was retried")
            if len({(e.trial_id, e.step_index) for e in attempts}) != len(attempts):
                raise ValueError("duplicate attempts for a trial step")
            if any(e.kind.startswith("memory_prefix") for e in full_events):
                raise ValueError("a scripted prefix was recorded")
            if len(attempts) != sum(row["model_attempts"] for row in summary["conditions"]):
                raise ValueError("attempt count differs from the summary")
            if journal.call_counts()[1] != 0:
                raise ValueError("provider control requests were recorded")
            if ledger.in_flight_reservation_usd != 0:
                raise ValueError("requests remain in flight")
            if ledger.budget_accounted_spend_usd > Decimal("5.00"):
                raise ValueError("aggregate budget exceeds the approved cap")
            if summary["stop_reason"] == "aggregate_budget_stop" and not (
                ledger.blocked
                or ledger.budget_accounted_spend_usd + config.request_maximum_usd > Decimal("5.00")
            ):
                raise ValueError("budget stop is not supported by the ledger")
            for job in plan["jobs"]:
                if identity_failure(journal, job["trial_id"], config):
                    raise ValueError("provider identity or price guard failed")
            for mode, manifest in plan["policy_manifests"].items():
                if (
                    build_screenshot_policy_manifest(
                        ROOT,
                        config=config,
                        code_revision=manifest["code_revision"],
                        retain_screenshots=mode == "history",
                    ).to_dict()
                    != manifest
                ):
                    raise ValueError("policy manifest differs from the frozen policy")
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


def verify_public() -> None:
    if read("files.json") != hashes():
        raise ValueError("public evidence hashes differ from the frozen bundle")
    if (DIRECTORY / "report.md").read_text() != render_report(read("summary.json")):
        raise ValueError("report does not match the stored summary")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--journal",
        action="store_true",
        help="Requires the original source revision and journal at this phase closure.",
    )
    args = parser.parse_args()
    verify_public()
    if args.journal and read("verification.json") != audit_journal():
        raise ValueError("private journal verification differs from the stored receipt")
    print(
        json.dumps({"verified": True, "private_journal_checked": args.journal, "provider_calls": 0})
    )


if __name__ == "__main__":
    main()
