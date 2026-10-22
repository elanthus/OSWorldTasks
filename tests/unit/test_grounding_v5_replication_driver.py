"""Exercise the campaign driver with in-process CLI fixtures and real journals."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest
from test_grounding_v5_claude_code_transport import SuccessfulProcess

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.cli_memory_calibration import build_memory_manifest
from pixelgym.grounding.v5.contracts import CallCaps
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.provider_adapters import ClaudeCliAdapter
from scripts import run_grounding_v5_d59_replication as driver

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("expected_route", claude.APPROVED_ROUTES)
@pytest.mark.parametrize("response_route", claude.APPROVED_ROUTES)
def test_parser_binds_to_its_runtime_route(expected_route, response_route):
    identity = claude.ClaudeRuntimeIdentity(
        cli_version=expected_route.cli_version,
        auth_method=expected_route.auth_method,
        subscription_type=expected_route.subscription_type,
        requested_model=claude.MODEL,
        reasoning_effort=claude.MODEL_REASONING_EFFORT,
        help_sha256="sha256:fixture",
    )
    response = {
        "model": claude.MODEL,
        "content": '{"action_type":0,"x":0,"y":0,"key":0}',
        "usage": {
            "auth_method": response_route.auth_method,
            "subscription_type": response_route.subscription_type,
            "cli_version": response_route.cli_version,
            "command_contract_digest": claude.command_contract_digest(),
            "experiment_charge_usd": "0.00",
            "model_reasoning_effort": claude.MODEL_REASONING_EFFORT,
            "policy_violation": "none",
        },
    }
    parser = claude.ClaudeCodePolicy(runtime_identity=identity)
    adapter = object.__new__(ClaudeCliAdapter)
    adapter.runtime_identity = identity
    assert adapter._policy("fixture").expected_route == expected_route
    assert claude.ClaudeCodePolicy().expected_route == claude.FROZEN_ROUTE
    if expected_route == response_route:
        assert parser.parse(json.dumps(response).encode(), b"{}") == {
            "action_type": 0,
            "x": 0,
            "y": 0,
            "key": 0,
        }
    else:
        with pytest.raises(ValueError, match="identity"):
            parser.parse(json.dumps(response).encode(), b"{}")


@pytest.mark.parametrize("scenario", ["invalid", "cap", "pre_send"])
def test_driver_summaries_use_independent_reader_counter_and_bound_source(
    tmp_path, monkeypatch, scenario
):
    cap_hit = scenario == "cap"
    plan = json.loads((ROOT / driver.PLAN_PATH).read_text())
    plan.pop("authentication_recovery", None)  # This fixture exercises the legacy summary path.
    identity = claude.ClaudeRuntimeIdentity(**plan["runtime_identity"])
    base = claude.build_claude_policy_manifest(
        ROOT,
        code_revision="offline",
        runtime_identity=identity,
        resolved_model=claude.MODEL,
        api_retry_limit=0,
    )
    manifests = {
        mode: build_memory_manifest(
            ROOT, base, retain_screenshots=mode == "history", allow_connection_retry=True
        )
        for mode in ("history", "stateless")
    }
    jobs = [
        {
            "trial_id": "offline-" + mode,
            "mode": mode,
            "phase": "primary",
            "seed": 6000,
            "repeat": 0,
            "action_limit": 26,
        }
        for mode in manifests
    ]
    approval = {
        "approved_caps": CallCaps(52, 1 if cap_hit else 4, 0, 1 if cap_hit else 4).to_dict(),
        "approved_runtime_window_hours": 6,
    }
    binding = {"fixture": "offline"}
    output = tmp_path / "output"
    output.mkdir()
    for name, value in [
        ("execution-plan", plan),
        ("owner-approval", approval),
        ("execution-binding", binding),
    ]:
        driver.write_object(output / (name + ".json"), value)
    monkeypatch.setattr(
        driver, "prepared_inputs", lambda *a: (plan, approval, jobs, identity, binding)
    )
    monkeypatch.setattr(driver, "live_manifests", lambda *a, **kw: manifests)
    monkeypatch.setattr(driver, "sandbox_launch", lambda output: None)
    monkeypatch.setattr(driver, "retain", lambda *a: {"fixture": "archive"})
    both_sends = threading.Barrier(2)

    class Process(SuccessfulProcess):
        def __init__(self):
            super().__init__(
                result_text=('{"action_type":0,"x":0,"y":0,"key":0}' if cap_hit else "{}")
            )

        def communicate(self, input=None, timeout=None):
            if not cap_hit:
                both_sends.wait(timeout=5)
            return super().communicate(input, timeout)

    real_transport = claude.ClaudeCodeTransport

    def process_factory(*args, **kwargs):
        if scenario == "pre_send":
            raise FileNotFoundError("offline launch fixture")
        return Process()

    def transport(**kwargs):
        return real_transport(
            **kwargs,
            environment={"PATH": "/bin", "HOME": str(tmp_path)},
            process_factory=process_factory,
        )

    monkeypatch.setattr(claude, "ClaudeCodeTransport", transport)
    real_events = V5AttemptJournal.events
    readers = []

    def events(journal, trial_id=None):
        if trial_id is None:
            # Summary scans may run only on an independent read-only connection.
            assert journal._connection.execute("PRAGMA query_only").fetchone()[0] == 1
            readers.append(journal)
        return real_events(journal, trial_id)

    monkeypatch.setattr(V5AttemptJournal, "events", events)

    def no_git(*a, **kw):
        raise AssertionError("summary must use the plan source rather than invoking git")

    monkeypatch.setattr(subprocess, "check_output", no_git)
    driver.execute(
        "pilot",
        tmp_path / "approval.json",
        output,
        workers=2 if scenario == "invalid" else 1,
        retention_archive=tmp_path / "archive",
        retention_backup=tmp_path / "backup",
    )
    summary = driver.read_object(output / "summary.json")
    assert summary["source_revision"] == plan["source_binding"]["source_revision"]
    count = 2 if scenario == "invalid" else 1
    assert summary["attempted"] == summary["completed"] == count
    assert summary["unrun"] == 2 - count
    assert summary["stop_reason"] == (
        "approved_cap_reached"
        if cap_hit
        else "execution_error"
        if scenario == "pre_send"
        else "completed_all_assignments"
    )
    assert summary["phase_usage"]["primary"]["provider_wire_requests"] == count
    assert all(
        row["model_attempts"] == row["provider_wire_requests"] == 1 for row in summary["results"]
    )
    assert len(readers) == count + 1 and all(reader is readers[0] for reader in readers)
    if cap_hit:
        row = summary["results"][0]
        assert row["incomplete_assignment"] is True and row["success"] is False
        assert row["environment_actions"] == row["environment_actions_dispatched"] == 1
        assert row["final_policy_checkpoint_digest"] is None
    if scenario == "pre_send":
        row = summary["results"][0]
        assert row["classification"] == "request_failure"
        assert row["request_failure_phase"] == "pre_send"
        assert row["success"] is False
        assert row["environment_actions_dispatched"] == 0
