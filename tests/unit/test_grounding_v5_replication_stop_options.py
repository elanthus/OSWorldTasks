"""Offline checks of unapproved stop-rule alternatives; no waits or model calls."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest
from test_grounding_v5_claude_code_policy import request
from test_grounding_v5_claude_code_transport import SuccessfulProcess, runtime_identity
from test_grounding_v5_d59_replication import plan as build_offline_plan

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5 import d59_replication as rep
from pixelgym.grounding.v5.journal import CallCapExceededError, V5AttemptJournal
from pixelgym.grounding.v5.replication_stop_rules import (
    PROPOSED_STOP_RULE_VERSION,
    FailureStreak,
    ProposedStopRule,
)
from scripts.run_grounding_v5_d59_replication import assignment_rate_limit_metadata


@pytest.fixture
def candidate_plan(monkeypatch):
    return build_offline_plan.__wrapped__(monkeypatch)


@pytest.mark.parametrize(
    "classification,action,streak,blocked,expected",
    [
        ("request_failure", "stop", 1, False, None),
        ("request_failure", "stop", 2, False, None),
        ("request_failure", "stop", 3, False, "consecutive_request_failures"),
        ("request_failure", "stop", 1, True, "invocation_ledger_blocked"),
        ("policy_violation", "stop", 0, False, "policy_violation"),
        ("policy_violation", "continue", 0, False, None),
        ("policy_violation", "continue", 0, True, "invocation_ledger_blocked"),
        ("phase_time_stop", "continue", 0, False, "runtime_window_exhausted"),
    ],
)
def test_proposed_pure_stop_rule(classification, action, streak, blocked, expected):
    assert (
        rep.campaign_stop_reason(
            classification,
            ledger_blocked=blocked,
            consecutive_infrastructure_failures=0,
            consecutive_request_failures=streak,
            stop_rule_version=PROPOSED_STOP_RULE_VERSION,
            stop_rule_options=ProposedStopRule("per_worker", action),
        )
        == expected
    )


@pytest.mark.parametrize("classification", ["request_failure", "policy_violation"])
def test_v1_does_not_silently_adopt_the_proposal(classification):
    assert (
        rep.campaign_stop_reason(
            classification,
            ledger_blocked=False,
            consecutive_infrastructure_failures=0,
        )
        == f"unexpected_classification:{classification}"
    )
    with pytest.raises(ValueError, match="version"):
        rep.campaign_stop_reason(
            classification,
            ledger_blocked=False,
            consecutive_infrastructure_failures=0,
            stop_rule_options=ProposedStopRule("per_worker", "continue"),
        )


@pytest.mark.parametrize("phase,expected", [("pre_send", "execution_error"), ("post_send", None)])
def test_pre_send_faults_stop_as_execution_errors(phase, expected):
    assert (
        rep.campaign_stop_reason(
            "request_failure",
            ledger_blocked=False,
            consecutive_infrastructure_failures=0,
            consecutive_request_failures=1,
            request_failure_phase=phase,
            stop_rule_version=PROPOSED_STOP_RULE_VERSION,
            stop_rule_options=ProposedStopRule("per_worker", "stop"),
        )
        == expected
    )


def test_per_worker_streak_does_not_combine_one_shared_outage():
    streak = FailureStreak("per_worker")
    assert [streak.record(True, worker=i, started=0, ended=1) for i in range(4)] == [1] * 4
    assert streak.record(True, worker=0, started=2, ended=3) == 2
    assert streak.record(False, worker=1, started=2, ended=3) == 0
    assert streak.record(True, worker=0, started=4, ended=5) == 3
    assert streak.record(True, worker=1, started=4, ended=5) == 1


def test_non_overlapping_streak_counts_disjoint_failure_intervals_only():
    streak = FailureStreak("non_overlapping")
    intervals = [(0, 5), (1, 6), (2, 7), (7, 8), (8, 9)]
    assert [
        streak.record(True, worker=i % 4, started=s, ended=e) for i, (s, e) in enumerate(intervals)
    ] == [1, 1, 1, 2, 3]
    assert streak.record(False, worker=2, started=9, ended=10) == 0
    assert streak.record(True, worker=0, started=10, ended=11) == 1


def test_non_overlap_selects_intervals_without_inferring_outage_clusters():
    streak = FailureStreak("non_overlapping")
    # The late long interval bridges earlier choices, but cannot retrospectively
    # change which completed intervals the pre-registered greedy rule selected.
    intervals = [(0, 1), (2, 3), (0.5, 4), (4.5, 5)]
    assert [
        streak.record(True, worker=0, started=start, ended=end) for start, end in intervals
    ] == [1, 2, 2, 3]


@pytest.mark.parametrize("scope", [None, "per_worker", "non_overlapping"])
@pytest.mark.parametrize("classification", ["infrastructure_failure", "request_failure"])
def test_parallel_outage_obeys_selected_rule_and_retains_every_assignment(scope, classification):
    started = threading.Barrier(4)

    def run(job):
        started.wait(timeout=5)
        return {**job, "classification": classification}

    options = (
        {}
        if scope is None
        else {
            "stop_rule_version": PROPOSED_STOP_RULE_VERSION,
            "stop_rule_options": ProposedStopRule(scope, "stop"),
            "on_backoff": lambda row: None,
            "wait_for_backoff": lambda seconds: None,
        }
    )
    outcome = rep.run_campaign(
        [{"trial_id": str(i), "worker": i} for i in range(4)],
        workers=4,
        run_assignment=run,
        window_exhausted=lambda: False,
        ledger_blocked=lambda: False,
        **options,
    )
    assert len(outcome.rows) == outcome.dispatched == 4
    assert outcome.stop_reason == (
        "completed_all_assignments"
        if scope is not None
        else "consecutive_infrastructure_failures"
        if classification == "infrastructure_failure"
        else "unexpected_classification:request_failure"
    )


@pytest.mark.parametrize("start,end", [(2, 1), (float("nan"), 2), (1, float("inf"))])
def test_overlap_rule_rejects_invalid_timing(start, end):
    with pytest.raises(ValueError, match="timing"):
        FailureStreak("non_overlapping").record(True, worker=0, started=start, ended=end)


@pytest.mark.parametrize(
    "hint,expected", [(None, 30), (-1, 30), (45, 45), (9999, 60), (float("nan"), 30)]
)
def test_backoff_is_bounded(hint, expected):
    assert ProposedStopRule("per_worker", "continue").backoff_seconds(hint) == expected


@pytest.mark.parametrize("seconds", [0, -1, 61, float("inf"), float("nan")])
def test_invalid_backoff_is_rejected_before_dispatch(seconds):
    with pytest.raises(ValueError, match="backoff"):
        ProposedStopRule("per_worker", "continue", rate_limit_backoff_seconds=seconds)


def test_rate_limit_backoff_is_reported_before_next_dispatch_and_no_retry():
    events = []
    rows = []
    now = 0.0

    def wait(seconds):
        nonlocal now
        events.append(("wait", seconds))
        now += seconds

    def run(job):
        events.append(("dispatch", job["trial_id"]))
        return {
            **job,
            "worker": 0,
            "classification": "request_failure",
            "rate_limited": True,
            "retry_after_seconds": 9999,
        }

    outcome = rep.run_campaign(
        [{"trial_id": str(i)} for i in range(4)],
        workers=1,
        run_assignment=run,
        window_exhausted=lambda: False,
        ledger_blocked=lambda: False,
        on_result=rows.append,
        stop_rule_version=PROPOSED_STOP_RULE_VERSION,
        stop_rule_options=ProposedStopRule("per_worker", "continue"),
        on_backoff=lambda row: events.append((row["stage"], row["trial_id"])),
        wait_for_backoff=wait,
        monotonic=lambda: now,
    )
    assert outcome.stop_reason == "consecutive_request_failures"
    assert outcome.dispatched == len(rows) == 3
    assert events == [
        ("dispatch", "0"),
        ("scheduled", "0"),
        ("wait", 60),
        ("completed", "0"),
        ("dispatch", "1"),
        ("scheduled", "1"),
        ("wait", 60),
        ("completed", "1"),
        ("dispatch", "2"),
    ]


def test_backoff_requires_a_journal_and_wait_and_honours_the_window():
    options = ProposedStopRule("per_worker", "continue")
    expired = False

    def wait(seconds):
        nonlocal expired
        expired = True

    kwargs = {
        "jobs": [{"trial_id": "first"}, {"trial_id": "unrun"}],
        "workers": 1,
        "run_assignment": lambda job: {
            **job,
            "worker": 0,
            "classification": "request_failure",
            "rate_limited": True,
        },
        "window_exhausted": lambda: expired,
        "ledger_blocked": lambda: False,
        "stop_rule_version": PROPOSED_STOP_RULE_VERSION,
        "stop_rule_options": options,
    }
    with pytest.raises(ValueError, match="backoff"):
        rep.run_campaign(**kwargs)
    outcome = rep.run_campaign(**kwargs, on_backoff=lambda receipt: None, wait_for_backoff=wait)
    assert outcome.dispatched == 1 and outcome.stop_reason == "runtime_window_exhausted"


@pytest.mark.parametrize("summary_delay,expected_waits", [(0, [60]), (120, [])])
def test_concurrent_rejections_share_one_completion_based_dispatch_deadline(
    monkeypatch, summary_delay, expected_waits
):
    started = threading.Barrier(4)
    all_completed = threading.Event()
    lock = threading.Lock()
    completed = 0
    now = 0.0
    waits, receipts = [], []

    class Pool(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            def tracked():
                nonlocal completed
                fn(*args, **kwargs)
                with lock:
                    completed += 1
                    if completed == 4:
                        all_completed.set()

            return super().submit(tracked)

    monkeypatch.setattr(rep, "ThreadPoolExecutor", Pool)

    def run(job):
        if job["index"] < 4:
            started.wait(timeout=5)
        return {
            **job,
            "classification": "request_failure" if job["index"] < 4 else "success_termination",
            "rate_limited": job["index"] < 4,
            "retry_after_seconds": 9999,
        }

    def record(row):
        nonlocal now
        if row["completion_index"] == 0:
            assert all_completed.wait(timeout=5)
            now += summary_delay

    def wait(seconds):
        nonlocal now
        waits.append(seconds)
        now += seconds

    outcome = rep.run_campaign(
        [{"trial_id": str(i), "index": i, "worker": i % 4} for i in range(5)],
        workers=4,
        run_assignment=run,
        window_exhausted=lambda: False,
        ledger_blocked=lambda: False,
        on_result=record,
        stop_rule_version=PROPOSED_STOP_RULE_VERSION,
        stop_rule_options=ProposedStopRule("per_worker", "stop"),
        on_backoff=receipts.append,
        wait_for_backoff=wait,
        monotonic=lambda: now,
    )
    assert outcome.stop_reason == "completed_all_assignments"
    assert outcome.dispatched == len(outcome.rows) == 5
    assert waits == expected_waits
    scheduled = [receipt for receipt in receipts if receipt["stage"] == "scheduled"]
    assert len(scheduled) == 4
    assert {receipt["dispatch_not_before"] for receipt in scheduled} == {60}
    assert len(receipts) == 4 + len(expected_waits)


def test_cap_exhaustion_is_a_registered_stop_and_drains(tmp_path, monkeypatch):
    started = threading.Barrier(2)
    release = threading.Event()
    recorded = []

    class Pool(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)
            if args[0] == 0:
                future.add_done_callback(lambda f: release.set())
            return future

    monkeypatch.setattr(rep, "ThreadPoolExecutor", Pool)

    def run(job):
        started.wait(timeout=5)
        if job["trial_id"] == "0":
            raise CallCapExceededError("approved model-attempt cap reached")
        assert release.wait(timeout=5)
        return {**job, "classification": "success_termination"}

    outcome = rep.run_campaign(
        [{"trial_id": str(i)} for i in range(4)],
        workers=2,
        run_assignment=run,
        window_exhausted=lambda: False,
        ledger_blocked=lambda: False,
        on_result=recorded.append,
    )
    assert outcome.stop_reason == "approved_cap_reached"
    assert outcome.dispatched == 2 and [r["trial_id"] for r in recorded] == ["0", "1"]
    assert recorded[0]["classification"] == "approved_cap_reached"
    assert recorded[0]["incomplete_assignment"] is True


def test_one_unparseable_post_send_fault_blocks_all_workers(tmp_path):
    class BrokenIO(SuccessfulProcess):
        failed = False

        def communicate(self, input=None, timeout=None):
            if not self.failed:
                self.failed = True
                raise OSError("synthetic post-send I/O failure")
            return "", "synthetic I/O failure"

    ledger = claude.SubscriptionExemptLedger(Decimal(1), Decimal(0))
    invocations = claude.ClaudeInvocationJournal(tmp_path / "invocations.sqlite")
    transport = claude.ClaudeCodeTransport(
        ledger=ledger,
        invocation_journal=invocations,
        runtime_identity=runtime_identity(),
        process_factory=lambda *a, **kw: BrokenIO(returncode=1),
    )
    started = threading.Barrier(4)
    release = threading.Event()
    rows = []

    def run(job):
        started.wait(timeout=5)
        if job["trial_id"] == "0":
            outcome = transport.send(request(), idempotency_key="blocked", deadline_seconds=125)
            assert outcome.fault is not None and ledger.blocked
            return {**job, "classification": outcome.fault.classification}
        assert release.wait(timeout=5)
        return {**job, "classification": "success_termination"}

    def report(row):
        rows.append(row)
        release.set()

    try:
        outcome = rep.run_campaign(
            [{"trial_id": str(i)} for i in range(8)],
            workers=4,
            run_assignment=run,
            window_exhausted=lambda: False,
            ledger_blocked=lambda: ledger.blocked,
            on_result=report,
        )
        assert outcome.stop_reason == "invocation_ledger_blocked"
        assert outcome.dispatched == len(rows) == 4
        assert ledger.unresolved == {"blocked"}
    finally:
        release.set()
        transport.close()
        invocations.close()


def test_rate_limit_metadata_uses_fault_facts_and_is_trial_scoped(tmp_path):
    journal = V5AttemptJournal(tmp_path / "metadata.sqlite")
    try:
        journal.append_event(
            event_key="limited",
            kind="retryable_rate_limit",
            trial_id="limited",
            step_index=0,
            payload={"cli_fault": {"kind": "subscription_rate_limit"}, "retry_after_seconds": 45},
        )
        assert assignment_rate_limit_metadata(journal, "limited") == {
            "rate_limited": True,
            "retry_after_seconds": 45,
            "request_failure_phase": None,
            "request_failure_kind": "subscription_rate_limit",
        }
        assert assignment_rate_limit_metadata(journal, "other") == {
            "rate_limited": False,
            "retry_after_seconds": None,
            "request_failure_phase": None,
            "request_failure_kind": None,
        }
        journal.append_event(
            event_key="pre-send",
            kind="sealed_unsuccessful_result",
            trial_id="limited",
            step_index=1,
            payload={"cli_fault": {"kind": "pre_send", "phase": "pre_send"}},
        )
        assert assignment_rate_limit_metadata(journal, "limited")["request_failure_phase"] == (
            "pre_send"
        )
    finally:
        journal.close()


def test_backoff_is_not_used_for_drained_results_after_a_stop():
    started = threading.Barrier(2)
    release = threading.Event()
    waits = []

    def run(job):
        started.wait(timeout=5)
        if job["worker"] == 1:
            assert release.wait(timeout=5)
        return {
            **job,
            "classification": ("policy_violation" if job["worker"] == 0 else "request_failure"),
            "rate_limited": job["worker"] == 1,
        }

    outcome = rep.run_campaign(
        [{"trial_id": str(i), "worker": i % 2} for i in range(4)],
        workers=2,
        run_assignment=run,
        window_exhausted=lambda: False,
        ledger_blocked=lambda: False,
        on_result=lambda row: release.set(),
        stop_rule_version=PROPOSED_STOP_RULE_VERSION,
        stop_rule_options=ProposedStopRule("per_worker", "stop"),
        on_backoff=lambda row: waits.append(row),
        wait_for_backoff=waits.append,
    )
    assert outcome.stop_reason == "policy_violation" and outcome.dispatched == 2
    assert len(outcome.rows) == 2 and waits == []


def test_last_assignment_does_not_delay_for_a_nonexistent_next_dispatch():
    waits = []
    outcome = rep.run_campaign(
        [{"trial_id": "last", "worker": 0}],
        workers=1,
        run_assignment=lambda job: {
            **job,
            "classification": "request_failure",
            "rate_limited": True,
        },
        window_exhausted=lambda: False,
        ledger_blocked=lambda: False,
        stop_rule_version=PROPOSED_STOP_RULE_VERSION,
        stop_rule_options=ProposedStopRule("per_worker", "continue"),
        on_backoff=lambda row: waits.append(row),
        wait_for_backoff=waits.append,
    )
    assert outcome.stop_reason == "completed_all_assignments" and waits == []


@pytest.mark.parametrize("scope", ["per_worker", "non_overlapping"])
@pytest.mark.parametrize("disposition", ["stop", "continue"])
def test_proposed_plan_fields_without_mutating_frozen_candidate(candidate_plan, scope, disposition):
    root = Path(__file__).parents[2]
    path = root / rep.OUTPUT_DIRECTORY / "execution-plan.json"
    before = path.read_bytes()
    # Compare v1 and v2 at one source revision, independently of which version
    # the owner selected in the checked-in candidate or retained branch history.
    frozen = candidate_plan
    identity = claude.ClaudeRuntimeIdentity(**frozen["runtime_identity"])
    proposal = rep.execution_plan(
        root,
        source_revision=candidate_plan["source_binding"]["source_revision"],
        runtime_identity=identity,
        stop_rule_options=ProposedStopRule(scope, disposition),
    )
    changed = {k for k, v in proposal.items() if frozen[k] != v}
    assert changed == {
        "stop_rule",
        "concurrency",
        "execution_plan_digest",
        "owner_direction",
    }
    assert proposal["stop_rule"]["request_failure_disposition"]["pre_send"].startswith(
        "execution_error"
    )
    assert ("unapproved" not in proposal["stop_rule"]["origin"]) == (
        scope == "per_worker" and disposition == "stop"
    )
    assert rep.stop_rule_options_for_plan(proposal) == ProposedStopRule(scope, disposition)
    assert not proposal["execution_enabled"] and proposal["approved_model_attempt_cap"] == 0
    assert path.read_bytes() == before
