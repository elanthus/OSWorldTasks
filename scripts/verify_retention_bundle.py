"""Verify published retention bindings and regenerate Markdown without private journals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.retention import SCHEMA, _files
from pixelgym.grounding.v5.retention_validation import CASES, render_report


def verify_bundle(bundle: Path) -> dict[str, Any]:
    files = _files(bundle)
    expected = {
        "observations.json",
        "report.json",
        "REPORT.md",
        "source-binding.json",
        "archive-manifest.json",
        "retention-receipt.json",
    }
    if set(files) != expected:
        raise ValueError("public bundle contains missing or unlisted files")
    values = {
        name: json.loads(path.read_bytes()) for name, path in files.items() if name != "REPORT.md"
    }
    report, receipt = values["report.json"], values["retention-receipt.json"]
    manifest, binding = values["archive-manifest.json"], values["source-binding.json"]
    if (
        manifest["schema_version"] != SCHEMA
        or content_digest(manifest) != receipt["manifest_digest"]
        or content_digest(report) != receipt["report_digest"]
        or content_digest(binding) != report["source_binding_digest"]
        or content_digest(values["observations.json"]) != report["observations_digest"]
    ):
        raise ValueError("public bundle digest binding mismatch")
    entries = {entry["path"]: entry for entry in manifest["files"]}
    if len(entries) != len(manifest["files"]):
        raise ValueError("duplicate archive manifest member")
    for name in ("report.json", "observations.json", "source-binding.json"):
        data = files[name].read_bytes()
        if entries[name]["sha256"] != "sha256:" + sha256_bytes(data) or entries[name][
            "size"
        ] != len(data):
            raise ValueError("public projection differs from its archived bytes")
    source = {
        name.removeprefix("source/"): entry["sha256"]
        for name, entry in entries.items()
        if name.startswith("source/")
    }
    if source != binding["files"]:
        raise ValueError("source binding differs from archive manifest")
    if (
        report["case_count"] != len(CASES)
        or [row["case"] for row in report["cases"]] != list(CASES)
        or [row["case"] for row in values["observations.json"]["cases"]] != list(CASES)
        or report["real_provider_requests"] != 0
        or report["milestone_verdict"] is not None
        or receipt["real_provider_requests"] != 0
        or receipt["deleted_source"] is not False
        or receipt["cleanup_precondition_satisfied"] is not True
        or receipt["source_fingerprint"] != manifest["source_fingerprint"]
        or len(receipt["restored_copies"]) != 2
        or len(receipt["restored_source_checks"]) != 2
    ):
        raise ValueError("public campaign scope or restore receipt mismatch")
    for restored in receipt["restored_copies"]:
        if (
            restored["manifest_digest"] != receipt["manifest_digest"]
            or restored["source_fingerprint"] != manifest["source_fingerprint"]
            or restored["restored_file_count"] != len(entries)
        ):
            raise ValueError("restored copy does not bind the published archive")
    for check in receipt["restored_source_checks"]:
        if (
            check["exit_status"] != 0
            or check["output"]["report_digest"] != receipt["report_digest"]
            or check["output"]["case_count"] != len(CASES)
            or check["output"]["real_provider_requests"] != 0
        ):
            raise ValueError("restored-source observation mismatch")
    if files["REPORT.md"].read_bytes() != render_report(report).encode():
        raise ValueError("public Markdown differs from stored report")
    return {
        "schema_version": "pixelgym-public-retention-verification-v1",
        "checked_files": len(files),
        "report_digest": content_digest(report),
        "scope": "published bindings and report only; private archive bytes are not verified",
        "provider_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_bundle(args.bundle), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
