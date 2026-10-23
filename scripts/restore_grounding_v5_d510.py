"""Restore both parser-sensitivity evidence copies and reproduce their audited projections.

No policy, model, authentication probe or task is executed. Archive snapshots
precede the final retention receipt, so a separate audit view supplies that one
already-published metadata field. Restored bytes themselves remain unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.retention import (
    _no_links,
    _separate,
    restore_archive,
    source_fingerprint,
    write_private,
)
from pixelgym.serialization import canonical_json_bytes
from scripts import report_grounding_v5_d59_extraction as successor
from scripts import report_grounding_v5_d59_replication as replication


def final_summary_bytes(restored: bytes, final: bytes, projection: dict) -> bytes:
    """Allow only the documented, independently bound post-capture receipt addition."""
    snapshot, finished = json.loads(restored), json.loads(final)
    replication.require(
        "sha256:" + sha256_bytes(final) == projection["source_artifacts"]["summary.json"],
        "final summary differs from the published binding",
    )
    replication.require(snapshot.get("retention") is None, "archive already carries a receipt")
    retained = projection["retention"]
    receipt = {**retained, "manifest_digest": retained["archive"]["manifest_digest"]}
    replication.require(
        {**snapshot, "retention": receipt} == finished,
        "restored summary differs beyond the final retention receipt",
    )
    return final


def drill(run: Path, archive: Path, backup: Path, destination: Path) -> dict:
    """Restore both full and pilot copies into fresh private directories; never clean up."""
    run, archive, backup, destination = map(_no_links, (run, archive, backup, destination))
    _separate(run, archive, backup, destination)
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    observations = []
    for phase in ("pilot", "full"):
        name = "pilot-report.json" if phase == "pilot" else "report.json"
        expected = replication.read(successor.OUTPUT / name)
        manifest = expected["retention"]["archive"]["manifest_digest"]
        for label, store in (("archive", archive), ("backup", backup)):
            restored = destination / phase / label / "restored"
            receipt = restore_archive(store / phase, restored, expected_digest=manifest)
            before = source_fingerprint(restored)
            # Hard links avoid a second multi-GB copy. Every audit database connection
            # is read-only; a post-audit fingerprint rejects any mutation of these bytes.
            view = restored.parent / "audit-view"
            view.mkdir(mode=0o700)
            for path in restored.iterdir():
                if path.name != "summary.json":
                    os.link(path, view / path.name)
            summary = final_summary_bytes(
                (restored / "summary.json").read_bytes(),
                (run / phase / "summary.json").read_bytes(),
                expected,
            )
            write_private(view / "summary.json", summary)
            observed = successor.audit(view, archive / phase, backup / phase)
            replication.require(observed == expected, "restored projection differs")
            replication.require(source_fingerprint(restored) == before, "restored bytes changed")
            observations.append(
                {
                    "phase": phase,
                    "copy": label,
                    "restore": receipt,
                    "restored_fingerprint_before": before,
                    "restored_fingerprint_after": before,
                    "projection_digest": content_digest(observed),
                    "projection_matches_published": True,
                    "restored_bytes_unchanged": True,
                    "completed": observed["completed"],
                    "unresolved_timeouts": observed["unresolved_invocations"],
                    "summary_overlay": (
                        "only bound final retention receipt; restored file untouched"
                    ),
                }
            )
    return {
        "schema_version": "pixelgym-d510-campaign-restore-drill-v1",
        "observations": observations,
        "model_calls": 0,
        "authentication_calls": 0,
        "tasks_reexecuted": 0,
        "original_results_modified": False,
        "originals_or_restored_evidence_deleted": False,
        "cleanup_authorized": False,
        "copies_on_same_local_disk": True,
        "off_host_restore_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("run", "archive", "backup", "destination", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    _separate(
        *(
            _no_links(getattr(args, name))
            for name in ("run", "archive", "backup", "destination", "output")
        )
    )
    if args.output.exists():
        raise FileExistsError("restore receipt already exists")
    result = drill(args.run, args.archive, args.backup, args.destination)
    write_private(args.output, canonical_json_bytes(result) + b"\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
