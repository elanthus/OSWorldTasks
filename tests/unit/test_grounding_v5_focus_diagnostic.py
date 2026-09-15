"""Real renderer and journal fixtures for the bounded repair diagnostic."""

import json
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from pixelgym.grounding.v5.contracts import CallCaps
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_focus_diagnostic import cases, prefix_actions, run_condition
from pixelgym.grounding.v5.policies import click_action, key_action
from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger
from pixelgym.grounding.v5.request_budget_v2 import IsolatedRequestBoundTransport
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    build_screenshot_policy_manifest,
)
from tests.unit.test_grounding_v5_request_budget import CONFIG
from tests.unit.test_grounding_v5_timeout_reuse import response

ROOT = Path(__file__).parents[2]


def apply(backend, actions):
    for action in actions:
        if action["action_type"] == 1:
            backend.click(action["x"], action["y"])
        else:
            from pixelgym.actions import KEY_ALLOWLIST

            backend.key(KEY_ALLOWLIST[action["key"]])


def test_focus_changes_only_input_pixels_and_preserves_checkpoint_semantics():
    old, new = MemoryBackend(), FocusMemoryBackend()
    try:
        for backend in (old, new):
            backend.reset(5000)
            apply(backend, prefix_actions(5000, "unfocused"))
        assert np.array_equal(old.screenshot(), new.screenshot())
        x0, y0, x1, y1 = next(
            c.bbox for c in new.visible_controls() if c.control_id == "text_input"
        )
        for backend in (old, new):
            backend.click(*backend.control_center("text_input"))
        assert old.checkpoint() == new.checkpoint()
        mask = np.any(old.screenshot() != new.screenshot(), axis=2)
        assert mask.sum() > 0
        mask[y0 : y1 + 1, x0 : x1 + 1] = False
        assert not mask.any()
        saved = new.checkpoint()
        frame = new.screenshot()
        new.restore(saved)
        assert np.array_equal(frame, new.screenshot())
        for backend in (old, new):
            for char in backend.task.stages[1].required_text:
                backend.key(char)
            backend.click(*backend.control_center("continue"))
        assert old.checkpoint() == new.checkpoint() and new.stage_index == 2
        assert np.array_equal(old.screenshot(), new.screenshot())
    finally:
        old.close()
        new.close()


@pytest.mark.parametrize("index", range(10))
def test_declared_single_action_diagnostic_and_idempotent_resume(tmp_path, index):
    case = cases()[index]
    planner = FocusMemoryBackend()
    try:
        planner.reset(case["seed"])
        apply(planner, prefix_actions(case["seed"], case["state_name"]))
        name = case["state_name"]
        if name == "unfocused":
            action = click_action(*planner.control_center("text_input"))
        elif name == "complete":
            action = click_action(*planner.control_center("continue"))
        elif name in ("focused_empty", "partial"):
            count = len(json.loads(planner.checkpoint())["text_value"])
            action = key_action(planner.task.stages[1].required_text[count])
        else:
            action = click_action(
                *planner.control_center(planner.task.stages[case["stage_index"]].target_control_id)
            )
    finally:
        planner.close()
    if action["action_type"] == 1:
        action.update(x=int(action["x"] * 1000 / 1024), y=int(action["y"] * 1000 / 768))
    body = json.loads(response().read())
    body["choices"][0]["message"]["content"] = json.dumps(action)
    import io

    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        transport = IsolatedRequestBoundTransport(
            CONFIG,
            lifecycle_id="test",
            ledger=ledger,
            environment={"OPENROUTER_API_KEY": "fixture"},
            urlopen=lambda *a, **kw: io.BytesIO(json.dumps(body).encode()),
        )
        manifest = build_screenshot_policy_manifest(
            ROOT, config=CONFIG, code_revision="fixture", retain_screenshots=False
        )

        def run():
            return run_condition(
                journal,
                case=case,
                trial_id="test",
                mode="stateless",
                policy=ScreenshotMemoryPolicy(CONFIG, retain_screenshots=False),
                manifest=manifest,
                transport=transport,
                caps=CallCaps(400, 20, 0, 20),
                plan_digest="fixture",
            )

        row = run()
        assert row["desired_transition"] and row["action_dispatched"]
        count = len(journal.events())
        assert run() == row and len(journal.events()) == count
        assert ledger.wire_requests_sent == 1


def test_runner_discards_late_action_but_reconciles_late_charge(tmp_path, monkeypatch):
    import threading

    from pixelgym.grounding.v5 import memory_focus_diagnostic as diagnostic
    from pixelgym.grounding.v5.runner import BoundedCallResult

    entered, release = threading.Event(), threading.Event()
    threads = []

    class TimeoutAtWire:
        def execute(self, call, *, timeout_seconds):
            thread = threading.Thread(target=call)
            threads.append(thread)
            thread.start()
            assert entered.wait(5)
            return BoundedCallResult(None, True)

    real_runner = diagnostic.MemoryDiagnosticRunner
    monkeypatch.setattr(
        diagnostic,
        "MemoryDiagnosticRunner",
        lambda **kwargs: real_runner(**kwargs, deadline_executor=TimeoutAtWire()),
    )

    def wire(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return response()

    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        transport = IsolatedRequestBoundTransport(
            CONFIG,
            lifecycle_id="deadline",
            ledger=ledger,
            environment={"OPENROUTER_API_KEY": "fixture"},
            urlopen=wire,
        )
        manifest = build_screenshot_policy_manifest(
            ROOT,
            config=CONFIG,
            code_revision="fixture",
            retain_screenshots=False,
        )
        try:
            row = run_condition(
                journal,
                case=cases()[0],
                trial_id="late",
                mode="stateless",
                policy=ScreenshotMemoryPolicy(CONFIG, retain_screenshots=False),
                manifest=manifest,
                transport=transport,
                caps=CallCaps(400, 20, 0, 20),
                plan_digest="fixture",
            )
            assert row["classification"] == "infrastructure_failure"
            assert not row["action_dispatched"] and transport.retired
            assert ledger.unknown_reservation_usd > 0
        finally:
            release.set()
            for thread in threads:
                thread.join(5)
                assert not thread.is_alive()
        assert not any(e.kind == "dispatch_started" for e in journal.events("late"))
        assert not any(e.kind == "canonical_response_persisted" for e in journal.events("late"))
        assert ledger.spent_usd == Decimal("0.01")
        assert ledger.unknown_reservation_usd == 0 and not ledger.blocked
