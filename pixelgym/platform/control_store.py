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

from pixelgym.platform.contracts import ArtifactRef, CandidateState, GateReport, PolicyManifest
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.policy import verify_policy_manifest


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
    state: CandidateState
    version: int


@dataclass(frozen=True)
class DeploymentRecord:
    deployment_id: str
    candidate_id: str
    policy_id: str
    previous_deployment_id: str | None
    action: str
    actor: str
    reason: str
    created_at_utc: str
    generation: int


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
  previous_deployment_id TEXT REFERENCES deployments(deployment_id),
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
        target = str(database)
        self.connection = sqlite3.connect(
            target,
            check_same_thread=False,
            isolation_level=None,
            uri=target.startswith("file:"),
        )
        self.connection.row_factory = sqlite3.Row

    def migrate(self) -> None:
        with self._lock:
            self.connection.executescript(SCHEMA)

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
        connection.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                event_id,
                event_type,
                actor,
                subject_id,
                canonical_json_bytes(details).decode(),
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

    def link_run(self, submission_id: str, *, metaflow_pathspec: str, mlflow_run_id: str) -> None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT metaflow_pathspec, mlflow_run_id FROM submissions WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
            if row is None:
                raise KeyError(submission_id)
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
        allowed = {"Submitted", "Running", "Complete", "Failed", "Cancelled"}
        if status not in allowed:
            raise ValueError("unknown submission status")
        with self.transaction() as connection:
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
    ) -> CandidateRecord:
        verify_policy_manifest(policy)
        if gate_report.schema_version != "pixelgym-promotion-gate-report-v1":
            raise ValueError("candidate gate report schema version is unsupported")
        if gate_report.policy_id != policy.policy_id or gate_report.run_id != source_run_id:
            raise ValueError("candidate policy, run, and gate report identities must match")
        required_passes = (
            gate_report.accuracy.passed,
            gate_report.cost_usd_per_100.passed,
            gate_report.provider_latency_p95_ms.passed,
            gate_report.completeness.passed,
            gate_report.compatibility_passed,
            gate_report.code_revision_passed,
        )
        if gate_report.overall_passed and (not all(required_passes) or gate_report.reasons):
            raise ValueError("passing gate report has failed components or blocking reasons")
        if not gate_report.overall_passed and not gate_report.reasons:
            raise ValueError("failed gate report must retain at least one blocking reason")
        report_bytes = canonical_json_bytes(gate_report.to_dict())
        report_sha = sha256_bytes(report_bytes)
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
                state.value,
            )
            if existing:
                expected = values[1:7]
                actual = tuple(existing[key] for key in (
                    "source_run_id", "policy_id", "policy_json", "gate_report_json",
                    "gate_report_sha256", "artifacts_json"
                ))
                if actual != expected or existing["state"] not in {
                    state.value,
                    CandidateState.APPROVED.value,
                }:
                    raise ConflictError("candidate identity already has different evidence")
            else:
                connection.execute(
                    "INSERT INTO candidates(candidate_id, source_run_id, policy_id, policy_json, gate_report_json, gate_report_sha256, artifacts_json, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
                self._audit(
                    connection,
                    "candidate.gates_evaluated",
                    "system",
                    candidate_id,
                    {"state": state.value, "gate_report_sha256": report_sha},
                )
        return self.get_candidate(candidate_id)

    def get_candidate(self, candidate_id: str) -> CandidateRecord:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise KeyError(candidate_id)
            return CandidateRecord(
                candidate_id=row["candidate_id"],
                source_run_id=row["source_run_id"],
                policy=PolicyManifest(**json.loads(row["policy_json"])),
                gate_report=json.loads(row["gate_report_json"]),
                gate_report_sha256=row["gate_report_sha256"],
                artifacts=tuple(
                    ArtifactRef(**value) for value in json.loads(row["artifacts_json"])
                ),
                state=CandidateState(row["state"]),
                version=row["version"],
            )

    def list_candidates(self) -> list[CandidateRecord]:
        with self._lock:
            ids = [
                row[0]
                for row in self.connection.execute(
                    "SELECT candidate_id FROM candidates ORDER BY rowid DESC"
                )
            ]
            return [self.get_candidate(candidate_id) for candidate_id in ids]

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
                existing = connection.execute(
                    "SELECT * FROM approvals WHERE candidate_id = ?", (candidate_id,)
                ).fetchone()
                if existing["actor"] == actor and existing["reason"] == reason.strip():
                    return dict(existing)
                raise ConflictError("candidate is already approved with different evidence")
            if row["state"] != CandidateState.ELIGIBLE.value:
                raise TransitionError("only an eligible candidate can be approved")
            created = self._now()
            approval_id = "approval-" + sha256_bytes(
                canonical_json_bytes(
                    {"candidate_id": candidate_id, "actor": actor, "reason": reason.strip(), "created": created}
                )
            )[:24]
            connection.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    approval_id,
                    candidate_id,
                    actor,
                    reason.strip(),
                    gate_report_sha256,
                    row["policy_id"],
                    created,
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
            if candidate["gate_report_sha256"] != approval["gate_report_sha256"]:
                raise TransitionError("approved gate report no longer verifies")
            if pointer["deployment_id"] != expected_deployment_id or pointer["generation"] != expected_generation:
                raise ConflictError("active deployment changed concurrently")
            previous_deployment_id = expected_deployment_id
            if action == "rollback":
                if expected_deployment_id is None:
                    raise TransitionError("there is no active deployment to roll back")
                current = connection.execute(
                    "SELECT * FROM deployments WHERE deployment_id = ?",
                    (expected_deployment_id,),
                ).fetchone()
                if current is None or current["previous_deployment_id"] is None:
                    raise TransitionError("there is no previous deployment to roll back to")
                target = connection.execute(
                    "SELECT * FROM deployments WHERE deployment_id = ?",
                    (current["previous_deployment_id"],),
                ).fetchone()
                if target is None or target["candidate_id"] != candidate_id:
                    raise TransitionError("rollback target is not the active deployment's predecessor")
                previous_deployment_id = target["previous_deployment_id"]
            generation = expected_generation + 1
            material = {
                "candidate_id": candidate_id,
                "policy_id": candidate["policy_id"],
                "previous": previous_deployment_id,
                "action": action,
                "generation": generation,
            }
            deployment_id = "deployment-" + sha256_bytes(canonical_json_bytes(material))[:24]
            created = self._now()
            connection.execute(
                "INSERT INTO deployments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    deployment_id,
                    candidate_id,
                    candidate["policy_id"],
                    previous_deployment_id,
                    action,
                    actor,
                    reason.strip(),
                    created,
                    generation,
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
                {**material, "reason": reason.strip()},
            )
        return self.get_deployment(deployment_id)

    def previous_target(self, current: DeploymentRecord) -> DeploymentRecord:
        if current.previous_deployment_id is None:
            raise TransitionError("there is no previous deployment to roll back to")
        return self.get_deployment(current.previous_deployment_id)

    def audit_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {**dict(row), "details": json.loads(row["details_json"])}
                for row in self.connection.execute("SELECT * FROM audit_events ORDER BY rowid")
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
