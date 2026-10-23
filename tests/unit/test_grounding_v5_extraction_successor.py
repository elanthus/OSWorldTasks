"""Successor source binding, approvals and read-only diagnostic fixture coverage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
from test_grounding_v5_action_extraction import ACTION, PROSE, identity, response

from pixelgym.grounding.v5 import d59_extraction_successor as successor
from pixelgym.grounding.v5 import d59_replication as replication
from pixelgym.grounding.v5.action_extraction import PARSER_VERSION
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from scripts import diagnose_grounding_v5_d59_action_extraction as diagnostic
from scripts import prepare_grounding_v5_d59_extraction as prepare
from scripts import run_grounding_v5_d59_replication as driver

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def candidate(monkeypatch):
    def digest(root, _revision, path):
        return "sha256:" + hashlib.sha256((root / path).read_bytes()).hexdigest()

    monkeypatch.setattr(replication, "_validate_policy_inputs", lambda *a: None)
    monkeypatch.setattr(replication, "_sha256_at_revision", digest)
    monkeypatch.setattr(successor, "_sha256_at_revision", digest)
    original = json.loads((ROOT / successor.ORIGINAL_PLAN_PATH).read_text())
    from pixelgym.grounding.v5.claude_code_policy import ClaudeRuntimeIdentity

    return successor.execution_plan(
        ROOT,
        source_revision="test-source",
        runtime_identity=ClaudeRuntimeIdentity(**original["runtime_identity"]),
    )


def test_successor_is_separate_disabled_and_preserves_registered_design(candidate):
    original = json.loads((ROOT / successor.ORIGINAL_PLAN_PATH).read_text())
    assert candidate["execution_plan_digest"] != original["execution_plan_digest"]
    assert candidate["execution_enabled"] is candidate["subscription_execution_authorized"] is False
    assert (
        candidate["approved_model_attempt_cap"]
        == candidate["approved_provider_wire_request_cap"]
        == 0
    )
    assert candidate["provider_calls_made_during_successor_preparation"] == 0
    assert candidate["relationship"]["independent_confirmation"] is False
    for field in (
        "phase_caps",
        "allocation",
        "primary_comparison",
        "runtime",
        "runtime_identity",
        "route",
        "authentication_recovery",
        "stop_rule",
        "security_boundary",
        "concurrency",
    ):
        assert candidate[field] == original[field]
    assert len(replication.phase_jobs(candidate, "pilot")) == 4
    assert len(replication.phase_jobs(candidate, "full")) == 432
    assert not {j["trial_id"] for j in replication.phase_jobs(candidate, "full")} & {
        j["trial_id"] for j in replication.phase_jobs(original, "full")
    }
    for mode in ("history", "stateless"):
        assert candidate["policy_manifests"][mode]["parser_version"] == PARSER_VERSION
        assert (
            candidate["policy_manifests"][mode]["policy_id"]
            != original["policy_manifests"][mode]["policy_id"]
        )
    approval = replication.expected_approval(candidate, "pilot")
    replication.validate_approval(candidate, approval, phase="pilot")
    with pytest.raises(ValueError, match="exact"):
        replication.validate_approval(
            candidate, replication.expected_approval(original, "pilot"), phase="pilot"
        )
    assert set(successor.SOURCE_FILES) <= candidate["source_binding"]["files"].keys()


def test_exact_changed_field_inventory_and_offline_outputs(candidate, monkeypatch):
    original = json.loads((ROOT / successor.ORIGINAL_PLAN_PATH).read_text())
    monkeypatch.setattr(prepare, "execution_plan", lambda *a, **kw: candidate)
    result = prepare.outputs(ROOT, "test-source")
    assert json.loads(result["execution-plan.json"]) == candidate
    changes = json.loads(result["plan-field-changes.json"])
    assert changes["changed_fields"] == successor.changed_fields(original, candidate)
    paths = {item["field"] for item in changes["changed_fields"]}
    assert "/primary_jobs" in paths and "/pilot/jobs" in paths
    assert "/runtime" not in paths and "/security_boundary" not in paths
    assert changes["owner_approval_recorded"] is False
    assert successor.changed_fields(candidate, candidate) == []


def test_changed_frozen_identity_rejected():
    with pytest.raises(ValueError, match="exact runtime identity"):
        successor.execution_plan(ROOT, source_revision="test", runtime_identity=identity())


@pytest.mark.parametrize("alteration", ["cap", "parser", "schema"])
def test_prepared_inputs_reject_tampered_successor_before_auth_or_sandbox(
    tmp_path, monkeypatch, candidate, alteration
):
    bad = deepcopy(candidate)
    if alteration == "cap":
        bad["phase_caps"]["aggregate"]["model_attempt_cap"] += 1
    elif alteration == "parser":
        bad["policy_manifests"]["history"]["parser_version"] = "unregistered"
    else:
        bad["schema_version"] = "unsupported-successor"
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(bad))
    monkeypatch.setattr(driver, "require_clean_tracked_worktree", lambda *a: None)
    monkeypatch.setattr(successor, "execution_plan", lambda *a, **kw: candidate)
    monkeypatch.setattr(driver, "execution_plan", lambda *a, **kw: candidate)

    def forbidden(*args, **kwargs):
        raise AssertionError("no authentication, sandbox probe or provider launch permitted")

    monkeypatch.setattr(driver, "confirmed_live_identity", forbidden)
    monkeypatch.setattr(driver, "run_cli_sandbox_probe", forbidden)
    with pytest.raises(ValueError, match="committed candidate"):
        driver.prepared_inputs(
            "pilot", tmp_path / "missing-approval.json", tmp_path / "out", plan_path=path
        )


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    plan = {
        "runtime_identity": identity().to_dict(),
        "primary_jobs": [{"trial_id": "one", "phase": "primary", "mode": "history"}],
        "reliability_jobs": [],
    }
    plan["execution_plan_digest"] = content_digest(plan)
    monkeypatch.setattr(diagnostic, "ORIGINAL_PLAN_DIGEST", plan["execution_plan_digest"])
    path = tmp_path / "attempts.sqlite"
    journal = V5AttemptJournal(path)
    for step, text in enumerate((json.dumps(ACTION), PROSE, "Before\n```json\n{}\n```")):
        digest = journal.put_object(
            "canonical_provider_response", json.dumps(response(text), sort_keys=True).encode()
        )
        journal.append_event(
            event_key=f"completed-{step}",
            kind="attempt_completed",
            trial_id="one",
            step_index=step,
            attempt_index=0,
            payload={"response_digest": digest},
        )
        if step:
            journal.append_event(
                event_key=f"failed-{step}",
                kind="sealed_unsuccessful_result",
                trial_id="one",
                step_index=step,
                attempt_index=0,
                payload={"failure_code": "parse_failure"},
            )
    journal.close()
    return path, plan


def test_read_only_diagnostic_keeps_originals_and_discloses_limits(evidence):
    path, plan = evidence
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = diagnostic.diagnose(path, plan)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert report["counts"] == {
        "previously_valid_unchanged": 1,
        "newly_recoverable_action": 1,
        "still_rejected": 1,
    }
    assert report["terminal_parser_failures_examined"] == 2
    assert report["environment_actions_replayed"] == report["provider_calls"] == 0
    assert report["successor_task_success_inferred"] is report["original_results_changed"] is False
    assert "Before" not in json.dumps(report)
    assert not path.with_suffix(".sqlite-wal").exists()


@pytest.mark.parametrize("case", ["corrupt", "missing-seal"])
def test_diagnostic_fails_closed_on_incomplete_or_corrupt_evidence(evidence, case):
    path, plan = evidence
    with sqlite3.connect(path) as connection:
        if case == "corrupt":
            connection.execute("UPDATE objects SET data=?", (b"altered",))
        else:
            connection.execute("DELETE FROM events WHERE kind='sealed_unsuccessful_result'")
    with pytest.raises(ValueError, match="corrupt|terminal parser-failure"):
        diagnostic.diagnose(path, plan)


@pytest.mark.parametrize("case", ["valid", "identity-mismatch", "sandbox-refused"])
def test_successor_prepare_requires_fresh_identity_and_sandbox_receipts(
    tmp_path, monkeypatch, candidate, case
):
    from dataclasses import replace
    from types import SimpleNamespace

    from pixelgym.grounding.v5 import claude_code_policy as claude

    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate))
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps(replication.expected_approval(candidate, "pilot")))
    monkeypatch.setattr(driver, "require_clean_tracked_worktree", lambda *a: None)
    monkeypatch.setattr(successor, "execution_plan", lambda *a, **kw: candidate)
    frozen = claude.ClaudeRuntimeIdentity(**candidate["runtime_identity"])
    calls = []

    def probe(**kwargs):
        calls.append("identity")
        kwargs["on_subscription_verified"]({"fixture": "same-credential-Max-profile"})
        return (
            replace(frozen, help_sha256="sha256:wrong-cli")
            if case == "identity-mismatch"
            else frozen
        )

    def sandbox_probe(**kwargs):
        calls.append("sandbox")
        return SimpleNamespace(
            passed=case != "sandbox-refused", to_dict=lambda: {"fixture": "sandbox"}
        )

    monkeypatch.setattr(claude, "probe_claude_runtime", probe)
    monkeypatch.setattr(driver, "cli_executable", lambda: ROOT / "pyproject.toml")
    monkeypatch.setattr(driver, "run_cli_sandbox_probe", sandbox_probe)
    if case == "valid":
        plan, _, jobs, observed, binding = driver.prepared_inputs(
            "pilot",
            approval,
            tmp_path / "output",
            credential_value="fixture-credential",
            plan_path=path,
        )
        assert plan == candidate and observed == frozen and len(jobs) == 4
        assert set(binding["source_files"]) == set(successor.SOURCE_FILES)
        assert binding["provider_calls_made_during_binding"] == 0
        assert calls == ["identity", "sandbox"]
    else:
        with pytest.raises(ValueError, match="identity differs|sandbox probe"):
            driver.prepared_inputs(
                "pilot",
                approval,
                tmp_path / "output",
                credential_value="fixture-credential",
                plan_path=path,
            )
        assert calls == (["identity"] if case == "identity-mismatch" else ["identity", "sandbox"])


def test_field_inventory_uses_exact_json_pointers_and_distinguishes_missing_null_and_types():
    assert successor.changed_fields({"a/b~c": 1}, {"a/b~c": True})[0]["field"] == "/a~1b~0c"
    added = successor.changed_fields({}, {"added": None})[0]
    assert added["original_present"] is False and added["successor_present"] is True
    assert added["original"] is None and added["successor"] == content_digest(None)


def test_diagnostic_binds_original_audit_lineage(evidence):
    path, plan = evidence
    first = diagnostic.diagnose(path, plan)
    lineage = first["journal_lineage"]
    assert (
        diagnostic.diagnose(path, plan, expected_integrity=lineage)[
            "lineage_matches_original_audit"
        ]
        is True
    )
    with pytest.raises(ValueError, match="original audited report"):
        diagnostic.diagnose(
            path, plan, expected_integrity={**lineage, "event_chain_digest": "changed"}
        )
