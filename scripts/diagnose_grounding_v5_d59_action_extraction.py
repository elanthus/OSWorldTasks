"""Read retained responses offline; compare parsers without changing original outcomes."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pixelgym.actions import build_action_space, validate_action
from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.action_extraction import ADAPTER_VERSION, FENCE, SingleFencedActionPolicy
from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.d59_extraction_successor import ORIGINAL_PLAN_DIGEST, OUTPUT_DIRECTORY
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.serialization import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]


def diagnose(
    journal_path: Path, plan: dict[str, Any], *, expected_integrity: dict[str, Any] | None = None
) -> dict[str, Any]:
    if (
        plan.get("execution_plan_digest") != ORIGINAL_PLAN_DIGEST
        or content_digest({k: v for k, v in plan.items() if k != "execution_plan_digest"})
        != ORIGINAL_PLAN_DIGEST
    ):
        raise ValueError("diagnostic requires the unchanged executed replication plan")
    before = journal_path.stat()
    old = claude.ClaudeCodePolicy(
        runtime_identity=claude.ClaudeRuntimeIdentity(**plan["runtime_identity"])
    )
    new = SingleFencedActionPolicy(
        runtime_identity=claude.ClaudeRuntimeIdentity(**plan["runtime_identity"])
    )
    space = build_action_space(claude.SCREEN_WIDTH, claude.SCREEN_HEIGHT)
    jobs = {job["trial_id"]: job for job in plan["primary_jobs"] + plan["reliability_jobs"]}
    totals: Counter[str] = Counter()
    groups: dict[str, Counter[str]] = defaultdict(Counter)
    failures = []
    query = (
        "?mode=ro"
        if journal_path.with_name(journal_path.name + "-wal").exists()
        else "?mode=ro&immutable=1"
    )
    connection = sqlite3.connect(journal_path.resolve().as_uri() + query, uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        version = connection.execute(
            "SELECT value FROM journal_metadata WHERE key='event_chain_digest_version'"
        ).fetchone()
        if version != ("v2",):
            raise ValueError("diagnostic requires the original v2 journal schema")
        chain = [
            {
                "sequence": sequence,
                "event_key": key,
                "kind": kind,
                "trial_id": trial,
                "step_index": step,
                "attempt_index": attempt,
                "payload": json.loads(payload),
            }
            for sequence, key, kind, trial, step, attempt, payload in connection.execute(
                "SELECT sequence,event_key,kind,trial_id,step_index,attempt_index,payload "
                "FROM events ORDER BY sequence"
            )
        ]
        lineage = {
            "digest_version": "v2",
            "event_chain_digest": content_digest(chain),
            "event_count": len(chain),
            "object_count": connection.execute("SELECT count(*) FROM objects").fetchone()[0],
        }
        del chain
        if expected_integrity is not None and any(
            expected_integrity.get(key) != value for key, value in lineage.items()
        ):
            raise ValueError("journal lineage differs from the original audited report")
        sealed = {
            (trial, step, attempt)
            for trial, step, attempt, payload in connection.execute(
                "SELECT trial_id,step_index,attempt_index,payload FROM events "
                "WHERE kind='sealed_unsuccessful_result'"
            )
            if json.loads(payload).get("failure_code") == "parse_failure"
        }
        for trial, step, attempt, payload in connection.execute(
            "SELECT trial_id,step_index,attempt_index,payload FROM events "
            "WHERE kind='attempt_completed' ORDER BY sequence"
        ):
            event = json.loads(payload)
            digest = event["response_digest"]
            row = connection.execute(
                "SELECT kind,data FROM objects WHERE digest=?", (digest,)
            ).fetchone()
            if (
                row is None
                or row[0] != "canonical_provider_response"
                or "sha256:" + sha256_bytes(row[1]) != digest
            ):
                raise ValueError("response object is missing, wrong-kind or corrupt")
            response = row[1]
            original_candidate = None
            try:
                original_candidate = old.parse(response, b"{}")
                validate_action(space, original_candidate)
                was_accepted = True
            except (ValueError, TypeError, KeyError):
                was_accepted = False
            try:
                candidate = new.parse(response, b"{}")
                now_accepted = True
            except (ValueError, TypeError, KeyError):
                candidate = None
                now_accepted = False
            if was_accepted and (not now_accepted or original_candidate != candidate):
                raise ValueError("adapter changed a previously valid action")
            category = (
                "previously_valid_unchanged"
                if was_accepted
                else "newly_recoverable_action"
                if now_accepted
                else "still_rejected"
            )
            totals[category] += 1
            job = jobs[trial]
            groups[f"{job['phase']}/{job['mode']}"][category] += 1
            if not was_accepted:
                if (trial, step, attempt) not in sealed:
                    raise ValueError(
                        "old rejection lacks the original terminal parser-failure record"
                    )
                value = json.loads(response)
                fences = list(FENCE.finditer(value["content"]))
                body_valid = False
                if len(fences) == 1:
                    try:
                        body_action = old.parse(
                            canonical_json_bytes({**value, "content": fences[0].group(1)}), b"{}"
                        )
                        validate_action(space, body_action)
                        body_valid = True
                    except (ValueError, TypeError, KeyError):
                        pass
                failures.append(
                    {
                        "trial_id": trial,
                        "step_index": step,
                        "attempt_index": attempt,
                        "phase": job["phase"],
                        "mode": job["mode"],
                        "category": category,
                        "fenced_body_valid_under_original_schema_and_bounds": body_valid,
                        "receipt": dict(new.last_receipt),
                    }
                )
        if len(failures) != len(sealed):
            raise ValueError("not every retained terminal parse failure was examined")
    finally:
        connection.close()
    after = journal_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("input journal changed during offline diagnostic")
    result = {
        "schema_version": "pixelgym-agent-v5-d59-offline-action-extraction-diagnostic-v1",
        "original_plan_digest": ORIGINAL_PLAN_DIGEST,
        "adapter_version": ADAPTER_VERSION,
        "journal_lineage": lineage,
        "lineage_matches_original_audit": expected_integrity is not None,
        "counts": dict(sorted(totals.items())),
        "canonical_responses_examined": sum(totals.values()),
        "terminal_parser_failures_examined": len(failures),
        "terminal_failures_with_valid_fenced_body": sum(
            row["fenced_body_valid_under_original_schema_and_bounds"] for row in failures
        ),
        "by_phase_and_mode": {
            key: dict(sorted(value.items())) for key, value in sorted(groups.items())
        },
        "terminal_failures": failures,
        "input_access": (
            "SQLite mode=ro and query_only; canonical object digest checked per response"
        ),
        "input_file_size_and_mtime_unchanged": True,
        "original_results_changed": False,
        "successor_task_success_inferred": False,
        "environment_actions_replayed": 0,
        "provider_calls": 0,
        "limitation": (
            "Post-hoc syntax and bounds diagnostic only. "
            "No task continuation or success is inferred. "
            "Step-limit failures remain, and the original unresolved timeout stays unresolved."
        ),
    }
    validate_credential_free(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument(
        "--output", type=Path, default=ROOT / OUTPUT_DIRECTORY / "offline-diagnostic.json"
    )
    args = parser.parse_args()
    plan = json.loads(
        (ROOT / "artifacts/grounding-v5-d59-replication/execution-plan.json").read_text()
    )
    original_report = json.loads(
        (ROOT / "artifacts/grounding-v5-d59-replication-results/report.json").read_text()
    )
    result = diagnose(args.journal, plan, expected_integrity=original_report["journal_integrity"])
    result["original_report_digest"] = content_digest(original_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(result) + b"\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "counts",
                    "canonical_responses_examined",
                    "terminal_parser_failures_examined",
                    "provider_calls",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
