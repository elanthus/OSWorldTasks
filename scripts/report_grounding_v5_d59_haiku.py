"""Publish an allowlisted D5.9 result projection without provider calls or raw responses."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.d59_haiku_execution import sha256_file
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.serialization import canonical_json_bytes

ROW_FIELDS = (
    "trial_id",
    "phase",
    "mode",
    "seed",
    "repeat",
    "classification",
    "success",
    "environment_actions",
    "model_attempts",
    "provider_control_requests",
    "provider_wire_requests",
)


def counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        phase: {
            mode: dict(
                Counter(
                    r["classification"] for r in rows if r["phase"] == phase and r["mode"] == mode
                )
            )
            for mode in ("history", "stateless")
        }
        for phase in ("primary", "reliability")
    }


def project(directory: Path) -> dict[str, Any]:
    summary = json.loads((directory / "summary.json").read_text())
    binding = json.loads((directory / "execution-binding.json").read_text())
    amendment = json.loads((directory / "continuation-approval.json").read_text())
    rows = [{key: row[key] for key in ROW_FIELDS} for row in summary["results"]]
    if (
        len(rows) != summary["completed"]
        or len({r["trial_id"] for r in rows}) != len(rows)
        or counts(rows) != summary["counts"]
        or summary["stop_reason"] != "completed_all_assignments"
        or summary["completed"] != summary["assigned"]
        or summary["unrun"] != 0
        or summary["error"] is not None
        or summary["subprocesses_closed"] is not True
    ):
        raise ValueError("summary is not a consistent completed campaign")
    if content_digest(binding) != summary["execution_binding_digest"]:
        raise ValueError("runtime binding does not match the completed summary")
    if content_digest(amendment) != summary["continuation_digest"]:
        raise ValueError("continuation authorization does not match the completed summary")
    report = {
        "schema_version": "pixelgym-d59-public-results-v1",
        "execution_plan_digest": summary["execution_plan_digest"],
        "source_revision": summary["source_revision"],
        "continuation_source_revision": summary["continuation_source_revision"],
        "continuation_source_sha256": amendment["continuation_source_sha256"],
        "continuation_rule": amendment["continuation_rule"],
        "original_started_at": amendment["original_started_at"],
        "deadline": amendment["deadline"],
        "runtime_identity": binding["runtime_identity"],
        "assigned": summary["assigned"],
        "completed": len(rows),
        "unrun": summary["unrun"],
        "stop_reason": summary["stop_reason"],
        "counts": counts(rows),
        "phase_usage": summary["phase_usage"],
        "provider_processes_started": summary["provider_processes_started"],
        "unresolved_invocations": summary["unresolved_invocations"],
        "subprocesses_closed": summary["subprocesses_closed"],
        "incremental_experiment_charge_usd": summary["incremental_experiment_charge_usd"],
        "human_gate": summary["human_gate"],
        "journal_integrity": summary["journal_integrity"],
        "invocation_integrity_digest": content_digest(summary["invocation_integrity"]),
        "source_artifacts": {
            name: sha256_file(directory / name)
            for name in (
                "summary.json",
                "execution-binding.json",
                "continuation-approval.json",
                "execution-plan.json",
                "owner-approval.json",
            )
        },
        "results": rows,
    }
    validate_credential_free(report)
    return report


def render(report: dict[str, Any]) -> str:
    if counts(report["results"]) != report["counts"]:
        raise ValueError("public counts differ from per-assignment evidence")
    lines = [
        "# D5.9 Haiku execution results",
        "",
        (
            f"Completed {report['completed']} of {report['assigned']} assignments. "
            "This records execution outcomes; D5.10 remains a human decision."
        ),
        "",
        "| Phase | Policy | Success | Action limit | Malformed output | Infrastructure |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for phase in ("primary", "reliability"):
        for mode in ("history", "stateless"):
            group = report["counts"][phase][mode]
            values = [
                group.get(key, 0)
                for key in (
                    "success_termination",
                    "step_limit_truncation",
                    "invalid_output",
                    "infrastructure_failure",
                )
            ]
            lines.append(f"| {phase} | {mode} | " + " | ".join(map(str, values)) + " |")
    lines += [
        "",
        "Reliability assignments are repeated cases and are shown separately from the primary comparison.",
        "",
        (
            "The owner authorized continuation after malformed output and, later, after exhausted "
            "connection resets. Each failed assignment remains in the results. Prior assignments "
            "were not replayed; retries kept the same per-action and aggregate caps. These are "
            "disclosed changes to the original campaign stop rule, made after observing failures."
        ),
        "",
        (
            f"The journals record {report['provider_processes_started']} provider processes. "
            f"All local subprocesses are closed; {report['unresolved_invocations']} timed-out calls "
            "retain unknown provider completion. Recorded incremental experiment charge: "
            f"${report['incremental_experiment_charge_usd']}."
        ),
        "",
        (
            "The bound runtime uses Claude Max subscription authentication. The charge field is "
            "runner accounting, not a provider billing audit."
        ),
        "",
        (
            "[report.json](report.json) contains the per-assignment projection, runtime identity, "
            "source revisions, and digests of the retained source evidence. Raw journals contain "
            "responses, screenshots, and checkpoints and stay local. Public readers can recompute "
            "the counts but cannot independently replay the private journals from this projection."
        ),
        "",
        (
            "Generated by `scripts/report_grounding_v5_d59_haiku.py` from stored evidence, "
            "without model calls or reassessing responses."
        ),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    report = project(args.input)
    files = {
        "report.json": canonical_json_bytes(report) + b"\n",
        "report.md": render(report).encode(),
    }
    if not args.verify:
        args.output.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = args.output / name
        if args.verify:
            if path.read_bytes() != data:
                raise ValueError(f"stored report differs: {name}")
        else:
            path.write_bytes(data)
    print(json.dumps({"assignments": report["completed"], "report_digest": content_digest(report)}))


if __name__ == "__main__":
    main()
