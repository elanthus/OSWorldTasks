"""History boundary and CLI dispatch regression tests."""

import base64
from decimal import Decimal

import pytest

from pixelgym.grounding.v5 import codex_cli_policy as codex
from pixelgym.grounding.v5.cli_memory_calibration import CliMemoryPolicy
from pixelgym.grounding.v5.contracts import sha256_bytes
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.runner import PolicyVisibleResult


def test_matched_first_request_history_retention_and_reset():
    backend = FocusMemoryBackend()
    try:
        backend.reset(5112)
        frame = backend.screenshot().tobytes()
        policies = [
            CliMemoryPolicy(codex.CodexCliPolicy(codex.LUNA_MEDIUM), retain_screenshots=h)
            for h in (True, False)
        ]
        states = [p.observe_screenshot(p.reset("test task"), frame) for p in policies]
        first = [p.build_request(s, frame) for p, s in zip(policies, states, strict=True)]
        assert first[0] == first[1]
        assert first[0]["image_history"] == []
        action = {"action_type": 0, "x": 0, "y": 0, "key": 0}
        result = PolicyVisibleResult("sha256:" + sha256_bytes(frame), 0.0, False, False, 0)
        states = [
            p.observe_screenshot(p.post_dispatch_state(s, action, result), frame)
            for p, s in zip(policies, states, strict=True)
        ]
        second = [p.build_request(s, frame) for p, s in zip(policies, states, strict=True)]
        assert len(second[0]["image_history"]) == 1
        assert second[1] == first[1]
        assert "After image 1:" in second[0]["prompt"]
        assert policies[0].reset("new") == b'{"actions":[],"frames":[],"instruction":"new"}'
    finally:
        backend.close()


def test_history_requires_checkpointed_frame():
    p = CliMemoryPolicy(codex.CodexCliPolicy(codex.LUNA_MEDIUM), retain_screenshots=True)
    with pytest.raises(ValueError, match="checkpointed"):
        p.build_request(p.reset("test"), bytes(1024 * 768 * 3))


def test_codex_history_digest_rejected_before_send(tmp_path):
    journal = codex.CodexCliInvocationJournal(tmp_path / "invocations.sqlite", codex.LUNA_MEDIUM)
    identity = codex.CodexRuntimeIdentity(
        codex.CODEX_CLI_VERSION,
        codex.AUTH_MODE,
        "gpt-5.6-luna",
        "3000",
        ("medium",),
        ("text", "image"),
        272000,
        "sha256:help",
        "sha256:features",
        True,
    )
    ledger = codex.SubscriptionExemptLedger(Decimal(1), Decimal(0))
    transport = codex.CodexCliTransport(
        ledger=ledger,
        invocation_journal=journal,
        runtime_identity=identity,
        config=codex.LUNA_MEDIUM,
    )
    p = CliMemoryPolicy(codex.CodexCliPolicy(codex.LUNA_MEDIUM), retain_screenshots=False)
    request = p.build_request(p.reset("test"), bytes(1024 * 768 * 3))
    request["image_history"] = [
        {"image_png_base64": base64.b64encode(b"prior").decode(), "image_sha256": "sha256:wrong"}
    ]
    try:
        assert (
            transport._validate_request(request, deadline_seconds=95)
            == "image_history_digest_mismatch"
        )
        assert ledger.processes_started == 0
        request["image_history"] *= 32
        assert transport._validate_request(request, deadline_seconds=95) == "image_history_invalid"
    finally:
        transport.close()
        journal.close()


def test_runner_rejects_manifest_mode_mismatch():
    from types import SimpleNamespace

    from pixelgym.grounding.v5.cli_memory_calibration import CliMemoryRunner
    from pixelgym.grounding.v5.contracts import CallCaps

    manifest = SimpleNamespace(
        inference_parameters=(),
        max_model_attempts_per_action=1,
        memory_policy_version="pixelgym-cli-screenshot-stateless-v1",
        transport_retry_rule="cli-one-confirmed-stopped-timeout-retry-v1",
    )
    with pytest.raises(ValueError, match="memory mode"):
        CliMemoryRunner(
            journal=object(),
            manifest=manifest,
            transport=object(),
            policy=CliMemoryPolicy(codex.CodexCliPolicy(), retain_screenshots=True),
            approved_caps=CallCaps(32, 32, 0, 32),
        )


def test_expired_phase_stops_before_screenshot_or_provider():
    from pixelgym.grounding.v5.cli_memory_calibration import CliMemoryRunner

    runner = object.__new__(CliMemoryRunner)
    runner.time_exhausted = lambda: True
    assert runner._act(state=b"checkpoint") == {
        "classification": "phase_time_stop",
        "state": b"checkpoint",
    }
