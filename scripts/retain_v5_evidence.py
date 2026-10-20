"""Archive, back up, restore, and check cleanup preconditions without provider calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.v5.retention import (
    _checkout,
    _no_links,
    _separate,
    backup_archive,
    check_cleanup,
    create_archive,
    restore_archive,
    verify_archive,
    write_private,
)
from pixelgym.serialization import canonical_json_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="capture stopped evidence and a second copy")
    capture.add_argument("--source", type=Path, required=True)
    capture.add_argument("--archive", type=Path, required=True)
    capture.add_argument("--backup", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--digest", required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--digest", required=True)
    cleanup = commands.add_parser(
        "check-cleanup", help="restore both copies; never delete evidence"
    )
    cleanup.add_argument("--source", type=Path, required=True)
    cleanup.add_argument("--archive", type=Path, required=True)
    cleanup.add_argument("--backup", type=Path, required=True)
    cleanup.add_argument("--digest", required=True)
    cleanup.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "capture":
        # Preflight both destinations before capture; no existing evidence is overwritten.
        _separate(*map(_no_links, (args.source, args.archive, args.backup)))
        if _checkout(_no_links(args.backup)) is not None:
            raise ValueError("private backups must be outside Git checkouts")
        if args.backup.exists():
            raise FileExistsError("backup already exists")
        result = create_archive(args.source, args.archive)
        backup = backup_archive(
            args.archive, args.backup, expected_digest=result["manifest_digest"]
        )
        result = {"archive": result, "backup": backup}
    elif args.command == "verify":
        result = verify_archive(args.archive, expected_digest=args.digest)
    elif args.command == "restore":
        result = restore_archive(args.archive, args.destination, expected_digest=args.digest)
    else:
        _separate(*map(_no_links, (args.source, args.archive, args.backup, args.receipt)))
        if args.receipt.exists():
            raise FileExistsError("receipt already exists")
        result = check_cleanup(args.source, args.archive, args.backup, expected_digest=args.digest)
        write_private(args.receipt, canonical_json_bytes(result))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
