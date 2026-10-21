"""Bind immutable stopped qualifications into a separately approved replacement allowance."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.retention import restore_archive, verify_archive
from pixelgym.platform.s7_source import verify_snapshot, write_json


def _read(path: Path) -> dict[str, Any]:
    from pixelgym.platform.s7_qualification import read_json

    return read_json(path)


def stopped_count(run: Path) -> int:
    """Read stopped evidence; do not resume its journal or campaign timer."""
    from pixelgym.platform.s7_evidence import verify_seal

    verify_seal(run)
    failure, source = _read(run / "failure.json"), _read(run / "source-binding.json")
    verify_snapshot(run / "source", source)
    if (
        failure["qualification_complete"] is not False
        or failure["activation_performed"] is not False
        or failure["publication_performed"] is not False
        or failure["real_provider_requests"] != 0
        or failure["monetary_spend_usd"] != 0
        or (run / "results.json").exists()
    ):
        raise ValueError("S7 predecessor is not a stopped zero-spend qualification")
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
    return count


def freeze_predecessors(run: Path, archives: tuple[Path, ...]) -> dict[str, Any]:
    records = []
    previous = 0
    for index, archive in enumerate(archives):
        manifest = json.loads((archive / "manifest.json").read_bytes())
        digest = content_digest(manifest)
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
    accounting = {
        "records": records,
        "previous_qualification_sends": previous,
        "qualification_allowance": previous + 77,
        "campaign_allowance": previous + 111,
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
    if (
        previous != accounting["previous_qualification_sends"]
        or accounting["qualification_allowance"] != previous + 77
        or accounting["campaign_allowance"] != previous + 111
    ):
        raise ValueError("S7 cumulative allowance differs from immutable predecessors")
    return previous
