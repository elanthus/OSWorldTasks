"""No-call checks of the D5.9 replication freeze, its approvals, and its stop rule."""

from __future__ import annotations

import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5 import d59_replication as replication
from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps, content_digest
from pixelgym.grounding.v5.d59_haiku_execution import validate_assignments
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.os_sandbox import cli_profile_template_digest
from scripts.run_grounding_v5_d59_replication import assignment_call_usage

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
            manifest["transport_retry_rule"]
            == "claude-one-stopped-timeout-connection-or-auth-retry-v1"
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
    active = 0
    peak = 0
    lock = threading.Lock()
    wave = threading.Barrier(3)

    def run(job: dict) -> dict:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        wave.wait(timeout=5)
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
    assert peak == 3
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
        assert started.wait(timeout=5)
        return {**job, "classification": "policy_violation"}  # not a continuing outcome

    outcome = replication.run_campaign(
        _jobs(3),
        workers=2,
        run_assignment=run,
        window_exhausted=lambda: False,
        ledger_blocked=lambda: False,
        # Keep t-0 in flight until the stopping result has actually been observed.
        # Releasing it merely when it starts races dispatch of t-2 against t-1.
        on_result=lambda row: release.set() if row["trial_id"] == "t-1" else None,
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


def test_stop_rule_maps_the_runner_time_stop_to_the_window_reason() -> None:
    assert (
        replication.campaign_stop_reason(
            "phase_time_stop", ledger_blocked=False, consecutive_infrastructure_failures=0
        )
        == "runtime_window_exhausted"
    )
    # A blocked ledger still takes priority.
    assert (
        replication.campaign_stop_reason(
            "phase_time_stop", ledger_blocked=True, consecutive_infrastructure_failures=0
        )
        == "invocation_ledger_blocked"
    )


def test_campaign_drains_in_flight_results_when_a_worker_raises() -> None:
    import threading

    release = threading.Event()

    def run(job: dict) -> dict:
        if job["trial_id"] == "t-0":
            release.wait(timeout=5)
            return {**job, "classification": "success_termination"}
        if job["trial_id"] == "t-1":
            release.set()
            raise RuntimeError("worker failed")
        return {**job, "classification": "success_termination"}

    recorded: list[dict] = []
    with pytest.raises(RuntimeError, match="worker failed"):
        replication.run_campaign(
            _jobs(4),
            workers=2,
            run_assignment=run,
            window_exhausted=lambda: False,
            ledger_blocked=lambda: False,
            on_result=recorded.append,
        )
    # t-0 was in flight when t-1 raised: it is still recorded; t-2 and t-3 never dispatch.
    assert {r["trial_id"] for r in recorded} == {"t-0"}


@pytest.mark.parametrize("third_is_failure", [False, True])
def test_campaign_preserves_completions_accumulated_during_reporting(
    monkeypatch: pytest.MonkeyPatch, third_is_failure: bool
) -> None:
    gates = [threading.Event() for _ in range(4)]
    finished = [threading.Event() for _ in range(4)]
    started = threading.Barrier(4)
    gates[0].set()

    class ObservedPool(ThreadPoolExecutor):
        """Observe actual task completion, without sleeping or replacing scheduling."""

        submitted = 0

        def submit(self, fn, /, *args, **kwargs):
            index = self.submitted
            self.submitted += 1
            future = super().submit(fn, *args, **kwargs)
            if index < 4:
                future.add_done_callback(lambda _future: finished[index].set())
            return future

    monkeypatch.setattr(replication, "ThreadPoolExecutor", ObservedPool)

    def run(job: dict) -> dict:
        index = int(job["trial_id"].split("-")[1])
        if index < 4:
            started.wait(timeout=5)
            assert gates[index].wait(timeout=5)
        failure = index < 2 or (index == 2 if third_is_failure else index == 3)
        return {
            **job,
            "classification": "infrastructure_failure" if failure else "success_termination",
        }

    def report(row: dict) -> None:
        if row["plan_index"] == 0:
            # All three completions accumulate before reporting returns. Their
            # order must survive batching, including a success that resets the streak.
            for index in range(1, 4):
                gates[index].set()
                assert finished[index].wait(timeout=5)

    outcome = replication.run_campaign(
        _jobs(5),
        workers=4,
        run_assignment=run,
        window_exhausted=lambda: False,
        ledger_blocked=lambda: False,
        on_result=report,
    )
    expected_count = 4 if third_is_failure else 5
    assert [row["plan_index"] for row in outcome.rows] == list(range(expected_count))
    assert [row["completion_index"] for row in outcome.rows] == list(range(expected_count))
    assert outcome.dispatched == expected_count
    assert outcome.stop_reason == (
        "consecutive_infrastructure_failures" if third_is_failure else "completed_all_assignments"
    )


@pytest.mark.parametrize("fail_again", [False, True])
def test_campaign_drains_after_result_callback_failure(fail_again: bool) -> None:
    started = threading.Barrier(2)
    release = threading.Event()
    recorded: list[dict] = []
    first_error = OSError("summary write failed")

    def run(job: dict) -> dict:
        started.wait(timeout=5)
        if job["trial_id"] == "t-1":
            assert release.wait(timeout=5)
        return {**job, "classification": "success_termination"}

    def report(row: dict) -> None:
        recorded.append(row)
        if row["trial_id"] == "t-0":
            release.set()
            raise first_error
        if fail_again:
            raise ValueError("later reporting failure")

    with pytest.raises(OSError, match="summary write failed") as caught:
        replication.run_campaign(
            _jobs(4),
            workers=2,
            run_assignment=run,
            window_exhausted=lambda: False,
            ledger_blocked=lambda: False,
            on_result=report,
        )
    assert caught.value is first_error
    assert [row["trial_id"] for row in recorded] == ["t-0", "t-1"]
    assert [row["completion_index"] for row in recorded] == [0, 1]


def test_assignment_usage_attributes_overlapping_reservations_and_retries(tmp_path: Path) -> None:
    journals = [V5AttemptJournal(tmp_path / "attempts.sqlite") for _ in range(2)]
    reserved = threading.Barrier(2)
    caps = CallCaps(2, 3, 1, 4)

    def reserve(index: int) -> dict[str, int]:
        journal = journals[index]
        trial = f"trial-{index}"
        for attempt in range(index + 1):
            identity = AttemptIdentity(trial, 0, attempt)
            journal.reserve_attempt_started(
                identity,
                provider_endpoint_identity="scripted",
                request_digest=content_digest({"trial": trial, "attempt": attempt}),
                idempotency_key=identity.key,
                model_attempt_reservation=1,
                control_request_reservation=0,
                pre_call_checkpoint=b"{}",
                approved_caps=caps,
            )
        if index == 1:
            journal.reserve_control_request(identity, request_kind="cancel", approved_caps=caps)
        reserved.wait(timeout=5)
        # Both trials now overlap in the same database; unknown/unsettled attempts
        # still count and the second attempt stays attributed to its own trial.
        return assignment_call_usage(journal, trial)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, range(2)))
        assert results == [
            {"model_attempts": 1, "provider_control_requests": 0, "provider_wire_requests": 1},
            {"model_attempts": 2, "provider_control_requests": 1, "provider_wire_requests": 3},
        ]
        model, control = journals[0].call_counts()
        assert sum(row["model_attempts"] for row in results) == model == 3
        assert sum(row["provider_wire_requests"] for row in results) == model + control == 4
        assert assignment_call_usage(journals[0], "unrun") == {
            "model_attempts": 0,
            "provider_control_requests": 0,
            "provider_wire_requests": 0,
        }
    finally:
        for journal in journals:
            journal.close()


def test_probe_runner_is_time_bounded() -> None:
    import subprocess

    with pytest.raises(subprocess.TimeoutExpired):
        claude._run_probe(["/bin/sleep", "5"], timeout_seconds=0.2)
