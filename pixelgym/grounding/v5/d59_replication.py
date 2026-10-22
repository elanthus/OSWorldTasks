"""No-call freeze of the D5.9 replication: a successor campaign run beside D5.9.

The replication keeps the D5.9 tasks, statistical design, caps, and runtime window by
digest, and changes four things, each pre-registered here before any provider call:

1. The policy pair runs under the kernel sandbox with the egress proxy
   (docs/v5-policy-sandbox.md), so every launch record carries ``os_sandbox_applied: true``.
2. The route is the installed CLI with token authentication (``REPLICATION_ROUTE``).
3. The stop rule is the amended D5.9 rule, frozen up front: per-episode failures count and
   the campaign continues; only caps, the runtime window, a blocked ledger, or repeated
   infrastructure failures stop it.
4. The reporting rule is fixed before the run: the replication is the headline because its
   journals are retained, D5.9 remains the pre-registered primary reported unchanged, and a
   disagreement larger than the 20-point minimum relevant difference is a finding.

D5.9 itself is not rerun, replayed, or reclassified.
"""

from __future__ import annotations

import hashlib
import queue
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.cli_memory_calibration import build_memory_manifest
from pixelgym.grounding.v5.contracts import CallCaps, PolicyManifest, content_digest
from pixelgym.grounding.v5.d59_haiku_retry_successor import (
    _binding,
    _read,
    _sha256_at_revision,
    _validate_policy_inputs,
)
from pixelgym.grounding.v5.egress_proxy import egress_allowlist_digest
from pixelgym.grounding.v5.os_sandbox import cli_profile_template_digest
from pixelgym.grounding.v5.sandbox import OS_SANDBOX_CLI_LAUNCH_MECHANISM

PREDECESSOR_PATH = "artifacts/grounding-v5-d59-haiku-network-retry/execution-plan.json"
PREDECESSOR_DIGEST = "sha256:3f50d15f72244cfc8fd422cd16ab8dc6ab8edd873f6c9a92375472671fdcd092"
D59_RESULTS_PATH = "artifacts/grounding-v5-d59-haiku-results/report.json"
D59_ANALYSIS_PATH = "artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.json"
PRE_REGISTRATION_PATH = "artifacts/grounding-v5-d59-freeze/analysis-plan.md"
OUTPUT_DIRECTORY = "artifacts/grounding-v5-d59-replication"
SOURCE_FILES = (
    "pixelgym/grounding/v5/d59_replication.py",
    "pixelgym/grounding/v5/claude_code_policy.py",
    "pixelgym/grounding/v5/os_sandbox.py",
    "pixelgym/grounding/v5/egress_proxy.py",
    "pixelgym/grounding/v5/sandbox.py",
    "scripts/prepare_grounding_v5_d59_replication.py",
    "scripts/run_grounding_v5_d59_replication.py",
)
TRIAL_PREFIX = "d59-rep-"
PILOT_TRIAL_PREFIX = "d59-rep-pilot-"
PILOT_SEED = 6000
PILOT_WINDOW_HOURS = 6
STOP_RULE_VERSION = "pixelgym-agent-v5-d59-replication-stop-rule-v1"
CONSECUTIVE_INFRASTRUCTURE_FAILURE_LIMIT = 3
EPISODE_OUTCOMES_THAT_CONTINUE = (
    "success_termination",
    "step_limit_truncation",
    "invalid_output",
    "infrastructure_failure",
)
CAMPAIGN_STOPS_ON = (
    "approved_cap_reached",
    "runtime_window_exhausted",
    "invocation_ledger_blocked",
    "consecutive_infrastructure_failures",
    "execution_error",
)
DISAGREEMENT_THRESHOLD_POINTS = 20
# Concurrency is pre-registered: it changes timing and rate-limit exposure, never the
# denominator. Each worker owns a transport (its own egress proxy and invocation journal);
# the attempt journal file, the ledger, the caps, and the window are shared.
MAX_WORKERS = 4
OWNER_STATEMENTS = (
    ("2026-10-21", "Replication sounds good."),
    (
        "2026-10-21",
        "We will use the latest version of the amended stop rule for the replication, right?",
    ),
    ("2026-10-22", "yes build the freeze"),
)


def _caps(jobs: list[dict[str, Any]]) -> dict[str, int]:
    return CallCaps.calculate(
        max_episode_steps=tuple(int(job["action_limit"]) for job in jobs),
        max_model_attempts_per_action=2,
        max_cancellation_requests_per_attempt=0,
        max_reconciliation_requests_per_attempt=0,
    ).to_dict()


def live_manifests(
    root: Path, *, source_revision: str, identity: claude.ClaudeRuntimeIdentity
) -> dict[str, PolicyManifest]:
    """The sandboxed, token-route policy pair; a new policy ID for each condition."""

    if identity.route != claude.REPLICATION_ROUTE:
        raise ValueError("replication manifests require the replication route")
    base = claude.build_claude_policy_manifest(
        root,
        code_revision=source_revision,
        runtime_identity=identity,
        resolved_model=claude.MODEL,
        api_retry_limit=0,
        sandboxed_launch=True,
    )
    return {
        mode: build_memory_manifest(
            root, base, retain_screenshots=mode == "history", allow_connection_retry=True
        )
        for mode in ("history", "stateless")
    }


def _rename(jobs: list[dict[str, Any]], old: str, new: str) -> list[dict[str, Any]]:
    renamed = []
    for source in jobs:
        job = dict(source)
        trial = str(job["trial_id"])
        if not trial.startswith(old):
            raise ValueError("predecessor trial ID does not use the expected namespace")
        job["trial_id"] = new + trial[len(old) :]
        renamed.append(job)
    if len({job["trial_id"] for job in renamed}) != len(renamed):
        raise ValueError("replication trial IDs are not unique")
    return renamed


def pilot_jobs(
    primary: list[dict[str, Any]], reliability: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Four assignments: both arms of the pilot seed, primary and first reliability repeat."""

    selected = [
        job for job in primary if int(job["seed"]) == PILOT_SEED and int(job["repeat"]) == 0
    ] + [job for job in reliability if int(job["seed"]) == PILOT_SEED and int(job["repeat"]) == 1]
    if len(selected) != 4 or {str(job["mode"]) for job in selected} != {"history", "stateless"}:
        raise ValueError("pilot seed does not yield both arms in both phases")
    return _rename(selected, TRIAL_PREFIX, PILOT_TRIAL_PREFIX)


def execution_plan(
    root: Path, *, source_revision: str, runtime_identity: claude.ClaudeRuntimeIdentity
) -> dict[str, Any]:
    predecessor = _read(root, PREDECESSOR_PATH)
    body = {k: v for k, v in predecessor.items() if k != "execution_plan_digest"}
    if (
        content_digest(body) != PREDECESSOR_DIGEST
        or predecessor.get("execution_plan_digest") != PREDECESSOR_DIGEST
    ):
        raise ValueError("replication predecessor changed")
    results = _read(root, D59_RESULTS_PATH)
    if results.get("execution_plan_digest") != PREDECESSOR_DIGEST:
        raise ValueError("D5.9 results do not bind the predecessor plan")
    if results.get("assigned") != 432 or results.get("completed") != 432:
        raise ValueError("D5.9 results are not the completed 432-assignment projection")
    analysis = _read(root, D59_ANALYSIS_PATH)
    _validate_policy_inputs(root, source_revision)
    source = {path: _sha256_at_revision(root, source_revision, path) for path in SOURCE_FILES}
    for path, digest in source.items():
        if "sha256:" + hashlib.sha256((root / path).read_bytes()).hexdigest() != digest:
            raise ValueError(f"execution source differs from revision: {path}")
    if runtime_identity.route != claude.REPLICATION_ROUTE:
        raise ValueError("replication requires the token route on the installed CLI")
    manifests = live_manifests(root, source_revision=source_revision, identity=runtime_identity)
    policies = {mode: manifest.to_dict() for mode, manifest in manifests.items()}
    predecessor_ids = {value["policy_id"] for value in predecessor["policy_manifests"].values()}
    if any(value["policy_id"] in predecessor_ids for value in policies.values()):
        raise ValueError("replication policy identity did not change")
    for value in policies.values():
        enforcement = value["sandbox"]["runtime_enforcement"]
        if (
            enforcement["mechanism_name"] != OS_SANDBOX_CLI_LAUNCH_MECHANISM
            or enforcement["os_sandbox_applied"] is not True
            or dict(value["inference_parameters"]).get("cli_api_retry_limit") != "0"
        ):
            raise ValueError("replication policy is not a sandboxed zero-retry launch")

    primary = _rename(predecessor["primary_jobs"], "d59-haiku-r3-", TRIAL_PREFIX)
    reliability = _rename(predecessor["reliability_jobs"], "d59-haiku-r3-", TRIAL_PREFIX)
    pilot = pilot_jobs(primary, reliability)
    value = deepcopy(body)
    value.update(
        schema_version="pixelgym-agent-v5-d59-replication-plan-v1",
        status="frozen_candidate_awaiting_exact_subscription_execution_approval",
        execution_enabled=False,
        subscription_execution_authorized=False,
        approved_model_attempt_cap=0,
        approved_provider_wire_request_cap=0,
        provider_calls_made_during_successor_preparation=0,
        owner_direction={
            "statements": [
                {"recorded_at": date, "statement": text} for date, text in OWNER_STATEMENTS
            ],
            "interpretation": (
                "Run a pre-registered replication beside D5.9 under the amended stop rule, "
                "the kernel sandbox, and the token route. D5.9 is reported unchanged."
            ),
        },
        relationship={
            "kind": "replication_beside_d59",
            "d59_execution_plan": _binding(root, PREDECESSOR_PATH, predecessor),
            "d59_results": _binding(root, D59_RESULTS_PATH, results),
            "d59_confirmatory_analysis": _binding(root, D59_ANALYSIS_PATH, analysis),
            "pre_registration_record": PRE_REGISTRATION_PATH,
            "outcomes_reused": 0,
            "assignments_replayed": 0,
            "d59_disposition": "pre_registered_primary_reported_unchanged",
        },
        predecessor={
            "execution_plan": _binding(root, PREDECESSOR_PATH, predecessor),
            "outcomes_reused": 0,
            "assignments_replayed": 0,
            "disposition": "prior_campaigns_preserved_without_reclassification",
        },
        source_binding={"source_revision": source_revision, "files": source},
        runtime_identity=runtime_identity.to_dict(),
        route={
            "cli_version": runtime_identity.cli_version,
            "auth_method": runtime_identity.auth_method,
            "subscription_type": runtime_identity.subscription_type,
            "login_source": (
                f"{claude.OAUTH_TOKEN_ENVIRONMENT_VARIABLE} in the runner environment, created "
                "by `claude setup-token`; never journaled"
            ),
            "confirmed_before_execution": (
                "`claude auth status --json` under the launch environment must report "
                "loggedIn and the declared authMethod before any provider call"
            ),
        },
        policy_manifests=policies,
        security_boundary={
            "mechanism": OS_SANDBOX_CLI_LAUNCH_MECHANISM,
            "os_sandbox_applied": {"history": True, "stateless": True},
            "profile_template_digest": cli_profile_template_digest(),
            "egress_targets": list(claude.DEFAULT_EGRESS_TARGETS),
            "egress_allowlist_digest": egress_allowlist_digest(claude.DEFAULT_EGRESS_TARGETS),
            "supported_claim": (
                "the kernel denied every channel except one loopback port; the runner's proxy "
                "allowed only the frozen provider target and journaled every attempt"
            ),
            "unsupported_claim": (
                "hostname filtering is runner code, not kernel policy; the CLI can list the "
                "shared scratch root"
            ),
            "design_record": "docs/v5-policy-sandbox.md",
        },
        stop_rule={
            "version": STOP_RULE_VERSION,
            "episode_outcomes_that_continue": list(EPISODE_OUTCOMES_THAT_CONTINUE),
            "campaign_stops_on": list(CAMPAIGN_STOPS_ON),
            "consecutive_infrastructure_failure_limit": (CONSECUTIVE_INFRASTRUCTURE_FAILURE_LIMIT),
            "assignments_replayed": 0,
            "post_result_changes_permitted": False,
            "origin": (
                "the D5.9 continuation rule "
                "malformed-output-or-exhausted-stopped-connection-reset-v2, applied there "
                "after outcomes were observed and frozen here before any call"
            ),
        },
        reporting_rule={
            "headline_result": "replication",
            "headline_reason": "its journals are retained and verifiable",
            "d59_result": "pre_registered_primary_reported_unchanged",
            "disagreement_threshold_points": DISAGREEMENT_THRESHOLD_POINTS,
            "disagreement_handling": (
                "publish both results and the difference as a finding; never select one "
                "after observing both"
            ),
            "analysis_unchanged": (
                "two-sided exact McNemar at alpha 0.05, 20-point minimum relevant "
                "difference, family-stratified logical-cluster bootstrap seed 20260911 with "
                "10,000 resamples"
            ),
            "both_published": True,
        },
        retention={
            "required_before_cleanup": True,
            "workflow": "docs/evidence-retention.md",
            "capture_after_run": True,
        },
        concurrency={
            "max_workers": MAX_WORKERS,
            "dispatch": "plan order to the first free worker; completion order is recorded",
            "stop_rule_evaluation": (
                "in completion order; no new dispatch after a stop reason; in-flight "
                "assignments complete and are recorded"
            ),
            "per_worker": ["egress proxy", "invocation journal", "transport"],
            "shared": [
                "attempt journal file with one connection per worker",
                "subscription ledger",
                "approved caps",
                "runtime window",
            ],
            "rate_limit_rejections": "request failures that stay in the denominator",
        },
        pilot={
            "requires_separate_approval": True,
            "pooling": "never pooled with the full campaign; reported separately",
            "seed": PILOT_SEED,
            "jobs": pilot,
            "phase_caps": _caps(pilot),
            "proposed_window_hours": PILOT_WINDOW_HOURS,
        },
        primary_jobs=primary,
        reliability_jobs=reliability,
        remaining_human_boundary={
            "approve_exact_plan_digest": True,
            "approve_pilot_caps_and_window": True,
            "approve_full_caps_and_window_after_pilot": True,
            "create_route_login": True,
            "D5_10_verdict_remains_human_owned": True,
        },
    )
    value.pop("change_control", None)
    value.pop("owner_exception", None)
    return {**value, "execution_plan_digest": content_digest(value)}


def expected_approval(plan: dict[str, Any], phase: str) -> dict[str, Any]:
    if phase == "pilot":
        caps = plan["pilot"]["phase_caps"]
        window = plan["pilot"]["proposed_window_hours"]
    elif phase == "full":
        caps = plan["phase_caps"]["aggregate"]
        window = plan["runtime"]["proposed_aggregate_window_hours"]
    else:
        raise ValueError("phase must be pilot or full")
    return {
        "execution_plan_digest": plan["execution_plan_digest"],
        "phase": phase,
        "approved_caps": caps,
        "approved_runtime_window_hours": window,
        "subscription_execution_authorized": True,
        "incremental_experiment_charge_cap_usd": "0.00",
    }


def validate_approval(plan: dict[str, Any], approval: dict[str, Any], *, phase: str) -> None:
    """Bind execution of one phase to a separately recorded decision for this exact plan."""

    body = {key: value for key, value in plan.items() if key != "execution_plan_digest"}
    if plan.get("execution_plan_digest") != content_digest(body):
        raise ValueError("replication plan digest is invalid")
    if approval != expected_approval(plan, phase):
        raise ValueError("approval must bind the exact replication plan, phase, caps, and window")


def phase_jobs(plan: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    if phase == "pilot":
        return [dict(job) for job in plan["pilot"]["jobs"]]
    if phase == "full":
        return [dict(job) for job in plan["primary_jobs"] + plan["reliability_jobs"]]
    raise ValueError("phase must be pilot or full")


@dataclass
class CampaignOutcome:
    """What a campaign recorded, in completion order, and why it stopped."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "running"
    dispatched: int = 0


def run_campaign(
    jobs: list[dict[str, Any]],
    *,
    workers: int,
    run_assignment: Callable[[dict[str, Any]], dict[str, Any]],
    window_exhausted: Callable[[], bool],
    ledger_blocked: Callable[[], bool],
    on_result: Callable[[dict[str, Any]], None] = lambda row: None,
) -> CampaignOutcome:
    """Dispatch assignments to ``workers`` threads under the pre-registered stop rule.

    ``run_assignment`` must be safe to call from several threads at once; the caller gives
    each thread its own transport. Rows are appended in completion order with their plan
    index, so the plan order can be restored. A stop reason ends dispatch; assignments
    already in flight finish and are recorded.
    """

    if workers < 1 or workers > MAX_WORKERS:
        raise ValueError(f"workers must be in [1, {MAX_WORKERS}]")
    outcome = CampaignOutcome()
    consecutive_infrastructure_failures = 0
    completions: queue.SimpleQueue[tuple[int, dict[str, Any] | None, BaseException | None]] = (
        queue.SimpleQueue()
    )

    def complete_assignment(index: int) -> None:
        # Enqueue at the worker boundary. A set of finished futures cannot retain
        # completion order when several finish during a slow result callback.
        try:
            row = run_assignment(jobs[index])
        except BaseException as exc:  # noqa: BLE001 - drain before re-raising
            completions.put((index, None, exc))
        else:
            completions.put((index, row, None))

    def record(index: int, row: dict[str, Any]) -> str | None:
        nonlocal consecutive_infrastructure_failures
        if row["classification"] == "infrastructure_failure":
            consecutive_infrastructure_failures += 1
        else:
            consecutive_infrastructure_failures = 0
        row = {**row, "plan_index": index, "completion_index": len(outcome.rows)}
        outcome.rows.append(row)
        reason = campaign_stop_reason(
            str(row["classification"]),
            ledger_blocked=ledger_blocked(),
            consecutive_infrastructure_failures=consecutive_infrastructure_failures,
        )
        on_result(row)
        return reason

    pending = 0
    first_error: BaseException | None = None
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="d59-rep") as pool:
        next_index = 0
        while next_index < len(jobs) or pending:
            while (
                outcome.stop_reason == "running"
                and next_index < len(jobs)
                and pending < workers
                and completions.empty()
            ):
                if window_exhausted():
                    outcome.stop_reason = "runtime_window_exhausted"
                    break
                pool.submit(complete_assignment, next_index)
                pending += 1
                outcome.dispatched += 1
                next_index += 1
            if not pending:
                break
            index, row, error = completions.get()
            pending -= 1
            try:
                if error is not None:
                    raise error
                assert row is not None
                reason = record(index, row)
                if reason is not None and outcome.stop_reason == "running":
                    outcome.stop_reason = reason
            except BaseException as exc:  # noqa: BLE001 - includes result callback failures
                # Stop dispatch on either worker or reporting failure, but continue
                # delivering every in-flight result before re-raising the first error.
                if first_error is None:
                    first_error = exc
                if outcome.stop_reason == "running":
                    outcome.stop_reason = "execution_error"
            if outcome.stop_reason != "running" and not pending:
                break
    if first_error is not None:
        raise first_error
    if outcome.stop_reason == "running":
        outcome.stop_reason = "completed_all_assignments"
    return outcome


def campaign_stop_reason(
    classification: str,
    *,
    ledger_blocked: bool,
    consecutive_infrastructure_failures: int,
) -> str | None:
    """The pre-registered stop rule, applied after each assignment's result.

    Returns the stop reason, or None when the campaign continues.
    """

    if ledger_blocked:
        return "invocation_ledger_blocked"
    if classification == "phase_time_stop":
        # The runner refused to start an attempt inside the window's final request deadline.
        return "runtime_window_exhausted"
    if classification not in EPISODE_OUTCOMES_THAT_CONTINUE:
        return f"unexpected_classification:{classification}"
    if consecutive_infrastructure_failures >= CONSECUTIVE_INFRASTRUCTURE_FAILURE_LIMIT:
        return "consecutive_infrastructure_failures"
    return None
