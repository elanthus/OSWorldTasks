from __future__ import annotations

import base64
import importlib
import io
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.service import (
    API_SCHEMA_VERSION,
    LoadedPolicy,
    PolicyRuntime,
    ProviderFailure,
    create_serving_app,
)
from pixelgym.platform.web import create_control_app


def _image(width: int = 100, height: int = 80, image_format: str = "PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format=image_format)
    return output.getvalue()


class ServingFake:
    def __init__(self, raw: str = '{"x":20,"y":30}', failure: str | None = None) -> None:
        self.raw = raw
        self.failure = failure
        self.calls = 0

    def ground(self, **request):
        self.calls += 1
        if self.failure:
            raise ProviderFailure(self.failure, "private provider detail")
        return self.raw, "request-1", 5.0, {"input_tokens": 1}


def _loaded(policy, provider, *, approved: bool = True, gate_passed: bool = True) -> LoadedPolicy:
    return LoadedPolicy(policy, "deployment-1", "candidate-1", provider, approved, gate_passed)


def test_serving_contract_and_identity_headers(policy_factory) -> None:
    provider = ServingFake()
    client = TestClient(create_serving_app(PolicyRuntime(_loaded(policy_factory(), provider))))
    response = client.post(
        "/api/v1/ground",
        json={
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "image/png",
            "target": "Click the Company name field",
        },
    )
    assert response.status_code == 200
    assert response.json()["schema_version"] == API_SCHEMA_VERSION
    assert response.json()["prediction"] == {"x": 20, "y": 30}
    assert response.headers["x-pixelgym-policy-id"] == policy_factory().policy_id
    assert response.headers["x-pixelgym-deployment-id"] == "deployment-1"
    assert provider.calls == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"image_base64": "not-base64", "media_type": "image/png", "target": "target"},
        {"image_base64": base64.b64encode(b"not-image").decode(), "media_type": "image/png", "target": "target"},
        {"image_base64": base64.b64encode(_image()).decode(), "media_type": "text/plain", "target": "target"},
        {"image_base64": base64.b64encode(_image()).decode(), "media_type": "image/png", "target": "   "},
        {"image_base64": base64.b64encode(_image()).decode(), "media_type": "image/png", "target": "target", "extra": "forbidden"},
    ],
)
def test_invalid_requests_fail_before_provider(policy_factory, payload: dict) -> None:
    provider = ServingFake()
    client = TestClient(create_serving_app(PolicyRuntime(_loaded(policy_factory(), provider))))
    assert client.post("/api/v1/ground", json=payload).status_code in {400, 415, 422}
    assert provider.calls == 0


def test_bad_dimensions_and_media_mismatch_fail_before_provider(policy_factory) -> None:
    provider = ServingFake()
    client = TestClient(create_serving_app(PolicyRuntime(_loaded(policy_factory(), provider))))
    too_wide = client.post(
        "/api/v1/ground",
        json={"image_base64": base64.b64encode(_image(4097, 1)).decode(), "media_type": "image/png", "target": "target"},
    )
    mismatch = client.post(
        "/api/v1/ground",
        json={"image_base64": base64.b64encode(_image()).decode(), "media_type": "image/jpeg", "target": "target"},
    )
    assert too_wide.status_code == 400 and mismatch.status_code == 415
    assert provider.calls == 0


def test_parser_and_provider_failures_are_explicit_without_retry(policy_factory) -> None:
    invalid = ServingFake("not-json")
    client = TestClient(create_serving_app(PolicyRuntime(_loaded(policy_factory(), invalid))))
    payload = {"image_base64": base64.b64encode(_image()).decode(), "media_type": "image/png", "target": "target"}
    response = client.post("/api/v1/ground", json=payload)
    assert response.status_code == 200 and response.json()["parse_status"] == "invalid"
    assert invalid.calls == 1
    timeout = ServingFake(failure="timeout")
    response = TestClient(create_serving_app(PolicyRuntime(_loaded(policy_factory(), timeout)))).post("/api/v1/ground", json=payload)
    assert response.status_code == 504
    assert "private provider detail" not in response.text
    assert timeout.calls == 1


def test_unapproved_policy_cannot_become_ready(policy_factory) -> None:
    with pytest.raises(ValueError, match="unapproved"):
        PolicyRuntime(_loaded(policy_factory(), ServingFake(), approved=False))
    client = TestClient(create_serving_app(PolicyRuntime()))
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503


def test_bootstrap_import_is_side_effect_free_and_factory_uses_explicit_migration(
    tmp_path: Path, repository_root: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "PIXELGYM_REPOSITORY_ROOT",
        "PIXELGYM_CONTROL_DB",
        "PIXELGYM_IMMUTABLE_ROOT",
        "PIXELGYM_IMMUTABLE_BUCKET",
        "PIXELGYM_CSRF_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    sys.modules.pop("pixelgym.platform.bootstrap", None)
    module = importlib.import_module("pixelgym.platform.bootstrap")
    assert not (tmp_path / ".cache").exists()

    monkeypatch.setenv("PIXELGYM_REPOSITORY_ROOT", str(repository_root))
    monkeypatch.setenv("PIXELGYM_CONTROL_DB", str(tmp_path / "state/control.db"))
    monkeypatch.setenv("PIXELGYM_IMMUTABLE_ROOT", str(tmp_path / "immutable"))
    monkeypatch.setenv("PIXELGYM_CSRF_SECRET", "test-secret-at-least-sixteen")
    (tmp_path / "state").mkdir()
    migrated = ControlStore(
        tmp_path / "state/control.db", reviewer_identity="local-reviewer"
    )
    migrated.migrate()
    client = TestClient(module.create_app())

    assert client.get("/").status_code == 200
    assert client.get("/health/live").status_code == 200
    assert (tmp_path / "state/control.db").is_file()


def test_bootstrap_factory_fails_clearly_before_explicit_migration(
    tmp_path: Path, repository_root: Path, monkeypatch
) -> None:
    from pixelgym.platform import bootstrap

    monkeypatch.setenv("PIXELGYM_REPOSITORY_ROOT", str(repository_root))
    monkeypatch.setenv("PIXELGYM_CONTROL_DB", str(tmp_path / "state/control.db"))
    monkeypatch.setenv("PIXELGYM_CSRF_SECRET", "test-secret-at-least-sixteen")
    monkeypatch.delenv("PIXELGYM_IMMUTABLE_BUCKET", raising=False)

    with pytest.raises(RuntimeError, match="not migrated"):
        bootstrap.create_app()


def test_bootstrap_factory_requires_explicit_csrf_secret(
    tmp_path: Path, repository_root: Path, monkeypatch
) -> None:
    from pixelgym.platform import bootstrap

    monkeypatch.setenv("PIXELGYM_REPOSITORY_ROOT", str(repository_root))
    monkeypatch.setenv("PIXELGYM_CONTROL_DB", str(tmp_path / "control.db"))
    monkeypatch.delenv("PIXELGYM_CSRF_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="CSRF_SECRET"):
        bootstrap.create_app()


def _csrf(text: str) -> str:
    return re.search(r'<meta name="csrf-token" content="([0-9a-f]+)">', text).group(1)


def test_web_submission_is_allowlisted_idempotent_and_synthetic_labeled(tmp_path: Path) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    scheduled: list[tuple[str, dict]] = []
    client = TestClient(
        create_control_app(
            control,
            csrf_secret="test-secret-at-least-sixteen",
            submit_callback=lambda submission, payload: scheduled.append((submission, payload)),
        )
    )
    page = client.get("/")
    token = _csrf(page.text)
    form = {
        "csrf_token": token,
        "dataset": "day3-frozen-v1",
        "prompt_version": "2",
        "model": "day3-replay-revised-v2",
        "condition": "raw",
        "maximum_calls": "100",
        "price_catalog": "pixelgym-demo-prices-v1",
    }
    first = client.post("/experiments", data=form, follow_redirects=False)
    second = client.post("/experiments", data=form, follow_redirects=False)
    assert first.status_code == second.status_code == 303
    assert first.headers["location"] == second.headers["location"]
    blocked = client.post("/experiments", data={**form, "model": "shell-command"})
    assert blocked.status_code == 422
    assert "DEMO PROVIDER" in page.text


def test_empty_form_body_and_unknown_candidates_are_client_errors(tmp_path: Path) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    client = TestClient(
        create_control_app(control, csrf_secret="test-secret-at-least-sixteen")
    )

    empty = client.post(
        "/experiments",
        content=b"",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert empty.status_code == 422
    assert client.get("/candidates/nope").status_code == 404
    assert client.get("/compare?candidate=nope").status_code == 404


def test_failed_candidate_has_visible_reasons_and_no_approval_control(
    tmp_path: Path, passing_evidence, gate_policy
) -> None:
    policy, summary, _ = passing_evidence
    from dataclasses import replace

    summary = replace(summary, accuracy=0.5, correct_count=50)
    report = evaluate_gates(gate_policy, summary)
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    candidate = control.register_candidate(source_run_id=summary.run_id, policy=policy, gate_report=report, artifacts=[])
    client = TestClient(create_control_app(control, csrf_secret="test-secret-at-least-sixteen"))
    page = client.get(f"/candidates/{candidate.candidate_id}")
    assert "Approval unavailable" in page.text
    assert "below the minimum" in page.text
    assert "Approve exact candidate" not in page.text
    assert "target_bbox" not in page.text and "expected answer" not in page.text
    token = _csrf(page.text)
    direct = client.post(
        f"/api/candidates/{candidate.candidate_id}/approve",
        json={"reason": "bypass"},
        headers={"X-CSRF-Token": token},
    )
    assert direct.status_code == 409


def test_missing_gate_evidence_renders_as_blocked_instead_of_crashing(
    tmp_path: Path, passing_evidence
) -> None:
    from dataclasses import replace

    policy, summary, report = passing_evidence
    missing = replace(
        report,
        cost_usd_per_100=replace(report.cost_usd_per_100, observed=None, passed=False),
        provider_latency_p95_ms=replace(
            report.provider_latency_p95_ms, observed=None, passed=False
        ),
        overall_passed=False,
        reasons=("cost and latency evidence are missing",),
    )
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    candidate = control.register_candidate(
        source_run_id=summary.run_id, policy=policy, gate_report=missing, artifacts=[]
    )
    client = TestClient(create_control_app(control, csrf_secret="test-secret-at-least-sixteen"))
    runs = client.get("/runs")
    detail = client.get(f"/candidates/{candidate.candidate_id}")
    assert runs.status_code == detail.status_code == 200
    assert "missing" in runs.text and "missing" in detail.text
    assert "Approval unavailable" in detail.text


def test_compare_always_displays_accuracy_cost_latency_and_compatibility(
    tmp_path: Path, passing_evidence, policy_factory
) -> None:
    from dataclasses import replace

    policy, summary, report = passing_evidence
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    first = control.register_candidate(source_run_id=summary.run_id, policy=policy, gate_report=report, artifacts=[])
    second_policy = policy_factory(model="another-exact-model")
    second_report = replace(report, policy_id=second_policy.policy_id, run_id="run-2")
    second = control.register_candidate(source_run_id="run-2", policy=second_policy, gate_report=second_report, artifacts=[])
    client = TestClient(create_control_app(control, csrf_secret="test-secret-at-least-sixteen"))
    response = client.get(f"/compare?candidate={first.candidate_id}&candidate={second.candidate_id}")
    assert "COMPATIBLE" in response.text
    assert "Cost / 100" in response.text
    assert "Provider p95" in response.text
    assert "Accuracy" in response.text
    assert "80.0%" in response.text
