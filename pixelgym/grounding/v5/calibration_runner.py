"""Single manifest-driven campaign runner with provider behavior behind adapters."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.plan import (
    RUNNER_RESULT_SCHEMA_VERSION,
    CalibrationPlan,
    TaskAssignment,
)
from pixelgym.grounding.v5.runner import EpisodeResult


@dataclass(frozen=True)
class SpendSnapshot:
    known_spend_usd: Decimal
    unknown_reservation_usd: Decimal
    budget_accounted_spend_usd: Decimal
    blocked: bool = False

    @classmethod
    def zero(cls) -> SpendSnapshot:
        return cls(Decimal(0), Decimal(0), Decimal(0))

    def __post_init__(self) -> None:
        amounts = (
            self.known_spend_usd,
            self.unknown_reservation_usd,
            self.budget_accounted_spend_usd,
        )
        if any(not amount.is_finite() or amount < 0 for amount in amounts):
            raise ValueError("adapter spend snapshot must contain finite non-negative values")
        if self.budget_accounted_spend_usd < (
            self.known_spend_usd + self.unknown_reservation_usd
        ):
            raise ValueError("budget-accounted spend omits known spend or unknown reservations")

    def to_dict(self) -> dict[str, Any]:
        return {
            "known_spend_usd": str(self.known_spend_usd),
            "unknown_reservation_usd": str(self.unknown_reservation_usd),
            "budget_accounted_spend_usd": str(self.budget_accounted_spend_usd),
            "blocked": self.blocked,
        }


class CalibrationProviderAdapter(Protocol):
    """Narrow campaign boundary; argv, parsing, and accounting stay provider-owned."""

    adapter_name: str
    transport_name: str

    def bind_journal(self, journal: V5AttemptJournal) -> None: ...

    def execute(
        self,
        assignment: TaskAssignment,
        *,
        journal: V5AttemptJournal,
        approved_caps: CallCaps,
    ) -> EpisodeResult: ...

    def reconcile(
        self,
        assignment: TaskAssignment,
        *,
        journal: V5AttemptJournal,
        approved_caps: CallCaps,
    ) -> EpisodeResult: ...

    def spend_snapshot(self) -> SpendSnapshot: ...
    def provider_accounting(self) -> Mapping[str, Any]: ...
    def transport_records(self) -> Sequence[Mapping[str, Any]]: ...
    def close(self) -> None: ...
    def cleanup_evidence(self) -> Mapping[str, bool]: ...


def run_calibration_plan(
    repository_root: Path,
    *,
    plan: CalibrationPlan,
    approved_plan_sha256: str,
    adapter: CalibrationProviderAdapter,
) -> dict[str, Any]:
    """Execute or reconcile one approved plan without replaying completed assignments."""

    if approved_plan_sha256 != plan.digest:
        raise ValueError("approved runner plan digest does not match canonical plan bytes")
    if adapter.adapter_name != plan.provider.adapter:
        raise ValueError("provider adapter differs from approved runner plan")
    if adapter.transport_name != plan.provider.transport:
        raise ValueError("provider transport differs from approved runner plan")
    _verify_repository_state(repository_root, plan)
    _verify_task_manifest(repository_root, plan)
    output_directory = repository_root / plan.outputs.directory
    summary_path = output_directory / plan.outputs.summary
    journal_path = output_directory / plan.outputs.journal
    if output_directory.exists() and plan.outputs.resume_mode == "forbid":
        raise FileExistsError(f"refusing to replace runner output: {plan.outputs.directory}")
    output_directory.mkdir(parents=True, exist_ok=True)
    if summary_path.exists():
        prior = _load_json_object(summary_path)
        if prior.get("approved_plan_sha256") != plan.digest:
            raise ValueError("existing summary belongs to a different runner plan")
        if prior.get("run_state") == "complete":
            try:
                _validate_completed_summary(
                    prior,
                    plan=plan,
                    journal_path=journal_path,
                    adapter=adapter,
                )
            finally:
                adapter.close()
            return prior

    journal = V5AttemptJournal(journal_path)
    episode_results: list[dict[str, Any]] = []
    execution_error: dict[str, str] | None = None
    stop_reason = "completed_all_assignments"
    consecutive_failures = 0
    try:
        adapter.bind_journal(journal)
        for assignment in plan.assignments:
            completed = journal.event(_completed_key(assignment))
            if completed is not None:
                result = _episode_from_mapping(completed.payload["episode_result"])
            else:
                started_key = _started_key(assignment)
                started = journal.event(started_key)
                if started is None:
                    journal.append_event(
                        event_key=started_key,
                        kind="campaign_assignment_started",
                        trial_id=_trial_id(assignment),
                        step_index=0,
                        payload={"assignment": assignment.to_dict()},
                    )
                    result = adapter.execute(
                        assignment,
                        journal=journal,
                        approved_caps=plan.budgets.caps,
                    )
                else:
                    result = adapter.reconcile(
                        assignment,
                        journal=journal,
                        approved_caps=plan.budgets.caps,
                    )
            _validate_episode_identity(assignment, result)
            if completed is None:
                journal.append_event(
                    event_key=_completed_key(assignment),
                    kind="campaign_assignment_completed",
                    trial_id=_trial_id(assignment),
                    step_index=0,
                    payload={"episode_result": result.to_dict()},
                )
            episode_results.append({"slot": assignment.slot, **result.to_dict()})
            spend = _validated_spend(adapter.spend_snapshot(), plan)
            if spend.blocked:
                stop_reason = "spend_ledger_blocked"
                break
            classification = result.classification
            if (
                plan.retry_breaker.continue_on_transport_retry_exhaustion
                and classification == "infrastructure_failure"
                and any(
                    event.kind == "sealed_unsuccessful_result"
                    and event.trial_id == result.trial_id
                    and event.payload.get("failure_code") == "transport_fault_retry_exhausted"
                    for event in journal.events()
                )
            ):
                # Preserve the failed episode in the denominator, but an exhausted
                # transport error does not trip the campaign's failure breaker.
                continue
            if classification in plan.retry_breaker.hard_stop_classifications:
                stop_reason = f"hard_stop_classification:{classification}"
                break
            if classification not in plan.retry_breaker.continue_classifications:
                raise ValueError(
                    f"classification {classification!r} has no approved continuation rule"
                )
            if classification in _NORMAL_CLASSIFICATIONS:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= plan.retry_breaker.consecutive_failure_limit:
                    stop_reason = "consecutive_failure_breaker"
                    break
    except BaseException as exc:
        execution_error = {"type": type(exc).__name__}
        stop_reason = "execution_error"
        raise
    finally:
        spend = adapter.spend_snapshot()
        provider_accounting = dict(adapter.provider_accounting())
        transport_records = [dict(record) for record in adapter.transport_records()]
        integrity = journal.integrity_report()
        calls = journal.call_counts()
        attempted_count = len(
            {
                event.trial_id
                for event in journal.events()
                if event.kind == "campaign_assignment_started"
            }
        )
        journal.close()
        adapter.close()
        cleanup = {
            "journal_closed": True,
            **adapter.cleanup_evidence(),
        }
        summary = _summary(
            plan=plan,
            episode_results=episode_results,
            transport_records=transport_records,
            spend=spend,
            provider_accounting=provider_accounting,
            call_counts=calls,
            journal_integrity=integrity,
            execution_error=execution_error,
            stop_reason=stop_reason,
            attempted_count=attempted_count,
            cleanup=cleanup,
        )
        _write_json(summary_path, summary)
    return summary


_NORMAL_CLASSIFICATIONS = frozenset(
    {"success_termination", "step_limit_truncation", "pilot_action_limit"}
)


def _validate_completed_summary(
    summary: Mapping[str, Any],
    *,
    plan: CalibrationPlan,
    journal_path: Path,
    adapter: CalibrationProviderAdapter,
) -> None:
    validate_credential_free(summary)
    identities = {
        "schema_version": RUNNER_RESULT_SCHEMA_VERSION,
        "run_state": "complete",
        "purpose": plan.purpose,
        "approved_plan_sha256": plan.digest,
        "code_revision": plan.code_revision,
        "assigned_policy_task_pairs": len(plan.assignments),
        "stop_conditions": list(plan.stop_conditions),
        "execution_error": None,
    }
    for field, expected in identities.items():
        if summary.get(field) != expected:
            raise ValueError(f"completed summary {field} mismatch")
    if not journal_path.is_file():
        raise FileNotFoundError("completed summary journal is missing")
    journal = V5AttemptJournal(journal_path)
    try:
        adapter.bind_journal(journal)
        integrity = journal.integrity_report()
        calls = journal.call_counts()
        attempted = len(
            {
                event.trial_id
                for event in journal.events()
                if event.kind == "campaign_assignment_started"
            }
        )
        journal_results: list[dict[str, Any]] = []
        for assignment in plan.assignments:
            completed = journal.event(_completed_key(assignment))
            if completed is None:
                break
            result = _episode_from_mapping(completed.payload.get("episode_result"))
            _validate_episode_identity(assignment, result)
            journal_results.append({"slot": assignment.slot, **result.to_dict()})
        spend = _validated_spend(adapter.spend_snapshot(), plan)
    finally:
        journal.close()
    classifications = Counter(str(result["classification"]) for result in journal_results)
    denominators = {
        "attempted": attempted,
        "invalid_output": classifications["invalid_output"],
        "infrastructure_failure": classifications["infrastructure_failure"],
        "policy_violation": classifications["policy_violation"],
    }
    evidence = {
        "attempted_policy_task_pairs": attempted,
        "successful_policy_task_pairs": sum(
            bool(result["success"]) for result in journal_results
        ),
        "completed_all_assigned_pairs": len(journal_results) == len(plan.assignments),
        "classifications": dict(sorted(classifications.items())),
        "outcome_denominators": denominators,
        "episode_results": journal_results,
        "model_attempt_reservations": calls[0],
        "provider_control_requests": calls[1],
        "provider_wire_request_reservations": calls[0] + calls[1],
        "spend": spend.to_dict(),
        "journal_integrity": integrity,
    }
    for field, expected in evidence.items():
        if summary.get(field) != expected:
            raise ValueError(f"completed summary {field} does not match its journal")


def _summary(
    *,
    plan: CalibrationPlan,
    episode_results: Sequence[Mapping[str, Any]],
    transport_records: Sequence[Mapping[str, Any]],
    spend: SpendSnapshot,
    provider_accounting: Mapping[str, Any],
    call_counts: tuple[int, int],
    journal_integrity: Mapping[str, Any],
    execution_error: Mapping[str, str] | None,
    stop_reason: str,
    attempted_count: int,
    cleanup: Mapping[str, bool],
) -> dict[str, Any]:
    classifications = Counter(str(result["classification"]) for result in episode_results)
    denominators = {
        "attempted": attempted_count,
        "invalid_output": classifications["invalid_output"],
        "infrastructure_failure": classifications["infrastructure_failure"],
        "policy_violation": classifications["policy_violation"],
    }
    return {
        "schema_version": RUNNER_RESULT_SCHEMA_VERSION,
        "run_state": "complete" if execution_error is None else "incomplete",
        "purpose": plan.purpose,
        "approved_plan_sha256": plan.digest,
        "code_revision": plan.code_revision,
        "assigned_policy_task_pairs": len(plan.assignments),
        "attempted_policy_task_pairs": attempted_count,
        "successful_policy_task_pairs": sum(bool(result["success"]) for result in episode_results),
        "completed_all_assigned_pairs": len(episode_results) == len(plan.assignments),
        "classifications": dict(sorted(classifications.items())),
        "outcome_denominators": denominators,
        "episode_results": list(episode_results),
        "model_attempt_reservations": call_counts[0],
        "provider_control_requests": call_counts[1],
        "provider_wire_request_reservations": call_counts[0] + call_counts[1],
        "spend": spend.to_dict(),
        "provider_accounting": dict(provider_accounting),
        "stop_reason": stop_reason,
        "execution_error": execution_error,
        "transport_records": list(transport_records),
        "journal_integrity": dict(journal_integrity),
        "stop_conditions": list(plan.stop_conditions),
        "cleanup": dict(cleanup),
    }


def _verify_repository_state(repository_root: Path, plan: CalibrationPlan) -> None:
    revision = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if revision != plan.code_revision:
        raise ValueError("runner plan code revision differs from the current checkout")
    dirty = subprocess.run(
        ["git", "-C", str(repository_root), "status", "--porcelain", "--untracked-files=no"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if dirty:
        raise ValueError("tracked worktree must be clean before provider requests")


def _verify_task_manifest(repository_root: Path, plan: CalibrationPlan) -> None:
    manifest = _load_json_object(repository_root / plan.manifest_path)
    embedded_digest = manifest.get("manifest_digest")
    recomputed_digest = _task_manifest_content_digest(manifest)
    if embedded_digest != plan.manifest_digest or recomputed_digest != plan.manifest_digest:
        raise ValueError(f"task manifest digest mismatch: {plan.manifest_path}")
    expected = {(item.seed, item.task_id, item.family) for item in plan.assignments}
    observed: set[tuple[int, str, str]] = set()
    records = manifest.get("records")
    if not isinstance(records, list):
        raise TypeError(f"task manifest records are missing: {plan.manifest_path}")
    for record in records:
        if not isinstance(record, dict):
            continue
        seed_record = record.get("seed_record")
        if not isinstance(seed_record, dict):
            continue
        seed = seed_record.get("seed")
        task_id = record.get("task_id")
        family = seed_record.get("family")
        if type(seed) is int and isinstance(task_id, str) and isinstance(family, str):
            observed.add((seed, task_id, family))
    missing = sorted(expected - observed)
    if missing:
        raise ValueError(f"task manifest does not contain approved assignment: {missing[0]}")


def _task_manifest_content_digest(manifest: Mapping[str, Any]) -> str:
    return content_digest(
        {key: value for key, value in manifest.items() if key != "manifest_digest"}
    )


def _validated_spend(snapshot: SpendSnapshot, plan: CalibrationPlan) -> SpendSnapshot:
    SpendSnapshot(**snapshot.__dict__)
    if snapshot.budget_accounted_spend_usd > plan.budgets.maximum_spend_usd:
        raise RuntimeError("provider spend exceeds the approved maximum")
    return snapshot


def _episode_from_mapping(value: object) -> EpisodeResult:
    if not isinstance(value, dict):
        raise TypeError("campaign completion event has malformed episode evidence")
    return EpisodeResult(
        trial_id=str(value["trial_id"]),
        task_id=str(value["task_id"]),
        success=bool(value["success"]),
        classification=str(value["classification"]),
        environment_actions=int(value["environment_actions"]),
        model_attempts=int(value["model_attempts"]),
        provider_control_requests=int(value["provider_control_requests"]),
        provider_wire_requests=int(value["provider_wire_requests"]),
        final_policy_checkpoint_digest=str(value["final_policy_checkpoint_digest"]),
    )


def _validate_episode_identity(assignment: TaskAssignment, result: EpisodeResult) -> None:
    if result.task_id != assignment.task_id or result.trial_id != _trial_id(assignment):
        raise ValueError("provider adapter returned an episode for the wrong assignment")


def _trial_id(assignment: TaskAssignment) -> str:
    return f"manifest-{assignment.slot}-{assignment.ordinal:04d}-{assignment.task_id}"


def _started_key(assignment: TaskAssignment) -> str:
    return f"campaign/assignment-{assignment.ordinal:04d}/started"


def _completed_key(assignment: TaskAssignment) -> str:
    return f"campaign/assignment-{assignment.ordinal:04d}/completed"


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"missing or malformed JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    validate_credential_free(value)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_spend_snapshot(value: Mapping[str, Any]) -> SpendSnapshot:
    """Strict helper for narrow adapters that expose provider accounting records."""

    try:
        return SpendSnapshot(
            known_spend_usd=Decimal(str(value["known_spend_usd"])),
            unknown_reservation_usd=Decimal(str(value["unknown_reservation_usd"])),
            budget_accounted_spend_usd=Decimal(str(value["budget_accounted_spend_usd"])),
            blocked=bool(value.get("blocked", False)),
        )
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise ValueError("provider adapter returned malformed spend accounting") from exc
