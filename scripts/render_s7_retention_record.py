"""Render or check an S7 retention record's Markdown from its stored JSON; no replay, no network."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = "pixelgym-s7-successor-retention-record-v1"
REQUIRED_OUTPUT = (
    "activation_performed",
    "cumulative_qualification_sends",
    "fake_sends",
    "inspection_unchanged",
    "policy_or_transport_executed",
    "publication_performed",
    "real_provider_requests",
    "report_sha256",
    "source_revision",
    "verification_sha256",
)


def check_record(record: dict[str, Any]) -> None:
    """Fail closed on any internal disagreement; never reinterpret stored outputs."""
    if record.get("schema_version") != SCHEMA:
        raise ValueError("unexpected retention record schema")
    archive = record["archive"]
    copies = [record["local_restore"], record["remote_copy"]["restored_archive"]]
    for copy in copies:
        if (
            copy["manifest_digest"] != archive["manifest_digest"]
            or copy["bytes"] != archive["bytes"]
            or copy["restored_file_count"] != archive["file_count"]
            or copy["file_count"] != archive["file_count"]
        ):
            raise ValueError("restored copy differs from the archive receipt")
    remote = record["remote_copy"]
    if remote["manifest_digest"] != archive["manifest_digest"]:
        raise ValueError("remote receipt is bound to a different archive")
    if remote["fresh_git_and_lfs_cache"] is not True or remote["real_provider_requests"] != 0:
        raise ValueError("remote restore did not use a fresh cache without provider requests")
    budget = remote["budget_observation"]
    if budget["budget_usd"] != 0 or budget["stop_usage"] is not True:
        raise ValueError("remote copy was not made under a $0 stop-usage observation")
    if budget["repository"] != remote["repository"]:
        raise ValueError("budget observation names a different repository")
    checks = record["checks"]
    if [c["label"] for c in checks] != ["original", "local-restore", "remote-restore"]:
        raise ValueError("retention record must hold the three ordered verifier checks")
    outputs = []
    for check in checks:
        output = check["output"]
        missing = [key for key in REQUIRED_OUTPUT if key not in output]
        if missing or check["exit_status"] != 0:
            raise ValueError(f"verifier check {check['label']} is incomplete or failed")
        if (
            output["activation_performed"] is not False
            or output["publication_performed"] is not False
            or output["policy_or_transport_executed"] is not False
            or output["real_provider_requests"] != 0
            or output["inspection_unchanged"] is not True
        ):
            raise ValueError(f"verifier check {check['label']} reports a boundary violation")
        if output["source_revision"] != record["qualified_source_revision"]:
            raise ValueError("verifier source revision differs from the record")
        outputs.append(json.dumps(output, sort_keys=True))
    if len(set(outputs)) != 1 or record["digests_identical_across_checks"] is not True:
        raise ValueError("verifier outputs are not identical across checks")
    if (
        record["milestone_verdict"] is not None
        or record["activation_performed"] is not False
        or record["publication_of_qualification_content"] is not False
    ):
        raise ValueError("retention record must not carry a verdict, activation, or publication")


def _row(*cells: object) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def render(record: dict[str, Any]) -> str:
    check_record(record)
    archive, local, remote = record["archive"], record["local_restore"], record["remote_copy"]
    restored = remote["restored_archive"]
    budget = remote["budget_observation"]
    revision = record["qualified_source_revision"]
    lines = [
        "# S7 successor qualification: local retention record",
        "",
        "Rendered by `scripts/render_s7_retention_record.py` from `recovery.json`, which the",
        "operator assembled from private receipts and verifier outputs. No model was called, and",
        "the verifier replayed stored caller actions without making any additional fake send. No",
        "activation or publication was performed, and no milestone verdict is declared.",
        "",
        f"Run `{record['run_id']}`, qualified source revision `{revision}`.",
        "",
        _row("Copy", "Files", "Bytes", "Manifest digest"),
        _row("---", "---:", "---:", "---"),
        _row(
            "local archive",
            archive["file_count"],
            archive["bytes"],
            f"`{archive['manifest_digest']}`",
        ),
        _row(
            "local restore",
            local["restored_file_count"],
            local["bytes"],
            f"`{local['manifest_digest']}`",
        ),
        _row(
            "remote restore (fresh Git/LFS cache)",
            restored["restored_file_count"],
            restored["bytes"],
            f"`{restored['manifest_digest']}`",
        ),
        "",
        f"Private LFS repository `{remote['repository']}`, pinned commit `{remote['commit']}`,",
        f"packed object {remote['lfs_object']['size']} bytes. Budget observed at",
        f"{budget['observed_at_utc']}: ${budget['budget_usd']}, stop usage {budget['stop_usage']}.",
        "",
        _row(
            "Verifier check",
            "Exit",
            "Fake sends",
            "Cumulative",
            "Report digest",
            "Verification digest",
            "Inspection unchanged",
        ),
        _row("---", "---:", "---", "---:", "---", "---", "---"),
    ]
    for check in record["checks"]:
        out = check["output"]
        lines.append(
            _row(
                check["label"],
                check["exit_status"],
                json.dumps(out["fake_sends"]),
                out["cumulative_qualification_sends"],
                f"`{out['report_sha256'][:19]}…`",
                f"`{out['verification_sha256'][:19]}…`",
                out["inspection_unchanged"],
            )
        )
    lines += [
        "",
        "The fake-send column is the qualification's stored count, not sends made during",
        "verification.",
        f"Digests identical across checks: {record['digests_identical_across_checks']}.",
        "",
        "Each check ran the retained serving verifier in an isolated interpreter (`env -i`,",
        "`python -I -B`) against the bundle's own hash-bound source snapshot. It verifies the",
        "seal, source binding, and stored results, replays stored caller actions, regenerates the",
        "report from stored results only, and confirms the inspection left the bundle unchanged.",
        "",
        "Limitations:",
    ]
    lines += [f"- {item}" for item in record["limitations"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True, help="recovery.json to render")
    parser.add_argument(
        "--check", type=Path, help="existing REPORT.md that must equal the rendering"
    )
    args = parser.parse_args()
    rendered = render(json.loads(args.record.read_bytes()))
    if args.check is None:
        sys.stdout.write(rendered)
        return
    if args.check.read_text() != rendered:
        raise SystemExit("REPORT.md differs from the rendering of recovery.json")
    print(json.dumps({"record": str(args.record), "report_matches": True}))


if __name__ == "__main__":
    main()
