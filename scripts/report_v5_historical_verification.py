"""Render the historical-verification supplement from stored observations only."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.verify_v5_at_revision import CASES, EVIDENCE_REVISION, ROOT, SCHEMA

SUPPLEMENT = ROOT / "artifacts/grounding-v5-d510-historical-verification-20261010"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render(directory: Path = SUPPLEMENT, *, root: Path = ROOT, check: bool = False) -> None:
    original = root / "artifacts/grounding-v5-d510" / EVIDENCE_REVISION
    original_manifest = json.loads((original / "evidence-manifest.json").read_text())
    for row in original_manifest["package_files"]:
        if _sha(original / row["path"]) != row["sha256"]:
            raise ValueError(f"original evidence changed: {row['path']}")
    investigation = json.loads((directory / "investigation.json").read_text())
    run = json.loads((directory / "entry-point-run.json").read_text())
    if run["schema_version"] != SCHEMA or run["verdict"] is not None:
        raise ValueError("unexpected entry-point record or human verdict")
    observations = run["observations"]
    if [row["original_record"] for row in observations] != list(CASES):
        raise ValueError("entry-point observations must retain all eight records in order")
    if investigation["verdict"] is not None or set(investigation["selected_verifications"]) != set(
        CASES
    ):
        raise ValueError("unexpected investigation records or human verdict")
    lines = [
        "# D5.10 historical-verification observations",
        "",
        "Verdict: `null`. The project owner retains the D5.10 decision.",
        "",
        "Generated from stored JSON only; this renderer does not execute verifiers.",
        "",
        "The [original package](../grounding-v5-d510/"
        + EVIDENCE_REVISION
        + "/REPORT.md) is unchanged. "
        "Its exit-one observations remain historical observations. The manifest beside this report "
        "binds that package's manifest and every file in this supplement.",
        "",
        "## Initial investigation",
        "",
        (
            "[investigation.json](investigation.json) preserves the earlier investigation "
            "byte for byte, "
            "including exact differences, historical source hashes, commands, outputs, "
            "reproduction "
            "helpers, and setup failures. Its source comparisons refer to the original evidence "
            "revision, not a new benchmark execution."
        ),
        "",
        "| Original record | Historical revision | Exit | Command seconds |",
        "| --- | --- | ---: | ---: |",
    ]
    for number in CASES:
        row = investigation["selected_verifications"][number]
        lines.append(
            f"| {number} | `{row['source_revision']}` | {row['exit_status']} | "
            f"{row['duration_seconds']} |"
        )
    lines += [
        "",
        "## Entry-point run",
        "",
        (
            "[entry-point-run.json](entry-point-run.json) records a separate run through "
            "`python -m scripts.verify_v5_at_revision all`. It includes the launcher's file hash, "
            "dependency versions, full child-command outputs, timeouts, and artifact-change lists."
        ),
        "",
        "The entry point reads tracked artifacts from `" + EVIDENCE_REVISION + "` and source from "
        "each selected historical revision. It does not verify later working-tree edits. Record 46 "
        "runs full admission; it does not use `--skip-admission`.",
        "",
    ]
    for row in observations:
        number = row["original_record"]
        original_record = next(original.glob(f"commands/{number}-*.json"))
        old = json.loads(original_record.read_text())
        lines += [
            f"### Record {number}",
            "",
            (
                f"Original observation: [{original_record.name}](../grounding-v5-d510/"
                f"{EVIDENCE_REVISION}/commands/{original_record.name}), "
                f"exit `{old['exit_status']}`."
            ),
            "",
            f"Source revision: `{row['source_revision']}`.",
            "",
            f"Command: `{row.get('command', 'not started')}`",
            "",
            (
                f"Exit: `{row['exit_status']}`. "
                f"Command seconds: `{row.get('command_duration_seconds')}`. "
                f"Artifact files checked: `{row.get('artifact_file_count')}`."
            ),
            "",
            (
                f"Verification errors: `{json.dumps(row['verification_errors'])}`. "
                f"Artifact changes: `{json.dumps(row.get('artifact_changes'))}`."
            ),
            "",
            "Full output:",
            "",
            "```text",
            row["output"].rstrip("\n"),
            "```",
            "",
        ]
    lines += [
        "## Limits",
        "",
        (
            "These are historical artifact-verification observations. They do not recover "
            "the deleted D5.9 private journals, establish an OS-enforced policy boundary, "
            "approve public wording, "
            "or declare a milestone verdict. The initial investigation and the entry-point run are "
            "separate observations; neither replaces the original failures."
        ),
        "",
    ]
    report = ("\n".join(lines)).encode()
    manifest = {
        "schema_version": "pixelgym-d510-historical-supplement-v1",
        "verdict": None,
        "original_evidence_revision": EVIDENCE_REVISION,
        "original_manifest_sha256": _sha(original / "evidence-manifest.json"),
        "renderer_sha256": _sha(Path(__file__)),
        "files": {
            "investigation.json": _sha(directory / "investigation.json"),
            "entry-point-run.json": _sha(directory / "entry-point-run.json"),
            "REPORT.md": hashlib.sha256(report).hexdigest(),
        },
    }
    outputs = {
        "REPORT.md": report,
        "manifest.json": (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
    }
    for name, payload in outputs.items():
        path = directory / name
        if check:
            if path.read_bytes() != payload:
                raise ValueError(f"stored supplement differs: {name}")
        elif path.exists() and path.read_bytes() != payload:
            raise ValueError(f"refusing to replace existing supplement file: {name}")
    if not check:
        for name, payload in outputs.items():
            if not (directory / name).exists():
                with (directory / name).open("xb") as stream:
                    stream.write(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    render(check=args.check)


if __name__ == "__main__":
    main()
