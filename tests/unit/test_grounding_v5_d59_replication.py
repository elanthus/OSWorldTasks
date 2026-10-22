"""No-call checks of the D5.9 replication freeze, its approvals, and its stop rule."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5 import d59_replication as replication
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.d59_haiku_execution import validate_assignments
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.os_sandbox import cli_profile_template_digest

ROOT = Path(__file__).resolve().parents[2]


def _identity() -> claude.ClaudeRuntimeIdentity:
    return claude.ClaudeRuntimeIdentity(
        cli_version=claude.REPLICATION_CLI_VERSION,
        auth_method="oauth_token",
        subscription_type="max",
        requested_model=claude.MODEL,
        reasoning_effort=claude.MODEL_REASONING_EFFORT,
        help_sha256="sha256:" + "7" * 64,
    )


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


@pytest.fixture
def plan(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Build the candidate against the current checkout, bypassing the clean-tree check.

    The plan builder requires every policy input to match the recorded revision. The test
    stubs that check so the plan can be built from a dirty development tree; the committed
    artifact's own verifier keeps the strict check.
    """

    monkeypatch.setattr(replication, "_validate_policy_inputs", lambda _root, _rev: None)
    monkeypatch.setattr(
        replication,
        "_sha256_at_revision",
        lambda root, _rev, path: (
            "sha256:" + __import__("hashlib").sha256((root / path).read_bytes()).hexdigest()
        ),
    )
    return replication.execution_plan(ROOT, source_revision=_head(), runtime_identity=_identity())


def test_replication_plan_preserves_the_d59_design_and_changes_the_policy_pair(plan: dict) -> None:
    predecessor = json.loads((ROOT / replication.PREDECESSOR_PATH).read_text())
    for field in ("phase_caps", "primary_comparison", "allocation", "runtime", "budget"):
        assert plan[field] == predecessor[field]
    assert plan["execution_enabled"] is False
    assert plan["subscription_execution_authorized"] is False
    assert plan["approved_model_attempt_cap"] == 0
    assert "change_control" not in plan and "owner_exception" not in plan
    jobs = validate_assignments(plan)
    assert len(jobs) == 432
    assert all(job["trial_id"].startswith("d59-rep-") for job in jobs)
    assert not any(job["trial_id"].startswith("d59-rep-pilot-") for job in jobs)
    predecessor_ids = {v["policy_id"] for v in predecessor["policy_manifests"].values()}
    for mode in ("history", "stateless"):
        manifest = plan["policy_manifests"][mode]
        assert manifest["policy_id"] not in predecessor_ids
        enforcement = manifest["sandbox"]["runtime_enforcement"]
        assert manifest["sandbox"]["schema_version"] == "pixelgym-agent-v5-sandbox-v4"
        assert enforcement["mechanism_name"] == "darwin_sandbox_exec_cli_launch"
        assert enforcement["os_sandbox_applied"] is True
        assert enforcement["profile_template_digest"] == cli_profile_template_digest()
        parameters = dict(manifest["inference_parameters"])
        assert parameters["cli_version"] == claude.REPLICATION_CLI_VERSION
        assert parameters["auth_method"] == "oauth_token"
        assert parameters["cli_api_retry_limit"] == "0"
        assert (
            manifest["transport_retry_rule"] == "claude-one-stopped-timeout-or-connection-retry-v1"
        )
    assert plan["relationship"]["kind"] == "replication_beside_d59"
    assert plan["relationship"]["d59_results"]["path"] == replication.D59_RESULTS_PATH
    assert plan["relationship"]["outcomes_reused"] == 0
    assert plan["security_boundary"]["os_sandbox_applied"] == {"history": True, "stateless": True}
    assert plan["route"]["auth_method"] == "oauth_token"
    validate_credential_free(plan)


def test_replication_plan_pre_registers_stop_and_reporting_rules(plan: dict) -> None:
    stop = plan["stop_rule"]
    assert stop["version"] == replication.STOP_RULE_VERSION
    assert set(stop["episode_outcomes_that_continue"]) == {
        "success_termination",
        "step_limit_truncation",
        "invalid_output",
        "infrastructure_failure",
    }
    assert stop["consecutive_infrastructure_failure_limit"] == 3
    assert stop["post_result_changes_permitted"] is False
    reporting = plan["reporting_rule"]
    assert reporting["headline_result"] == "replication"
    assert reporting["d59_result"] == "pre_registered_primary_reported_unchanged"
    assert reporting["disagreement_threshold_points"] == 20
    assert reporting["both_published"] is True
    assert plan["retention"]["required_before_cleanup"] is True


def test_pilot_is_four_assignments_with_its_own_caps_and_namespace(plan: dict) -> None:
    pilot = plan["pilot"]
    jobs = pilot["jobs"]
    assert len(jobs) == 4
    assert {(j["phase"], j["mode"], j["repeat"]) for j in jobs} == {
        ("primary", "history", 0),
        ("primary", "stateless", 0),
        ("reliability", "history", 1),
        ("reliability", "stateless", 1),
    }
    assert all(j["seed"] == replication.PILOT_SEED for j in jobs)
    assert all(j["trial_id"].startswith("d59-rep-pilot-") for j in jobs)
    full_ids = {j["trial_id"] for j in plan["primary_jobs"] + plan["reliability_jobs"]}
    assert not full_ids & {j["trial_id"] for j in jobs}
    assert (
        pilot["phase_caps"]["model_attempt_cap"]
        < plan["phase_caps"]["aggregate"]["model_attempt_cap"]
    )
    assert pilot["requires_separate_approval"] is True
    assert replication.phase_jobs(plan, "pilot") == jobs
    assert len(replication.phase_jobs(plan, "full")) == 432


def test_approval_binds_one_phase_of_the_exact_plan(plan: dict) -> None:
    pilot = replication.expected_approval(plan, "pilot")
    full = replication.expected_approval(plan, "full")
    replication.validate_approval(plan, pilot, phase="pilot")
    replication.validate_approval(plan, full, phase="full")
    assert pilot["approved_caps"] == plan["pilot"]["phase_caps"]
    assert full["approved_caps"] == plan["phase_caps"]["aggregate"]
    assert pilot["approved_runtime_window_hours"] == replication.PILOT_WINDOW_HOURS
    assert full["approved_runtime_window_hours"] == 168
    with pytest.raises(ValueError, match="approval must bind"):
        replication.validate_approval(plan, pilot, phase="full")
    for field, value in (
        ("execution_plan_digest", "sha256:wrong"),
        ("subscription_execution_authorized", False),
        ("incremental_experiment_charge_cap_usd", "1.00"),
        ("approved_caps", {}),
    ):
        changed = {**full, field: value}
        with pytest.raises(ValueError, match="approval must bind"):
            replication.validate_approval(plan, changed, phase="full")
    tampered = dict(plan)
    tampered["primary_jobs"] = [dict(plan["primary_jobs"][0], seed=6001)] + plan["primary_jobs"][1:]
    with pytest.raises(ValueError, match="digest is invalid"):
        replication.validate_approval(tampered, full, phase="full")
    retried = dict(tampered)
    retried["execution_plan_digest"] = content_digest(
        {k: v for k, v in tampered.items() if k != "execution_plan_digest"}
    )
    with pytest.raises(ValueError, match="approval must bind"):
        replication.validate_approval(retried, full, phase="full")


def test_replication_requires_the_token_route_on_the_installed_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(replication, "_validate_policy_inputs", lambda _root, _rev: None)
    frozen = claude.ClaudeRuntimeIdentity(
        cli_version=claude.CLAUDE_CLI_VERSION,
        auth_method=claude.AUTH_METHOD,
        subscription_type=claude.SUBSCRIPTION_TYPE,
        requested_model=claude.MODEL,
        reasoning_effort=claude.MODEL_REASONING_EFFORT,
        help_sha256="sha256:help",
    )
    with pytest.raises(ValueError, match="replication route"):
        replication.live_manifests(ROOT, source_revision=_head(), identity=frozen)


@pytest.mark.parametrize(
    ("classification", "blocked", "consecutive", "expected"),
    [
        ("success_termination", False, 0, None),
        ("step_limit_truncation", False, 0, None),
        ("invalid_output", False, 0, None),
        ("infrastructure_failure", False, 1, None),
        ("infrastructure_failure", False, 2, None),
        ("infrastructure_failure", False, 3, "consecutive_infrastructure_failures"),
        ("success_termination", True, 0, "invocation_ledger_blocked"),
        ("policy_violation", False, 0, "unexpected_classification:policy_violation"),
    ],
)
def test_stop_rule_continues_on_episode_failures_and_stops_on_systemic_ones(
    classification: str, blocked: bool, consecutive: int, expected: str | None
) -> None:
    assert (
        replication.campaign_stop_reason(
            classification,
            ledger_blocked=blocked,
            consecutive_infrastructure_failures=consecutive,
        )
        == expected
    )


# --- Concurrent campaign ----------------------------------------------------------------


def _jobs(n: int) -> list[dict]:
    return [{"trial_id": f"t-{i}", "seed": 6000 + i, "mode": "history"} for i in range(n)]


def test_campaign_runs_every_assignment_across_workers_and_restores_plan_order() -> None:
    import threading
    import time

    active = 0
    peak = 0
    lock = threading.Lock()

    def run(job: dict) -> dict:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return {**job, "classification": "success_termination"}

    outcome = replication.run_campaign(
        _jobs(12),
        workers=3,
        run_assignment=run,
        window_exhausted=lambda: False,
        ledger_blocked=lambda: False,
    )
    assert outcome.stop_reason == "completed_all_assignments"
    assert outcome.dispatched == 12
    assert len(outcome.rows) == 12
    assert peak <= 3
    assert sorted(r["plan_index"] for r in outcome.rows) == list(range(12))
    assert [r["completion_index"] for r in outcome.rows] == list(range(12))
    assert {r["trial_id"] for r in outcome.rows} == {f"t-{i}" for i in range(12)}


def test_campaign_stops_dispatch_after_three_consecutive_infrastructure_failures() -> None:
    sequence = iter(
        [
            "success_termination",
            "infrastructure_failure",
            "infrastructure_failure",
            "infrastructure_failure",
            "success_termination",
            "success_termination",
        ]
    )

    def run(job: dict) -> dict:
        return {**job, "classification": next(sequence)}

    outcome = replication.run_campaign(
        _jobs(6),
        workers=1,
        run_assignment=run,
        window_exhausted=lambda: False,
        ledger_blocked=lambda: False,
    )
    assert outcome.stop_reason == "consecutive_infrastructure_failures"
    assert outcome.dispatched == 4
    assert [r["classification"] for r in outcome.rows] == [
        "success_termination",
        "infrastructure_failure",
        "infrastructure_failure",
        "infrastructure_failure",
    ]


def test_campaign_records_in_flight_assignments_after_a_stop_and_honours_the_window() -> None:
    import threading

    release = threading.Event()
    started = threading.Event()

    def run(job: dict) -> dict:
        if job["trial_id"] == "t-0":
            started.set()
            release.wait(timeout=5)
            return {**job, "classification": "success_termination"}
        return {**job, "classification": "policy_violation"}  # not a continuing outcome

    def late_release(outcome_holder: list) -> None:
        started.wait(timeout=5)
        release.set()

    holder: list = []
    threading.Thread(target=late_release, args=(holder,), daemon=True).start()
    outcome = replication.run_campaign(
        _jobs(3),
        workers=2,
        run_assignment=run,
        window_exhausted=lambda: False,
        ledger_blocked=lambda: False,
    )
    # t-1 stops the campaign while t-0 is in flight; t-0 is still recorded, t-2 never runs.
    assert outcome.stop_reason == "unexpected_classification:policy_violation"
    assert outcome.dispatched == 2
    assert {r["trial_id"] for r in outcome.rows} == {"t-0", "t-1"}

    windowed = replication.run_campaign(
        _jobs(3),
        workers=2,
        run_assignment=lambda job: {**job, "classification": "success_termination"},
        window_exhausted=lambda: True,
        ledger_blocked=lambda: False,
    )
    assert windowed.stop_reason == "runtime_window_exhausted"
    assert windowed.dispatched == 0 and windowed.rows == []
    with pytest.raises(ValueError, match="workers must be"):
        replication.run_campaign(
            _jobs(1),
            workers=replication.MAX_WORKERS + 1,
            run_assignment=run,
            window_exhausted=lambda: False,
            ledger_blocked=lambda: False,
        )


def test_plan_pre_registers_concurrency(plan: dict) -> None:
    concurrency = plan["concurrency"]
    assert concurrency["max_workers"] == replication.MAX_WORKERS == 4
    assert "egress proxy" in concurrency["per_worker"]
    assert "subscription ledger" in concurrency["shared"]
