"""No-call preparation options use temporary plans and never touch frozen evidence."""

import json
import sys
from dataclasses import asdict

import pytest

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.replication_stop_rules import (
    PROPOSED_STOP_RULE_VERSION,
    ProposedStopRule,
)
from scripts import prepare_grounding_v5_d59_replication as prepare


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    route = claude.REPLICATION_ROUTE
    identity = claude.ClaudeRuntimeIdentity(
        cli_version=route.cli_version,
        auth_method=route.auth_method,
        subscription_type=route.subscription_type,
        requested_model=claude.MODEL,
        reasoning_effort=claude.MODEL_REASONING_EFFORT,
        help_sha256="sha256:fixture",
    )
    output = {
        "execution_plan_digest": "sha256:fixture",
        "policy_manifests": {mode: {"policy_id": mode} for mode in ("history", "stateless")},
        "pilot": {"jobs": []},
        "primary_jobs": [],
        "reliability_jobs": [],
        "execution_enabled": False,
    }
    probes, builds, writes = [], [], []
    monkeypatch.setattr(prepare, "ROOT", tmp_path)

    def probe(selected):
        probes.append(selected)
        return identity

    def build(root, **kwargs):
        builds.append((root, kwargs))
        return output

    monkeypatch.setattr(claude, "probe_claude_route", probe)
    monkeypatch.setattr(prepare, "execution_plan", build)
    monkeypatch.setattr(prepare, "write_outputs", lambda *a, **kw: writes.append((a, kw)))

    def invoke(*arguments):
        monkeypatch.setattr(sys, "argv", ["prepare", "--source-revision", "bound", *arguments])
        prepare.main()

    return tmp_path, identity, probes, builds, writes, invoke


def test_default_preparation_keeps_v1_and_probes_only_the_route(preparation):
    root, identity, probes, builds, writes, invoke = preparation
    invoke()
    assert probes == [claude.REPLICATION_ROUTE]
    assert builds == [
        (
            root,
            {"source_revision": "bound", "runtime_identity": identity, "stop_rule_options": None},
        )
    ]
    assert len(writes) == 1 and writes[0][1] == {
        "verify": False,
        "public": root / prepare.OUTPUT_DIRECTORY,
    }
    assert not (root / prepare.OUTPUT_DIRECTORY).exists()


@pytest.mark.parametrize("scope", ["per_worker", "non_overlapping"])
@pytest.mark.parametrize("action", ["stop", "continue"])
def test_v2_requires_explicit_owner_choices_and_preserves_defaults(preparation, scope, action):
    _, _, probes, builds, writes, invoke = preparation
    invoke(
        "--stop-rule-version",
        PROPOSED_STOP_RULE_VERSION,
        "--failure-streak-scope",
        scope,
        "--policy-violation-action",
        action,
    )
    assert builds[0][1]["stop_rule_options"] == ProposedStopRule(scope, action)
    assert len(probes) == len(writes) == 1


def test_v2_numeric_options_are_validated_and_forwarded(preparation):
    _, _, _, builds, _, invoke = preparation
    invoke(
        "--stop-rule-version",
        PROPOSED_STOP_RULE_VERSION,
        "--failure-streak-scope",
        "per_worker",
        "--policy-violation-action",
        "stop",
        "--consecutive-request-failure-limit",
        "5",
        "--rate-limit-backoff-seconds",
        "10",
        "--maximum-rate-limit-backoff-seconds",
        "20",
    )
    assert builds[0][1]["stop_rule_options"] == ProposedStopRule("per_worker", "stop", 5, 10, 20)


@pytest.mark.parametrize(
    "options", [None, ProposedStopRule("non_overlapping", "continue", 4, 10, 40)]
)
def test_verification_infers_stored_rule_and_does_not_probe(preparation, options):
    root, identity, probes, builds, writes, invoke = preparation
    public = root / prepare.OUTPUT_DIRECTORY
    public.mkdir(parents=True)
    rule = {"version": prepare.STOP_RULE_VERSION}
    if options is not None:
        rule = {"version": PROPOSED_STOP_RULE_VERSION, "amendment_options": asdict(options)}
    stored = {"runtime_identity": identity.to_dict(), "stop_rule": rule}
    path = public / "execution-plan.json"
    path.write_text(json.dumps(stored))
    before = path.read_bytes()
    invoke("--verify")
    assert not probes
    assert builds[0][1]["runtime_identity"] == identity
    assert builds[0][1]["stop_rule_options"] == options
    assert writes[0][1]["verify"] is True
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "arguments",
    [
        ["--failure-streak-scope", "per_worker"],
        ["--stop-rule-version", prepare.STOP_RULE_VERSION, "--policy-violation-action", "stop"],
        ["--consecutive-request-failure-limit", "3"],
        ["--rate-limit-backoff-seconds", "30"],
        ["--maximum-rate-limit-backoff-seconds", "60"],
        ["--stop-rule-version", "unregistered"],
        ["--stop-rule-version", PROPOSED_STOP_RULE_VERSION],
        ["--stop-rule-version", PROPOSED_STOP_RULE_VERSION, "--failure-streak-scope", "per_worker"],
        [
            "--stop-rule-version",
            PROPOSED_STOP_RULE_VERSION,
            "--policy-violation-action",
            "continue",
        ],
        ["--verify", "--stop-rule-version", PROPOSED_STOP_RULE_VERSION],
        ["--verify", "--failure-streak-scope", "per_worker"],
        [
            "--stop-rule-version",
            PROPOSED_STOP_RULE_VERSION,
            "--failure-streak-scope",
            "per_worker",
            "--policy-violation-action",
            "stop",
            "--consecutive-request-failure-limit",
            "0",
        ],
        [
            "--stop-rule-version",
            PROPOSED_STOP_RULE_VERSION,
            "--failure-streak-scope",
            "per_worker",
            "--policy-violation-action",
            "stop",
            "--maximum-rate-limit-backoff-seconds",
            "61",
        ],
    ],
)
def test_invalid_combinations_fail_before_probe_build_or_write(preparation, arguments):
    _, _, probes, builds, writes, invoke = preparation
    with pytest.raises(SystemExit) as exc:
        invoke(*arguments)
    assert exc.value.code == 2
    assert not probes and not builds and not writes
