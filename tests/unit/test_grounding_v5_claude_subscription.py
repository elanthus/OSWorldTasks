"""Offline same-credential tier verification: no network, CLI launch, or sleeps."""

import json
import ssl
import subprocess
import tempfile
from pathlib import Path

import pytest
from test_grounding_v5_d59_replication import plan as offline_plan

from pixelgym.grounding.v5 import claude_code_policy as policy
from pixelgym.grounding.v5 import claude_subscription as subscription
from pixelgym.grounding.v5 import d59_replication as replication
from pixelgym.grounding.v5.evidence import validate_credential_free
from scripts import run_grounding_v5_d59_replication as driver


@pytest.fixture
def profile():
    return {
        "account": {"uuid": "fixture-account", "email": "private@example.invalid"},
        "organization": {"uuid": "fixture-org", "organization_type": "claude_max"},
    }


def test_profile_receipt_retains_only_verified_organization_tier(profile):
    receipt = subscription.max_profile_receipt(profile)
    assert receipt["organization_type"] == "claude_max"
    assert receipt["same_authentication_as_cli"] is True
    assert receipt["authenticated_read_requests"] == 1 and receipt["model_requests"] == 0
    assert "fixture-account" not in json.dumps(receipt)
    assert "fixture-org" not in json.dumps(receipt)
    assert "private@example.invalid" not in json.dumps(receipt)
    validate_credential_free(receipt)


@pytest.mark.parametrize("tier", ["claude_pro", "claude_team", "claude_enterprise", "max", None])
def test_profile_rejects_every_non_max_or_missing_organization_tier(profile, tier):
    profile["organization"]["organization_type"] = tier
    profile["account"]["has_claude_max"] = True
    with pytest.raises(subscription.SubscriptionVerificationError, match="Max organization"):
        subscription.max_profile_receipt(profile)


@pytest.mark.parametrize("body", [None, [], {}, {"account": {}}, {"organization": {}}])
def test_profile_rejects_missing_or_malformed_account_and_organization(body):
    with pytest.raises(subscription.SubscriptionVerificationError):
        subscription.max_profile_receipt(body)


@pytest.mark.parametrize("field", ["account", "organization"])
@pytest.mark.parametrize("identifier", [None, "", " ", 7])
def test_profile_requires_nonempty_identity_fields(profile, field, identifier):
    profile[field]["uuid"] = identifier
    with pytest.raises(subscription.SubscriptionVerificationError):
        subscription.max_profile_receipt(profile)


@pytest.fixture
def wire(monkeypatch, profile):
    calls = []
    state = {"status": 200, "body": json.dumps(profile).encode(), "failure": None}

    class Connection:
        def __init__(self, host, *, timeout, context):
            assert host == "api.anthropic.com"
            assert timeout == 10
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            calls.append(("connect", host))

        def request(self, method, path, *, headers):
            assert method == "GET" and path == "/api/oauth/profile"
            assert headers["Authorization"] == "Bearer opaque-fixture-value"
            assert headers["Cache-Control"] == "no-cache"
            calls.append(("request", method, path))
            if state["failure"] is not None:
                raise state["failure"]

        def getresponse(self):
            return self

        @property
        def status(self):
            return state["status"]

        def read(self, limit):
            assert limit == subscription.MAX_PROFILE_BYTES + 1
            calls.append(("read", limit))
            return state["body"][:limit]

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(subscription.http.client, "HTTPSConnection", Connection)
    monkeypatch.setenv("HTTPS_PROXY", "https://untrusted.invalid")
    return state, calls


def test_profile_request_is_fixed_bounded_and_uses_the_active_credential(wire):
    _, calls = wire
    receipt = subscription.verify_max_subscription("opaque-fixture-value")
    assert receipt["http_status"] == 200
    assert [call[0] for call in calls] == ["connect", "request", "read", "close"]


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 429, 500])
def test_profile_refusals_and_redirects_stop_without_reading_bodies_or_retrying(wire, status):
    state, calls = wire
    state["status"] = status
    with pytest.raises(subscription.SubscriptionVerificationError) as error:
        subscription.verify_max_subscription("opaque-fixture-value")
    assert "opaque-fixture-value" not in str(error.value)
    assert [call[0] for call in calls] == ["connect", "request", "close"]
    if status in (401, 403):
        assert "profile-readable OAuth login is required" in str(error.value)


@pytest.mark.parametrize("body", [b"not json", b"\xff", b"x" * 65537])
def test_profile_invalid_json_and_oversize_responses_stop(wire, body):
    state, calls = wire
    state["body"] = body
    with pytest.raises(subscription.SubscriptionVerificationError):
        subscription.verify_max_subscription("opaque-fixture-value")
    assert calls[-1] == ("close",)


def test_network_failure_hides_underlying_credential_shaped_exception(wire):
    state, calls = wire
    state["failure"] = TimeoutError("unsafe response opaque-fixture-value")
    with pytest.raises(subscription.SubscriptionVerificationError) as error:
        subscription.verify_max_subscription("opaque-fixture-value")
    assert str(error.value) == "OAuth profile verification failed"
    assert error.value.__suppress_context__
    assert calls[-1] == ("close",)


def runtime_probe(profile, tier=None, *, logged_in=True, version=None):
    auth = {"loggedIn": logged_in, "authMethod": "oauth_token", "subscriptionType": tier}
    outputs = {
        "--version": (version or policy.REPLICATION_CLI_VERSION) + "\n",
        "auth": json.dumps(auth),
        "--help": " ".join(policy.sanitized_command_contract()),
    }
    calls, verified = [], []
    environment = {policy.OAUTH_TOKEN_ENVIRONMENT_VARIABLE: "opaque-fixture-value"}

    def run(command, launch_environment):
        assert launch_environment is environment
        assert command[1] in outputs  # --print/model calls are impossible here.
        return subprocess.CompletedProcess(command, 0, outputs[command[1]], "")

    def probe(value):
        calls.append(value)
        return subscription.max_profile_receipt(profile)

    return run, probe, environment, calls, verified


@pytest.mark.parametrize("tier", [None, "max"])
def test_null_cli_tier_and_local_max_hint_both_require_fresh_server_proof(profile, tier):
    run, probe, environment, calls, verified = runtime_probe(profile, tier)
    identity = policy.probe_claude_runtime(
        run=run,
        environment=environment,
        subscription_probe=probe,
        on_subscription_verified=verified.append,
    )
    assert identity.route == policy.REPLICATION_ROUTE
    assert calls == ["opaque-fixture-value"]
    assert verified[0]["cli_reported_subscription_type"] == tier
    validate_credential_free(verified)


@pytest.mark.parametrize("tier", ["pro", "team", "enterprise", "", "unknown"])
def test_explicit_cli_tier_mismatch_is_not_overridden_by_a_max_profile(profile, tier):
    run, probe, environment, calls, verified = runtime_probe(profile, tier)
    with pytest.raises(ValueError, match="approved Max identity"):
        policy.probe_claude_runtime(run=run, environment=environment, subscription_probe=probe)
    assert not calls and not verified


def test_local_max_hint_cannot_cover_up_a_non_max_server_profile(profile):
    profile["organization"]["organization_type"] = "claude_pro"
    run, probe, environment, calls, verified = runtime_probe(profile, "max")
    with pytest.raises(subscription.SubscriptionVerificationError):
        policy.probe_claude_runtime(
            run=run,
            environment=environment,
            subscription_probe=probe,
            on_subscription_verified=verified.append,
        )
    assert calls == ["opaque-fixture-value"] and not verified


def test_missing_active_credential_cannot_fall_back_to_an_unrelated_login(profile, monkeypatch):
    run, probe, environment, calls, _ = runtime_probe(profile)
    environment.clear()
    monkeypatch.setenv(policy.OAUTH_TOKEN_ENVIRONMENT_VARIABLE, "unrelated-value")
    with pytest.raises(ValueError, match="active runner credential"):
        policy.probe_claude_runtime(run=run, environment=environment, subscription_probe=probe)
    assert not calls


@pytest.mark.parametrize("logged_in", [False, None, 1])
def test_logged_out_cli_cannot_be_rescued_by_a_max_profile(profile, logged_in):
    run, probe, environment, calls, _ = runtime_probe(profile, logged_in=logged_in)
    with pytest.raises(ValueError, match="not logged in"):
        policy.probe_claude_runtime(run=run, environment=environment, subscription_probe=probe)
    assert not calls


def test_wrong_cli_route_is_rejected_before_sending_the_profile_request(profile):
    run, probe, environment, calls, _ = runtime_probe(profile, version=policy.CLAUDE_CLI_VERSION)
    with pytest.raises(ValueError, match="approved Max identity"):
        policy.probe_claude_runtime(run=run, environment=environment, subscription_probe=probe)
    assert not calls


def test_every_probe_reverifies_the_server_tier_instead_of_reusing_local_evidence(profile):
    run, probe, environment, calls, _ = runtime_probe(profile)
    policy.probe_claude_runtime(run=run, environment=environment, subscription_probe=probe)
    profile["organization"]["organization_type"] = "claude_pro"
    with pytest.raises(subscription.SubscriptionVerificationError):
        policy.probe_claude_runtime(run=run, environment=environment, subscription_probe=probe)
    assert len(calls) == 2


def test_driver_binds_verified_profile_evidence_without_copying_local_tier_hints(
    profile, monkeypatch, tmp_path
):
    run, probe, environment, _, _ = runtime_probe(profile)
    real_probe = policy.probe_claude_runtime
    identity = real_probe(run=run, environment=environment, subscription_probe=probe)
    monkeypatch.setenv(policy.OAUTH_TOKEN_ENVIRONMENT_VARIABLE, "opaque-fixture-value")
    monkeypatch.setenv("CLAUDE_CODE_SUBSCRIPTION_TYPE", "max")
    monkeypatch.setattr(
        policy,
        "probe_claude_runtime",
        lambda **kwargs: real_probe(
            run=lambda command, launch: run(command, environment),
            environment=kwargs["environment"],
            subscription_probe=probe,
            on_subscription_verified=kwargs["on_subscription_verified"],
        ),
    )
    observed, receipt = driver.confirmed_live_identity({"runtime_identity": identity.to_dict()})
    assert observed == identity and receipt["organization_type"] == "claude_max"
    with tempfile.TemporaryDirectory() as temporary:
        launch = driver.route_probe_environment(Path(temporary))
    assert "CLAUDE_CODE_SUBSCRIPTION_TYPE" not in launch


def test_driver_refuses_an_identity_without_its_mandatory_verification_receipt(
    profile, monkeypatch
):
    run, probe, environment, _, _ = runtime_probe(profile)
    identity = policy.probe_claude_runtime(
        run=run, environment=environment, subscription_probe=probe
    )
    monkeypatch.setenv(policy.OAUTH_TOKEN_ENVIRONMENT_VARIABLE, "opaque-fixture-value")
    monkeypatch.setattr(policy, "probe_claude_runtime", lambda **kwargs: identity)
    with pytest.raises(ValueError, match="fresh same-credential Max verification"):
        driver.confirmed_live_identity({"runtime_identity": identity.to_dict()})


def test_successor_preserves_max_and_scientific_rules_but_requires_a_new_exact_approval(
    monkeypatch,
):
    bound = offline_plan.__wrapped__(monkeypatch)
    root = Path(__file__).parents[2]
    path = root / replication.OUTPUT_DIRECTORY / "execution-plan.json"
    before = path.read_bytes()
    frozen = json.loads(before)
    candidate = replication.execution_plan(
        root,
        source_revision=bound["source_binding"]["source_revision"],
        runtime_identity=policy.ClaudeRuntimeIdentity(**frozen["runtime_identity"]),
        stop_rule_options=replication.stop_rule_options_for_plan(frozen),
    )
    assert {key for key in candidate if candidate[key] != frozen[key]} == {
        "source_binding",
        "policy_manifests",
        "route",
        "execution_plan_digest",
    }
    assert candidate["runtime_identity"] == frozen["runtime_identity"]
    assert candidate["stop_rule"] == frozen["stop_rule"]
    assert candidate["pilot"]["phase_caps"] == frozen["pilot"]["phase_caps"]
    assert (
        candidate["route"]["subscription_verification"]["required_organization_type"]
        == "claude_max"
    )
    assert not candidate["execution_enabled"] and candidate["approved_model_attempt_cap"] == 0
    with pytest.raises(ValueError, match="exact replication plan"):
        replication.validate_approval(
            candidate, replication.expected_approval(frozen, "pilot"), phase="pilot"
        )
    assert path.read_bytes() == before
