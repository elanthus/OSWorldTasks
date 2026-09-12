"""Exact carry-forward, reset episodes, and no replay with the repaired runner."""

import json
from collections import Counter
from contextlib import closing
from decimal import Decimal

import pytest

from pixelgym.grounding.v5.contracts import CallCaps
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import SpendLedger
from pixelgym.grounding.v5.reliable_calibration import run_episode
from pixelgym.grounding.v5.reliable_memory import (
    ReliableMemoryPolicy,
    build_reliable_manifest,
    reliable_config,
)
from pixelgym.grounding.v5.runner import InjectedInterruption
from scripts.run_grounding_v5_reliable_continuation import PREVIOUS, continuation_assignments, read
from tests.unit.test_grounding_v5_memory_calibration import CONFIG, PLAN, ROOT, GoldenTransport, job

CONFIG = reliable_config(CONFIG)


def test_exact_ten_preserved_failures_and_ninety_untouched_assignments():
    old, summary = read(PREVIOUS / "execution-plan.json"), read(PREVIOUS / "summary.json")
    jobs, execution, preserved = continuation_assignments(old, summary)
    assert len(jobs) == 100 and len(execution) == 90 and len(preserved) == 10
    assert Counter(j["mode"] for j in execution) == {"history": 45, "stateless": 45}
    assert preserved == [r for r in summary["conditions"] if r["classification"] != "not_run"]
    assert {(j["seed"], j["mode"]) for j in execution}.isdisjoint(
        (r["seed"], r["mode"]) for r in preserved
    )
    assert [{k: v for k, v in j.items() if k != "trial_id"} for j in jobs] == [
        {k: v for k, v in j.items() if k != "trial_id"} for j in old["jobs"]
    ]


@pytest.mark.parametrize("mutation", ["omit", "task", "success", "reorder"])
def test_changed_previous_assignments_are_rejected(mutation):
    old, summary = read(PREVIOUS / "execution-plan.json"), read(PREVIOUS / "summary.json")
    if mutation == "omit":
        summary["conditions"][0]["classification"] = "not_run"
    elif mutation == "task":
        summary["conditions"][0]["task_id"] = "different"
    elif mutation == "success":
        summary["conditions"][0]["classification"] = "success_termination"
    else:
        summary["conditions"].reverse()
    with pytest.raises(ValueError):
        continuation_assignments(old, summary)


def execute(journal, transport, *, mode="history", **kwargs):
    return run_episode(
        journal,
        job=job(mode),
        manifest=build_reliable_manifest(
            ROOT, config=CONFIG, code_revision="test", retain_screenshots=mode == "history"
        ),
        policy=ReliableMemoryPolicy(CONFIG, retain_screenshots=mode == "history"),
        transport=transport,
        ledger=transport.ledger,
        caps=CallCaps(100, 300, 0, 300),
        plan_digest=PLAN,
        **kwargs,
    )


@pytest.mark.parametrize("mode,wrong", [("history", False), ("stateless", True)])
def test_repaired_runner_starts_at_reset_and_preserves_first_choice_scores(tmp_path, mode, wrong):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        transport = GoldenTransport(
            SpendLedger(Decimal(28), Decimal(0), journal=journal), wrong_memory=wrong
        )
        row = execute(journal, transport, mode=mode)
        assert row["success"] is not wrong
        assert row["reached_consumers"] == [5, 7]
        assert [c["correct"] for c in row["first_attempts"]] == [not wrong, True]
        assert row["model_attempts"] == row["environment_actions_dispatched"]
        initial = journal.event(f"{mode}/initial_screenshot")
        checkpoint = json.loads(
            journal.get_object(initial.payload["environment_checkpoint_digest"])
        )
        assert checkpoint["stage_index"] == 0 and not checkpoint["deferred_choices"]
        assert not any("prefix" in e.kind for e in journal.events())
        count = len(transport.model_requests)
        assert execute(journal, transport, mode=mode) == row
        assert len(transport.model_requests) == count


@pytest.mark.parametrize("stop", ["budget", "time", "interruption"])
def test_stops_and_interruption_never_resend_a_started_episode(tmp_path, stop):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        transport = GoldenTransport(
            SpendLedger(Decimal(28), Decimal(28 if stop == "budget" else 0), journal=journal)
        )
        if stop == "interruption":

            def interrupt(name):
                if name == "dispatch_committed":
                    raise InjectedInterruption(name)

            with pytest.raises(InjectedInterruption):
                execute(journal, transport, boundary=interrupt)
            count = len(transport.model_requests)
            row = execute(journal, transport)
            assert row["classification"] == "interrupted_episode"
            assert len(transport.model_requests) == count == 1
        else:
            row = execute(journal, transport, time_exhausted=lambda: stop == "time")
            assert row["classification"] == (
                "budget_stop" if stop == "budget" else "phase_time_stop"
            )
            assert not transport.model_requests


@pytest.mark.parametrize(
    "driver_name", ["reliable_continuation", "reliable_extension", "owner_budget_continuation"]
)
def test_driver_records_all_ninety_failures_without_diagnostic_caps_or_streak_stop(
    tmp_path, monkeypatch, driver_name
):
    from importlib import import_module

    driver = import_module(f"scripts.run_grounding_v5_{driver_name}")

    public = tmp_path / "public"
    public.mkdir()
    journal_path = tmp_path / "aggregate.sqlite"
    snapshot = read(driver.DIAGNOSTIC / "price-recheck.json")
    from datetime import UTC, datetime

    snapshot["observed_at"] = datetime.now(UTC).isoformat()
    driver.write(public / "price-recheck.json", snapshot)
    cfg = reliable_config(driver.config_from_snapshot(snapshot))
    _, jobs, _ = continuation_assignments(
        read(PREVIOUS / "execution-plan.json"), read(PREVIOUS / "summary.json")
    )
    with closing(V5AttemptJournal(journal_path)) as journal:
        if driver_name == "owner_budget_continuation":
            journal.append_event(
                event_key=driver.OWNER_AUTHORIZATION_KEY,
                kind="owner_zero_hold_budget_authorized",
                trial_id="__spend_ledger__",
                step_index=0,
                payload={"approval_digest": PLAN, "rule": driver.OWNER_BUDGET_RULE},
            )
            ledger = driver.OwnerZeroHoldLedger(
                Decimal(28), Decimal(0), journal=journal, approval_digest=PLAN
            )
        else:
            ledger = driver.ReboundedMemoryLedger(Decimal(28), Decimal(0), journal=journal)
        prior, integrity = ledger.to_dict(), journal.integrity_report()
    plan = {
        "execution_plan_digest": PLAN,
        "owner_budget_approval_digest": PLAN,
        "curl_identity": {},
        "prior_pilot_spend": prior,
        "prior_integrity": integrity,
        "phase_wall_clock_limit_seconds": 5400,
        "runtime_deadline": datetime.now(UTC).timestamp() + 60,
        "phase_cap_usd": "28",
        "phase_caps": CallCaps(2880, 8640, 0, 8640).to_dict(),
        "aggregate_caps": CallCaps(2880, 8640, 0, 8640).to_dict(),
        "execution_jobs": jobs,
        "driver_code_revision": "test",
        "policy_manifests": {
            m: build_reliable_manifest(
                ROOT, config=cfg, code_revision="test", retain_screenshots=m == "history"
            ).to_dict()
            for m in ("history", "stateless")
        },
    }
    driver.write(public / "execution-plan.json", plan)
    monkeypatch.setattr(driver, "PUBLIC", public)
    monkeypatch.setattr(driver, "PRIVATE", tmp_path)
    monkeypatch.setattr(driver, "JOURNAL", journal_path)
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(
        driver, "build_reliable_manifest", lambda root, **kw: build_reliable_manifest(ROOT, **kw)
    )
    monkeypatch.setattr(driver, "canonical_plan", lambda: plan)
    monkeypatch.setattr(driver, "curl_identity", dict)
    monkeypatch.setattr(driver, "git", lambda *args: "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-secret")
    recorded = []

    def failed_episode(journal, *, job, **kwargs):
        recorded.append(job)
        return {
            **job,
            "classification": "infrastructure_failure",
            "success": False,
            "reached_consumers": [],
        }

    monkeypatch.setattr(driver, "run_episode", failed_episode)
    monkeypatch.setattr(driver, "phase_summary", lambda *args: {"scores": {}})
    monkeypatch.setattr(driver, "publish", lambda value: None)
    if driver_name != "reliable_continuation":
        deadline = plan["runtime_deadline"]
        plan["runtime_deadline"] = 1
        driver.write(public / "execution-plan.json", plan)
        with pytest.raises(ValueError, match="six-hour window is exhausted"):
            driver.execute(PLAN)
        with closing(V5AttemptJournal(journal_path)) as journal:
            assert journal.event(f"{driver.PHASE}/started") is None
        plan["runtime_deadline"] = deadline
        driver.write(public / "execution-plan.json", plan)
    driver.execute(PLAN)
    assert recorded == jobs
    with closing(V5AttemptJournal(journal_path)) as journal:
        binding = next(
            e.payload for e in journal.events() if e.kind == "reliable_transport_binding"
        )
        assert Decimal(binding["phase_spend_limit"]) == 28
        assert binding["phase_wire_limit"] == 8640
        if driver_name != "reliable_continuation":
            assert binding["phase_deadline"] == plan["runtime_deadline"]
        assert journal.event(f"{driver.PHASE}/closed").payload == {
            "stop_reason": "all_assignments_completed",
            "transport_idle": True,
        }
        assert journal.call_counts() == (0, 0)
    with pytest.raises(ValueError, match="cannot restart"):
        driver.execute(PLAN)
