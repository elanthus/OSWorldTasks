"""Bind immutable stopped qualifications into a separately approved replacement allowance."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.retention import restore_archive, verify_archive
from pixelgym.platform.s7_policy import EVIDENCE_LABEL, POLICY_SHA256, policy_document
from pixelgym.platform.s7_source import verify_snapshot, write_json


def _read(path: Path) -> dict[str, Any]:
    from pixelgym.platform.s7_qualification import read_json

    return read_json(path)


def stopped_count(run: Path) -> int:
    """Read stopped evidence; do not resume its journal or campaign timer."""
    from pixelgym.platform.s7_evidence import verify_seal

    verify_seal(run)
    failure, source = _read(run / "failure.json"), _read(run / "source-binding.json")
    plan = _read(run / "plan.json")
    if (
        plan.get("schema_version") != "pixelgym-s7-qualification-plan-v1"
        or plan.get("evidence_label") != EVIDENCE_LABEL
        or plan.get("gate_policy_sha256") != POLICY_SHA256
    ):
        raise ValueError("S7 predecessor plan is not under the approved gate policy")
    verify_snapshot(run / "source", source)
    if (
        failure["qualification_complete"] is not False
        or failure["activation_performed"] is not False
        or failure["publication_performed"] is not False
        or failure["real_provider_requests"] != 0
        or failure["monetary_spend_usd"] != 0
    ):
        raise ValueError("S7 predecessor is not a stopped zero-spend qualification")
    recorded_failure = failure.get("schema_version") == "pixelgym-s7-failure-v1" and failure.get(
        "stage"
    ) in {"qualification", "finalization"}
    if ((run / "results.json").exists() or (run / "packages").exists()) and not recorded_failure:
        raise ValueError("stopped qualification contains conflicting completion evidence")
    if "source_revision" in failure and failure["source_revision"] != source["revision"]:
        raise ValueError("S7 stopped source revision differs from its binding")
    with closing(
        sqlite3.connect(
            (run / "send-budget.sqlite").resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        )
    ) as db:
        counts: dict[str, int] = dict(
            db.execute("SELECT phase, count(*) FROM sends GROUP BY phase")
        )
    count = counts.get("qualification", 0)
    if set(counts) - {"qualification"} or count != failure["fake_send_reservations"]:
        raise ValueError("S7 predecessor accounting differs from its durable ledger")
    if recorded_failure and failure["stage"] == "finalization":
        results = _read(run / "results.json")
        if results["fake_sends"] != counts or results["plan_sha256"] != content_digest(plan):
            raise ValueError("S7 partial finalization differs from its durable accounting")
    return count


def freeze_predecessors(
    run: Path, archives: tuple[tuple[Path, str], ...], *, expected_sends: int
) -> dict[str, Any]:
    if type(expected_sends) is not int or expected_sends < 0:
        raise ValueError("explicit nonnegative expected predecessor send count is required")
    records = []
    previous = 0
    for index, (archive, digest) in enumerate(archives):
        manifest = json.loads((archive / "manifest.json").read_bytes())
        receipt = verify_archive(archive, expected_digest=digest)
        relative = f"predecessors/{index}/run"
        restore_archive(archive, run / relative, expected_digest=digest)
        count = stopped_count(run / relative)
        write_json(run / f"predecessors/{index}/archive-manifest.json", manifest)
        write_json(run / f"predecessors/{index}/archive-receipt.json", receipt)
        records.append(
            {
                "path": relative,
                "archive_manifest_sha256": digest,
                "seal_sha256": content_digest(_read(run / relative / "seal.json")),
                "plan_sha256": content_digest(_read(run / relative / "plan.json")),
                "qualification_sends": count,
            }
        )
        previous += count
    if previous != expected_sends:
        raise ValueError("S7 predecessor sends differ from the explicit approved count")
    policy = policy_document()
    accounting = {
        "records": records,
        "previous_qualification_sends": previous,
        "expected_previous_qualification_sends": expected_sends,
        "qualification_allowance": previous + policy["phase_caps"]["qualification"],
        "campaign_allowance": previous + policy["campaign_cap"],
    }
    verify_predecessors(run, accounting)
    return accounting


def verify_predecessors(run: Path, accounting: dict[str, Any]) -> int:
    previous = 0
    identities = set()
    for index, record in enumerate(accounting["records"]):
        relative = f"predecessors/{index}/run"
        if record["path"] != relative:
            raise ValueError("S7 predecessor path differs from its assigned index")
        retained = run / relative
        manifest = _read(run / f"predecessors/{index}/archive-manifest.json")
        receipt = _read(run / f"predecessors/{index}/archive-receipt.json")
        if (
            content_digest(manifest) != record["archive_manifest_sha256"]
            or receipt["manifest_digest"] != record["archive_manifest_sha256"]
            or content_digest(_read(retained / "seal.json")) != record["seal_sha256"]
            or content_digest(_read(retained / "plan.json")) != record["plan_sha256"]
            or record["plan_sha256"] in identities
        ):
            raise ValueError("S7 predecessor identity or archive binding differs")
        identities.add(record["plan_sha256"])
        count = stopped_count(retained)
        if count != record["qualification_sends"]:
            raise ValueError("S7 predecessor count changed")
        previous += count
    policy = policy_document()
    if (
        previous != accounting["previous_qualification_sends"]
        or previous != accounting.get("expected_previous_qualification_sends", previous)
        or accounting["qualification_allowance"] != previous + policy["phase_caps"]["qualification"]
        or accounting["campaign_allowance"] != previous + policy["campaign_cap"]
    ):
        raise ValueError("S7 cumulative allowance differs from immutable predecessors")
    return previous
