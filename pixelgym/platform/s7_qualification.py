"""S7 preparation and qualification. This module has no promotion or deployment path."""

from __future__ import annotations

import json
import shutil
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import (
    CallCaps,
    PolicyManifest,
    TransportOutcome,
    content_digest,
    sandbox_endpoint_allowlist_digest,
)
from pixelgym.grounding.v5.fixtures import scripted_policy_manifest
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import CallCapExceededError, V5AttemptJournal
from pixelgym.grounding.v5.policies import Mutation, golden_actions, mutation_trace
from pixelgym.grounding.v5.retention import _checkout
from pixelgym.grounding.v5.retention_validation import CASES, SCHEMA, SEED
from pixelgym.grounding.v5.runner import InjectedInterruption, V5Runner
from pixelgym.platform.policy_subprocess import SandboxedPolicyProcess
from pixelgym.platform.s7_budget import BudgetedScriptedTransport, S7SendBudget
from pixelgym.platform.s7_policy import EVIDENCE_LABEL, POLICY_SHA256, policy_document
from pixelgym.platform.s7_predecessors import freeze_predecessors, verify_predecessors
from pixelgym.platform.s7_source import (
    ENDPOINT,
    runtime_lock,
    snapshot,
    verify_host_source,
    verify_runtime,
    verify_snapshot,
    verify_worker_binding,
    worker_binding,
    worker_spec,
    write_json,
)


def read_json(path: Path) -> dict[str, Any]:
    from pixelgym.serialization import canonical_json_bytes

    value: dict[str, Any] = json.loads(path.read_bytes())
    if not isinstance(value, dict) or canonical_json_bytes(value) != path.read_bytes():
        raise ValueError("S7 document must be a canonical JSON object")
    return value


def manifest(
    source: dict[str, Any], lock: dict[str, Any], worker: dict[str, Any]
) -> PolicyManifest:
    # Preserve the frozen parser/reducer/coordinate contracts, replacing fixture identities.
    original = scripted_policy_manifest()
    fields = dict(original.__dict__)
    fields.pop("policy_id")
    fields.update(
        harness_digest=content_digest(source),
        dependency_lock_digest=content_digest(lock),
        system_prompt_digest=content_digest({"scripted_factory": worker}),
        max_model_attempts_per_action=1,
        max_cancellation_requests_per_attempt=0,
        max_reconciliation_requests_per_attempt=0,
        request_deadline_seconds=5.0,
        cancellation_mode="none",
        reconciliation_deadline_seconds=0.0,
        sandbox=replace(
            original.sandbox,
            runtime_digest=lock["executable_sha256"],
            provider_endpoint=ENDPOINT,
            endpoint_allowlist_digest=sandbox_endpoint_allowlist_digest(
                ENDPOINT, policy_version=original.sandbox.network_policy_version
            ),
        ),
        code_revision=source["revision"],
        dirty_worktree_policy="reject",
    )
    return PolicyManifest.build(**fields)


def prepare(
    root: Path,
    run: Path,
    *,
    run_id: str,
    approval: str,
    expected_predecessor_sends: int,
    predecessor_archives: tuple[tuple[Path, str], ...] = (),
) -> dict[str, Any]:
    """Freeze clean source, actions, runtime constraints, and plan before any fake send."""
    if run.exists() or _checkout(run) is not None or not run_id or not approval.strip():
        raise ValueError("new private run outside Git and explicit qualification approval required")
    verify_host_source(root)
    run.mkdir(mode=0o700, parents=True)
    source = snapshot(root, run / "source")
    lock = runtime_lock()
    write_json(run / "source-binding.json", source)
    write_json(run / "runtime-lock.json", lock)
    write_json(run / "gate-policy.json", policy_document())
    cumulative = freeze_predecessors(
        run, predecessor_archives, expected_sends=expected_predecessor_sends
    )
    task = generate_task(SEED)
    with closing(V5FakeBackend()) as backend:
        backend.reset(SEED)
        golden = list(golden_actions(task, backend))
    if len(golden) != 20 or task.max_episode_steps != 26:
        raise ValueError("seed-5000 fixture differs from the approved 20/26 action bounds")
    actions = {
        "A": [{"action_type": 0, "x": 0, "y": 0, "key": 0}],
        "B": golden,
        "premature_commit": list(mutation_trace(task, Mutation.PREMATURE_COMMIT).actions),
        "step_limit": list(mutation_trace(task, Mutation.STEP_BUDGET_EXHAUSTION).actions),
    }
    packages = {}
    for role in ("A", "B"):
        worker = worker_binding(actions[role], source, lock)
        execution = manifest(source, lock, worker)
        packages[role] = {
            "worker": worker,
            "manifest": execution.to_dict(),
            "max_steps": 1 if role == "A" else 26,
            "deployment_attempt_cap": policy_document()["deployment_attempt_caps"][role],
        }
    plan = {
        "schema_version": "pixelgym-s7-qualification-plan-v1",
        "evidence_label": EVIDENCE_LABEL,
        "run_id": run_id,
        "run_reference": "s7-scripted-qualification/" + run_id,
        "qualification_approval": approval,
        "activation_approved": False,
        "publication_approved": False,
        "gate_policy_sha256": POLICY_SHA256,
        "source_binding_sha256": content_digest(source),
        "runtime_lock_sha256": content_digest(lock),
        "seed": SEED,
        "packages": packages,
        "actions": actions,
        "shared_cases": list(CASES),
        "expected_case_sends": [20, 26, 26, 1, 1, 1, 1],
        "qualification_cap": policy_document()["phase_caps"]["qualification"],
        "campaign_cap": policy_document()["campaign_cap"],
        "cumulative_accounting": cumulative,
    }
    write_json(run / "plan.json", plan)
    return plan


def _load_manifest(value: dict[str, Any]) -> PolicyManifest:
    from pixelgym.grounding.v5.contracts import SandboxManifest

    fields = dict(value)
    fields["sandbox"] = SandboxManifest(**fields["sandbox"])
    fields["inference_parameters"] = tuple(tuple(pair) for pair in fields["inference_parameters"])
    return PolicyManifest(**fields)


def qualify(root: Path, run: Path) -> dict[str, Any]:
    """One frozen qualification; existing attempts/results prohibit rerunning it."""
    plan = read_json(run / "plan.json")
    source, lock = read_json(run / "source-binding.json"), read_json(run / "runtime-lock.json")
    if (run / "send-budget.sqlite").exists() or (run / "results.json").exists():
        raise ValueError("S7 qualification already started; do not rerun or reset its cap")
    verify_host_source(root)
    if (
        content_digest(source) != plan["source_binding_sha256"]
        or content_digest(lock) != plan["runtime_lock_sha256"]
        or plan["gate_policy_sha256"] != POLICY_SHA256
        or plan["activation_approved"] is not False
        or plan["publication_approved"] is not False
    ):
        raise ValueError("S7 frozen plan bindings or approval boundary changed")
    verify_snapshot(run / "source", source)
    verify_runtime(lock)
    verify_predecessors(run, plan["cumulative_accounting"])
    budget = S7SendBudget(run / "send-budget.sqlite")
    try:
        return _qualify(root, run, plan, source, lock, budget)
    except BaseException as exc:
        record_failure(run, stage="qualification", cause=exc)
        raise


def _qualify(
    root: Path,
    run: Path,
    plan: dict[str, Any],
    source: dict[str, Any],
    lock: dict[str, Any],
    budget: S7SendBudget,
) -> dict[str, Any]:
    records, workers = [], []
    for case in (*CASES, "baseline"):
        role = "A" if case == "baseline" else "B"
        specification = plan["packages"][role]
        worker = specification["worker"]
        verify_worker_binding(root, source, lock, worker)
        directory = run / "shared" / "cases" / case
        directory.mkdir(parents=True, mode=0o700)
        actions = plan["actions"].get(case, plan["actions"][role])
        actual_worker = worker_binding(actions, source, lock)
        execution = manifest(source, lock, actual_worker)
        if case in {"golden", "baseline"} and execution.to_dict() != specification["manifest"]:
            raise ValueError("qualification execution differs from the prospective package")
        policy = SandboxedPolicyProcess(
            spec=worker_spec(root, run, actual_worker), require_os_sandbox=True
        )
        try:
            workers.append(
                {
                    "case": case,
                    "worker": actual_worker,
                    "manifest": execution.to_dict(),
                    "os_sandbox_applied": policy.os_sandbox_applied,
                    "mechanism": policy.sandbox_mechanism,
                    "profile_sha256": policy.sandbox_profile_digest,
                }
            )
            write_json(directory / "worker.json", workers[-1])
            outcomes = None
            if case in {"malformed", "invalid_action"}:
                outcomes = [
                    TransportOutcome(
                        "response",
                        {
                            "response_id": "fixture",
                            "model": "no-cost-scripted-policy",
                            "content": "not-json"
                            if case == "malformed"
                            else '{"action_type":1,"x":-1,"y":0,"key":0}',
                            "finish_reason": "stop",
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                        },
                    )
                ]
            transport = BudgetedScriptedTransport(budget, phase="qualification", outcomes=outcomes)
            attempts = 1 if case in {"baseline", "call_cap", "malformed", "invalid_action"} else 26
            caps = CallCaps(26, attempts, 0, attempts)
            trial = "retention-" + case
            record: dict[str, Any] = {"case": case, "seed": SEED, "trial_id": trial}
            path = directory / "attempts.sqlite"
            with closing(V5AttemptJournal(path)) as journal:
                runner = V5Runner(
                    journal=journal,
                    manifest=execution,
                    policy=policy,
                    transport=transport,
                    approved_caps=caps,
                    interrupt_after="sealed_action_intent" if case == "resume" else None,
                )
                try:
                    record["episode"] = runner.run(
                        trial_id=trial,
                        task=generate_task(SEED),
                        action_limit=1 if case == "baseline" else None,
                    ).to_dict()
                except InjectedInterruption as exc:
                    if case != "resume":
                        raise
                    record["interruption_boundary"] = exc.boundary
                    record["interrupted_integrity"] = journal.integrity_report()
                except CallCapExceededError:
                    if case != "call_cap":
                        raise
                    record["classification"] = "call_cap_exceeded"
        finally:
            policy.close()
        if case == "resume":
            shutil.copyfile(path, directory / "interrupted.sqlite")
            recovery_transport = BudgetedScriptedTransport(budget, phase="qualification")
            with closing(V5AttemptJournal(path)) as journal, closing(V5FakeBackend()) as backend:
                # No policy request is made on this path, but the exact worker remains enforced.
                recovered_policy = SandboxedPolicyProcess(
                    spec=worker_spec(root, run, actual_worker), require_os_sandbox=True
                )
                try:
                    workers.append(
                        {
                            "case": "resume_recovery",
                            "worker": actual_worker,
                            "manifest": execution.to_dict(),
                            "os_sandbox_applied": recovered_policy.os_sandbox_applied,
                            "mechanism": recovered_policy.sandbox_mechanism,
                            "profile_sha256": recovered_policy.sandbox_profile_digest,
                        }
                    )
                    write_json(directory / "recovery-worker.json", workers[-1])
                    runner = V5Runner(
                        journal=journal,
                        manifest=execution,
                        policy=recovered_policy,
                        transport=recovery_transport,
                        approved_caps=caps,
                    )
                    first = runner.recover_step(
                        trial_id=trial, step_index=0, task=generate_task(SEED), backend=backend
                    )
                    second = runner.recover_step(
                        trial_id=trial, step_index=0, task=generate_task(SEED), backend=backend
                    )
                    record["recovery"] = {
                        "classification": first["classification"],
                        "second_classification": second["classification"],
                        "environment_actions": backend.action_count,
                        "additional_sends": len(recovery_transport.model_requests),
                        "additional_control_requests": len(recovery_transport.control_requests),
                    }
                finally:
                    recovered_policy.close()
        record["scripted_sends"] = len(transport.model_requests)
        record["scripted_control_requests"] = len(transport.control_requests)
        records.append(record)
        write_json(directory / "observation.json", record)
    shared = {"schema_version": SCHEMA, "cases": records[:-1]}
    write_json(run / "shared" / "observations.json", shared)
    budget.pause_for_owner_review()
    results = {
        "schema_version": "pixelgym-s7-qualification-results-v1",
        "plan_sha256": content_digest(plan),
        "baseline": records[-1],
        "shared": shared,
        "workers": workers,
        "fake_sends": budget.counts(),
        "transport": "BudgetedScriptedTransport",
        "real_provider_requests": 0,
        "provider_control_requests": sum(r["scripted_control_requests"] for r in records),
        "monetary_spend_usd": 0,
        "activation_performed": False,
        "publication_performed": False,
    }
    write_json(run / "results.json", results)
    return results


def seal(run: Path) -> dict[str, Any]:
    """Seal final qualification bytes; callers must have stopped all writers first."""
    from pixelgym.platform.s7_evidence import inventory

    value = inventory(run)
    write_json(run / "seal.json", value)
    return value


def record_failure(run: Path, *, stage: str, cause: BaseException) -> None:
    """Retain a terminal failure after writers stop; never resume or reset its ledger."""
    if stage not in {"qualification", "finalization"} or (run / "seal.json").exists():
        raise ValueError("only unsealed S7 execution can acquire a terminal failure record")
    counts = S7SendBudget(run / "send-budget.sqlite").counts()
    write_json(
        run / "failure.json",
        {
            "schema_version": "pixelgym-s7-failure-v1",
            "qualification_complete": False,
            "activation_performed": False,
            "publication_performed": False,
            "source_revision": read_json(run / "source-binding.json")["revision"],
            "stage": stage,
            # Exception text may contain provider content or private paths. Retain a type only.
            "cause": type(cause).__name__,
            "fake_send_reservations": counts.get("qualification", 0),
            "real_provider_requests": 0,
            "monetary_spend_usd": 0,
        },
    )
    seal(run)
