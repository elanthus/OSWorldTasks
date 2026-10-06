#!/usr/bin/env python3
"""Index stored D5.10 gate observations without rerunning or judging them.

``render`` reads the stored command records under ``<evidence-dir>/commands/``, the
hand-authored ``<evidence-dir>/checklist-map.json``, and the repository files that the map
references (only to confirm that they exist, bind their SHA-256, and confirm quoted text is
present verbatim). It writes ``REPORT.md``, ``evidence-manifest.json``, and
``redaction-scan.json`` into the evidence directory. ``scan`` prints a redaction scan of one
directory and exits non-zero when any pattern matches.

This module never starts a process, never reruns a check, and never assigns a verdict. Every
validation runs before any output is written, so a missing record, artifact, or quoted text
leaves the evidence directory unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

COMMAND_SCHEMA = "pixelgym-d412-command-record-v1"
MAP_SCHEMA = "pixelgym-d510-checklist-map-v1"
MANIFEST_SCHEMA = "pixelgym-d510-evidence-manifest-v1"
REDACTION_SCHEMA = "pixelgym-d510-redaction-scan-v1"
GATE = "D5.10"
GENERATED_FILES = ("REPORT.md", "evidence-manifest.json", "redaction-scan.json")
RECORD_NAME = re.compile(r"^commands/(?P<number>\d{2})-[a-z0-9]+(?:-[a-z0-9]+)*\.json$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
CHECKLIST_SOURCE = re.compile(r"^(?P<revision>[0-9a-f]{7,40}):(?P<path>[^:]+):(?P<line>\d+)$")

# The first seven categories repeat the D4.12 scan in
# scripts/generate_d412_evidence_report.py:_redaction_scan; the last three are D5.10 additions
# for temporary paths and provider-credential shapes.
REDACTION_PATTERNS: dict[str, re.Pattern[bytes]] = {
    "local_absolute_paths": re.compile(rb"/(?:Users|home)/|/private/(?:tmp|var)/|/var/folders/"),
    "host_identity_paths_or_emails": re.compile(
        rb"/(?:Users|home)/[^/\s]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    ),
    "local_demo_credentials": re.compile(
        rb"local_demo_postgres_only|local_demo_minio_only|local-demo-csrf-secret"
    ),
    "aws_access_keys": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "private_key_markers": re.compile(rb"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "privileged_json_fields": re.compile(
        rb'"(?:expected_answer|ground_truth|bbox|bounding_box)"\s*:'
    ),
    "unredacted_image_payloads": re.compile(rb'"image_base64"\s*:\s*"[A-Za-z0-9+/]{100}'),
    "system_temp_paths": re.compile(rb"(?<![A-Za-z0-9._~-])/tmp/"),
    "provider_api_keys": re.compile(
        rb"(?<![A-Za-z0-9])(?:sk-ant-[A-Za-z0-9_-]{8,}|sk-(?:proj|or-v1)-[A-Za-z0-9_-]{8,}"
        rb"|AIza[0-9A-Za-z_-]{30,})"
    ),
    "bearer_tokens": re.compile(rb"Bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
}

_SUMMARY_LINE = re.compile(
    r"^=*\s*(?P<items>\d+ [a-z]+(?: [a-z]+)?(?:, \d+ [a-z]+(?: [a-z]+)?)*)"
    r" in (?P<runtime>[0-9.]+)s(?: \([0-9:]+\))?\s*=*$"
)
_NO_TESTS_LINE = re.compile(r"^=*\s*no tests ran in (?P<runtime>[0-9.]+)s(?: \([0-9:]+\))?\s*=*$")
_SUMMARY_ITEM = re.compile(r"(?P<count>\d+) (?P<label>[a-z]+(?: [a-z]+)?)")
_LABELS = {
    "warning": "warnings",
    "error": "errors",
    "test collected": "collected",
    "tests collected": "collected",
}


class EvidenceError(ValueError):
    """The stored evidence is incomplete or inconsistent; nothing was written."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def parse_pytest_summary(output: str) -> dict[str, Any] | None:
    """Return the counts on the last pytest summary line, exactly as printed."""

    for line in reversed(output.splitlines()):
        stripped = line.strip()
        match = _SUMMARY_LINE.match(stripped)
        if match:
            counts: dict[str, Any] = {}
            for item in _SUMMARY_ITEM.finditer(match["items"]):
                label = _LABELS.get(item["label"], item["label"])
                counts[label] = int(item["count"])
            counts["runtime"] = float(match["runtime"])
            return counts
        match = _NO_TESTS_LINE.match(stripped)
        if match:
            return {"no_tests_ran": True, "runtime": float(match["runtime"])}
    return None


def scan_contents(contents: dict[str, bytes]) -> dict[str, Any]:
    """Count redaction-pattern matches in named byte strings."""

    findings = {name: 0 for name in REDACTION_PATTERNS}
    files_with_findings: dict[str, list[str]] = {}
    for name in sorted(contents):
        for category, pattern in REDACTION_PATTERNS.items():
            count = len(pattern.findall(contents[name]))
            if count:
                findings[category] += count
                files_with_findings.setdefault(name, []).append(category)
    return {
        "schema_version": REDACTION_SCHEMA,
        "files_scanned": len(contents),
        "finding_counts": findings,
        "files_with_findings": files_with_findings,
    }


def scan_directory(directory: Path, *, exclude: Sequence[str] = ()) -> dict[str, Any]:
    """Count redaction-pattern matches in every file under one directory."""

    return scan_contents(
        {
            path.relative_to(directory).as_posix(): path.read_bytes()
            for path in _files_under(directory, exclude=exclude)
        }
    )


def _files_under(directory: Path, *, exclude: Sequence[str] = ()) -> list[Path]:
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.relative_to(directory).as_posix() not in exclude
    )


def _load_records(evidence_dir: Path) -> dict[str, dict[str, Any]]:
    commands = evidence_dir / "commands"
    _require(commands.is_dir(), f"missing command record directory: {commands.name}")
    records: dict[str, dict[str, Any]] = {}
    numbers: set[str] = set()
    for path in sorted(commands.glob("*.json")):
        relative = path.relative_to(evidence_dir).as_posix()
        name = RECORD_NAME.match(relative)
        _require(name is not None, f"unexpected command record name: {relative}")
        assert name is not None
        _require(name["number"] not in numbers, f"duplicate record number: {relative}")
        numbers.add(name["number"])
        data = path.read_bytes()
        record = json.loads(data)
        _require(isinstance(record, dict), f"command record is not an object: {relative}")
        _require(
            record.get("schema_version") == COMMAND_SCHEMA,
            f"unexpected command record schema: {relative}",
        )
        for key, kind in (
            ("command", str),
            ("argv", list),
            ("cwd", str),
            ("environment", dict),
            ("started_at_utc", str),
            ("ended_at_utc", str),
            ("duration_seconds", (int, float)),
            ("exit_status", int),
            ("output", str),
        ):
            _require(isinstance(record.get(key), kind), f"{relative} lacks a valid {key}")
        record["_sha256"] = _sha256(data)
        record["_size"] = len(data)
        records[relative] = record
    _require(bool(records), "no command records are stored")
    return records


def _record_text(records: dict[str, dict[str, Any]], relative: str, purpose: str) -> str:
    _require(relative in records, f"{purpose} record is missing: {relative}")
    return str(records[relative]["output"])


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _check_checklist_text(
    item: dict[str, Any], source_lines: list[str], revision: str, source_path: str
) -> None:
    match = CHECKLIST_SOURCE.match(str(item.get("source", "")))
    _require(match is not None, f"{item.get('id')}: invalid source citation")
    assert match is not None
    _require(
        revision.startswith(match["revision"]) and match["path"] == source_path,
        f"{item['id']}: source citation does not name the stored checklist source",
    )
    first = int(match["line"])
    last = int(item.get("source_end_line", first))
    _require(1 <= first <= last <= len(source_lines), f"{item['id']}: source lines out of range")
    span = " ".join(source_lines[first - 1 : last])
    text = str(item["checklist_text"])
    if item["kind"] == "done_when":
        _require(
            source_lines[first - 1].startswith("- [ ] "),
            f"{item['id']}: cited line is not a Done-when checklist line",
        )
        _require(
            _normalized(span.removeprefix("- [ ] ")) == _normalized(text),
            f"{item['id']}: checklist text is not verbatim",
        )
    else:
        _require(item["kind"] == "delivery_row", f"{item['id']}: unknown item kind")
        _require(text in span, f"{item['id']}: delivery-row text is not verbatim")


def _repository_file(repository_root: Path, relative: str, purpose: str) -> Path:
    path = (repository_root / relative).resolve()
    _require(
        path.is_relative_to(repository_root.resolve()),
        f"{purpose} path escapes the repository: {relative}",
    )
    _require(path.is_file(), f"{purpose} path is missing: {relative}")
    return path


def _observation_cell(records: dict[str, dict[str, Any]], references: Sequence[str]) -> str:
    if not references:
        return "no stored observation"
    parts = []
    for relative in references:
        record = records[relative]
        summary = parse_pytest_summary(record["output"])
        text = f"`{Path(relative).stem}`: exit {record['exit_status']}"
        if summary is not None:
            text += "; pytest " + json.dumps(summary, sort_keys=True)
        parts.append(text)
    return "<br>".join(parts)


def _line_span(linked: dict[str, Any]) -> str:
    span = str(linked.get("lines", ""))
    if not span:
        return ""
    return f" lines {span}" if "-" in span else f" line {span}"


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _fence(text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _validate(repository_root: Path, evidence_dir: Path) -> dict[str, Any]:
    map_path = evidence_dir / "checklist-map.json"
    _require(map_path.is_file(), "checklist-map.json is missing")
    checklist_map = json.loads(map_path.read_text(encoding="utf-8"))
    _require(checklist_map.get("schema_version") == MAP_SCHEMA, "unexpected checklist-map schema")
    _require(checklist_map.get("gate") == GATE, "checklist map names a different gate")
    _require(checklist_map.get("verdict") is None, "the checklist map must not carry a verdict")
    records = _load_records(evidence_dir)

    identity = checklist_map["identity_records"]
    revision = _record_text(records, identity["revision"], "revision").strip()
    _require(REVISION.match(revision) is not None, "stored revision is not a full commit ID")
    _require(evidence_dir.name == revision, "evidence directory name differs from the revision")
    branch = _record_text(records, identity["branch"], "branch").strip()
    status_records = {
        relative: _record_text(records, relative, "worktree status")
        for relative in identity["worktree_status"]
    }

    source = checklist_map["checklist_source"]
    source_lines = _record_text(records, source["record"], "checklist source").splitlines()
    _require(
        REVISION.match(str(source["revision"])) is not None,
        "checklist source revision is not a full commit ID",
    )
    wording_record = checklist_map["public_wording_inventory_record"]
    _record_text(records, wording_record, "public wording inventory")

    artifacts: dict[str, dict[str, Any]] = {}
    items = checklist_map["items"]
    _require(isinstance(items, list) and bool(items), "checklist map has no items")
    seen_ids: set[str] = set()
    for item in items:
        _require(item["id"] not in seen_ids, f"duplicate checklist id: {item['id']}")
        seen_ids.add(item["id"])
        _check_checklist_text(item, source_lines, str(source["revision"]), str(source["path"]))
        for relative in item["records"]:
            _require(relative in records, f"{item['id']}: referenced record is missing: {relative}")
        for relative in item["artifacts"]:
            path = _repository_file(repository_root, relative, f"{item['id']} artifact")
            data = path.read_bytes()
            artifacts[relative] = {"path": relative, "sha256": _sha256(data), "size": len(data)}
        for linked in item.get("linked_records", []):
            path = _repository_file(repository_root, linked["path"], f"{item['id']} linked record")
            data = path.read_bytes()
            _require(
                str(linked["verbatim"]).encode("utf-8") in data,
                f"{item['id']}: quoted text is not verbatim in {linked['path']}",
            )
            artifacts[linked["path"]] = {
                "path": linked["path"],
                "sha256": _sha256(data),
                "size": len(data),
            }
    for entry in checklist_map.get("not_run", []):
        for linked in entry.get("linked_records", []):
            path = _repository_file(repository_root, linked["path"], "not-run linked record")
            data = path.read_bytes()
            _require(
                str(linked["verbatim"]).encode("utf-8") in data,
                f"not-run item quoted text is not verbatim in {linked['path']}",
            )
            artifacts[linked["path"]] = {
                "path": linked["path"],
                "sha256": _sha256(data),
                "size": len(data),
            }
        for relative in entry.get("records", []):
            _require(relative in records, f"not-run item references a missing record: {relative}")
    for entry in checklist_map.get("withdrawn_records", []):
        _require(
            entry["record"] not in records,
            f"withdrawn record is still stored in the package: {entry['record']}",
        )
        for relative in entry.get("replacement_records", []):
            _require(relative in records, f"replacement record is missing: {relative}")
    return {
        "map": checklist_map,
        "records": records,
        "revision": revision,
        "branch": branch,
        "status_records": status_records,
        "artifacts": artifacts,
        "wording_record": wording_record,
    }


def _render_report(state: dict[str, Any]) -> str:
    checklist_map = state["map"]
    records: dict[str, dict[str, Any]] = state["records"]
    started = min(record["started_at_utc"] for record in records.values())
    ended = max(record["ended_at_utc"] for record in records.values())
    lines = [
        "# D5.10 raw evidence index",
        "",
        f"Evidence revision: `{state['revision']}`",
        "",
        f"Evidence branch: `{state['branch']}`",
        "",
        f"Observation window: `{started}` to `{ended}`",
        "",
        "Verdict: `null`",
        "",
        "Verdict owner: project owner",
        "",
        (
            "This index copies and links stored observations only. It does not rerun "
            "commands, reinterpret gate semantics, declare a milestone verdict, approve "
            "public wording, or change human-owned checklist state."
        ),
        "",
        "## Path placeholders",
        "",
        "| Placeholder | Meaning |",
        "| --- | --- |",
    ]
    for placeholder, meaning in sorted(checklist_map["placeholders"].items()):
        lines.append(f"| `{placeholder}` | {_escape_cell(meaning)} |")
    lines += ["", "## Recording notes", ""]
    lines += [f"- {note}" for note in checklist_map["recording_notes"]]
    lines += [
        "",
        "## Checklist evidence",
        "",
        (
            "Checklist text is copied verbatim from "
            f"`{checklist_map['checklist_source']['revision']}:"
            f"{checklist_map['checklist_source']['path']}`. The observation column copies exit "
            "statuses and parsed pytest counts from the listed records."
        ),
        "",
        "| # | Checklist line | Source | Stored raw observation | Records | Artifacts |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for number, item in enumerate(checklist_map["items"], start=1):
        record_cell = ", ".join(f"`{path}`" for path in item["records"]) or "none"
        artifact_cell = ", ".join(f"`{path}`" for path in item["artifacts"]) or "none"
        lines.append(
            f"| {number} | {_escape_cell(item['checklist_text'])} | `{item['source']}` | "
            f"{_observation_cell(records, item['records'])} | {record_cell} | {artifact_cell} |"
        )
    lines += ["", "## Recorded exceptions, waivers, and limitations", ""]
    linked_any = False
    for number, item in enumerate(checklist_map["items"], start=1):
        for linked in item.get("linked_records", []):
            linked_any = True
            where = _line_span(linked)
            lines += [f"Checklist row {number}: `{linked['path']}`{where}", ""]
            lines += [f"> {line}" if line else ">" for line in linked["verbatim"].splitlines()]
            lines.append("")
    if not linked_any:
        lines += ["None linked.", ""]
    lines += ["## Records withdrawn before packaging", ""]
    for entry in checklist_map.get("withdrawn_records", []):
        lines.append(
            f"- `{entry['record']}` (sha256 `{entry['sha256']}`, exit "
            f"{entry['exit_status']}): {entry['reason']}"
        )
        if entry.get("summary_line"):
            lines.append(f"  - Last output line, copied verbatim: `{entry['summary_line']}`")
        if entry.get("replacement_records"):
            replacements = ", ".join(f"`{path}`" for path in entry["replacement_records"])
            lines.append(f"  - Replacement records: {replacements}")
    if not checklist_map.get("withdrawn_records"):
        lines.append("None.")
    lines += ["", "## Items not run", ""]
    for entry in checklist_map.get("not_run", []):
        lines.append(f"- `{entry['item']}`: {entry['reason']}")
        for relative in entry.get("records", []):
            lines.append(f"  - Record: `{relative}`")
        for linked in entry.get("linked_records", []):
            where = _line_span(linked)
            lines.append(f"  - `{linked['path']}`{where}:")
            lines += [f"    > {line}" for line in linked["verbatim"].splitlines()]
    if not checklist_map.get("not_run"):
        lines.append("None listed.")
    wording = records[state["wording_record"]]
    fence = _fence(wording["output"])
    lines += [
        "",
        "## Public wording inventory",
        "",
        (
            f"Copied verbatim from `{state['wording_record']}` (exit "
            f"{wording['exit_status']}). Each line is `file:line:text` as printed by:"
        ),
        "",
        f"`{wording['command']}`",
        "",
        fence,
        wording["output"].rstrip("\n"),
        fence,
        "",
        "## Raw command inventory",
        "",
        (
            "Each JSON record stores the exact argv/command, cwd, public environment, UTC "
            "timestamps, process runtime, exit status, and complete combined output. Failed "
            "and skipped observations remain in the inventory."
        ),
        "",
        "| Record | Exit | Runtime (s) | Parsed pytest summary |",
        "| --- | ---: | ---: | --- |",
    ]
    for relative, record in sorted(records.items()):
        summary = json.dumps(parse_pytest_summary(record["output"]), sort_keys=True)
        lines.append(
            f"| `{relative}` | {record['exit_status']} | {record['duration_seconds']} | "
            f"`{summary}` |"
        )
    lines += [
        "",
        "## Integrity and redaction indexes",
        "",
        (
            "`evidence-manifest.json` lists the SHA-256 and size of every other file in this "
            "directory and of each referenced repository artifact. `redaction-scan.json` "
            "lists the prohibited-pattern counts for every file in this directory except "
            "itself."
        ),
        "",
    ]
    report = "\n".join(lines)
    _require(
        "- [ ]" not in report and "- [x]" not in report.lower(),
        "generated report must not contain checklist boxes",
    )
    return report


def _manifest(
    state: dict[str, Any], package_contents: dict[str, bytes], redaction: dict[str, Any]
) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = state["records"]
    package_files = [
        {"path": name, "sha256": _sha256(data), "size": len(data)}
        for name, data in sorted(package_contents.items())
    ]
    command_records = []
    for relative, record in sorted(records.items()):
        entry = {
            "path": relative,
            "sha256": record["_sha256"],
            "size": record["_size"],
            "command": record["command"],
            "exit_status": record["exit_status"],
            "duration_seconds": record["duration_seconds"],
            "started_at_utc": record["started_at_utc"],
            "ended_at_utc": record["ended_at_utc"],
        }
        summary = parse_pytest_summary(record["output"])
        if summary is not None:
            entry["observed_test_summary"] = summary
        command_records.append(entry)
    items = []
    for number, item in enumerate(state["map"]["items"], start=1):
        items.append(
            {
                "number": number,
                "id": item["id"],
                "kind": item["kind"],
                "source": item["source"],
                "checklist_text": item["checklist_text"],
                "records": item["records"],
                "artifacts": item["artifacts"],
                "linked_records": [linked["path"] for linked in item.get("linked_records", [])],
                "stored_raw_observation": _observation_cell(records, item["records"]),
            }
        )
    return {
        "schema_version": MANIFEST_SCHEMA,
        "gate": GATE,
        "verdict": None,
        "verdict_owner": "project owner",
        "evidence_revision": state["revision"],
        "evidence_branch": state["branch"],
        "worktree_status_observations": state["status_records"],
        "checklist_source": state["map"]["checklist_source"],
        "command_records": command_records,
        "package_files": package_files,
        "referenced_artifacts": [state["artifacts"][key] for key in sorted(state["artifacts"])],
        "checklist_items": items,
        "not_run": state["map"].get("not_run", []),
        "withdrawn_records": state["map"].get("withdrawn_records", []),
        "redaction_scan": redaction,
    }


def generate(repository_root: Path, evidence_dir: Path) -> dict[str, Any]:
    """Validate the stored evidence, then write the report, scan, and manifest."""

    repository_root = repository_root.resolve()
    evidence_dir = evidence_dir.resolve()
    state = _validate(repository_root, evidence_dir)
    stored = {
        path.relative_to(evidence_dir).as_posix(): path.read_bytes()
        for path in _files_under(evidence_dir, exclude=GENERATED_FILES)
    }
    report = _render_report(state).encode("utf-8")
    # The scan covers every package file except redaction-scan.json itself. Its expected
    # result is fixed before the manifest exists: zero findings over the stored files, the
    # report, and the manifest. The manifest binds that exact scan, and the scan is then
    # repeated over the real bytes and must match before anything is written.
    expected = {
        "schema_version": REDACTION_SCHEMA,
        "files_scanned": len(stored) + 2,
        "finding_counts": {name: 0 for name in REDACTION_PATTERNS},
        "files_with_findings": {},
    }
    redaction_bytes = _json_text(expected).encode("utf-8")
    package = {**stored, "REPORT.md": report, "redaction-scan.json": redaction_bytes}
    manifest = _manifest(state, package, expected)
    manifest_bytes = _json_text(manifest).encode("utf-8")
    observed = scan_contents(
        {**stored, "REPORT.md": report, "evidence-manifest.json": manifest_bytes}
    )
    _require(
        observed == expected,
        f"redaction scan found prohibited data: {observed['files_with_findings']}",
    )
    (evidence_dir / "REPORT.md").write_bytes(report)
    (evidence_dir / "redaction-scan.json").write_bytes(redaction_bytes)
    (evidence_dir / "evidence-manifest.json").write_bytes(manifest_bytes)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="mode", required=True)
    render = commands.add_parser("render", help="write REPORT.md, the manifest, and the scan")
    render.add_argument("--repository-root", type=Path, default=Path.cwd())
    render.add_argument("--evidence-dir", type=Path, required=True)
    scan = commands.add_parser("scan", help="print a redaction scan of one directory")
    scan.add_argument("--path", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "scan":
        _require(args.path.is_dir(), f"scan path is not a directory: {args.path.name}")
        result = scan_directory(args.path)
        print(_json_text(result), end="")
        return 1 if any(result["finding_counts"].values()) else 0
    manifest = generate(args.repository_root, args.evidence_dir)
    print(
        json.dumps(
            {
                "evidence_revision": manifest["evidence_revision"],
                "checklist_items": len(manifest["checklist_items"]),
                "command_records": len(manifest["command_records"]),
                "verdict": manifest["verdict"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
