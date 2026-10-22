"""Authentication dispatch guards retain registered stop reasons and drain results."""

import json
import threading
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import d59_replication as campaign
from scripts import run_grounding_v5_d59_replication as driver


@pytest.mark.parametrize(
    "reason",
    [
        "approved_cap_reached",
        "invocation_ledger_blocked",
        "runtime_window_exhausted",
    ],
)
def test_auth_dispatch_guard_preserves_stop_and_drains_one_inflight_assignment(reason):
    entered = threading.Event()
    release = threading.Event()
    dispatched = []
    calls = 0

    def assignment(job):
        dispatched.append(job["trial_id"])
        entered.set()
        assert release.wait(timeout=5)
        return {**job, "classification": "success"}

    def guard():
        nonlocal calls
        calls += 1
        if calls == 2:
            assert entered.wait(timeout=5)
            release.set()
            raise campaign.CampaignDispatchStopped(reason)

    outcome = campaign.run_campaign(
        [{"trial_id": str(i)} for i in range(4)],
        workers=2,
        run_assignment=assignment,
        ledger_blocked=lambda: False,
        window_exhausted=lambda: False,
        before_dispatch=guard,
    )
    assert outcome.stop_reason == reason
    assert dispatched == ["0"] and outcome.dispatched == 1
    assert [row["trial_id"] for row in outcome.rows] == ["0"]


def test_stale_plan_without_auth_protocol_is_rejected_before_live_identity_probe(monkeypatch):
    root = Path(__file__).parents[2]
    old = json.loads((root / driver.PLAN_PATH).read_text())
    old.pop("authentication_recovery", None)
    replacement = {**old, "authentication_recovery": {"version": "fixture"}}
    monkeypatch.setattr(driver, "require_clean_tracked_worktree", lambda root: None)
    monkeypatch.setattr(driver, "read_object", lambda path: old)
    monkeypatch.setattr(driver, "execution_plan", lambda *a, **k: replacement)
    monkeypatch.setattr(
        driver, "confirmed_live_identity", lambda *a, **k: pytest.fail("must not probe")
    )
    with pytest.raises(ValueError, match="differs from the committed candidate"):
        driver.prepared_inputs("pilot", Path("approval"), Path("output"))
