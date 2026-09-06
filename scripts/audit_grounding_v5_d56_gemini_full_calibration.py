#!/usr/bin/env python3
"""Audit the frozen Gemini 3.7 Flash calibration without making provider calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.d56_calibration import _historical_calibration_manifest
from pixelgym.grounding.v5.evidence import (
    JOURNAL_DIGEST_VERSION_V1,
    JOURNAL_DIGEST_VERSION_V2,
    JOURNAL_INTEGRITY_SCHEMA_VERSION,
    journal_integrity_audit_record,
    validate_credential_free,
)
from pixelgym.serialization import canonical_json_bytes

AUDIT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-gemini-integrity-audit-v1"
PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-gemini-full-calibration-plan-v1"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-gemini-full-calibration-result-v1"

_REQUIRED_TABLES = {"events", "object_roles", "objects"}
_DIGEST_VERSION_METADATA_KEY = "event_chain_digest_version"
_TERMINAL_ATTEMPT_KINDS = {
    "attempt_completed",
    "confirmed_cancellation",
    "confirmed_no_response_timeout",
    "unknown_outcome_infrastructure_failure",
}
_STRUCTURED_OBJECT_ROLES = {
    "canonical_provider_response",
    "environment_checkpoint",
    "environment_resume_record",
    "parsed_action_candidate",
    "policy_checkpoint",
    "sealed_action",
}


class IntegrityAuditError(RuntimeError):
    """A frozen-evidence invariant did not verify."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityAuditError(message)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise IntegrityAuditError(f"expected a JSON object: {path}")
    return value


def _file_record(repository_root: Path, path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {
        "path": _display_path(repository_root, path),
        "sha256": f"sha256:{digest.hexdigest()}",
        "size_bytes": size,
    }


def _reference_role(kind: str, field: str) -> str | None:
    roles = {
        ("attempt_started", "pre_call_checkpoint_digest"): "policy_checkpoint",
        (
            "canonical_response_persisted",
            "canonical_response_digest",
        ): "canonical_provider_response",
        ("initial_screenshot", "screenshot_digest"): "screenshot",
        ("initial_screenshot", "environment_checkpoint_digest"): "environment_checkpoint",
        ("initial_screenshot", "environment_resume_digest"): "environment_resume_record",
        ("parsed_action_candidate", "candidate_digest"): "parsed_action_candidate",
        ("parsed_action_candidate", "post_parse_checkpoint_digest"): "policy_checkpoint",
        ("sealed_action_intent", "candidate_digest"): "parsed_action_candidate",
        ("sealed_action_intent", "action_digest"): "sealed_action",
        ("sealed_action_intent", "environment_checkpoint_digest"): "environment_checkpoint",
        ("sealed_action_intent", "environment_resume_digest"): "environment_resume_record",
        ("dispatch_committed", "screenshot_digest"): "screenshot",
        ("dispatch_committed", "post_dispatch_checkpoint_digest"): "policy_checkpoint",
        ("dispatch_committed", "environment_checkpoint_digest"): "environment_checkpoint",
        ("dispatch_committed", "environment_resume_digest"): "environment_resume_record",
    }
    if kind in _TERMINAL_ATTEMPT_KINDS and field == "post_attempt_checkpoint_digest":
        return "policy_checkpoint"
    if kind == "attempt_completed" and field == "response_digest":
        return "canonical_provider_response"
    return roles.get((kind, field))


def _audit_journal(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        _require(integrity_rows == ["ok"], "SQLite integrity_check did not return ok")
        _require(
            list(connection.execute("PRAGMA foreign_key_check")) == [],
            "SQLite foreign-key check found a violation",
        )
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        _require(_REQUIRED_TABLES <= tables, "journal schema is missing a required table")
        digest_version = JOURNAL_DIGEST_VERSION_V1
        if "journal_metadata" in tables:
            digest_version_rows = list(
                connection.execute(
                    "SELECT value FROM journal_metadata WHERE key = ?",
                    (_DIGEST_VERSION_METADATA_KEY,),
                )
            )
            _require(
                digest_version_rows == [(JOURNAL_DIGEST_VERSION_V2,)],
                "journal digest version metadata is invalid",
            )
            digest_version = JOURNAL_DIGEST_VERSION_V2

        roles_by_digest: dict[str, set[str]] = defaultdict(set)
        for digest, role in connection.execute("SELECT digest, kind FROM object_roles"):
            roles_by_digest[str(digest)].add(str(role))
        role_pairs = {(digest, role) for digest, roles in roles_by_digest.items() for role in roles}

        object_count = 0
        object_bytes = 0
        structured_object_count = 0
        object_digests: set[str] = set()
        for digest_value, primary_kind, data_value in connection.execute(
            "SELECT digest, kind, data FROM objects ORDER BY digest"
        ):
            digest = str(digest_value)
            data = bytes(data_value)
            roles = roles_by_digest.get(digest, set())
            _require(roles, "journal object has no declared role")
            _require(str(primary_kind) in roles, "journal primary object kind has no role row")
            _require(
                f"sha256:{sha256_bytes(data)}" == digest,
                "journal object digest mismatch",
            )
            if roles & _STRUCTURED_OBJECT_ROLES:
                try:
                    decoded = json.loads(data)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise IntegrityAuditError("structured journal object is not JSON") from exc
                _require(
                    canonical_json_bytes(decoded) == data,
                    "structured journal object is not canonically serialized",
                )
                validate_credential_free(decoded)
                if "canonical_provider_response" in roles:
                    _require(
                        isinstance(decoded, dict)
                        and set(decoded)
                        == {"response_id", "model", "content", "finish_reason", "usage"},
                        "canonical provider response fields differ from the frozen schema",
                    )
                structured_object_count += 1
            object_count += 1
            object_bytes += len(data)
            object_digests.add(digest)
        _require(
            set(roles_by_digest) == object_digests,
            "object role table contains a dangling or missing object relation",
        )

        event_chain: list[dict[str, Any]] = []
        event_counts: Counter[str] = Counter()
        events_by_kind: dict[str, dict[tuple[str, int, int | None], dict[str, Any]]] = defaultdict(
            dict
        )
        idempotency_keys: set[str] = set()
        sequences: list[int] = []
        for row in connection.execute(
            "SELECT sequence, event_key, kind, trial_id, step_index, attempt_index, payload "
            "FROM events ORDER BY sequence"
        ):
            sequence = int(row[0])
            event_key = str(row[1])
            kind = str(row[2])
            trial_id = str(row[3])
            step_index = int(row[4])
            attempt_index = None if row[5] is None else int(row[5])
            encoded = bytes(row[6])
            try:
                payload = json.loads(encoded)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise IntegrityAuditError("journal event payload is not JSON") from exc
            _require(isinstance(payload, dict), "journal event payload is not an object")
            _require(
                canonical_json_bytes(payload) == encoded,
                "journal event payload is not canonically serialized",
            )
            validate_credential_free(payload)
            identity = (trial_id, step_index, attempt_index)
            _require(identity not in events_by_kind[kind], "duplicate journal event identity")
            events_by_kind[kind][identity] = payload
            if kind == "attempt_started":
                idempotency_keys.add(str(payload["idempotency_key"]))
            for field, value in payload.items():
                role = _reference_role(kind, field)
                if role is not None and value is not None:
                    _require(
                        (str(value), role) in role_pairs,
                        f"{kind}.{field} does not resolve to its required object role",
                    )
            if kind == "sealed_action_intent":
                intent = {
                    field: payload[field]
                    for field in (
                        "candidate_digest",
                        "action_digest",
                        "environment_resume_digest",
                        "environment_checkpoint_digest",
                    )
                }
                _require(
                    content_digest(intent) == payload.get("sealed_intent_digest"),
                    "sealed action intent digest mismatch",
                )
            event_record = {
                "sequence": sequence,
                "event_key": event_key,
                "kind": kind,
                "payload": payload,
            }
            if digest_version == JOURNAL_DIGEST_VERSION_V2:
                event_record = {
                    "sequence": sequence,
                    "event_key": event_key,
                    "kind": kind,
                    "trial_id": trial_id,
                    "step_index": step_index,
                    "attempt_index": attempt_index,
                    "payload": payload,
                }
            event_chain.append(event_record)
            event_counts[kind] += 1
            sequences.append(sequence)

        _require(
            sequences == list(range(1, len(sequences) + 1)),
            "journal event sequence is not contiguous",
        )
        computed = {
            "schema_version": JOURNAL_INTEGRITY_SCHEMA_VERSION,
            "object_count": object_count,
            "event_count": len(event_chain),
            "event_chain_digest": content_digest(event_chain),
        }
        if digest_version == JOURNAL_DIGEST_VERSION_V2:
            computed["digest_version"] = digest_version
        _require(computed == expected, "recomputed journal integrity differs from summary")

        started = set(events_by_kind["attempt_started"])
        terminal = set().union(*(set(events_by_kind[kind]) for kind in _TERMINAL_ATTEMPT_KINDS))
        _require(started == terminal, "attempt-started and terminal identities differ")
        _require(
            all(identity[2] == 0 for identity in started),
            "frozen single-attempt policy contains a retry attempt",
        )
        completed = set(events_by_kind["attempt_completed"])
        canonical = set(events_by_kind["canonical_response_persisted"])
        unknown = set(events_by_kind["unknown_outcome_infrastructure_failure"])
        _require(completed == canonical, "completed attempts and canonical responses differ")
        _require(not canonical & unknown, "unknown attempt has a canonical response")
        for identity, terminal_payload in events_by_kind["attempt_completed"].items():
            canonical_payload = events_by_kind["canonical_response_persisted"][identity]
            _require(
                terminal_payload.get("response_digest")
                == canonical_payload.get("canonical_response_digest"),
                "completed attempt response binding differs",
            )

        candidate_steps = set(events_by_kind["parsed_action_candidate"])
        intent_steps = set(events_by_kind["sealed_action_intent"])
        dispatch_started = set(events_by_kind["dispatch_started"])
        dispatch_committed = set(events_by_kind["dispatch_committed"])
        _require(
            candidate_steps == intent_steps == dispatch_started == dispatch_committed,
            "successful action lineage is incomplete",
        )
        for identity, intent in events_by_kind["sealed_action_intent"].items():
            started_payload = events_by_kind["dispatch_started"][identity]
            committed_payload = events_by_kind["dispatch_committed"][identity]
            sealed_digest = intent["sealed_intent_digest"]
            _require(
                started_payload.get("sealed_intent_digest") == sealed_digest
                and committed_payload.get("sealed_intent_digest") == sealed_digest,
                "dispatch lineage does not bind the sealed intent",
            )
            _require(
                started_payload.get("backend_acceptance") == "not_yet_invoked"
                and committed_payload.get("backend_acceptance") == "accepted_once",
                "dispatch acceptance classification differs from the frozen contract",
            )

        model_attempts = sum(
            int(payload["model_attempt_reservation"])
            for payload in events_by_kind["attempt_started"].values()
        )
        control_requests = sum(
            int(payload["control_request_reservation"])
            for payload in events_by_kind["provider_control_request_reserved"].values()
        )
        return {
            "sqlite_integrity_check": "ok",
            "sqlite_foreign_key_violations": 0,
            "integrity": journal_integrity_audit_record(computed),
            "object_bytes_verified": object_bytes,
            "structured_objects_verified": structured_object_count,
            "event_counts": dict(sorted(event_counts.items())),
            "model_attempt_reservations": model_attempts,
            "provider_control_request_reservations": control_requests,
            "idempotency_keys": idempotency_keys,
            "initial_trial_ids": {identity[0] for identity in events_by_kind["initial_screenshot"]},
            "unknown_attempts": unknown,
            "final_policy_checkpoint_roles": roles_by_digest,
        }
    finally:
        connection.close()


def audit(
    repository_root: Path,
    *,
    plan_path: Path,
    run_directory: Path,
    approved_plan_sha256: str,
) -> dict[str, Any]:
    plan = _load_json(plan_path)
    summary_path = run_directory / "summary.json"
    journal_path = run_directory / "attempts.sqlite"
    summary = _load_json(summary_path)
    checks: list[dict[str, Any]] = []

    def verified(name: str, details: dict[str, Any]) -> None:
        checks.append({"name": name, "verified": True, **details})

    _require(plan.get("schema_version") == PLAN_SCHEMA_VERSION, "plan schema mismatch")
    _require(summary.get("schema_version") == RESULT_SCHEMA_VERSION, "result schema mismatch")
    computed_plan_digest = content_digest(plan)
    _require(computed_plan_digest == approved_plan_sha256, "approved plan digest mismatch")
    _require(
        summary.get("approved_plan_sha256") == approved_plan_sha256,
        "summary is not bound to the approved plan",
    )
    _require(summary.get("code_revision") == plan.get("code_revision"), "code revision mismatch")
    _require(summary.get("purpose") == plan.get("purpose"), "run purpose mismatch")
    verified(
        "approved_plan_and_run_identity",
        {
            "approved_plan_sha256": approved_plan_sha256,
            "code_revision": summary["code_revision"],
        },
    )

    task_order = plan.get("task_order")
    episode_results = summary.get("episode_results")
    _require(isinstance(task_order, list), "plan task order is not a list")
    _require(isinstance(episode_results, list), "episode results are not a list")
    assigned = int(plan["assigned_policy_task_pairs"])
    _require(len(task_order) == assigned, "assigned count and task order differ")
    _require(len({record["task_id"] for record in task_order}) == assigned, "task IDs repeat")
    _require(
        [result["task_id"] for result in episode_results]
        == [record["task_id"] for record in task_order[: len(episode_results)]],
        "episode results are not the frozen task-order prefix",
    )
    for result, task in zip(episode_results, task_order[: len(episode_results)], strict=True):
        _require(
            result["trial_id"] == f"d56-gemini-v2-{int(task['ordinal']):02d}-{task['task_id']}",
            "trial ID is not bound to its frozen task assignment",
        )
        _require(
            0 <= int(result["environment_actions"]) <= int(task["max_episode_steps"]),
            "episode action count exceeds its frozen horizon",
        )
    classifications = Counter(result["classification"] for result in episode_results)
    _require(summary["assigned_policy_task_pairs"] == assigned, "summary assigned count differs")
    _require(
        summary["attempted_policy_task_pairs"] == len(episode_results),
        "summary attempted count differs",
    )
    _require(
        summary["successful_policy_task_pairs"]
        == sum(bool(result["success"]) for result in episode_results),
        "summary success count differs",
    )
    _require(
        summary["classifications"] == dict(sorted(classifications.items())),
        "summary classification counts differ",
    )
    _require(
        summary["completed_all_assigned_pairs"] == (len(episode_results) == assigned),
        "summary completion flag differs",
    )
    verified(
        "assignment_and_episode_reconciliation",
        {
            "assigned_policy_task_pairs": assigned,
            "attempted_policy_task_pairs": len(episode_results),
            "successful_policy_task_pairs": summary["successful_policy_task_pairs"],
            "classifications": dict(sorted(classifications.items())),
        },
    )

    manifest = _historical_calibration_manifest(repository_root)
    manifest_path = repository_root / plan["calibration_partition"]["path"]
    manifest_file = _file_record(repository_root, manifest_path)
    _require(
        manifest_file["sha256"] == plan["calibration_partition"]["file_sha256"],
        "calibration manifest file digest mismatch",
    )
    _require(
        manifest["manifest_digest"] == plan["calibration_partition"]["manifest_digest"],
        "calibration manifest identity mismatch",
    )
    _require(
        [record["task_id"] for record in manifest["records"]]
        == [record["task_id"] for record in task_order],
        "calibration manifest order differs from approved plan",
    )
    verified(
        "calibration_partition_binding",
        {
            "manifest_digest": manifest["manifest_digest"],
            "episode_count": len(manifest["records"]),
        },
    )

    journal = _audit_journal(journal_path, summary["journal_integrity"])
    trial_ids = {result["trial_id"] for result in episode_results}
    _require(journal["initial_trial_ids"] == trial_ids, "journal trial set differs from summary")
    _require(
        journal["model_attempt_reservations"] == summary["model_attempt_reservations"],
        "journal model-attempt count differs from summary",
    )
    _require(
        journal["provider_control_request_reservations"] == summary["provider_control_requests"],
        "journal provider-control count differs from summary",
    )
    _require(
        sum(int(result["model_attempts"]) for result in episode_results)
        == summary["model_attempt_reservations"],
        "episode model-attempt total differs from summary",
    )
    _require(
        sum(int(result["environment_actions"]) for result in episode_results)
        == journal["event_counts"].get("dispatch_committed", 0),
        "episode action total differs from committed dispatch count",
    )
    for result in episode_results:
        final_digest = result["final_policy_checkpoint_digest"]
        _require(
            "policy_checkpoint"
            in journal["final_policy_checkpoint_roles"].get(final_digest, set()),
            "episode final policy checkpoint is missing",
        )
    verified(
        "journal_bytes_event_chain_and_lineage",
        {
            "journal_integrity": journal["integrity"],
            "object_bytes_verified": journal["object_bytes_verified"],
            "structured_objects_verified": journal["structured_objects_verified"],
            "event_counts": journal["event_counts"],
        },
    )

    transport_records = summary.get("transport_records")
    _require(isinstance(transport_records, list), "transport records are not a list")
    transport_keys = {str(record["idempotency_key"]) for record in transport_records}
    _require(transport_keys == journal["idempotency_keys"], "transport and journal calls differ")
    _require(
        len(transport_records)
        == summary["provider_calls_made"]
        == summary["provider_wire_requests"],
        "provider wire counts differ",
    )
    response_records = [record for record in transport_records if record["status"] == "response"]
    unknown_records = [record for record in transport_records if record["status"] == "unknown"]
    _require(
        all(
            record.get("response_model") == "google/gemini-3.7-flash"
            and record.get("upstream_provider") == "Google"
            for record in response_records
        ),
        "completed response identity differs from the approved route",
    )
    _require(
        len(unknown_records) == len(journal["unknown_attempts"]) == 1,
        "unknown-outcome counts differ",
    )
    unknown_identity = next(iter(journal["unknown_attempts"]))
    _require(
        episode_results[-1]["trial_id"] == unknown_identity[0]
        and episode_results[-1]["classification"] == "infrastructure_failure",
        "terminal infrastructure failure does not bind the unknown attempt",
    )
    response_cost = sum(
        (Decimal(str(record["cost_usd"])) for record in response_records), Decimal(0)
    )
    incremental = Decimal(summary["calibration_incremental_spend_usd"])
    prior = Decimal(summary["prior_aggregate_spend_usd"])
    actual = Decimal(summary["actual_aggregate_spend_usd"])
    maximum = Decimal(summary["maximum_aggregate_spend_usd"])
    remaining = Decimal(summary["remaining_aggregate_spend_usd"])
    _require(response_cost == incremental, "transport cost sum differs from run spend")
    _require(prior + incremental == actual, "aggregate spend arithmetic differs")
    _require(actual + remaining == maximum, "remaining spend arithmetic differs")
    verified(
        "provider_identity_calls_unknown_outcome_and_spend",
        {
            "provider_wire_requests": len(transport_records),
            "completed_provider_responses": len(response_records),
            "unknown_provider_outcomes": len(unknown_records),
            "unknown_transport_failure_code": unknown_records[0].get("failure_code"),
            "calibration_incremental_spend_usd": str(incremental),
            "actual_aggregate_spend_usd": str(actual),
            "remaining_aggregate_spend_usd": str(remaining),
        },
    )

    smoke = plan["successful_smoke_evidence"]
    # Plans record repository-relative paths; plans frozen before that normalisation
    # recorded absolute paths and are still audited as-is.
    smoke_summary_path = _under_root(repository_root, Path(smoke["summary_path"]))
    smoke_journal_path = _under_root(repository_root, Path(smoke["journal_path"]))
    smoke_summary_file = _file_record(repository_root, smoke_summary_path)
    smoke_journal_file = _file_record(repository_root, smoke_journal_path)
    _require(smoke_summary_file["sha256"] == smoke["summary_sha256"], "smoke summary changed")
    _require(smoke_journal_file["sha256"] == smoke["journal_sha256"], "smoke journal changed")
    smoke_journal = _audit_journal(smoke_journal_path, smoke["journal_integrity"])
    _require(
        smoke_journal["model_attempt_reservations"] == 1,
        "smoke journal no longer contains exactly one model attempt",
    )
    verified(
        "successful_smoke_predecessor_binding",
        {
            "smoke_summary_sha256": smoke["summary_sha256"],
            "smoke_journal_sha256": smoke["journal_sha256"],
            "smoke_journal_integrity": smoke_journal["integrity"],
        },
    )

    predecessor = plan["predecessor_relation"]
    predecessor_plan_path = repository_root / "artifacts/grounding-v5-d56-calibration-plan-v2.json"
    predecessor_summary_path = (
        repository_root / "artifacts/grounding-v5-d56-calibration-run-v2/summary.json"
    )
    predecessor_plan = _load_json(predecessor_plan_path)
    predecessor_summary_file = _file_record(repository_root, predecessor_summary_path)
    _require(
        content_digest(predecessor_plan) == predecessor["frozen_plan_sha256"],
        "frozen predecessor plan changed",
    )
    _require(
        predecessor_summary_file["sha256"] == predecessor["frozen_summary_sha256"],
        "frozen predecessor summary changed",
    )
    verified(
        "frozen_predecessor_binding",
        {
            "predecessor_plan_sha256": predecessor["frozen_plan_sha256"],
            "predecessor_summary_sha256": predecessor["frozen_summary_sha256"],
        },
    )

    _require(
        summary.get("cleanup") == {"journal_closed": True, "policy_and_environments_closed": True},
        "cleanup record differs",
    )
    _require(
        summary.get("publication_status") == "restricted_raw_responses_in_local_journal",
        "raw-response publication restriction differs",
    )
    verified(
        "cleanup_and_publication_restriction",
        {
            "cleanup": summary["cleanup"],
            "publication_status": summary["publication_status"],
        },
    )

    plan_file = _file_record(repository_root, plan_path)
    summary_file = _file_record(repository_root, summary_path)
    journal_file = _file_record(repository_root, journal_path)
    script_file = _file_record(repository_root, Path(__file__))
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "purpose": "no-call integrity audit of the frozen Gemini 3.7 Flash calibration evidence",
        "provider_calls_made": 0,
        "publication_status": "publishable_no_provider_payloads",
        "audit_implementation": script_file,
        "artifacts": {
            "approved_plan": plan_file,
            "run_summary": summary_file,
            "attempt_journal": journal_file,
            "calibration_manifest": manifest_file,
            "smoke_summary": smoke_summary_file,
            "smoke_attempt_journal": smoke_journal_file,
            "predecessor_summary": predecessor_summary_file,
        },
        "checks": checks,
        "result": {
            "checks_verified": len(checks),
            "checks_failed": 0,
            "provider_calls_made": 0,
            "milestone_gate_verdict": "not_evaluated_human_owned",
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--approved-plan-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _under_root(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _display_path(root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = _under_root(root, args.output)
    if output.exists():
        raise FileExistsError(f"refusing to replace existing audit report: {output}")
    report = audit(
        root,
        plan_path=_under_root(root, args.plan),
        run_directory=_under_root(root, args.run_directory),
        approved_plan_sha256=args.approved_plan_sha256,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": _display_path(root, output),
                "report_sha256": _file_record(root, output)["sha256"],
                **report["result"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
