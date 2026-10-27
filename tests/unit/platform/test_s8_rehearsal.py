"""Offline S8 rehearsal driver tests: synthetic qualification, unenforced local worker.

Nothing here touches the network, a browser, OSWorld, a model provider, or the private
successor qualification. The worker runs without an OS sandbox, so these results are never
OS-isolation evidence; the opt-in integration test exercises the real Darwin sandbox and a
real loopback listener.
"""

from __future__ import annotations

import json
import socket
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.platform import s7_runtime, s7_source, s8_rehearsal
from pixelgym.platform.s7_budget import S7SendBudget
from pixelgym.platform.s7_evidence import inventory, verify_seal
from pixelgym.platform.s7_policy import POLICY_SHA256, policy_document
from pixelgym.platform.s7_qualification import read_json
from pixelgym.platform.s7_source import write_json
from pixelgym.platform.s8_rehearsal import (
    APPROVAL_SCHEMA,
    EXPECTED_SENDS,
    STEP_NAMES,
    Rehearsal,
    RehearsalStopped,
    bind_approval,
    environment_action,
    inspect_rehearsal,
    prepare,
    render_report,
    stored_report,
)
from scripts import s8_rehearsal as cli
from scripts.s7_serving import no_network
from tests.unit.platform.test_s7_review_boundaries import qualified_fixture  # noqa: F401
from tests.unit.platform.test_s7_serving import fixture_host_source
from tests.unit.platform.test_stateful_control_plane import LocalWorkerLauncher

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def approval_document(qualification: Path, **changes: Any) -> dict[str, Any]:
    caps = policy_document()["deployment_attempt_caps"]
    packages = {}
    for role in ("A", "B"):
        package = read_json(qualification / f"packages/{role}/package.json")
        packages[role] = {
            "policy_id": package["policy_id"],
            "package_sha256": content_digest(package),
            "gate_report_sha256": content_digest(
                read_json(qualification / f"packages/{role}/gate-report.json")
            ),
            "deployment_attempt_cap": caps[role],
            "deployment_tier": "demo",
        }
    document = {
        "schema_version": APPROVAL_SCHEMA,
        "actor": "owner-unit-fixture",
        "decision": "unit fixture approval document; not an owner decision",
        "approved_on": "2026-10-26",
        "qualification_plan_sha256": content_digest(read_json(qualification / "plan.json")),
        "gate_policy_sha256": POLICY_SHA256,
        "packages": packages,
        "activation_approved": True,
        "publication_approved": False,
    }
    document.update(changes)
    return document


def unit_preparer(launcher: LocalWorkerLauncher) -> Any:
    """The real S7 preparer with an unenforced worker launcher; never isolation evidence."""

    def factory(root: Path, qualification: Path, state_root: Path, budget: S7SendBudget) -> Any:
        prepared = s7_runtime.preparer(
            root=root, qualification=qualification, state_root=state_root, budget=budget
        )
        prepared.loader.launcher = launcher
        prepared.loader.require_os_sandbox = False
        prepared.require_os_sandbox = False
        return prepared

    return factory


def synthetic_host(patch: pytest.MonkeyPatch) -> None:
    """The synthetic checkout binds source bytes; the worker imports the real checkout."""
    fixture_host_source(patch)
    patch.setattr(
        s7_runtime,
        "worker_spec",
        lambda root, evidence_root, binding: s7_source.worker_spec(
            REPOSITORY_ROOT, evidence_root, binding
        ),
    )


def prepared_state(qualification: Path, directory: Path, name: str = "state") -> Path:
    approval = directory / f"{name}-approval.json"
    approval.write_bytes(json.dumps(approval_document(qualification)).encode())
    state = directory / name
    prepare(qualification.parent / "checkout", qualification, state, approval_path=approval)
    return state


@pytest.fixture(scope="module")
def rehearsed(qualified_fixture: Path, tmp_path_factory: Any) -> SimpleNamespace:  # noqa: F811
    directory = tmp_path_factory.mktemp("s8-unit")
    launcher = LocalWorkerLauncher()
    with pytest.MonkeyPatch.context() as patch:
        synthetic_host(patch)
        state = prepared_state(qualified_fixture, directory)
        rehearsal = Rehearsal(
            qualified_fixture.parent / "checkout",
            state,
            preparer_factory=unit_preparer(launcher),
        )
        with cli.no_inet_sockets():
            lifecycle = rehearsal.run()
        with no_network():
            checks = cli.finalize(REPOSITORY_ROOT, state)
    return SimpleNamespace(
        qualification=qualified_fixture,
        state=state,
        lifecycle=lifecycle,
        checks=checks,
        launcher=launcher,
        rehearsal=rehearsal,
        steps={step["name"]: step["observations"] for step in lifecycle["steps"]},
    )


# Preparation and approval binding -----------------------------------------------------------


def test_prepare_backs_up_ledger_and_binds_approval(
    qualified_fixture: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    with pytest.MonkeyPatch.context() as patch:
        synthetic_host(patch)
        state = prepared_state(qualified_fixture, tmp_path)
    plan = read_json(state / "plan.json")
    assert plan["approval_sha256"] == content_digest(read_json(state / "approval.json"))
    assert plan["activation_approved"] is True and plan["publication_approved"] is False
    assert plan["expected_sends"] == EXPECTED_SENDS and plan["campaign_cap"] == 111
    assert plan["golden_action_count"] == 20
    receipt = read_json(state / "ledger-backup.json")
    assert receipt["counts"] == {"qualification": 77} and receipt["paused"] is True
    assert receipt["source_identity"] == receipt["backup_identity"]
    assert S7SendBudget(state / "send-budget.sqlite").counts() == {"qualification": 77}
    assert inventory(qualified_fixture) == read_json(qualified_fixture / "seal.json") | {
        "files": inventory(qualified_fixture)["files"]
    }
    with pytest.raises(ValueError, match="outside Git"):
        prepare(
            qualified_fixture.parent / "checkout",
            qualified_fixture,
            REPOSITORY_ROOT / "artifacts" / "s8-unit-refused",
            approval_path=tmp_path / "state-approval.json",
        )
    with pytest.raises(ValueError, match="new private S8 state"):
        prepare(
            qualified_fixture.parent / "checkout",
            qualified_fixture,
            state,
            approval_path=tmp_path / "state-approval.json",
        )


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"activation_approved": False}, "activation explicitly"),
        ({"publication_approved": True}, "never publication"),
        ({"actor": " "}, "actor is required"),
        ({"qualification_plan_sha256": "sha256:" + "0" * 64}, "not bound"),
        ({"gate_policy_sha256": "0" * 64}, "not bound"),
        ({"extra": True}, "fields differ"),
        ({"schema_version": "other"}, "unsupported S8 approval schema"),
    ],
)
def test_bind_approval_refuses_unbound_or_fabricated_terms(
    qualified_fixture: Path,  # noqa: F811
    change: dict[str, Any],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        bind_approval(qualified_fixture, approval_document(qualified_fixture, **change))


@pytest.mark.parametrize("field", ["deployment_attempt_cap", "deployment_tier", "package_sha256"])
def test_bind_approval_refuses_changed_package_terms(qualified_fixture: Path, field: str) -> None:  # noqa: F811
    document = approval_document(qualified_fixture)
    document["packages"]["B"][field] = (
        25
        if field == "deployment_attempt_cap"
        else "standard"
        if field == "deployment_tier"
        else "sha256:" + "1" * 64
    )
    with pytest.raises(ValueError, match="package B differs"):
        bind_approval(qualified_fixture, document)
    assert bind_approval(qualified_fixture, approval_document(qualified_fixture))["actor"]


def test_prepare_refuses_unsealed_or_stopped_qualification(
    qualified_fixture: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    import shutil

    copied = tmp_path / "qualification"
    shutil.copytree(qualified_fixture, copied)
    (copied / "extra.txt").write_text("unlisted")
    approval = tmp_path / "approval.json"
    approval.write_bytes(json.dumps(approval_document(copied)).encode())
    with pytest.MonkeyPatch.context() as patch:
        synthetic_host(patch)
        with pytest.raises(ValueError, match="sealed evidence"):
            prepare(REPOSITORY_ROOT, copied, tmp_path / "state", approval_path=approval)
    assert not (tmp_path / "state").exists()


# Lifecycle evidence ---------------------------------------------------------------------------


def test_unapproved_b_refused_with_pointer_and_counts_unchanged(rehearsed: SimpleNamespace) -> None:
    observed = rehearsed.steps[STEP_NAMES[0]]["unapproved_b_deploy"]
    assert observed["refusal"]["error_class"] == "TransitionError"
    assert "approved" in observed["refusal"]["message"]
    assert observed["active_pointer_before"] is None and observed["active_pointer_after"] is None
    assert observed["fake_sends_unchanged"] is True and observed["b_attempts"] == 0
    assert observed["audit"]["event_type"] == "deployment.deploy_failed"
    assert observed["audit"]["stage"] == "preactivation"
    step = rehearsed.lifecycle["steps"][0]
    assert step["fake_sends_before"] == step["fake_sends_after"] == {"qualification": 77}


def test_a_consumes_one_noop_with_zero_reward(rehearsed: SimpleNamespace) -> None:
    observed = rehearsed.steps[STEP_NAMES[1]]
    assert observed["approval_A"]["deployment_attempt_cap"] == 4
    assert observed["approval_A"]["deployment_tier"] == "demo"
    noop = observed["s8-a-noop-1"]
    assert noop["served_action"] == {"action_type": "NOOP", "x": None, "y": None, "key": None}
    assert noop["reward"] == 0.0 and noop["terminated"] is False
    assert noop["close"]["terminal_classification"] == "closed_by_caller"
    assert rehearsed.lifecycle["steps"][1]["version_attempts_after"] == {"A": 1, "B": 0}
    assert rehearsed.lifecycle["steps"][1]["fake_sends_after"] == {
        "qualification": 77,
        "A": 1,
        "smoke": 1,
    }


def test_recovery_consumes_no_extra_send_and_restores_the_same_deployment(
    rehearsed: SimpleNamespace,
) -> None:
    observed = rehearsed.steps[STEP_NAMES[2]]
    interruption = observed["interruption"]
    assert interruption["boundary"] == "canonical_response_persisted"
    assert interruption["status"] == 503 and interruption["code"] == "episode_host_unavailable"
    assert interruption["journal_step_kinds"] == [
        "episode_initialized",
        "attempt_started",
        "canonical_response_persisted",
    ]
    assert interruption["fake_sends_after_interruption"]["B"] == 1
    assert interruption["caller_checkpoint_unchanged"] is True
    assert interruption["caller_dispatches"] == 0
    assert observed["restore"]["same_deployment"] is True
    assert observed["restore"]["fake_sends_after_restore"]["smoke"] == 3
    recovery = observed["recovery"]
    assert recovery["status"] == 200 and recovery["additional_sends"] == 0
    assert recovery["attempt_count"] == 1
    assert recovery["fake_sends_before"] == recovery["fake_sends_after"]
    assert recovery["identity"]["deployment_id"] == rehearsed.lifecycle["deployments"]["B"]
    assert recovery["journal_step_kinds"].count("attempt_started") == 1
    assert "intent_issued" in recovery["journal_step_kinds"]


def test_changed_screenshot_context_cannot_trigger_blind_redispatch(
    rehearsed: SimpleNamespace,
) -> None:
    probe = rehearsed.steps[STEP_NAMES[2]]["changed_context_probe"]
    assert probe["status"] == 409 and probe["code"] == "intent_reference_invalid"
    assert probe["fake_sends_unchanged"] is True and probe["b_attempts"] == 1
    assert probe["caller_dispatches"] == 0


def test_golden_episode_rewards_one_exactly_once_and_close_is_idempotent(
    rehearsed: SimpleNamespace,
) -> None:
    golden = rehearsed.steps[STEP_NAMES[3]]["golden_episode"]
    assert golden["dispatches"] == 20 and golden["reward_one_count"] == 1
    assert golden["rewards"][-1] == 1.0 and golden["terminated"] is True
    assert golden["close"]["terminal_classification"] == "terminated"
    assert golden["close"]["model_attempts"] == 20
    assert golden["repeated_close_status"] == 200 and golden["repeated_close_identical"] is True
    assert golden["fake_sends_after_repeat"]["B"] == 20
    assert rehearsed.lifecycle["steps"][3]["version_attempts_after"]["B"] == 20


def test_cap_seals_at_exactly_24_before_a_fifth_send(rehearsed: SimpleNamespace) -> None:
    probe = rehearsed.steps[STEP_NAMES[4]]["cap_probe"]
    assert probe["b_attempts_after_each_act"] == [21, 22, 23, 24]
    assert probe["fourth_result_act"] == {
        "status": 200,
        "sealed_failure": "cap_reached",
        "intent_id": None,
    }
    assert probe["b_attempts_after_seal"] == 24 and probe["fake_sends_after_seal"]["B"] == 24
    assert probe["create_after_cap"] == [
        {"status": 429, "code": "deployment_attempt_cap_reached"},
        {"status": 429, "code": "deployment_attempt_cap_reached"},
    ]
    assert probe["fake_sends_after_refusals"] == probe["fake_sends_after_seal"]
    assert probe["b_attempts_after_refusals"] == 24
    assert probe["close"]["terminal_classification"] == "cap_reached"


def test_old_deployment_episodes_are_refused_as_deployment_changed(
    rehearsed: SimpleNamespace,
) -> None:
    redeploy = rehearsed.steps[STEP_NAMES[4]]
    assert redeploy["old_deployment_episode"] == {
        "episode_id": rehearsed.lifecycle["episodes"]["s8-b-golden"],
        "status": 409,
        "code": "deployment_changed",
    }
    assert rehearsed.steps[STEP_NAMES[5]]["old_b_episode"] == {
        "status": 409,
        "code": "deployment_changed",
    }


def test_counts_survive_restart_and_redeploy(rehearsed: SimpleNamespace) -> None:
    deployments = rehearsed.lifecycle["deployments"]
    assert len({deployments[k] for k in ("A", "B", "B-redeploy", "A-rollback")}) == 4
    steps = rehearsed.lifecycle["steps"]
    assert steps[2]["observations"]["restore"]["same_deployment"] is True
    assert steps[4]["observations"]["redeploy_B"]["deployment_id"] == deployments["B-redeploy"]
    assert steps[3]["version_attempts_after"]["B"] == 20
    assert steps[4]["observations"]["cap_probe"]["b_attempts_after_each_act"][0] == 21
    rollback = steps[5]["observations"]["rollback_to_A"]
    assert rollback["a_attempts_before"] == rollback["a_attempts_after"] == 1
    assert steps[5]["version_attempts_after"] == {"A": 2, "B": 24}


def test_rollback_to_exhausted_b_is_refused_with_failure_event_and_pointer_preserved(
    rehearsed: SimpleNamespace,
) -> None:
    observed = rehearsed.steps[STEP_NAMES[5]]
    pointer = observed["rollback_to_A"]
    assert pointer["candidate_id"] == rehearsed.lifecycle["candidates"]["A"]
    assert pointer["action"] == "rollback" and pointer["new_deployment_id"] is True
    rollback = observed["rollback_to_exhausted_B"]
    assert rollback["refusal"]["error_class"] == "TransitionError"
    assert rollback["audit"]["event_type"] == "deployment.rollback_failed"
    assert rollback["audit"]["stage"] == "preactivation"
    assert rollback["pointer_preserved"] is True and rollback["b_attempts"] == 24
    assert rollback["active_pointer_after"]["deployment_id"] == pointer["deployment_id"]
    redeploy = observed["redeploy_exhausted_B"]
    assert redeploy["refusal"]["error_class"] == "StatefulLoadError"
    assert "exhausted" in redeploy["refusal"]["message"]
    assert redeploy["audit"]["event_type"] == "deployment.deploy_failed"
    assert redeploy["pointer_preserved"] is True and redeploy["b_attempts"] == 24
    assert observed["smoke_sends_consumed_by_refusals"] == 1
    final = rehearsed.steps[STEP_NAMES[6]]["final_active_deployment"]
    assert final["deployment_id"] == rehearsed.lifecycle["deployments"]["A-rollback"]
    assert [h["action"] for h in rehearsed.steps[STEP_NAMES[6]]["deployment_history"]] == [
        "deploy",
        "deploy",
        "deploy",
        "rollback",
    ]


def test_every_fake_send_is_reserved_durably_within_caps(rehearsed: SimpleNamespace) -> None:
    counts = rehearsed.lifecycle["fake_sends"]
    assert counts == {"qualification": 77, "A": 2, "B": 24, "smoke": 6}
    assert sum(counts.values()) == 109 <= policy_document()["campaign_cap"]
    assert rehearsed.lifecycle["version_attempts"] == {"A": 2, "B": 24}
    assert rehearsed.lifecycle["real_provider_requests"] == 0
    assert rehearsed.lifecycle["activation_performed"] is True
    assert rehearsed.lifecycle["publication_performed"] is False
    assert rehearsed.lifecycle["milestone_verdict"] is None
    with closing(
        sqlite3.connect(
            (rehearsed.state / "send-budget.sqlite").resolve().as_uri() + "?mode=ro&immutable=1",
            uri=True,
        )
    ) as db:
        reserved = {
            row[0] for row in db.execute("SELECT request_key FROM sends WHERE phase IN ('A','B')")
        }
        assert db.execute("SELECT activation_approval FROM campaign").fetchone() == (
            rehearsed.lifecycle["approval_sha256"],
        )
    journal_keys = set()
    for candidate_id in rehearsed.lifecycle["candidates"].values():
        with closing(
            V5AttemptJournal(
                rehearsed.state / "packages" / candidate_id / "attempts.sqlite", read_only=True
            )
        ) as journal:
            journal_keys.update(
                e.payload["idempotency_key"]
                for e in journal.events()
                if e.kind == "attempt_started"
            )
    assert reserved == journal_keys and len(reserved) == 26
    budget = S7SendBudget(rehearsed.state / "send-budget.sqlite")
    with pytest.raises(Exception, match="cap reached"):
        budget.reserve("B", "fill-unused-allowance")
    assert budget.counts() == counts


def test_cleanup_stopped_every_worker_and_environment(rehearsed: SimpleNamespace) -> None:
    assert rehearsed.launcher.processes and all(
        process.poll() is not None for process in rehearsed.launcher.processes
    )
    assert rehearsed.rehearsal.stack is None
    assert all(env.closed for env in rehearsed.rehearsal.environments)
    assert rehearsed.steps[STEP_NAMES[6]]["workers_stopped"] is True
    assert rehearsed.steps[STEP_NAMES[6]]["environments_closed"] is True
    assert not list(rehearsed.state.rglob("*-wal"))


# Verification, reporting, and the execution boundary ----------------------------------------


def test_stored_verification_replays_caller_actions_and_binds_lifecycle(
    rehearsed: SimpleNamespace,
) -> None:
    verification = read_json(rehearsed.state / "verification.json")
    assert verification["lifecycle_sha256"] == content_digest(
        read_json(rehearsed.state / "lifecycle.json")
    )
    replays = {r["client_episode_ref"]: r for r in verification["replays"]}
    assert replays["s8-b-golden"]["reward_one_count"] == 1
    assert replays["s8-b-golden"]["replayed_dispatches"] == 20
    assert replays["s8-b-cap-probe"]["replayed_dispatches"] == 4
    assert replays["s8-a-noop-1"]["rewards"] == [0.0] == replays["s8-a-noop-2"]["rewards"]
    assert all(
        d["differing_pixels"] == 0 and d["max_channel_delta"] == 0
        for r in verification["replays"]
        for d in r["screenshot_differences"]
    )
    assert verification["recovery"]["additional_sends"] == 0
    assert [f["event_type"] for f in verification["refused_transitions"]] == [
        "deployment.deploy_failed",
        "deployment.rollback_failed",
        "deployment.deploy_failed",
    ]
    golden = rehearsed.lifecycle["episodes"]["s8-b-golden"]
    assert verification["server_episodes"][golden]["terminal_classification"] == "terminated"
    assert verification["operational_records"][golden] == {
        "opened.json": True,
        "closed.json": True,
        "steps": 20,
    }
    assert verification["campaign_total"] == 109


def test_verify_leaves_the_directory_unchanged_and_detects_drift(
    rehearsed: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = inventory(rehearsed.state)
    with no_network():
        result = cli.verify(rehearsed.state)
    assert result["fake_sends"] == rehearsed.lifecycle["fake_sends"]
    assert result["qualification_seal_verified"] is True
    assert result["milestone_verdict"] is None
    assert inventory(rehearsed.state) == before
    drifted = dict(read_json(rehearsed.state / "verification.json"))
    drifted["campaign_total"] = 1
    monkeypatch.setattr(cli, "inspect_rehearsal", lambda _: drifted)
    with pytest.raises(ValueError, match="stored semantic verification differs"):
        cli.verify(rehearsed.state)
    assert inventory(rehearsed.state) == before


def test_acceptance_checks_record_raw_command_status_and_output(
    rehearsed: SimpleNamespace,
) -> None:
    checks = read_json(rehearsed.state / "acceptance-checks.json")["checks"]
    assert [c["name"] for c in checks] == ["stored_verification_replay"]
    check = checks[0]
    assert check["command"][1:4] == ["-m", "scripts.s8_rehearsal", "verify"]
    assert check["exit_status"] == 0, check["stderr"]
    assert '"verification_sha256"' in check["stdout"]
    assert check["elapsed_seconds"] > 0
    verify_seal(rehearsed.state)


def test_report_reads_stored_results_only(
    rehearsed: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("report attempted execution")

    for name in (
        "Rehearsal",
        "build_stack",
        "CallerEnvironment",
        "replay_caller_episode",
        "inspect_rehearsal",
        "PixelGuiEnv",
        "V5FakeBackend",
        "ControlStore",
        "AsgiCaller",
        "LoopbackCaller",
        "create_serving_app",
        "create_episode_router",
        "DeploymentCoordinator",
        "StatefulRuntime",
    ):
        monkeypatch.setattr(s8_rehearsal, name, forbidden)
    for target in (
        "pixelgym.platform.policy_subprocess.SandboxedPolicyProcess",
        "pixelgym.platform.s7_budget.BudgetedScriptedTransport",
        "pixelgym.grounding.v5.runner.ScriptedTransport",
        "pixelgym.scripted_policy.ScriptedStatefulPolicy",
        "pixelgym.platform.stateful_runtime.create_serving_app",
        "pixelgym.platform.stateful_runtime.SandboxedPolicyProcess",
        "pixelgym.platform.stateful_service.create_episode_router",
        "pixelgym.platform.serving_episode.ServingEpisodeHost",
        "pixelgym.platform.s7_runtime.preparer",
    ):
        monkeypatch.setattr(target, forbidden)
    before = inventory(rehearsed.state)
    with no_network():
        report = stored_report(rehearsed.state)
        rendered = render_report(report)
        with pytest.raises(RuntimeError, match="network sockets"):
            socket.socket()
    assert report["milestone_verdict"] is None and report["lifecycle_complete"] is True
    assert report["fake_sends"] == {"qualification": 77, "A": 2, "B": 24, "smoke": 6}
    assert "No milestone verdict" in rendered
    assert "| 5 | redeploy_b_and_exhaust_cap |" in rendered
    assert 'sealed_failure: `"cap_reached"`' in rendered
    assert "deployment_changed" in rendered and "rollback_failed" in rendered
    assert "### stored_verification_replay" in rendered and "Exit status: 0." in rendered
    assert "| s8-b-golden | 20 | 1 | 0 | 0 |" in rendered
    assert "PASS" not in rendered and "FAIL" not in rendered.replace("deploy_failed", "").replace(
        "rollback_failed", ""
    )
    assert inventory(rehearsed.state) == before


def test_cli_report_and_verify_commands(
    rehearsed: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = inventory(rehearsed.state)
    for command in ("verify", "report"):
        monkeypatch.setattr("sys.argv", ["s8_rehearsal", command, "--state", str(rehearsed.state)])
        cli.main()
        output = capsys.readouterr().out
        assert ("verification_sha256" if command == "verify" else "# S8 scripted") in output
    assert inventory(rehearsed.state) == before
    parser_commands = {"prepare", "run", "verify", "report"}
    monkeypatch.setattr("sys.argv", ["s8_rehearsal", "activate"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2 and parser_commands == {"prepare", "run", "verify", "report"}


def test_rerun_is_refused_and_ledger_never_resets(rehearsed: SimpleNamespace) -> None:
    rehearsal = Rehearsal(rehearsed.qualification.parent / "checkout", rehearsed.state)
    with pytest.raises(RehearsalStopped, match="already executed"):
        rehearsal.run()
    assert (
        S7SendBudget(rehearsed.state / "send-budget.sqlite").counts()
        == (rehearsed.lifecycle["fake_sends"])
    )
    verify_seal(rehearsed.state)


# Failure retention ------------------------------------------------------------------------------


def test_stopped_lifecycle_retains_sealed_evidence_and_stops_workers(
    qualified_fixture: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = LocalWorkerLauncher()
    synthetic_host(monkeypatch)
    state = prepared_state(qualified_fixture, tmp_path, "stopped")

    def fault(self: Rehearsal) -> None:
        with self.step(STEP_NAMES[2]) as observations:
            observations["approval_B"] = self.approve("B")
            observations["deploy_B"] = self.deploy("B", "fixture deploy before fault")
            raise RuntimeError("fixture fault: private diagnostic is not copied")

    monkeypatch.setattr(Rehearsal, "step_3", fault)
    rehearsal = Rehearsal(
        qualified_fixture.parent / "checkout", state, preparer_factory=unit_preparer(launcher)
    )
    with cli.no_inet_sockets(), pytest.raises(RuntimeError, match="fixture fault"):
        rehearsal.run()
    failure = read_json(state / "failure.json")
    assert failure["stage"] == STEP_NAMES[2] and failure["cause"] == "RuntimeError"
    assert failure["message"] is None
    assert [s["name"] for s in failure["completed_steps"]] == list(STEP_NAMES[:2])
    assert failure["stopped_step"]["observations"]["deploy_B"]["deployment_id"]
    assert failure["fake_sends"] == {"qualification": 77, "A": 1, "smoke": 2}
    assert failure["activation_performed"] is True and failure["milestone_verdict"] is None
    assert rehearsal.stack is None
    assert launcher.processes and all(p.poll() is not None for p in launcher.processes)
    assert all(env.closed for env in rehearsal.environments)
    assert not (state / "lifecycle.json").exists() and not (state / "seal.json").exists()
    s8_rehearsal.seal(state)
    with no_network():
        report = stored_report(state)
        rendered = render_report(report)
    assert report["lifecycle_complete"] is False
    assert "Lifecycle stopped" in rendered and "approve_deploy_a_one_noop" in rendered
    with pytest.raises(RehearsalStopped, match="already executed"):
        Rehearsal(qualified_fixture.parent / "checkout", state).run()
    assert S7SendBudget(state / "send-budget.sqlite").counts() == failure["fake_sends"]


def test_expectation_violation_is_a_sealed_stop_with_its_message(
    qualified_fixture: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synthetic_host(monkeypatch)
    state = prepared_state(qualified_fixture, tmp_path, "violation")
    rehearsal = Rehearsal(qualified_fixture.parent / "checkout", state)
    monkeypatch.setattr(
        Rehearsal, "make_stack", lambda self: (_ for _ in ()).throw(RehearsalStopped("no stack"))
    )
    with no_network(), pytest.raises(RehearsalStopped, match="no stack"):
        rehearsal.run()
    failure = read_json(state / "failure.json")
    assert failure["message"] == "no stack" and failure["completed_steps"] == []
    assert failure["activation_performed"] is False
    assert failure["fake_sends"] == {"qualification": 77}


def test_lifecycle_steps_must_run_in_order(
    qualified_fixture: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synthetic_host(monkeypatch)
    state = prepared_state(qualified_fixture, tmp_path, "order")
    rehearsal = Rehearsal(qualified_fixture.parent / "checkout", state)
    with pytest.raises(RehearsalStopped, match="in order"), rehearsal.step(STEP_NAMES[1]):
        pass
    assert rehearsal.steps == []


def test_environment_action_translates_the_wire_shape() -> None:
    assert environment_action({"action_type": "NOOP", "x": None, "y": None, "key": None}) == {
        "action_type": 0,
        "x": 0,
        "y": 0,
        "key": 0,
    }
    assert environment_action({"action_type": "CLICK", "x": 30, "y": 40, "key": None}) == {
        "action_type": 1,
        "x": 30,
        "y": 40,
        "key": 0,
    }
    assert environment_action({"action_type": "KEY", "x": None, "y": None, "key": 3})["key"] == 3
    with pytest.raises(KeyError):
        environment_action({"action_type": "DRAG", "x": 1, "y": 1, "key": None})


def test_semantic_verification_refuses_tampered_lifecycle(
    rehearsed: SimpleNamespace, tmp_path: Path
) -> None:
    import shutil

    copied = tmp_path / "copy"
    shutil.copytree(rehearsed.state, copied)
    lifecycle = read_json(copied / "lifecycle.json")
    lifecycle["version_attempts"]["B"] = 23
    (copied / "lifecycle.json").unlink()
    write_json(copied / "lifecycle.json", lifecycle)
    with pytest.raises(ValueError, match="attempt counts differ"):
        inspect_rehearsal(copied)
    (copied / "seal.json").unlink()
    with pytest.raises(FileNotFoundError):
        stored_report(copied)
