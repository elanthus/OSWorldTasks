"""Opt-in real OS enforcement proof for the S5 serving policy subprocess."""

from __future__ import annotations

import http.server
import json
import os
import platform
import threading
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import pytest

from pixelgym.grounding.v5.contracts import TransportOutcome, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.platform.policy_subprocess import PolicyWorkerSpec, SandboxedPolicyProcess
from pixelgym.platform.serving_episode import ServingEpisodeHost, SQLiteServingSessionStore
from pixelgym.platform.stateful_contracts import (
    EvidenceClass,
    ReportedResult,
    StatefulPolicyPackage,
)
from tests.unit.platform.stateful_fixtures import evidence, identity, v5_manifest

pytestmark = [
    pytest.mark.v5_sandbox_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_V5_SANDBOX_TESTS") != "1" or platform.system() != "Darwin",
        reason="set PIXELGYM_RUN_V5_SANDBOX_TESTS=1 on Darwin outside a parent sandbox",
    ),
]

EPISODE_ID = "ep-" + "5" * 32
NOOP = {"action_type": 0, "x": 0, "y": 0, "key": 0}


class _ReachableListener(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class _CredentialTransport:
    def __init__(self, endpoint: str, credential: str) -> None:
        self.endpoint = endpoint
        self.credential = credential
        self.requests: list[dict[str, Any]] = []

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        self.requests.append(request)
        outgoing = urllib.request.Request(
            self.endpoint,
            data=json.dumps(request, sort_keys=True).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.credential}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(outgoing, timeout=deadline_seconds) as response:
            payload = json.loads(response.read())
        return TransportOutcome("response", payload)

    def cancel(self, *, idempotency_key: str, mode: str) -> Literal["cancelled", "unknown"]:
        del idempotency_key, mode
        return "cancelled"

    def reconcile(self, *, idempotency_key: str, deadline_seconds: float) -> TransportOutcome:
        del idempotency_key, deadline_seconds
        return TransportOutcome("unknown", failure_code="fixture_reconcile_unused")


def _package() -> StatefulPolicyPackage:
    return StatefulPolicyPackage.build(
        manifest=v5_manifest(),
        model_alias_disclosure=None,
        package_source_sha256="9" * 64,
        dependency_lock_sha256="8" * 64,
        max_steps=40,
        evidence_class=EvidenceClass.CALIBRATION,
        evidence=evidence(),
        code_revision="2" * 40,
        code_state="clean",
        source_tree_sha256="7" * 64,
        source_provenance_verified=True,
        source_provenance_failure_reason=None,
    )


def test_serving_policy_worker_denies_peer_listener_and_excludes_transport_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential = "fixture-only-credential"
    credential_variable = "PIXELGYM_S5_TEST_CREDENTIAL"
    monkeypatch.setenv(credential_variable, credential)
    captured_headers: list[str | None] = []
    captured_requests: list[dict[str, Any]] = []

    class AllowedProvider(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(length))
            captured_headers.append(self.headers.get("Authorization"))
            captured_requests.append(request)
            body = json.dumps(
                {
                    "response_id": "sandbox-fixture-response",
                    "model": "fake",
                    "content": json.dumps(NOOP, sort_keys=True),
                    "finish_reason": "stop",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    denied_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ReachableListener)
    provider_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), AllowedProvider)
    denied_thread = threading.Thread(target=denied_server.serve_forever, daemon=True)
    provider_thread = threading.Thread(target=provider_server.serve_forever, daemon=True)
    denied_thread.start()
    provider_thread.start()
    denied_url = f"http://127.0.0.1:{denied_server.server_port}/"
    with urllib.request.urlopen(denied_url, timeout=2) as response:
        assert response.status == 200

    policy_source = tmp_path / "policy-source"
    policy_source.mkdir()
    (policy_source / "sandbox_fixture_policy.py").write_text(
        "import hashlib, json, os, urllib.request\n"
        "class SandboxFixturePolicy:\n"
        "    def __init__(self, actions, denied_url, probe_variable):\n"
        "        self.actions = actions\n"
        "        self.denied_url = denied_url\n"
        "        self.probe_variable = probe_variable\n"
        "    def reset(self, task_instruction):\n"
        "        return json.dumps({'instruction': task_instruction}, "
        "sort_keys=True, separators=(',', ':')).encode()\n"
        "    def build_request(self, state, screenshot):\n"
        "        request = {'scripted_action': self.actions[0], "
        "'screenshot_digest': 'sha256:' + hashlib.sha256(screenshot).hexdigest()}\n"
        "        request['inherited_sensitive_env'] = self.probe_variable in os.environ\n"
        "        try:\n"
        "            urllib.request.urlopen(self.denied_url, timeout=1).read()\n"
        "            request['denied_listener_reachable'] = True\n"
        "        except Exception:\n"
        "            request['denied_listener_reachable'] = False\n"
        "        return request\n"
        "    def reduce_state(self, state, canonical_response): return state\n"
        "    def failure_state(self, state, failure_code): return state\n"
        "    def retryable_response_code(self, canonical_response): return None\n"
        "    def parse(self, canonical_response, state):\n"
        "        return json.loads(json.loads(canonical_response)['content'])\n"
        "    def post_parse_state(self, state, candidate): return state\n"
        "    def post_dispatch_state(self, state, action, result): return state\n"
        "    def close(self): pass\n",
        encoding="utf-8",
    )

    provider_endpoint = f"http://127.0.0.1:{provider_server.server_port}/"
    session_path = tmp_path / "host" / "sessions.sqlite"
    journal_path = tmp_path / "host" / "attempts.sqlite"
    session_path.parent.mkdir()
    spec = PolicyWorkerSpec.build(
        factory_module="sandbox_fixture_policy",
        factory_name="SandboxFixturePolicy",
        factory_kwargs={
            "actions": [NOOP],
            "denied_url": denied_url,
            "probe_variable": credential_variable,
        },
        import_roots=(policy_source,),
        provider_endpoint=provider_endpoint,
        protected_paths=(session_path, journal_path),
    )
    policy = SandboxedPolicyProcess(spec=spec)
    transport = _CredentialTransport(provider_endpoint, credential)
    package = _package()
    host = ServingEpisodeHost(
        session_store=SQLiteServingSessionStore(session_path),
        journal=V5AttemptJournal(journal_path),
        package=package,
        identity=replace(identity(), policy_id=package.policy_id),
        policy=policy,
        transport=transport,
        deployment_attempt_cap=20,
        episode_id_factory=lambda: EPISODE_ID,
    )
    try:
        host.create_episode(task_instruction="Complete", client_episode_ref="sandbox-1")
        result = host.act(episode_id=EPISODE_ID, screenshot=b"screenshot")
        final_screenshot = b"final-screenshot"
        final_result = ReportedResult(
            reward=1.0,
            terminated=True,
            truncated=False,
            screenshot_sha256="sha256:" + sha256_bytes(final_screenshot),
        )
        closed = host.close_episode(
            episode_id=EPISODE_ID,
            final_intent_id=result.intent_id,
            final_result=final_result,
            final_screenshot_sha256=final_result.screenshot_sha256,
            final_screenshot_object_key="serving-final-screenshots/sandbox-fixture.png",
        )
    finally:
        policy.close()
        denied_server.shutdown()
        provider_server.shutdown()
        denied_thread.join(timeout=5)
        provider_thread.join(timeout=5)
        denied_server.server_close()
        provider_server.server_close()

    assert result.action is not None and result.action.action_type == "NOOP"
    assert closed.terminal_classification.value == "terminated"
    assert policy.sandbox_mechanism == "darwin_sandbox_exec"
    assert policy.sandbox_profile_digest is not None
    assert captured_headers == [f"Bearer {credential}"]
    assert captured_requests == transport.requests
    assert captured_requests[0]["denied_listener_reachable"] is False
    assert captured_requests[0]["inherited_sensitive_env"] is False
