"""Transactional control-plane ledger with append-only lifecycle evidence."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pixelgym.platform.contracts import (
    ArtifactRef,
    CandidateState,
    GateReport,
    PolicyManifest,
    RunSummary,
)
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.policy import verify_policy_manifest
from pixelgym.platform.schema_validation import (
    ContractValidationError,
    PlatformSchemas,
    load_policy_manifest,
)


class ConflictError(RuntimeError):
    pass


class AuthorizationError(PermissionError):
    pass


class TransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    source_run_id: str
    policy: PolicyManifest
    gate_report: dict[str, Any]
    gate_report_sha256: str
    artifacts: tuple[ArtifactRef, ...]
    summary: RunSummary | None
    state: CandidateState
    version: int


@dataclass(frozen=True)
class DeploymentRecord:
    deployment_id: str
    candidate_id: str
    policy_id: str
    action: str
    actor: str
    reason: str
    created_at_utc: str
    generation: int


@dataclass(frozen=True)
class _DeploymentSchema:
    columns: frozenset[tuple[str, str, int, str | None, int, int, int]]
    foreign_keys: frozenset[tuple[object, ...]]
    indexes: frozenset[tuple[int, str, int, tuple[str, ...]]]
    checks: frozenset[str]
    triggers: frozenset[tuple[str, str]]


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS submissions (
  submission_id TEXT PRIMARY KEY,
  request_sha256 TEXT NOT NULL UNIQUE,
  request_json TEXT NOT NULL,
  status TEXT NOT NULL,
  metaflow_pathspec TEXT,
  mlflow_run_id TEXT,
  created_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
  candidate_id TEXT PRIMARY KEY,
  source_run_id TEXT NOT NULL,
  policy_id TEXT NOT NULL UNIQUE,
  policy_json TEXT NOT NULL,
  gate_report_json TEXT NOT NULL,
  gate_report_sha256 TEXT NOT NULL,
  artifacts_json TEXT NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}',
  state TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL UNIQUE REFERENCES candidates(candidate_id),
  actor TEXT NOT NULL,
  reason TEXT NOT NULL,
  gate_report_sha256 TEXT NOT NULL,
  policy_id TEXT NOT NULL,
  created_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deployments (
  deployment_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
  policy_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('deploy', 'rollback')),
  actor TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  generation INTEGER NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS active_pointer (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  deployment_id TEXT REFERENCES deployments(deployment_id),
  generation INTEGER NOT NULL
);
INSERT OR IGNORE INTO active_pointer(singleton, deployment_id, generation) VALUES(1, NULL, 0);
CREATE TABLE IF NOT EXISTS audit_events (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  details_json TEXT NOT NULL,
  created_at_utc TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS approvals_no_update BEFORE UPDATE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_delete BEFORE DELETE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS deployments_no_update BEFORE UPDATE ON deployments
BEGIN SELECT RAISE(ABORT, 'deployments are append-only'); END;
CREATE TRIGGER IF NOT EXISTS deployments_no_delete BEFORE DELETE ON deployments
BEGIN SELECT RAISE(ABORT, 'deployments are append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
"""


def _deployment_columns(connection: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in connection.execute("PRAGMA table_xinfo(deployments)")}


def _skip_sql_quoted(sql: str, cursor: int) -> int:
    quote = sql[cursor]
    terminator = "]" if quote == "[" else quote
    cursor += 1
    while cursor < len(sql):
        if sql[cursor] == terminator:
            if terminator != "]" and cursor + 1 < len(sql) and sql[cursor + 1] == terminator:
                cursor += 2
                continue
            return cursor + 1
        cursor += 1
    raise RuntimeError("deployments table has malformed quoted SQL")


def _skip_sql_comment(sql: str, cursor: int) -> int | None:
    if sql.startswith("--", cursor):
        newline = sql.find("\n", cursor + 2)
        return len(sql) if newline < 0 else newline + 1
    if sql.startswith("/*", cursor):
        end = sql.find("*/", cursor + 2)
        if end < 0:
            raise RuntimeError("deployments table has malformed SQL comment")
        return end + 2
    return None


def _next_active_check(sql: str, offset: int) -> int:
    cursor = offset
    while cursor < len(sql):
        if sql[cursor] in {"'", '"', "`", "["}:
            cursor = _skip_sql_quoted(sql, cursor)
            continue
        comment_end = _skip_sql_comment(sql, cursor)
        if comment_end is not None:
            cursor = comment_end
            continue
        if sql[cursor : cursor + len("CHECK")].upper() == "CHECK":
            before = sql[cursor - 1] if cursor else ""
            after_at = cursor + len("CHECK")
            after = sql[after_at] if after_at < len(sql) else ""
            if not (before.isalnum() or before == "_") and not (
                after.isalnum() or after == "_"
            ):
                return cursor
        cursor += 1
    return -1


def _deployment_checks(connection: sqlite3.Connection) -> frozenset[str]:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'deployments'"
    ).fetchone()
    if row is None or row[0] is None:
        return frozenset()
    sql = str(row[0])
    checks: set[str] = set()
    offset = 0
    while (check_at := _next_active_check(sql, offset)) >= 0:
        cursor = check_at + len("CHECK")
        while cursor < len(sql) and sql[cursor].isspace():
            cursor += 1
        if cursor >= len(sql) or sql[cursor] != "(":
            offset = cursor
            continue
        expression_start = cursor + 1
        depth = 1
        cursor += 1
        while cursor < len(sql) and depth:
            character = sql[cursor]
            if character in {"'", '"', "`", "["}:
                cursor = _skip_sql_quoted(sql, cursor)
                continue
            comment_end = _skip_sql_comment(sql, cursor)
            if comment_end is not None:
                cursor = comment_end
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            cursor += 1
        if depth:
            raise RuntimeError("deployments table has malformed CHECK constraint SQL")
        checks.add(" ".join(sql[expression_start : cursor - 1].split()))
        offset = cursor
    return frozenset(checks)


def _deployment_schema(connection: sqlite3.Connection) -> _DeploymentSchema:
    columns = frozenset(
        (
            str(row[1]),
            str(row[2]),
            int(row[3]),
            None if row[4] is None else str(row[4]),
            int(row[5]),
            int(row[0]),
            int(row[6]),
        )
        for row in connection.execute("PRAGMA table_xinfo(deployments)")
    )
    foreign_keys = frozenset(
        tuple(row[1:]) for row in connection.execute("PRAGMA foreign_key_list(deployments)")
    )
    indexes: set[tuple[int, str, int, tuple[str, ...]]] = set()
    for row in connection.execute("PRAGMA index_list(deployments)"):
        index_name = str(row[1]).replace("'", "''")
        index_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(f"PRAGMA index_info('{index_name}')")
        )
        indexes.add((int(row[2]), str(row[3]), int(row[4]), index_columns))
    return _DeploymentSchema(
        columns=columns,
        foreign_keys=foreign_keys,
        indexes=frozenset(indexes),
        checks=_deployment_checks(connection),
        triggers=frozenset(
            (str(row[0]), " ".join(str(row[1]).split()))
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = 'deployments'"
            )
        ),
    )


def _fresh_deployment_schema(*, legacy: bool = False) -> _DeploymentSchema:
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(SCHEMA)
        if legacy:
            connection.execute(
                "ALTER TABLE deployments ADD COLUMN previous_deployment_id "
                "TEXT REFERENCES deployments(deployment_id)"
            )
        return _deployment_schema(connection)


def _assert_deployment_columns(
    actual: set[str], expected: set[str], *, context: str
) -> None:
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise RuntimeError(
            f"{context} has a stale schema "
            f"(missing columns: {missing}; unexpected columns: {unexpected})"
        )


def _assert_deployment_schema(
    actual: _DeploymentSchema, expected: _DeploymentSchema, *, context: str
) -> None:
    _assert_deployment_columns(
        {column[0] for column in actual.columns},
        {column[0] for column in expected.columns},
        context=context,
    )
    mismatches = [
        name
        for name in ("columns", "foreign_keys", "indexes", "checks", "triggers")
        if getattr(actual, name) != getattr(expected, name)
    ]
    if mismatches:
        raise RuntimeError(
            f"{context} has a stale schema "
            f"(constraint or index mismatch: {', '.join(mismatches)})"
        )


class ControlStore:
    def __init__(
        self,
        database: Path | str,
        *,
        reviewer_identity: str,
        now: Callable[[], str] | None = None,
    ) -> None:
        if not reviewer_identity:
            raise ValueError("reviewer identity is required")
        self.reviewer_identity = reviewer_identity
        self._now = now or (lambda: datetime.now(UTC).isoformat())
        self._lock = threading.RLock()
        self.schemas = PlatformSchemas()
        target = str(database)
        self.connection = sqlite3.connect(
            target,
            check_same_thread=False,
            isolation_level=None,
            uri=target.startswith("file:"),
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def _migrate_legacy_deployments(self, expected_schema: _DeploymentSchema) -> None:
        """Rebuild the ledger without its obsolete predecessor link on any SQLite version."""
        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """CREATE TABLE deployments_without_previous (
                  deployment_id TEXT PRIMARY KEY,
                  candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
                  policy_id TEXT NOT NULL,
                  action TEXT NOT NULL CHECK(action IN ('deploy', 'rollback')),
                  actor TEXT NOT NULL,
                  reason TEXT NOT NULL,
                  created_at_utc TEXT NOT NULL,
                  generation INTEGER NOT NULL UNIQUE
                )"""
            )
            self.connection.execute(
                """INSERT INTO deployments_without_previous(
                    deployment_id, candidate_id, policy_id, action, actor, reason,
                    created_at_utc, generation
                )
                SELECT deployment_id, candidate_id, policy_id, action, actor, reason,
                       created_at_utc, generation
                FROM deployments"""
            )
            # Preserve the imported IDs verbatim: records from the predecessor-link
            # schema cannot be reproduced by the event-ledger ID formula.
            self.connection.execute("DROP TABLE deployments")
            self.connection.execute(
                "ALTER TABLE deployments_without_previous RENAME TO deployments"
            )
            self.connection.execute(
                """CREATE TRIGGER deployments_no_update BEFORE UPDATE ON deployments
                BEGIN SELECT RAISE(ABORT, 'deployments are append-only'); END"""
            )
            self.connection.execute(
                """CREATE TRIGGER deployments_no_delete BEFORE DELETE ON deployments
                BEGIN SELECT RAISE(ABORT, 'deployments are append-only'); END"""
            )
            _assert_deployment_schema(
                _deployment_schema(self.connection),
                expected_schema,
                context="deployment migration output",
            )
            violations = list(self.connection.execute("PRAGMA foreign_key_check"))
            if violations:
                raise RuntimeError("legacy deployment migration violates foreign keys")
            self.connection.commit()
        except BaseException:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")

    def migrate(self) -> None:
        with self._lock:
            deployments_exists = self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'deployments'"
            ).fetchone()
            if deployments_exists:
                deployment_schema = _deployment_schema(self.connection)
                is_legacy = "previous_deployment_id" in {
                    column[0] for column in deployment_schema.columns
                }
                _assert_deployment_schema(
                    deployment_schema,
                    _fresh_deployment_schema(legacy=is_legacy),
                    context="deployment migration input",
                )
            self.connection.executescript(SCHEMA)
            columns = {
                str(row["name"])
                for row in self.connection.execute("PRAGMA table_info(candidates)")
            }
            if "summary_json" not in columns:
                self.connection.execute(
                    "ALTER TABLE candidates ADD COLUMN summary_json TEXT NOT NULL DEFAULT '{}'"
                )
            expected_schema = _fresh_deployment_schema()
            deployment_columns = _deployment_columns(self.connection)
            if "previous_deployment_id" in deployment_columns:
                self._migrate_legacy_deployments(expected_schema)
            _assert_deployment_schema(
                _deployment_schema(self.connection),
                expected_schema,
                context="deployment migration output",
            )

    def require_migrated(self) -> None:
        """Fail clearly when the explicit migration step has not completed."""
        required = {
            "submissions",
            "candidates",
            "approvals",
            "deployments",
            "active_pointer",
            "audit_events",
        }
        with self._lock:
            existing = {
                str(row[0])
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        missing = sorted(required - existing)
        if missing:
            raise RuntimeError(
                "control database is not migrated; run scripts/platform_migrate.py "
                f"before serving (missing: {', '.join(missing)})"
            )
        with self._lock:
            pointer = self.connection.execute(
                "SELECT deployment_id, generation FROM active_pointer WHERE singleton = 1"
            ).fetchone()
            deployment_schema = _deployment_schema(self.connection)
            deployment_columns = {column[0] for column in deployment_schema.columns}
            foreign_keys_enabled = int(
                self.connection.execute("PRAGMA foreign_keys").fetchone()[0]
            )
        if not foreign_keys_enabled:
            raise RuntimeError("control database foreign-key enforcement is disabled")
        if pointer is None:
            raise RuntimeError(
                "control database migration is incomplete; active_pointer singleton row "
                "is missing; rerun scripts/platform_migrate.py"
            )
        if "previous_deployment_id" in deployment_columns:
            raise RuntimeError(
                "control database migration is incomplete; legacy deployment links remain; "
                "rerun scripts/platform_migrate.py"
            )
        _assert_deployment_schema(
            deployment_schema,
            _fresh_deployment_schema(),
            context="control database deployments table",
        )

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def _audit(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        actor: str,
        subject_id: str,
        details: dict[str, Any],
    ) -> None:
        sequence = int(
            connection.execute("SELECT COALESCE(MAX(rowid), 0) + 1 FROM audit_events").fetchone()[0]
        )
        material = {
            "event_type": event_type,
            "actor": actor,
            "subject_id": subject_id,
            "details": details,
            "created_at_utc": self._now(),
            "sequence": sequence,
        }
        event_id = "audit-" + sha256_bytes(canonical_json_bytes(material))[:24]
        details_json = canonical_json_bytes(details).decode()
        self.schemas.validate(
            "audit_event",
            {
                "event_id": event_id,
                "event_type": event_type,
                "actor": actor,
                "subject_id": subject_id,
                "details_json": details_json,
                "details": details,
                "created_at_utc": material["created_at_utc"],
            },
        )
        connection.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                event_id,
                event_type,
                actor,
                subject_id,
                details_json,
                material["created_at_utc"],
            ),
        )

    def submit(self, request: dict[str, Any]) -> str:
        encoded = canonical_json_bytes(request)
        digest = sha256_bytes(encoded)
        submission_id = "submission-" + digest[:24]
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT submission_id, request_json FROM submissions WHERE request_sha256 = ?",
                (digest,),
            ).fetchone()
            if existing:
                if existing["request_json"].encode() != encoded:
                    raise ConflictError("submission hash collision")
                return str(existing["submission_id"])
            connection.execute(
                "INSERT INTO submissions VALUES (?, ?, ?, 'Submitted', NULL, NULL, ?)",
                (submission_id, digest, encoded.decode(), self._now()),
            )
            self._audit(connection, "submission.created", "system", submission_id, request)
        return submission_id

    def get_submission(self, submission_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM submissions WHERE submission_id = ?", (submission_id,)
            ).fetchone()
            if row is None:
                raise KeyError(submission_id)
            return {**dict(row), "request": json.loads(row["request_json"])}

    def cancel_submission(self, submission_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        if actor != self.reviewer_identity:
            raise AuthorizationError("only the configured reviewer may cancel")
        if not reason.strip():
            raise ValueError("cancellation reason is required")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM submissions WHERE submission_id = ?", (submission_id,)
            ).fetchone()
            if row is None:
                raise KeyError(submission_id)
            if row["status"] == "Cancelled":
                return self.get_submission(submission_id)
            if row["status"] not in {"Submitted", "Running"}:
                raise TransitionError("only a submitted or running experiment can be cancelled")
            connection.execute(
                "UPDATE submissions SET status = 'Cancelled' WHERE submission_id = ?",
                (submission_id,),
            )
            self._audit(
                connection,
                "submission.cancelled",
                actor,
                submission_id,
                {"reason": reason.strip(), "previous_status": row["status"]},
            )
        return self.get_submission(submission_id)

    def record_tracking_reconciliation(
        self,
        *,
        subject_id: str,
        operation: str,
        error: str | None,
        resolved: bool,
    ) -> None:
        event_type = (
            "tracking.reconciliation_resolved"
            if resolved
            else "tracking.reconciliation_required"
        )
        with self.transaction() as connection:
            self._audit(
                connection,
                event_type,
                "system",
                subject_id,
                {"operation": operation, "error": error, "resolved": resolved},
            )

    def link_run(self, submission_id: str, *, metaflow_pathspec: str, mlflow_run_id: str) -> None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT metaflow_pathspec, mlflow_run_id, status FROM submissions WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
            if row is None:
                raise KeyError(submission_id)
            if row["status"] == "Cancelled":
                raise TransitionError("a cancelled submission cannot be linked to a run")
            if row["metaflow_pathspec"] not in {None, metaflow_pathspec} or row[
                "mlflow_run_id"
            ] not in {None, mlflow_run_id}:
                raise ConflictError("submission lineage cannot be changed")
            connection.execute(
                "UPDATE submissions SET metaflow_pathspec = ?, mlflow_run_id = ?, status = 'Running' WHERE submission_id = ?",
                (metaflow_pathspec, mlflow_run_id, submission_id),
            )

    def list_submissions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {**dict(row), "request": json.loads(row["request_json"])}
                for row in self.connection.execute(
                    "SELECT * FROM submissions ORDER BY created_at_utc DESC"
                )
            ]

    def mark_submission(self, submission_id: str, status: str) -> None:
        allowed = {"Submitted", "Running", "Complete", "Failed"}
        if status not in allowed:
            raise ValueError("unknown submission status")
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT status FROM submissions WHERE submission_id = ?", (submission_id,)
            ).fetchone()
            if current is None:
                raise KeyError(submission_id)
            if current["status"] == "Cancelled" and status != "Cancelled":
                raise TransitionError("a cancelled submission is terminal")
            changed = connection.execute(
                "UPDATE submissions SET status = ? WHERE submission_id = ?",
                (status, submission_id),
            ).rowcount
            if changed != 1:
                raise KeyError(submission_id)

    def register_candidate(
        self,
        *,
        source_run_id: str,
        policy: PolicyManifest,
        gate_report: GateReport,
        artifacts: list[ArtifactRef],
        summary: RunSummary | None = None,
        submission_id: str | None = None,
    ) -> CandidateRecord:
        self.schemas.validate("policy_package", policy.to_dict())
        self.schemas.validate("gate_report", gate_report.to_dict())
        verify_policy_manifest(policy)
        if gate_report.schema_version != "pixelgym-promotion-gate-report-v1":
            raise ValueError("candidate gate report schema version is unsupported")
        if gate_report.policy_id != policy.policy_id or gate_report.run_id != source_run_id:
            raise ValueError("candidate policy, run, and gate report identities must match")
        if summary is not None and (
            summary.run_id != source_run_id
            or summary.policy_id != policy.policy_id
            or summary.dataset_fingerprint != gate_report.dataset_fingerprint
        ):
            raise ValueError("candidate summary identities must match policy, run, and gate report")
        required_passes = (
            gate_report.accuracy.passed,
            gate_report.cost_usd_per_100.passed,
            gate_report.provider_latency_p95_ms.passed,
            gate_report.completeness.passed,
            gate_report.compatibility_passed,
            gate_report.code_revision_passed,
            (
                gate_report.confidence_bound is None
                or gate_report.confidence_bound.passed
            ),
        )
        if gate_report.overall_passed and (not all(required_passes) or gate_report.reasons):
            raise ValueError("passing gate report has failed components or blocking reasons")
        if not gate_report.overall_passed and not gate_report.reasons:
            raise ValueError("failed gate report must retain at least one blocking reason")
        report_bytes = canonical_json_bytes(gate_report.to_dict())
        report_sha = sha256_bytes(report_bytes)
        summary_bytes = canonical_json_bytes(summary.to_dict() if summary is not None else {})
        candidate_id = "candidate-" + policy.policy_id.removeprefix("sha256:")[:24]
        state = CandidateState.ELIGIBLE if gate_report.overall_passed else CandidateState.GATE_FAILED
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            values = (
                candidate_id,
                source_run_id,
                policy.policy_id,
                canonical_json_bytes(policy.to_dict()).decode(),
                report_bytes.decode(),
                report_sha,
                canonical_json_bytes([item.to_dict() for item in artifacts]).decode(),
                summary_bytes.decode(),
                state.value,
            )
            if existing:
                expected = values[1:8]
                actual = tuple(existing[key] for key in (
                    "source_run_id", "policy_id", "policy_json", "gate_report_json",
                    "gate_report_sha256", "artifacts_json", "summary_json"
                ))
                if actual != expected or existing["state"] not in {
                    state.value,
                    CandidateState.APPROVED.value,
                }:
                    raise ConflictError("candidate identity already has different evidence")
            else:
                connection.execute(
                    "INSERT INTO candidates(candidate_id, source_run_id, policy_id, policy_json, gate_report_json, gate_report_sha256, artifacts_json, summary_json, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
                self._audit(
                    connection,
                    "candidate.gates_evaluated",
                    "system",
                    candidate_id,
                    {"state": state.value, "gate_report_sha256": report_sha},
                )
            if submission_id is not None:
                submission = connection.execute(
                    "SELECT mlflow_run_id, status FROM submissions WHERE submission_id = ?",
                    (submission_id,),
                ).fetchone()
                if submission is None:
                    raise KeyError(submission_id)
                if submission["status"] == "Cancelled":
                    raise TransitionError("a cancelled submission cannot register a candidate")
                if submission["mlflow_run_id"] != source_run_id:
                    raise ConflictError("candidate run does not match submission lineage")
                connection.execute(
                    "UPDATE submissions SET status = 'Complete' WHERE submission_id = ?",
                    (submission_id,),
                )
        return self.get_candidate(candidate_id)

    def _candidate_record(self, row: sqlite3.Row) -> CandidateRecord:
        return CandidateRecord(
            candidate_id=row["candidate_id"],
            source_run_id=row["source_run_id"],
            policy=load_policy_manifest(self.schemas, json.loads(row["policy_json"])),
            gate_report=json.loads(row["gate_report_json"]),
            gate_report_sha256=row["gate_report_sha256"],
            artifacts=tuple(
                ArtifactRef(**value) for value in json.loads(row["artifacts_json"])
            ),
            summary=(
                RunSummary(**json.loads(row["summary_json"]))
                if json.loads(row["summary_json"])
                else None
            ),
            state=CandidateState(row["state"]),
            version=row["version"],
        )

    def get_candidate(self, candidate_id: str) -> CandidateRecord:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise KeyError(candidate_id)
            return self._candidate_record(row)

    def list_candidates(
        self, *, limit: int | None = None, offset: int = 0
    ) -> list[CandidateRecord]:
        if limit is not None and limit <= 0:
            raise ValueError("candidate limit must be positive")
        if offset < 0:
            raise ValueError("candidate offset must be non-negative")
        query = "SELECT * FROM candidates ORDER BY rowid DESC"
        parameters: tuple[int, ...] = ()
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            parameters = (limit, offset)
        elif offset:
            query += " LIMIT -1 OFFSET ?"
            parameters = (offset,)
        with self._lock:
            rows = list(self.connection.execute(query, parameters))
        # Policy manifests are validated lazily for every returned row. Keeping
        # validation outside the connection lock prevents schema work from
        # blocking writers while still detecting out-of-band row corruption.
        return [self._candidate_record(row) for row in rows]

    def list_candidate_providers(self) -> list[str]:
        with self._lock:
            rows = self.connection.execute(
                """SELECT DISTINCT json_extract(policy_json, '$.provider') AS provider
                FROM candidates
                WHERE json_type(policy_json, '$.provider') = 'text'
                ORDER BY provider"""
            ).fetchall()
        return [row["provider"] for row in rows]

    def _validate_candidate_evidence(self, row: sqlite3.Row) -> None:
        try:
            policy_value = json.loads(row["policy_json"])
            gate_report = json.loads(row["gate_report_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise ContractValidationError(
                "stored candidate evidence is not strict JSON"
            ) from exc
        policy = load_policy_manifest(self.schemas, policy_value)
        self.schemas.validate("gate_report", gate_report)
        # Keep strict validation before canonical serialization so corrupt stored
        # numbers fail as ContractValidationError rather than json.dumps ValueError.
        report_digest = sha256_bytes(canonical_json_bytes(gate_report))
        if report_digest != row["gate_report_sha256"]:
            raise ContractValidationError("stored gate_report digest does not verify")
        if policy.policy_id != row["policy_id"]:
            raise ContractValidationError("stored policy identity does not match candidate")
        if gate_report["policy_id"] != row["policy_id"]:
            raise ContractValidationError("stored gate_report policy identity does not match candidate")
        if gate_report["run_id"] != row["source_run_id"]:
            raise ContractValidationError("stored gate_report run identity does not match candidate")
        if not gate_report["overall_passed"]:
            raise ContractValidationError("stored candidate no longer has passing gates")
        if not policy.source_provenance_verified or not gate_report["code_revision_passed"]:
            raise ContractValidationError("stored candidate source provenance is not promotable")

    def _validate_candidate_approval_evidence(
        self,
        candidate: sqlite3.Row,
        approval: sqlite3.Row,
    ) -> None:
        self._validate_candidate_evidence(candidate)
        self.schemas.validate("approval", dict(approval))
        if approval["candidate_id"] != candidate["candidate_id"]:
            raise ContractValidationError("approval candidate identity does not match candidate")
        if approval["policy_id"] != candidate["policy_id"]:
            raise ContractValidationError("approval policy identity does not match candidate")
        if approval["gate_report_sha256"] != candidate["gate_report_sha256"]:
            raise TransitionError("approved gate report no longer verifies")

    def verify_candidate_approval(
        self, candidate_id: str
    ) -> tuple[CandidateRecord, dict[str, Any]]:
        """Validate stored candidate and approval evidence before external preparation."""
        with self._lock:
            candidate = self.connection.execute(
                "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if candidate is None:
                raise KeyError(candidate_id)
            if candidate["state"] != CandidateState.APPROVED.value:
                raise TransitionError("candidate must be approved before activation")
            approval = self.connection.execute(
                "SELECT * FROM approvals WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if approval is None:
                raise TransitionError("candidate approval evidence is missing")
            self._validate_candidate_approval_evidence(candidate, approval)
            return self._candidate_record(candidate), dict(approval)

    def approve(
        self,
        candidate_id: str,
        *,
        actor: str,
        reason: str,
        gate_report_sha256: str,
    ) -> dict[str, Any]:
        if actor != self.reviewer_identity:
            raise AuthorizationError("only the configured reviewer may approve")
        if not reason.strip():
            raise ValueError("approval reason is required")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise KeyError(candidate_id)
            if row["gate_report_sha256"] != gate_report_sha256:
                raise TransitionError("gate report digest changed or is missing")
            if row["state"] == CandidateState.APPROVED.value:
                self._validate_candidate_evidence(row)
                existing = connection.execute(
                    "SELECT * FROM approvals WHERE candidate_id = ?", (candidate_id,)
                ).fetchone()
                if existing is None:
                    raise ContractValidationError("approved candidate has no approval evidence")
                self.schemas.validate("approval", dict(existing))
                if existing["actor"] == actor and existing["reason"] == reason.strip():
                    return dict(existing)
                raise ConflictError("candidate is already approved with different evidence")
            if row["state"] != CandidateState.ELIGIBLE.value:
                raise TransitionError("only an eligible candidate can be approved")
            self._validate_candidate_evidence(row)
            created = self._now()
            approval_id = "approval-" + sha256_bytes(
                canonical_json_bytes(
                    {"candidate_id": candidate_id, "actor": actor, "reason": reason.strip(), "created": created}
                )
            )[:24]
            approval = {
                "approval_id": approval_id,
                "candidate_id": candidate_id,
                "actor": actor,
                "reason": reason.strip(),
                "gate_report_sha256": gate_report_sha256,
                "policy_id": row["policy_id"],
                "created_at_utc": created,
            }
            self.schemas.validate("approval", approval)
            connection.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?)",
                tuple(
                    approval[key]
                    for key in (
                        "approval_id",
                        "candidate_id",
                        "actor",
                        "reason",
                        "gate_report_sha256",
                        "policy_id",
                        "created_at_utc",
                    )
                ),
            )
            connection.execute(
                "UPDATE candidates SET state = ?, version = version + 1 WHERE candidate_id = ? AND state = ?",
                (CandidateState.APPROVED.value, candidate_id, CandidateState.ELIGIBLE.value),
            )
            self._audit(
                connection,
                "candidate.approved",
                actor,
                candidate_id,
                {"approval_id": approval_id, "reason": reason.strip()},
            )
            return dict(
                connection.execute(
                    "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
                ).fetchone()
            )

    def get_approval(self, candidate_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM approvals WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise KeyError(candidate_id)
            return dict(row)

    def active(self) -> tuple[DeploymentRecord | None, int]:
        with self._lock:
            pointer = self.connection.execute(
                "SELECT deployment_id, generation FROM active_pointer WHERE singleton = 1"
            ).fetchone()
            if pointer["deployment_id"] is None:
                return None, int(pointer["generation"])
            return self.get_deployment(pointer["deployment_id"]), int(pointer["generation"])

    def get_deployment(self, deployment_id: str) -> DeploymentRecord:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM deployments WHERE deployment_id = ?", (deployment_id,)
            ).fetchone()
            if row is None:
                raise KeyError(deployment_id)
            return DeploymentRecord(**dict(row))

    def _previous_target_row(
        self, connection: sqlite3.Connection, current: sqlite3.Row | DeploymentRecord
    ) -> sqlite3.Row:
        """Resolve the last known-good deploy outside the active rollback chain.

        A deploy starts a new chain. Each later rollback abandons its source and
        restores another candidate, so every candidate from that deploy through the
        current event is ineligible for the next rollback. Deriving the chain from
        immutable rows also gives pre-lineage databases the new semantics without
        rewriting their stored events.
        """
        current_generation = (
            current.generation
            if isinstance(current, DeploymentRecord)
            else int(current["generation"])
        )
        current_deployment_id = (
            current.deployment_id
            if isinstance(current, DeploymentRecord)
            else str(current["deployment_id"])
        )
        rows: list[sqlite3.Row] = list(
            connection.execute(
                "SELECT * FROM deployments WHERE generation <= ? "
                "ORDER BY generation DESC",
                (current_generation,),
            )
        )
        if not rows or rows[0]["deployment_id"] != current_deployment_id:
            raise TransitionError("active deployment event does not exist")

        abandoned_candidates: set[str] = set()
        chain_start = None
        for index, row in enumerate(rows):
            abandoned_candidates.add(str(row["candidate_id"]))
            if row["action"] == "deploy":
                chain_start = index
                break
        if chain_start is None:
            raise TransitionError("rollback history has no explicit deploy origin")

        for row in rows[chain_start + 1 :]:
            if (
                row["action"] == "deploy"
                and row["candidate_id"] not in abandoned_candidates
            ):
                return row
        raise TransitionError(
            "there is no eligible known-good deployment to roll back to"
        )

    def activate(
        self,
        candidate_id: str,
        *,
        actor: str,
        reason: str,
        action: str,
        expected_deployment_id: str | None,
        expected_generation: int,
    ) -> DeploymentRecord:
        if actor != self.reviewer_identity:
            raise AuthorizationError("only the configured reviewer may deploy or rollback")
        if action not in {"deploy", "rollback"}:
            raise ValueError("unknown deployment action")
        if not reason.strip():
            raise ValueError("deployment reason is required")
        with self.transaction() as connection:
            candidate = connection.execute(
                "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            approval = connection.execute(
                "SELECT * FROM approvals WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            pointer = connection.execute(
                "SELECT * FROM active_pointer WHERE singleton = 1"
            ).fetchone()
            if candidate is None or approval is None or candidate["state"] != CandidateState.APPROVED.value:
                raise TransitionError("deployment target is not approved")
            self._validate_candidate_approval_evidence(candidate, approval)
            if pointer["deployment_id"] != expected_deployment_id or pointer["generation"] != expected_generation:
                raise ConflictError("active deployment changed concurrently")
            rollback_lineage: dict[str, str] = {}
            if action == "rollback":
                if expected_deployment_id is None:
                    raise TransitionError("there is no active deployment to roll back")
                current = connection.execute(
                    "SELECT * FROM deployments WHERE deployment_id = ?",
                    (expected_deployment_id,),
                ).fetchone()
                if current is None:
                    raise TransitionError("active deployment event does not exist")
                target = self._previous_target_row(connection, current)
                if target["candidate_id"] != candidate_id:
                    raise TransitionError(
                        "rollback target is not the eligible known-good deployment"
                    )
                rollback_lineage = {
                    "abandoned_deployment_id": str(current["deployment_id"]),
                    "restored_deployment_id": str(target["deployment_id"]),
                }
            generation = expected_generation + 1
            material = {
                "candidate_id": candidate_id,
                "policy_id": candidate["policy_id"],
                "action": action,
                "generation": generation,
            }
            deployment_id = "deployment-" + sha256_bytes(canonical_json_bytes(material))[:24]
            created = self._now()
            deployment = {
                "deployment_id": deployment_id,
                "candidate_id": candidate_id,
                "policy_id": candidate["policy_id"],
                "action": action,
                "actor": actor,
                "reason": reason.strip(),
                "created_at_utc": created,
                "generation": generation,
            }
            self.schemas.validate("deployment", deployment)
            connection.execute(
                """INSERT INTO deployments(
                    deployment_id, candidate_id, policy_id, action, actor, reason,
                    created_at_utc, generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(
                    deployment[key]
                    for key in (
                        "deployment_id",
                        "candidate_id",
                        "policy_id",
                        "action",
                        "actor",
                        "reason",
                        "created_at_utc",
                        "generation",
                    )
                ),
            )
            changed = connection.execute(
                "UPDATE active_pointer SET deployment_id = ?, generation = ? WHERE singleton = 1 AND generation = ?",
                (deployment_id, generation, expected_generation),
            ).rowcount
            if changed != 1:
                raise ConflictError("atomic activation compare-and-swap lost")
            self._audit(
                connection,
                f"deployment.{action}",
                actor,
                deployment_id,
                {**material, **rollback_lineage, "reason": reason.strip()},
            )
        return self.get_deployment(deployment_id)

    def previous_target(self, current: DeploymentRecord) -> DeploymentRecord:
        """Return the latest explicit deploy outside the active rollback chain."""
        with self._lock:
            row = self._previous_target_row(self.connection, current)
            return DeploymentRecord(**dict(row))

    def audit_events(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[dict[str, Any]]:
        if limit is not None and limit <= 0:
            raise ValueError("audit event limit must be positive")
        if offset < 0:
            raise ValueError("audit event offset must be non-negative")
        direction = "DESC" if newest_first else "ASC"
        query = f"SELECT * FROM audit_events ORDER BY rowid {direction}"
        parameters: tuple[int, ...] = ()
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            parameters = (limit, offset)
        elif offset:
            query += " LIMIT -1 OFFSET ?"
            parameters = (offset,)
        with self._lock:
            rows = list(self.connection.execute(query, parameters))
        return [
            {**dict(row), "details": json.loads(row["details_json"])} for row in rows
        ]

    def approval_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(row)
                for row in self.connection.execute("SELECT * FROM approvals ORDER BY rowid")
            ]

    def deployment_history(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(row)
                for row in self.connection.execute(
                    "SELECT * FROM deployments ORDER BY generation"
                )
            ]
