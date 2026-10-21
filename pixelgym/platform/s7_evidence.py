"""Stored S7 evidence inspection, action replay, package construction, and reporting."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import numpy as np

from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import TERMINAL_ATTEMPT_KINDS, V5AttemptJournal
from pixelgym.grounding.v5.retention import _files, _hash
from pixelgym.grounding.v5.retention_validation import CASES, SEED, replay_campaign
from pixelgym.platform.contracts import ArtifactRef
from pixelgym.platform.immutable_store import ImmutableStore
from pixelgym.platform.s7_policy import (
    EVIDENCE_LABEL,
    POLICY_SHA256,
    QUALIFICATION_CAP,
    evaluate,
    policy_document,
    validate_report_rules,
)
from pixelgym.platform.s7_qualification import _load_manifest, read_json
from pixelgym.platform.s7_source import verify_snapshot, write_json
from pixelgym.platform.stateful_contracts import (
    EvidenceBinding,
    EvidenceClass,
    StatefulPolicyPackage,
)
from pixelgym.platform.stateful_control import StatefulGateReport, stateful_package_from_dict
from pixelgym.serialization import canonical_json_bytes


def file_identity(path: Path) -> dict[str, str]:
    """SQLite identity survives backup-API normalization; other bytes are exact."""
    with path.open("rb") as handle:
        sqlite = handle.read(16) == b"SQLite format 3\x00"
    if not sqlite:
        return {"kind": "bytes", "sha256": _hash(path)}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        if list(db.execute("PRAGMA integrity_check")) != [("ok",)]:
            raise ValueError("S7 SQLite integrity check failed")
        return {"kind": "sqlite-logical", "sha256": content_digest(list(db.iterdump()))}


def inventory(run: Path) -> dict[str, Any]:
    return {
        "schema_version": "pixelgym-s7-seal-v1",
        "files": {
            name: file_identity(path) for name, path in _files(run).items() if name != "seal.json"
        },
    }


def verify_seal(run: Path) -> None:
    if inventory(run) != read_json(run / "seal.json"):
        raise ValueError("S7 sealed evidence changed or contains missing/extra files")


def replay_case(run: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Dispatch stored actions only. Never construct a policy or transport."""
    task = generate_task(SEED)
    backend = V5FakeBackend()
    env = PixelGuiEnv(
        backend, instruction=task.instruction, max_episode_steps=task.max_episode_steps
    )
    rewards, differences = [], []
    trial = record["trial_id"]
    with closing(
        V5AttemptJournal(run / "shared/cases" / record["case"] / "attempts.sqlite", read_only=True)
    ) as journal:
        try:
            observation, _ = env.reset(seed=SEED)
            initial = journal.event(trial + "/initial_screenshot")
            if initial is None or observation.tobytes() != journal.get_object(
                initial.payload["screenshot_digest"]
            ):
                raise ValueError("S7 initial screenshot differs from replay")
            for event in journal.events(trial):
                if event.kind != "dispatch_committed":
                    continue
                intent = journal.event(f"{trial}/step-{event.step_index:04d}/sealed_action_intent")
                if intent is None:
                    raise ValueError("S7 dispatch has no action intent")
                action = json.loads(journal.get_object(intent.payload["action_digest"]))
                observation, reward, terminated, truncated, _ = env.step(action)
                stored = np.frombuffer(
                    journal.get_object(event.payload["screenshot_digest"]), dtype=np.uint8
                ).reshape(observation.shape)
                delta = np.abs(observation.astype(np.int16) - stored.astype(np.int16))
                comparison = {
                    "step_index": event.step_index,
                    "differing_pixels": int(np.count_nonzero(np.any(delta != 0, axis=2))),
                    "max_channel_delta": int(delta.max()),
                }
                differences.append(comparison)
                if (
                    comparison["differing_pixels"]
                    or reward != event.payload["reward"]
                    or terminated != event.payload["terminated"]
                    or truncated != event.payload["truncated"]
                    or backend.checkpoint()
                    != journal.get_object(event.payload["environment_checkpoint_digest"])
                ):
                    raise ValueError(f"S7 dispatch replay mismatch: {comparison}")
                rewards.append(reward)
            events = journal.events(trial)
            starts = [e for e in events if e.kind == "attempt_started"]
            terminals = [e for e in events if e.kind in TERMINAL_ATTEMPT_KINDS]
            unresolved = len(starts) - len(terminals)
            if any(e.kind == "unknown_outcome_infrastructure_failure" for e in terminals):
                unresolved += 1
            return {
                "case": record["case"],
                "replayed_dispatches": len(rewards),
                "rewards": rewards,
                "screenshot_differences": differences,
                "attempts": len(starts),
                "unresolved_attempts": unresolved,
                "duplicate_event_keys": len(events) - len({e.event_key for e in events}),
                "journal_integrity": journal.integrity_report(),
            }
        finally:
            env.close()


def inspect_qualification(run: Path) -> dict[str, Any]:
    from pixelgym.platform.s7_predecessors import verify_predecessors

    plan, results = read_json(run / "plan.json"), read_json(run / "results.json")
    previous = verify_predecessors(run, plan["cumulative_accounting"])
    source, lock = read_json(run / "source-binding.json"), read_json(run / "runtime-lock.json")
    verify_snapshot(run / "source", source)
    if results["plan_sha256"] != content_digest(plan) or (
        plan["source_binding_sha256"] != content_digest(source)
        or plan["runtime_lock_sha256"] != content_digest(lock)
        or read_json(run / "gate-policy.json") != policy_document()
        or results["shared"] != read_json(run / "shared/observations.json")
    ):
        raise ValueError("S7 qualification input bindings differ")
    records = [*results["shared"]["cases"], results["baseline"]]
    if [r["case"] for r in records] != [*CASES, "baseline"]:
        raise ValueError("S7 assigned cases are missing, duplicate, extra, or reordered")
    if (
        [r["scripted_sends"] for r in records] != [20, 26, 26, 1, 1, 1, 1, 1]
        or results["fake_sends"] != {"qualification": QUALIFICATION_CAP}
        or results["transport"] != "BudgetedScriptedTransport"
        or results["real_provider_requests"] != 0
        or results["monetary_spend_usd"] != 0
        or results["provider_control_requests"] != 0
        or results["activation_performed"] is not False
        or results["publication_performed"] is not False
    ):
        raise ValueError("S7 transport accounting or approval boundary differs")
    with closing(
        sqlite3.connect(
            (run / "send-budget.sqlite").resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        )
    ) as db:
        if dict(db.execute("SELECT phase, count(*) FROM sends GROUP BY phase")) != {
            "qualification": QUALIFICATION_CAP
        }:
            raise ValueError("S7 durable fake-send ledger differs from observations")
        reserved_keys = {row[0] for row in db.execute("SELECT request_key FROM sends")}
    journal_keys: set[str] = set()
    for record in records:
        with closing(
            V5AttemptJournal(
                run / "shared/cases" / record["case"] / "attempts.sqlite", read_only=True
            )
        ) as journal:
            journal_keys.update(
                e.payload["idempotency_key"]
                for e in journal.events(record["trial_id"])
                if e.kind == "attempt_started"
            )
    if reserved_keys != journal_keys:
        raise ValueError("S7 campaign reservations are not the recorded runner requests")
    shared = replay_campaign(run / "shared")
    replays = [replay_case(run, record) for record in records]
    if len(results["workers"]) != 9 or [w["case"] for w in results["workers"]] != [
        *CASES,
        "resume_recovery",
        "baseline",
    ]:
        raise ValueError("S7 worker allocation differs")
    for worker in results["workers"]:
        if (
            worker["os_sandbox_applied"] is not True
            or worker["mechanism"] != "darwin_sandbox_exec"
            or not isinstance(worker["profile_sha256"], str)
            or not worker["profile_sha256"].startswith("sha256:")
        ):
            raise ValueError("S7 qualification lacks enforced worker sandbox evidence")
        role = "A" if worker["case"] == "baseline" else "B"
        if worker["case"] in {"baseline", "golden"} and (
            worker["worker"] != plan["packages"][role]["worker"]
            or worker["manifest"] != plan["packages"][role]["manifest"]
        ):
            raise ValueError("qualification worker is not the prospective package")
    return {
        "schema_version": "pixelgym-s7-verification-v1",
        "results_sha256": content_digest(results),
        "source_binding_sha256": content_digest(source),
        "shared": shared,
        "replays": replays,
        "cumulative_qualification_sends": previous + QUALIFICATION_CAP,
        "cumulative_accounting": plan["cumulative_accounting"],
    }


def derive_metrics(
    role: str, results: dict[str, Any], verification: dict[str, Any]
) -> dict[str, Any]:
    """Derive the approved quantities from stored detailed results and replay observations."""
    if verification["results_sha256"] != content_digest(results):
        raise ValueError("S7 replay result binding differs")
    replays = verification["replays"]
    if [r["case"] for r in replays] != [*CASES, "baseline"]:
        raise ValueError("S7 replay assignment differs")
    target = replays[-1] if role == "A" else replays[0]
    episode = (
        results["baseline"]["episode"] if role == "A" else results["shared"]["cases"][0]["episode"]
    )
    expected_rewards = [0.0] if role == "A" else [0.0] * 19 + [1.0]
    outcome = (
        target["rewards"] == expected_rewards
        and episode["environment_actions"] == len(expected_rewards)
        and episode["classification"]
        == ("pilot_action_limit" if role == "A" else "success_termination")
        and episode["success"] is (role == "B")
    )
    return {
        "assigned_episode_coverage": 1.0
        if len(target["rewards"]) == len(expected_rewards)
        else 0.0,
        "package_outcome_matches_plan": int(outcome),
        "shared_control_cases_matched": verification["shared"]["case_count"],
        "invalid_backend_dispatches": sum(
            r["replayed_dispatches"]
            for r in replays
            if r["case"] in {"malformed", "invalid_action"}
        ),
        "duplicate_attempts_or_dispatches": sum(r["duplicate_event_keys"] for r in replays)
        + results["shared"]["cases"][-1]["recovery"]["additional_sends"],
        "replay_mismatches": sum(
            int(d["differing_pixels"] != 0 or d["max_channel_delta"] != 0)
            for r in replays
            for d in r["screenshot_differences"]
        ),
        "unresolved_attempt_outcomes": sum(r["unresolved_attempts"] for r in replays),
        "artifact_verification_failures": int(
            verification["results_sha256"] != content_digest(results)
        ),
        "real_provider_requests": results["real_provider_requests"],
        "provider_control_requests": results["provider_control_requests"],
        "monetary_spend_usd": results["monetary_spend_usd"],
    }


def build_packages(run: Path, verification: dict[str, Any]) -> dict[str, Any]:
    plan, results = read_json(run / "plan.json"), read_json(run / "results.json")
    source, lock = read_json(run / "source-binding.json"), read_json(run / "runtime-lock.json")
    validate_recorded_execution(plan, results)
    write_json(run / "verification.json", verification)
    packages = {}
    for role in ("A", "B"):
        spec = plan["packages"][role]
        manifest = _load_manifest(spec["manifest"])
        summary = {
            "schema_version": "pixelgym-s7-summary-v1",
            "evidence_label": EVIDENCE_LABEL,
            "role": role,
            "metrics": derive_metrics(role, results, verification),
            "binding": {
                "plan_sha256": content_digest(plan),
                "run_reference": plan["run_reference"],
                "v5_policy_id": manifest.policy_id,
                "package_source_sha256": content_digest(spec["worker"])[7:],
            },
            "inputs": {
                "results_sha256": content_digest(results),
                "verification_sha256": content_digest(verification),
                "source_binding_sha256": content_digest(source),
                "runtime_lock_sha256": content_digest(lock),
            },
        }
        package = StatefulPolicyPackage.build(
            manifest=manifest,
            model_alias_disclosure=None,
            package_source_sha256=content_digest(spec["worker"])[7:],
            dependency_lock_sha256=content_digest(lock)[7:],
            max_steps=spec["max_steps"],
            evidence_class=EvidenceClass.CALIBRATION,
            evidence=EvidenceBinding(
                run_kind="d56-calibration",
                run_reference=plan["run_reference"],
                plan_sha256=content_digest(plan),
                summary_sha256=content_digest(summary),
                assigned_episodes=1,
                attempted_episodes=1,
                exact_successes=int(role == "B"),
            ),
            code_revision=source["revision"],
            code_state="clean",
            source_tree_sha256=content_digest(source["files"])[7:],
            source_provenance_verified=True,
            source_provenance_failure_reason=None,
        )
        report = evaluate(package, summary)
        write_json(run / f"packages/{role}/summary.json", summary)
        write_json(run / f"packages/{role}/package.json", package.to_dict())
        write_json(run / f"packages/{role}/gate-report.json", report.to_dict())
        packages[role] = {
            "policy_id": package.policy_id,
            "package_sha256": content_digest(package.to_dict()),
            "gate_report_sha256": content_digest(report.to_dict()),
            "gate_overall_passed": report.overall_passed,
            "proposed_terms": {
                "deployment_tier": "demo",
                "deployment_attempt_cap": spec["deployment_attempt_cap"],
            },
        }
    return packages


def validate_recorded_execution(plan: dict[str, Any], results: dict[str, Any]) -> None:
    """Validate execution authority and isolation without constructing execution clients."""
    if (
        results["plan_sha256"] != content_digest(plan)
        or results["transport"] != "BudgetedScriptedTransport"
        or results["fake_sends"] != {"qualification": QUALIFICATION_CAP}
        or results["real_provider_requests"] != 0
        or results["provider_control_requests"] != 0
        or results["monetary_spend_usd"] != 0
        or results["activation_performed"] is not False
        or results["publication_performed"] is not False
        or plan["gate_policy_sha256"] != POLICY_SHA256
    ):
        raise ValueError("S7 recorded execution authority or accounting differs")
    if [w["case"] for w in results["workers"]] != [*CASES, "resume_recovery", "baseline"]:
        raise ValueError("S7 recorded worker allocation differs")
    for worker in results["workers"]:
        if worker["os_sandbox_applied"] is not True or worker["mechanism"] != "darwin_sandbox_exec":
            raise ValueError("S7 recorded execution lacks OS enforcement")


def gate_verifier(
    store: ImmutableStore,
    *,
    qualification_root: Path,
) -> Callable[[StatefulPolicyPackage, StatefulGateReport, tuple[ArtifactRef, ...]], None]:
    """Verify the stored S7 derivation at every control-plane mutation boundary."""

    def verify(
        package: StatefulPolicyPackage,
        report: StatefulGateReport,
        artifacts: tuple[ArtifactRef, ...],
    ) -> None:
        verify_seal(qualification_root)
        _verify_bound_evidence(store, qualification_root, package, report, artifacts)

    return verify


def _verify_bound_evidence(
    store: ImmutableStore,
    run: Path,
    package: StatefulPolicyPackage,
    report: StatefulGateReport,
    artifacts: tuple[ArtifactRef, ...],
) -> None:
    if (run / "failure.json").exists():
        raise ValueError("S7 stopped qualification cannot supply approval evidence")
    validate_report_rules(report)
    values: dict[str, dict[str, Any]] = {}
    for reference in artifacts:
        data = store.get_verified(reference)
        value = json.loads(data)
        if not isinstance(value, dict) or canonical_json_bytes(value) != data:
            raise ValueError("S7 evidence must be canonical JSON")
        values[content_digest(value)] = value

    def required(digest: str) -> dict[str, Any]:
        if digest not in values:
            raise ValueError("S7 candidate artifacts omit bound evidence: " + digest)
        return values[digest]

    summary = required(package.evidence.summary_sha256)
    plan = required(package.evidence.plan_sha256)
    inputs = summary["inputs"]
    results = required(inputs["results_sha256"])
    verification = required(inputs["verification_sha256"])
    source = required(inputs["source_binding_sha256"])
    lock = required(inputs["runtime_lock_sha256"])
    validate_recorded_execution(plan, results)
    if any(
        value != read_json(run / name)
        for name, value in (
            ("plan.json", plan),
            ("results.json", results),
            ("verification.json", verification),
            ("source-binding.json", source),
            ("runtime-lock.json", lock),
        )
    ):
        raise ValueError("S7 immutable receipt differs from the sealed private evidence")
    role = summary["role"]
    if role not in {"A", "B"}:
        raise ValueError("S7 stored report has an unsupported package role")
    spec = plan["packages"][role]
    if (
        summary["metrics"] != derive_metrics(role, results, verification)
        or evaluate(package, summary).to_dict() != report.to_dict()
        or content_digest(spec["worker"])[7:] != package.package_source_sha256
        or spec["manifest"] != package.v5_manifest().to_dict()
        or spec["max_steps"] != package.max_steps
        or source["revision"] != package.code_revision
        or content_digest(source["files"])[7:] != package.source_tree_sha256
        or content_digest(source) != plan["source_binding_sha256"]
        or content_digest(source) != verification["source_binding_sha256"]
        or content_digest(lock) != plan["runtime_lock_sha256"]
        or content_digest(lock)[7:] != package.dependency_lock_sha256
    ):
        raise ValueError("S7 stored report does not follow its bound evidence")


def _verify_packages(run: Path) -> None:
    """Validate artifacts before the finalizer seals; never use for control mutations."""
    from pixelgym.platform.immutable_store import LocalImmutableStore

    store = LocalImmutableStore(run / "immutable")
    refs = tuple(ArtifactRef(**v) for v in read_json(run / "artifact-references.json")["artifacts"])
    for role in ("A", "B"):
        package = stateful_package_from_dict(read_json(run / f"packages/{role}/package.json"))
        report = StatefulGateReport.from_dict(read_json(run / f"packages/{role}/gate-report.json"))
        _verify_bound_evidence(store, run, package, report, refs)


def verify_packages(run: Path) -> None:
    """Check sealed report derivations without rerunning a worker or transport."""
    verify_seal(run)
    _verify_packages(run)


def store_artifacts(run: Path) -> None:
    from pixelgym.platform.immutable_store import LocalImmutableStore

    store = LocalImmutableStore(run / "immutable")
    names = [
        "plan.json",
        "results.json",
        "verification.json",
        "source-binding.json",
        "runtime-lock.json",
        "gate-policy.json",
    ]
    names.extend(f"packages/{role}/summary.json" for role in ("A", "B"))
    references = [
        store.put_once("s7/" + name, (run / name).read_bytes(), media_type="application/json")
        for name in names
    ]
    from dataclasses import asdict

    write_json(run / "artifact-references.json", {"artifacts": [asdict(r) for r in references]})


def stored_report(run: Path) -> dict[str, Any]:
    """Read stored results and reports only; never replay actions or recompute gates."""
    verify_seal(run)
    if (run / "failure.json").exists():
        from pixelgym.platform.s7_predecessors import stopped_count

        failure = read_json(run / "failure.json")
        count = stopped_count(run)
        completed = [
            read_json(path) for path in sorted((run / "shared/cases").glob("*/observation.json"))
        ]
        return {
            "schema_version": "pixelgym-s7-stopped-review-report-v1",
            "evidence_label": EVIDENCE_LABEL,
            "qualification_complete": False,
            "plan_sha256": content_digest(read_json(run / "plan.json")),
            "failure": failure,
            "completed_cases": completed,
            "fake_sends": {"qualification": count},
            "real_provider_requests": failure["real_provider_requests"],
            "activation_performed": False,
            "publication_performed": False,
            "milestone_verdict": None,
        }
    packages = {}
    for role in ("A", "B"):
        package = stateful_package_from_dict(read_json(run / f"packages/{role}/package.json"))
        gate = StatefulGateReport.from_dict(read_json(run / f"packages/{role}/gate-report.json"))
        validate_report_rules(gate)
        if gate.binding_errors(package):
            raise ValueError("S7 stored gate/package binding differs")
        packages[role] = {
            "policy_id": package.policy_id,
            "package_sha256": content_digest(package.to_dict()),
            "gate_report_sha256": content_digest(gate.to_dict()),
            "gate": gate.to_dict(),
        }
    results = read_json(run / "results.json")
    return {
        "schema_version": "pixelgym-s7-review-report-v1",
        "evidence_label": EVIDENCE_LABEL,
        "gate_policy_sha256": POLICY_SHA256,
        "packages": packages,
        "fake_sends": results["fake_sends"],
        "real_provider_requests": results["real_provider_requests"],
        "activation_performed": results["activation_performed"],
        "publication_performed": results["publication_performed"],
        "milestone_verdict": None,
        "cumulative_accounting": read_json(run / "plan.json").get("cumulative_accounting"),
    }


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# S7 scripted qualification review",
        "",
        (
            "Generated from stored evidence. "
            "Activation and publication await owner approval. No milestone verdict."
        ),
        "",
    ]
    if report.get("qualification_complete") is False:
        failure = report["failure"]
        lines.extend(
            [
                "Qualification stopped; no package or gate report is usable for approval.",
                "",
                f"Frozen plan: `{report['plan_sha256']}`",
                "",
                f"Source revision: `{failure['source_revision']}`",
                "",
                f"Stopped at: `{failure['stage']}`. Cause: {failure['cause']}.",
                "",
                f"Fake-send reservations: {failure['fake_send_reservations']}.",
                "",
                "| Completed case | Stored classification | Fake sends |",
                "| --- | --- | ---: |",
            ]
        )
        for case in report["completed_cases"]:
            classification = case.get(
                "classification", case.get("episode", {}).get("classification")
            )
            lines.append(f"| {case['case']} | {classification} | {case['scripted_sends']} |")
        lines.extend(
            [
                "",
                (
                    f"Real-provider requests: {failure['real_provider_requests']}. "
                    f"Recorded spending (USD): {failure['monetary_spend_usd']}."
                ),
                "",
            ]
        )
        return "\n".join(lines)
    for role, entry in report["packages"].items():
        lines.extend(
            [
                f"## Package {role}",
                "",
                f"Policy ID: `{entry['policy_id']}`",
                "",
                f"Package digest: `{entry['package_sha256']}`",
                "",
                f"Gate report digest: `{entry['gate_report_sha256']}`",
                "",
                f"Code revision passed: {entry['gate']['code_revision_passed']}.",
                "",
                f"Stored gate overall_passed: {entry['gate']['overall_passed']}.",
                "",
                "Stored reasons: " + ("; ".join(entry["gate"]["reasons"]) or "none"),
                "",
                "| Observation | Stored value | Comparator | Threshold | Passed |",
                "| --- | ---: | --- | ---: | --- |",
            ]
        )
        for observation in entry["gate"]["observations"]:
            lines.append(
                f"| {observation['name']} | {observation['observed']} | "
                f"{observation['comparator']} | {observation['threshold']} | "
                f"{observation['passed']} |"
            )
        lines.append("")
    accounting = report.get("cumulative_accounting")
    if accounting:
        previous = accounting["previous_qualification_sends"]
        lines.extend(
            [
                (
                    f"Earlier stopped qualifications: {previous} fake sends. "
                    f"Cumulative qualification sends: {previous + QUALIFICATION_CAP} "
                    f"of {accounting['qualification_allowance']}. "
                    f"Overall campaign ceiling: {accounting['campaign_allowance']}."
                ),
                "",
            ]
        )
    lines.extend(
        [
            (
                f"Fake sends: {report['fake_sends']}. Real-provider requests: "
                f"{report['real_provider_requests']}. "
                "Real-provider requests and spending are declared constants of the "
                "fixture-only boundary, not independent network or billing measurements."
            ),
            "",
            (
                "The golden script is privileged "
                "fixture data. Calibration is a schema compatibility label. These results "
                "do not measure a model or reconstruct the missing historical D5.9 journals."
            ),
            "",
        ]
    )
    return "\n".join(lines)
