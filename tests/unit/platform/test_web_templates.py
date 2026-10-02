"""Rendered-page identity, autoescape, and template-inventory checks for the control web app.

The digest fixture was recorded from the inline f-string renderer at revision 2e22ab6, before the
Jinja2 template migration. The migrated renderer must reproduce every page byte for byte.
Record a new fixture only for an intentional, reviewed change to user-visible HTML:

    PIXELGYM_RECORD_WEB_PAGE_DIGESTS=1 pytest tests/unit/platform/test_web_templates.py
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pixelgym.platform.contracts import ArtifactRef, RunSummary
from pixelgym.platform.control_store import ControlStore, TransitionError
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.policy import PROMPT_NAME, build_policy_manifest, prompt_template
from pixelgym.platform.source_provenance import SOURCE_PROVENANCE_SCHEMA_VERSION, SourceProvenance
from pixelgym.platform.web import app as web_app

FIXTURE = Path(__file__).parent / "fixtures" / "web_pages_2e22ab6.json"
SECRET = "test-secret-at-least-sixteen"
SESSION_ID = "S" * 32
REVIEWER = "local-reviewer"
# A constant lock digest keeps candidate identities independent of the repository's live lock.
LOCK_DIGEST = "e" * 64
FIXED_NOW = "2026-01-02T03:04:05+00:00"


def _session_cookie() -> str:
    tag = hmac.new(
        SECRET.encode(), b"pixelgym-session-v1\0" + SESSION_ID.encode(), hashlib.sha256
    ).hexdigest()
    return f"{SESSION_ID}.{tag}"


def _csrf() -> str:
    return hmac.new(SECRET.encode(), _session_cookie().encode(), hashlib.sha256).hexdigest()


def _policy(
    gate_policy: Any,
    version: int,
    *,
    revision: str,
    provider: str = "scripted-demo",
    model: str | None = None,
    code_state: str = "clean",
) -> Any:
    return build_policy_manifest(
        provider=provider,
        model=model or ("day3-replay-baseline-v1" if version == 1 else "day3-replay-revised-v2"),
        prompt_name=PROMPT_NAME,
        prompt_version=version,
        prompt=prompt_template(version),
        condition="raw",
        parameters={"deterministic": True, "hidden_retries": 0},
        parser_version="pixelgym-grounding-parser-v1",
        scorer_version=gate_policy.required_scorer_version,
        overlay_version="none-raw-coordinate-policy",
        target_semantics=gate_policy.required_target_semantics,
        source_provenance=SourceProvenance(
            SOURCE_PROVENANCE_SCHEMA_VERSION, revision, "b" * 64, code_state, "git-build-inputs-v1"
        ),
        dependency_lock_sha256=LOCK_DIGEST,
    )


def _summary(gate_policy: Any, policy: Any, run_id: str, **changes: Any) -> RunSummary:
    summary = RunSummary(
        run_id=run_id,
        dataset_fingerprint=gate_policy.required_dataset_fingerprint,
        policy_id=policy.policy_id,
        scorer_version=gate_policy.required_scorer_version,
        target_semantics=gate_policy.required_target_semantics,
        expected_count=100,
        scored_count=100,
        unique_record_count=100,
        correct_count=80,
        accuracy=0.8,
        cost_usd_per_100=0.0,
        priced_call_count=100,
        unpriced_call_count=0,
        provider_latency_p95_ms=100.0,
        latency_measured_count=100,
        code_state="clean",
        code_provenance_verified=True,
    )
    return dataclasses.replace(summary, **changes)


def _artifact(logical_key: str, uri: str) -> ArtifactRef:
    return ArtifactRef(
        logical_key=logical_key,
        uri=uri,
        version_id="v1",
        sha256="a" * 64,
        size=1,
        media_type="application/json",
        retention_status="locked",
    )


def _register(
    control: ControlStore,
    gate_policy: Any,
    policy: Any,
    run_id: str,
    *,
    artifacts: list[ArtifactRef] | None = None,
    with_summary: bool = True,
    **summary_changes: Any,
) -> Any:
    summary = _summary(gate_policy, policy, run_id, **summary_changes)
    return control.register_candidate(
        source_run_id=run_id,
        policy=policy,
        gate_report=evaluate_gates(gate_policy, summary),
        artifacts=artifacts or [],
        summary=summary if with_summary else None,
    )


class _LedgerCoordinator:
    def __init__(self, control: ControlStore) -> None:
        self.control = control

    def deploy(self, candidate_id: str, **kwargs: Any) -> Any:
        return self.control.activate(candidate_id, action="deploy", **kwargs)

    def rollback(self, **kwargs: Any) -> Any:
        raise TransitionError("rollback <blocked> & refused for 'fixture'")


def _client(app: Any) -> TestClient:
    client = TestClient(app)
    client.cookies.set("pixelgym_session", _session_cookie())
    return client


HOSTILE_PROVIDER = "real-<script>alert(1)</script>"


def _populated_control(tmp_path: Path, gate_policy: Any) -> tuple[ControlStore, dict[str, str], str]:
    control = ControlStore(
        tmp_path / "control.db", reviewer_identity=REVIEWER, now=lambda: FIXED_NOW
    )
    control.migrate()
    baseline = _register(control, gate_policy, _policy(gate_policy, 1, revision="a" * 40), "run-a")
    revised = _register(control, gate_policy, _policy(gate_policy, 2, revision="a" * 40), "run-b")
    failing = _register(
        control,
        gate_policy,
        _policy(
            gate_policy,
            2,
            revision="c" * 40,
            provider=HOSTILE_PROVIDER,
            model="model's \"<script>x</script>\"",
            code_state="dirty",
        ),
        "run-c",
        artifacts=[
            _artifact("raw-responses/<one>.json", "https://evidence.test/raw/one.json?a=1&b=2"),
            _artifact("raw-responses/two.json", "javascript:alert(1)"),
            _artifact("runs/c/predictions.jsonl", "s3://bucket/runs/c/predictions.jsonl"),
            _artifact("runs/c/gate-report.json", "file:///etc/passwd"),
        ],
        accuracy=0.5,
        correct_count=50,
        unpriced_call_count=3,
        priced_call_count=97,
        code_state="dirty",
    )
    eligible = _register(
        control, gate_policy, _policy(gate_policy, 1, revision="d" * 40), "run-d", with_summary=False
    )
    for item in (baseline, revised):
        control.approve(
            item.candidate_id,
            actor=REVIEWER,
            reason="reviewed <evidence> & approved",
            gate_report_sha256=item.gate_report_sha256,
        )
    first = control.activate(
        baseline.candidate_id,
        actor=REVIEWER,
        reason="first",
        action="deploy",
        expected_deployment_id=None,
        expected_generation=0,
    )
    control.activate(
        revised.candidate_id,
        actor=REVIEWER,
        reason="second",
        action="deploy",
        expected_deployment_id=first.deployment_id,
        expected_generation=first.generation,
    )
    submission_id = control.submit(
        {
            "dataset": "day3-frozen-v1",
            "prompt_version": "1",
            "model": "day3-replay-baseline-v1",
            "condition": "raw",
            "maximum_calls": "100",
            "price_catalog": "pixelgym-demo-prices-v1",
        },
        actor=REVIEWER,
    )
    ids = {
        "a": baseline.candidate_id,
        "b": revised.candidate_id,
        "c": failing.candidate_id,
        "d": eligible.candidate_id,
    }
    return control, ids, submission_id


def _render_pages(tmp_path: Path, gate_policy: Any) -> dict[str, tuple[int, bytes]]:
    control, ids, submission_id = _populated_control(tmp_path, gate_policy)
    populated = _client(
        web_app.create_control_app(
            control,
            coordinator=_LedgerCoordinator(control),  # type: ignore[arg-type]
            csrf_secret=SECRET,
        )
    )
    empty_control = ControlStore(
        tmp_path / "empty.db", reviewer_identity=REVIEWER, now=lambda: FIXED_NOW
    )
    empty_control.migrate()
    empty = _client(
        web_app.create_control_app(empty_control, csrf_secret=SECRET, loopback_deployment=False)
    )
    a, b, c, d = ids["a"], ids["b"], ids["c"], ids["d"]
    pages: dict[str, tuple[TestClient, str]] = {
        "submit": (populated, "/"),
        "submission": (populated, f"/submissions/{submission_id}"),
        "runs": (populated, "/runs"),
        "runs_page_2": (populated, "/runs?page=2&provider="),
        "runs_filtered": (
            populated,
            "/runs?submitted=%3Cs%3E&provider=scripted-demo&lifecycle=Approved&dataset=%22x"
            "&code_revision=a&prompt_version=1&model=m%27&status=Running&date_from=2026-01-01"
            "&date_to=2026-12-31&gate_result=passed",
        ),
        "runs_gate_failed": (populated, "/runs?gate_result=failed&lifecycle=GateFailed"),
        "compare": (populated, "/compare"),
        "compare_compatible": (populated, f"/compare?candidate={a}&candidate={b}"),
        "compare_blocked": (populated, f"/compare?candidate={a}&candidate={d}&candidate={c}"),
        "prompt_diff": (populated, f"/compare/prompt-diff?candidate={a}&candidate={b}"),
        "candidate_approved": (populated, f"/candidates/{a}"),
        "candidate_disclosure": (populated, f"/candidates/{b}"),
        "candidate_failed": (populated, f"/candidates/{c}"),
        "candidate_eligible": (populated, f"/candidates/{d}"),
        "raw_responses": (populated, f"/candidates/{c}/evidence/raw-responses"),
        "raw_responses_empty": (populated, f"/candidates/{a}/evidence/raw-responses"),
        "deployment": (populated, "/deployment"),
        "deployment_audit": (populated, "/deployment/audit"),
        "deployment_audit_page_2": (populated, "/deployment/audit?page=2"),
        "empty_runs": (empty, "/runs"),
        "empty_compare": (empty, "/compare"),
        "empty_deployment": (empty, "/deployment"),
        "empty_deployment_audit": (empty, "/deployment/audit"),
    }
    rendered = {}
    for name, (client, path) in pages.items():
        response = client.get(path)
        rendered[name] = (response.status_code, response.content)
    blocked = populated.post(
        "/rollback",
        data={
            "csrf_token": _csrf(),
            "reason": "x",
            "expected_deployment_id": "",
            "expected_generation": "0",
        },
    )
    rendered["action_blocked"] = (blocked.status_code, blocked.content)
    return rendered


@pytest.fixture
def small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_app, "RUNS_PAGE_SIZE", 2)
    monkeypatch.setattr(web_app, "AUDIT_HISTORY_PAGE_SIZE", 3)
    monkeypatch.setattr(web_app, "DEPLOYMENT_AUDIT_WINDOW", 4)


@pytest.fixture
def rendered_pages(
    tmp_path: Path, gate_policy: Any, small_pages: None
) -> Iterator[dict[str, tuple[int, bytes]]]:
    yield _render_pages(tmp_path, gate_policy)


def test_every_page_matches_the_pre_template_digest(
    rendered_pages: dict[str, tuple[int, bytes]],
) -> None:
    observed = {
        name: {"status": status, "sha256": hashlib.sha256(body).hexdigest()}
        for name, (status, body) in sorted(rendered_pages.items())
    }
    if os.environ.get("PIXELGYM_RECORD_WEB_PAGE_DIGESTS") == "1":
        FIXTURE.write_text(json.dumps(observed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert observed == json.loads(FIXTURE.read_text(encoding="utf-8"))
