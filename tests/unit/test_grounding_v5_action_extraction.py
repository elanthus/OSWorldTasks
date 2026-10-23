"""Offline adversarial tests for the opt-in single-fence parser and its journal receipt."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from test_grounding_v5_claude_code_transport import SuccessfulProcess

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.action_extraction import (
    PARSER_VERSION,
    ExtractionMemoryRunner,
    SingleFencedActionPolicy,
    extraction_manifest,
)
from pixelgym.grounding.v5.cli_memory_calibration import CliMemoryPolicy, build_memory_manifest
from pixelgym.grounding.v5.codex_cli_policy import SubscriptionExemptLedger
from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.serialization import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]
ACTION = {"action_type": 1, "x": 100, "y": 200, "key": 0}
TEXT = json.dumps(ACTION)
FENCED = f"```json\n{TEXT}\n```"
PROSE = f"I will select the visible field.\n{FENCED}\nThen I will inspect the next image."


def identity(route=claude.REPLICATION_ROUTE):
    return claude.ClaudeRuntimeIdentity(
        cli_version=route.cli_version,
        auth_method=route.auth_method,
        subscription_type=route.subscription_type,
        requested_model=claude.MODEL,
        reasoning_effort=claude.MODEL_REASONING_EFFORT,
        help_sha256="sha256:fixture",
    )


def response(content=PROSE, route=claude.REPLICATION_ROUTE):
    return {
        "model": claude.MODEL,
        "content": content,
        "usage": {
            "cli_version": route.cli_version,
            "auth_method": route.auth_method,
            "subscription_type": route.subscription_type,
            "command_contract_digest": claude.command_contract_digest(),
            "experiment_charge_usd": "0.00",
            "model_reasoning_effort": claude.MODEL_REASONING_EFFORT,
            "policy_violation": "none",
        },
    }


@pytest.mark.parametrize(
    "content",
    [
        TEXT,
        FENCED,
        FENCED.replace("json", ""),
        PROSE,
        PROSE.replace("\n", "\r\n"),
        f" \n{TEXT}\n ",
        f"Before\n{FENCED}",
    ],
)
def test_exact_action_and_raw_response_preserved(content):
    policy = SingleFencedActionPolicy(runtime_identity=identity())
    encoded = canonical_json_bytes(response(content))
    before = bytes(encoded)
    receipts = []
    policy.on_receipt = receipts.append
    assert policy.parse(encoded, b"{}") == ACTION
    assert encoded == before
    assert len(receipts) == 1 and receipts[0]["decision"] == "accepted"
    assert receipts[0]["canonical_response_digest"] == content_digest(response(content))
    assert receipts[0]["candidate_digest"] == content_digest(ACTION)
    assert TEXT not in json.dumps(receipts)
    if content in (TEXT, FENCED):
        assert claude.ClaudeCodePolicy(runtime_identity=identity()).parse(encoded, b"{}") == ACTION
    if content == PROSE:
        assert receipts[0]["extraction_applied"] is True
        start, end = receipts[0]["selected_character_span"]
        assert content[start:end] == TEXT
        with pytest.raises(ValueError):
            claude.ClaudeCodePolicy(runtime_identity=identity()).parse(encoded, b"{}")


@pytest.mark.parametrize(
    "content",
    [
        "",
        "Here is an action: " + TEXT,
        PROSE + FENCED,
        PROSE + TEXT,
        TEXT + PROSE,
        PROSE + " [another alternative]",
        "`" + PROSE,
        PROSE.replace("json", "python"),
        PROSE.replace("json", "JSON"),
        PROSE.replace("```json", "````json"),
        PROSE.replace("\n```\n", "\n````\n"),
        "```json\n" + TEXT,
        "```json\n" + TEXT + TEXT + "\n```",
        "```json\n[" + TEXT + "]\n```",
        "```json\nnull\n```",
        '```json\n{"action_type":0,"x":0,"x":1,"y":0,"key":0}\n```',
        '```json\n{"action_type":0,"x":0,"y":0}\n```',
        "```json\n" + TEXT[:-1] + ',"reason":"foo"}\n```',
    ],
)
def test_ambiguous_malformed_and_wrong_schema_rejected(content):
    policy = SingleFencedActionPolicy(runtime_identity=identity())
    with pytest.raises((ValueError, TypeError)):
        policy.parse(canonical_json_bytes(response(content)), b"{}")
    assert policy.last_receipt["decision"] == "rejected"
    assert "candidate_digest" not in policy.last_receipt


@pytest.mark.parametrize(
    "field,value",
    [
        ("x", True),
        ("x", 1.0),
        ("y", "2"),
        ("key", None),
        ("x", []),
        ("x", -1),
        ("x", 1024),
        ("y", 768),
        ("key", len(KEY_ALLOWLIST)),
        ("action_type", 3),
        ("action_type", -1),
        ("key", -1),
    ],
)
def test_types_and_all_bounds_are_enforced_without_coercion(field, value):
    action = {**ACTION, field: value}
    policy = SingleFencedActionPolicy(runtime_identity=identity())
    with pytest.raises((ValueError, TypeError)):
        policy.parse(
            canonical_json_bytes(response(f"Before\n```json\n{json.dumps(action)}\n```")), b"{}"
        )
    assert policy.last_receipt["decision"] == "rejected"


@pytest.mark.parametrize(
    "field,value",
    [
        ("cli_version", claude.FROZEN_ROUTE.cli_version),
        ("auth_method", "claude.ai"),
        ("subscription_type", None),
        ("subscription_type", "pro"),
        ("policy_violation", "refused_egress_connect"),
        ("experiment_charge_usd", "0.01"),
        ("command_contract_digest", "sha256:other"),
        ("model_reasoning_effort", "high"),
    ],
)
def test_identity_is_checked_before_any_extraction(field, value):
    value_object = response()
    value_object["usage"][field] = value
    policy = SingleFencedActionPolicy(runtime_identity=identity())
    with pytest.raises(ValueError):
        policy.parse(canonical_json_bytes(value_object), b"{}")
    assert "envelope" not in policy.last_receipt
    assert policy.last_receipt["extraction_applied"] is False


def test_model_mismatch_and_cross_route_rejected_before_decoding():
    policy = SingleFencedActionPolicy(runtime_identity=identity())
    changed = response()
    changed["model"] = "different-model"
    for value in (changed, response(route=claude.FROZEN_ROUTE)):
        with pytest.raises(ValueError):
            policy.parse(canonical_json_bytes(value), b"{}")
        assert "envelope" not in policy.last_receipt


def test_receipt_does_not_reuse_previous_success():
    policy = SingleFencedActionPolicy(runtime_identity=identity())
    policy.parse(canonical_json_bytes(response()), b"{}")
    with pytest.raises(ValueError):
        policy.parse(canonical_json_bytes(response("invalid")), b"{}")
    assert policy.last_receipt["decision"] == "rejected"
    assert "candidate_digest" not in policy.last_receipt
    assert "selected_character_span" not in policy.last_receipt


def test_new_manifest_changes_parser_identity_preserving_security_prompt_and_retries():
    base = claude.build_claude_policy_manifest(
        ROOT,
        code_revision="test",
        runtime_identity=identity(),
        resolved_model=claude.MODEL,
        api_retry_limit=0,
        sandboxed_launch=True,
    )
    previous = deepcopy(base.to_dict())
    manifest = extraction_manifest(ROOT, base)
    assert base.to_dict() == previous
    assert manifest.policy_id != base.policy_id
    assert manifest.parser_version == PARSER_VERSION
    assert base.parser_version == claude.PARSER_VERSION
    for field in (
        "system_prompt_digest",
        "inference_parameters",
        "transport_retry_rule",
        "max_model_attempts_per_action",
        "response_schema_version",
    ):
        assert getattr(manifest, field) == getattr(base, field)
    sandbox = manifest.sandbox.to_dict()
    old_sandbox = base.sandbox.to_dict()
    sandbox.pop("runtime_digest")
    old_sandbox.pop("runtime_digest")
    assert sandbox == old_sandbox


@pytest.mark.parametrize("text,expected_actions", [(PROSE, 1), (PROSE + FENCED, 0)])
def test_real_runner_records_receipt_and_original_response_without_format_retry(
    tmp_path, text, expected_actions
):
    base = claude.build_claude_policy_manifest(
        ROOT,
        code_revision="test",
        runtime_identity=identity(),
        resolved_model=claude.MODEL,
        api_retry_limit=0,
    )
    manifest = extraction_manifest(
        ROOT,
        build_memory_manifest(
            ROOT,
            base,
            retain_screenshots=True,
            allow_connection_retry=True,
        ),
    )
    invocations = claude.ClaudeInvocationJournal(tmp_path / "invocations.sqlite")
    journal = V5AttemptJournal(tmp_path / "attempts.sqlite")
    calls = []

    def factory(*args, **kwargs):
        calls.append(1)
        return SuccessfulProcess(result_text=text)

    transport = claude.ClaudeCodeTransport(
        ledger=SubscriptionExemptLedger(Decimal(1), Decimal(0)),
        invocation_journal=invocations,
        runtime_identity=identity(),
        process_factory=factory,
        allow_timeout_retry=True,
        allow_connection_retry=True,
        api_retry_limit=0,
        environment={"PATH": "/bin"},
    )
    policy = SingleFencedActionPolicy(runtime_identity=identity())
    try:
        result = ExtractionMemoryRunner(
            manifest=manifest,
            journal=journal,
            policy=CliMemoryPolicy(policy, retain_screenshots=True),
            transport=transport,
            approved_caps=CallCaps(1, 2, 0, 2),
        ).run(
            trial_id="extract-fixture",
            task=generate_memory_task(6000),
            backend=FocusMemoryBackend(),
            action_limit=1,
        )
        assert len(calls) == result.model_attempts == 1
        assert result.environment_actions == expected_actions
        events = journal.events("extract-fixture")
        receipt_event = next(e for e in events if e.kind == "action_extraction")
        receipt = receipt_event.payload
        original = journal.get_object(
            receipt["canonical_response_digest"], expected_kind="canonical_provider_response"
        )
        assert json.loads(original)["content"] == text
        assert receipt["decision"] == ("accepted" if expected_actions else "rejected")
        if expected_actions:
            dispatched = next(e for e in events if e.kind == "dispatch_committed")
            assert receipt_event.sequence < dispatched.sequence
        else:
            assert result.classification == "invalid_output"
        assert policy.on_receipt is None
        assert journal.integrity_report()["event_count"] == len(events)
    finally:
        transport.close()
        invocations.close()
        journal.close()
