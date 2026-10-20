"""Back up sealed evidence to private GitHub LFS and verify a fresh remote restore."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.retention import _no_links, _separate, write_private
from pixelgym.grounding.v5.retention_lfs import RECEIPT_SCHEMA, backup_remote, restore_remote
from pixelgym.serialization import canonical_json_bytes
from scripts.validate_evidence_retention import _restored_check
from scripts.verify_retention_bundle import verify_bundle


def report_receipts(receipts: list[Path], bundles: list[Path]) -> str:
    """Bind stored remote observations to public bundles. Never fetch or execute evidence."""
    sources = {}
    for bundle in bundles:
        verify_bundle(bundle)
        manifest = json.loads((bundle / "archive-manifest.json").read_bytes())
        report = json.loads((bundle / "report.json").read_bytes())
        sources[content_digest(manifest)] = (manifest, report)
    rows = []
    objects = {}
    for path in receipts:
        receipt = json.loads(path.read_bytes())
        if (
            receipt.get("schema_version") != RECEIPT_SCHEMA
            or receipt.get("verification_scope") != "scripted-campaign"
            or receipt.get("fresh_git_and_lfs_cache") is not True
            or receipt.get("real_provider_requests") != 0
            or receipt["budget_observation"]["budget_usd"] != 0
            or receipt["budget_observation"]["stop_usage"] is not True
        ):
            raise ValueError("invalid scripted remote receipt")
        digest = receipt["manifest_digest"]
        manifest, report = sources[digest]
        restored = receipt["restored_archive"]
        check = receipt["restored_source_check"]
        output = check["output"]
        if (
            check["exit_status"] != 0
            or output["case_count"] != report["case_count"]
            or output["real_provider_requests"] != 0
            or output["report_digest"] != content_digest(report)
            or restored["manifest_digest"] != digest
            or restored["source_fingerprint"] != manifest["source_fingerprint"]
            or restored["file_count"] != len(manifest["files"])
            or restored["restored_file_count"] != len(manifest["files"])
            or restored["bytes"] != sum(entry["size"] for entry in manifest["files"])
        ):
            raise ValueError("remote receipt disagrees with the published validation bundle")
        local = json.loads(
            next(
                (bundle / "retention-receipt.json")
                for bundle in bundles
                if content_digest(json.loads((bundle / "archive-manifest.json").read_bytes()))
                == digest
            ).read_bytes()
        )
        if output["replay_digest"] != local["restored_source_checks"][0]["output"]["replay_digest"]:
            raise ValueError("remote replay digest differs from the recorded local replay")
        entry = receipt["lfs_object"]
        if entry["path"] != "archives/" + digest[7:] + ".tar.gz":
            raise ValueError("remote LFS path differs from its manifest identity")
        if entry["oid"] in objects and objects[entry["oid"]] != entry["size"]:
            raise ValueError("inconsistent LFS object size")
        objects[entry["oid"]] = entry["size"]
        rows.append(
            f"| `{path.name}` | `{receipt['commit']}` | {entry['size']} | "
            f"{output['case_count']} | {check['exit_status']} |"
        )
    return "\n".join(
        [
            "# Private Git LFS recovery observations",
            "",
            "Generated from stored receipts and checked against the published validation bundles.",
            "",
            "| Receipt | Pinned remote commit | Downloaded bytes | Scripted cases | Exit status |",
            "| --- | --- | ---: | ---: | ---: |",
            *rows,
            "",
            (
                f"Unique recorded LFS objects: {len(objects)}; "
                f"total packed bytes: {sum(objects.values())}."
            ),
            "",
            (
                "Each receipt records a fresh Git/LFS cache, archive-byte verification, and the "
                "isolated retained-source check. Its replay and report digests match the original "
                "public bundle. No experiment model calls were made."
            ),
            "",
            (
                "The GitHub $0 limit with stop usage was observed in the owner billing UI. The "
                "local observation does not configure or continuously verify that setting."
            ),
            "",
            (
                "These offline checks verify published bindings only. They cannot inspect private "
                "remote archive bytes or establish continuous availability. An authorized operator "
                "must run the restore command to repeat recovery. This evidence does not restore "
                "D5.9 or declare a milestone verdict."
            ),
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="render stored receipts without network or replay")
    report.add_argument("--receipt", type=Path, action="append", required=True)
    report.add_argument("--bundle", type=Path, action="append", required=True)
    for name in ("backup", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--repository", required=True, help="private owner/name on github.com")
        command.add_argument("--digest", required=True, help="previously recorded manifest SHA-256")
        command.add_argument("--budget-record", type=Path, required=True)
        command.add_argument(
            "--destination", type=Path, required=True, help="new restore directory"
        )
        command.add_argument("--receipt", type=Path, required=True)
        command.add_argument("--logs", type=Path)
        command.add_argument(
            "--validation-campaign",
            action="store_true",
            help="run the fixed scripted verifier from the trusted retained source; no model calls",
        )
        if name == "backup":
            command.add_argument("--archive", type=Path, required=True)
        else:
            command.add_argument("--commit", required=True, help="full remote Git commit")
    args = parser.parse_args()
    if args.command == "report":
        print(report_receipts(args.receipt, args.bundle), end="")
        return
    paths = [args.budget_record, args.destination, args.receipt]
    if args.command == "backup":
        paths.append(args.archive)
    if args.logs is not None:
        paths.append(args.logs)
    _separate(*map(_no_links, paths))
    if args.receipt.exists() or (args.logs is not None and args.logs.exists()):
        raise FileExistsError("receipt or log destination already exists")
    if args.validation_campaign and args.logs is None:
        parser.error("--validation-campaign requires --logs")
    options = {
        "verify_restored": _restored_check if args.validation_campaign else None,
        "logs": args.logs,
    }
    if args.command == "backup":
        result = backup_remote(
            args.archive,
            args.repository,
            args.digest,
            args.budget_record,
            args.destination,
            **options,
        )
    else:
        result = restore_remote(
            args.repository,
            args.commit,
            args.digest,
            args.destination,
            args.budget_record,
            **options,
        )
    write_private(args.receipt, canonical_json_bytes(result))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
