"""Opt-in local S8 rehearsal: real Darwin policy sandbox and a real 127.0.0.1 listener.

This is engineering validation of the driver against the real worker launcher and HTTP path.
It freezes and qualifies a throwaway S7 run from the clean current checkout, then executes the
seven-step rehearsal with fake sends only. It is not the owner-gated S8 rehearsal, produces no
approval, and makes no provider request.
"""

from __future__ import annotations

import json
import os
import platform
import socket
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.platform.s7_policy import POLICY_SHA256, policy_document
from pixelgym.platform.s7_qualification import prepare as prepare_qualification
from pixelgym.platform.s7_qualification import qualify, read_json
from pixelgym.platform.s8_rehearsal import (
    APPROVAL_SCHEMA,
    LoopbackCaller,
    Rehearsal,
    prepare,
    render_report,
    stored_report,
)
from scripts import s7_serving
from scripts import s8_rehearsal as cli

pytestmark = [
    pytest.mark.v5_sandbox_integration,
    pytest.mark.local_http_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_V5_SANDBOX_TESTS") != "1" or platform.system() != "Darwin",
        reason="set PIXELGYM_RUN_V5_SANDBOX_TESTS=1 on Darwin outside a parent sandbox",
    ),
]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _approval(qualification: Path) -> dict[str, object]:
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
    return {
        "schema_version": APPROVAL_SCHEMA,
        "actor": "integration-fixture",
        "decision": "throwaway integration approval; not an owner decision",
        "approved_on": "2026-10-26",
        "qualification_plan_sha256": content_digest(read_json(qualification / "plan.json")),
        "gate_policy_sha256": POLICY_SHA256,
        "packages": packages,
        "activation_approved": True,
        "publication_approved": False,
    }


def test_real_sandbox_and_loopback_listener_rehearsal(tmp_path: Path) -> None:
    qualification = tmp_path / "qualification"
    with s7_serving.no_network():
        prepare_qualification(
            REPOSITORY_ROOT,
            qualification,
            run_id="s8-integration-throwaway",
            approval="throwaway integration qualification; not an owner decision",
            expected_predecessor_sends=0,
        )
        qualify(REPOSITORY_ROOT, qualification)
        s7_serving.finalize(qualification)
    approval = tmp_path / "approval.json"
    approval.write_bytes(json.dumps(_approval(qualification)).encode())
    state = tmp_path / "state"
    with s7_serving.no_network():
        prepare(REPOSITORY_ROOT, qualification, state, approval_path=approval)
    port = _free_loopback_port()
    rehearsal = Rehearsal(
        REPOSITORY_ROOT, state, caller_factory=lambda app: LoopbackCaller(app, port=port)
    )
    lifecycle = rehearsal.run()
    assert lifecycle["fake_sends"] == {"qualification": 77, "A": 2, "B": 24, "smoke": 6}
    assert lifecycle["version_attempts"] == {"A": 2, "B": 24}
    with s7_serving.no_network():
        checks = cli.finalize(REPOSITORY_ROOT, state)
        result = cli.verify(state)
        rendered = render_report(stored_report(state))
    assert checks["checks"][0]["exit_status"] == 0, checks["checks"][0]["stderr"]
    assert result["campaign_total"] == 109 and result["qualification_seal_verified"] is True
    assert "No milestone verdict" in rendered
    # Real OS enforcement is the point of this test: every worker the lifecycle used was
    # launched by the Darwin sandbox launcher through the unchanged S7 preparer.
    workers = read_json(qualification / "results.json")["workers"]
    assert all(w["mechanism"] == "darwin_sandbox_exec" for w in workers)
    proofs = lifecycle["worker_proofs"]
    assert len(proofs) == 5 and all(p["mechanism"] == "darwin_sandbox_exec" for p in proofs)
    assert all(p["os_sandbox_applied"] is True for p in proofs)
    assert lifecycle["os_sandbox_required"] is True
    assert lifecycle["caller_kinds"] == [f"loopback-listener:127.0.0.1:{port}"] * 2
    verification = read_json(state / "verification.json")
    assert verification["worker_enforcement"]["all_enforced"] is True
