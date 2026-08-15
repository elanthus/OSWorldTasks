from __future__ import annotations

import asyncio
import base64
import dataclasses
import importlib
import io
import re
import sys
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pixelgym.platform.contracts import ArtifactRef
from pixelgym.platform.control_store import ControlStore, TransitionError
from pixelgym.platform.deployment_smoke import DeploymentSmokeError
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.immutable_store import LocalImmutableStore
from pixelgym.platform.operational_log import (
    OPERATIONAL_RECORD_SCHEMA_VERSION,
    ImmutableOperationalLog,
    MemoryOperationalLog,
    OperationalLogError,
    OperationalRecord,
)
from pixelgym.platform.policy import build_policy_manifest, prompt_template
from pixelgym.platform.service import (
    API_SCHEMA_VERSION,
    LoadedPolicy,
    PolicyRuntime,
    ProviderFailure,
    _operational_context,
    create_serving_app,
)
from pixelgym.platform.source_provenance import SOURCE_PROVENANCE_SCHEMA_VERSION, SourceProvenance
from pixelgym.platform.web import create_control_app
from scripts.capture_platform_api import _safe_body


def _image(width: int = 100, height: int = 80, image_format: str = "PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format=image_format)
    return output.getvalue()


class ServingFake:
    def __init__(
        self,
        raw: str = '{"x":20,"y":30}',
        failure: str | None = None,
        *,
        provider_request_id: object = "request-1",
        latency_ms: object = 5.0,
        usage: object = {"input_tokens": 1},
    ) -> None:
        self.raw = raw
        self.failure = failure
        self.provider_request_id = provider_request_id
        self.latency_ms = latency_ms
        self.usage = usage
        self.calls = 0

    def ground(self, **request):
        self.calls += 1
        if self.failure:
            raise ProviderFailure(self.failure, "private provider detail")
        return self.raw, self.provider_request_id, self.latency_ms, self.usage


def _loaded(policy, provider, *, approved: bool = True, gate_passed: bool = True) -> LoadedPolicy:
    return LoadedPolicy(policy, "deployment-1", "candidate-1", provider, approved, gate_passed)


def _serving_app(runtime: PolicyRuntime):
    return create_serving_app(runtime, operational_log=MemoryOperationalLog())


def test_serving_contract_and_identity_headers(policy_factory) -> None:
    provider = ServingFake()
    client = TestClient(_serving_app(PolicyRuntime(_loaded(policy_factory(), provider))))
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


def test_operational_record_is_redacted_immutable_and_identifies_served_policy(policy_factory) -> None:
    provider = ServingFake(usage={"input_tokens": 1, "output_tokens": 0})
    log = MemoryOperationalLog()
    policy = policy_factory()
    response = TestClient(
        create_serving_app(PolicyRuntime(_loaded(policy, provider)), operational_log=log)
    ).post(
        "/api/v1/ground",
        json={
            "image_base64": base64.b64encode(_image()).decode(),
            "media_type": "image/png",
            "target": "private target must not be retained",
        },
    )
    request_id = response.headers["x-pixelgym-request-id"]
    record = log.get(request_id)
    assert record is not None
    assert record.policy_id == policy.policy_id
    assert record.deployment_id == "deployment-1"
    assert record.exact_policy_version == "candidate-1"
    assert record.terminal_status == "completed"
    assert record.http_status == 200
    assert record.provider_request_id == "request-1"
    assert record.usage == {"input_tokens": 1, "output_tokens": 0}
    assert record.latency_ms >= 0 and record.occurred_at.endswith("+00:00")
    assert "target" not in record.to_dict() and "image" not in record.to_dict()
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.terminal_status = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record.usage["input_tokens"] = 99  # type: ignore[index]


def test_operational_log_distinguishes_absent_usage_and_failure_classes(policy_factory) -> None:
    payload = {
        "image_base64": base64.b64encode(_image()).decode(),
        "media_type": "image/png",
        "target": "target",
    }
    no_usage_log = MemoryOperationalLog()
    no_usage = TestClient(
        create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake(usage=None))), operational_log=no_usage_log
        )
    ).post("/api/v1/ground", json=payload)
    assert no_usage_log.get(no_usage.headers["x-pixelgym-request-id"]).usage is None

    bad_metadata_log = MemoryOperationalLog()
    malformed = TestClient(
        create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake(latency_ms=float("nan")))),
            operational_log=bad_metadata_log,
        )
    ).post("/api/v1/ground", json=payload)
    malformed_record = bad_metadata_log.get(malformed.headers["x-pixelgym-request-id"])
    assert malformed.status_code == 502
    assert malformed_record is not None and malformed_record.terminal_status == "provider_metadata_invalid"

    failed_log = MemoryOperationalLog()
    failed = TestClient(
        create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake(failure="timeout"))),
            operational_log=failed_log,
        )
    ).post("/api/v1/ground", json=payload)
    failed_record = failed_log.get(failed.headers["x-pixelgym-request-id"])
    assert failed.status_code == 504
    assert failed_record is not None and failed_record.terminal_status == "provider_timeout"


def test_operational_log_captures_rejected_and_invalid_output_requests(policy_factory) -> None:
    log = MemoryOperationalLog()
    client = TestClient(
        create_serving_app(PolicyRuntime(_loaded(policy_factory(), ServingFake("not-json"))), operational_log=log)
    )
    invalid_input = client.post(
        "/api/v1/ground", json={"image_base64": "bad", "media_type": "image/png", "target": "target"}
    )
    rejected = log.get(invalid_input.headers["x-pixelgym-request-id"])
    assert invalid_input.status_code == 400
    assert rejected is not None and rejected.terminal_status == "request_rejected"
    assert rejected.policy_id == policy_factory().policy_id

    invalid_output = client.post(
        "/api/v1/ground",
        json={"image_base64": base64.b64encode(_image()).decode(), "media_type": "image/png", "target": "target"},
    )
    record = log.get(invalid_output.headers["x-pixelgym-request-id"])
    assert invalid_output.status_code == 200
    assert record is not None and record.terminal_status == "invalid_output"


def test_immutable_operational_log_retrieves_verified_record_and_detects_tampering(
    tmp_path: Path, policy_factory
) -> None:
    log = ImmutableOperationalLog(LocalImmutableStore(tmp_path / "immutable"))
    client = TestClient(
        create_serving_app(PolicyRuntime(_loaded(policy_factory(), ServingFake())), operational_log=log)
    )
    response = client.post(
        "/api/v1/ground",
        json={"image_base64": base64.b64encode(_image()).decode(), "media_type": "image/png", "target": "target"},
    )
    request_id = response.headers["x-pixelgym-request-id"]
    assert log.get(request_id) is not None
    record_path = tmp_path / "immutable" / "objects" / "serving-operational-records" / f"{request_id}.json"
    record_path.write_text("{}\n")
    with pytest.raises(OperationalLogError, match="retrieve verified"):
        log.get(request_id)


def test_unavailable_operational_storage_fails_closed(policy_factory) -> None:
    class FailingLog:
        def append(self, record) -> None:
            raise OperationalLogError("unavailable")

        def get(self, request_id):
            return None

    response = TestClient(
        create_serving_app(PolicyRuntime(_loaded(policy_factory(), ServingFake())), operational_log=FailingLog())
    ).post(
        "/api/v1/ground",
        json={"image_base64": base64.b64encode(_image()).decode(), "media_type": "image/png", "target": "target"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "serving audit storage is unavailable"}


def test_operational_append_runs_off_the_event_loop_and_keeps_request_context(policy_factory) -> None:
    class ContextInspectingLog:
        def __init__(self, event_loop_thread: int) -> None:
            self.event_loop_thread = event_loop_thread
            self.append_thread: int | None = None
            self.context_request_id: str | None = None

        def append(self, record) -> None:
            self.append_thread = threading.get_ident()
            context = _operational_context.get()
            self.context_request_id = context.request_id if context is not None else None

        def get(self, request_id):
            return None

    async def exercise() -> tuple[ContextInspectingLog, httpx.Response, int]:
        event_loop_thread = threading.get_ident()
        log = ContextInspectingLog(event_loop_thread)
        app = create_serving_app(
            PolicyRuntime(_loaded(policy_factory(), ServingFake())), operational_log=log
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/ground",
                json={
                    "image_base64": base64.b64encode(_image()).decode(),
                    "media_type": "image/png",
                    "target": "target",
                },
            )
        return log, response, event_loop_thread

    log, response, event_loop_thread = asyncio.run(exercise())
    assert response.status_code == 200
    assert log.append_thread is not None and log.append_thread != event_loop_thread
    assert log.context_request_id == response.headers["x-pixelgym-request-id"]


def test_immutable_operational_log_does_not_serialize_distinct_request_writes(tmp_path: Path) -> None:
    class RendezvousStore:
        def __init__(self) -> None:
            self.delegate = LocalImmutableStore(tmp_path / "immutable")
            self.put_barrier = threading.Barrier(2)

        def put_once(self, logical_key, data, *, media_type):
            self.put_barrier.wait(timeout=1)
            return self.delegate.put_once(logical_key, data, media_type=media_type)

        def get_verified(self, reference):
            return self.delegate.get_verified(reference)

        def get_reference(self, logical_key):
            return self.delegate.get_reference(logical_key)

    def record(index: int) -> OperationalRecord:
        return OperationalRecord(
            schema_version=OPERATIONAL_RECORD_SCHEMA_VERSION,
            request_id=f"srv-{index:032x}",
            occurred_at="2026-08-15T00:00:00+00:00",
            policy_id="policy",
            deployment_id="deployment",
            exact_policy_version="candidate",
            terminal_status="completed",
            http_status=200,
            latency_ms=1.0,
            provider_latency_ms=None,
            provider_request_id=None,
            usage=None,
        )

    log = ImmutableOperationalLog(RendezvousStore())
    errors: list[OperationalLogError] = []

    def append(item: OperationalRecord) -> None:
        try:
            log.append(item)
        except OperationalLogError as exc:  # pragma: no cover - assertion below checks this path.
            errors.append(exc)

    first = threading.Thread(target=append, args=(record(1),))
    second = threading.Thread(target=append, args=(record(2),))
    first.start()
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive() and not second.is_alive()
    assert not errors


def test_no_active_deployment_is_operationally_recorded() -> None:
    log = MemoryOperationalLog()
    response = TestClient(create_serving_app(PolicyRuntime(), operational_log=log)).post(
        "/api/v1/ground",
        json={"image_base64": base64.b64encode(_image()).decode(), "media_type": "image/png", "target": "target"},
    )
    record = log.get(response.headers["x-pixelgym-request-id"])
    assert response.status_code == 503
    assert record is not None and record.terminal_status == "no_active_deployment"
    assert record.policy_id is None and record.deployment_id is None


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
    client = TestClient(_serving_app(PolicyRuntime(_loaded(policy_factory(), provider))))
    assert client.post("/api/v1/ground", json=payload).status_code in {400, 415, 422}
    assert provider.calls == 0


def test_bad_dimensions_and_media_mismatch_fail_before_provider(policy_factory) -> None:
    provider = ServingFake()
    client = TestClient(_serving_app(PolicyRuntime(_loaded(policy_factory(), provider))))
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
    client = TestClient(_serving_app(PolicyRuntime(_loaded(policy_factory(), invalid))))
    payload = {"image_base64": base64.b64encode(_image()).decode(), "media_type": "image/png", "target": "target"}
    response = client.post("/api/v1/ground", json=payload)
    assert response.status_code == 200 and response.json()["parse_status"] == "invalid"
    assert invalid.calls == 1
    timeout = ServingFake(failure="timeout")
    response = TestClient(_serving_app(PolicyRuntime(_loaded(policy_factory(), timeout)))).post("/api/v1/ground", json=payload)
    assert response.status_code == 504
    assert "private provider detail" not in response.text
    assert timeout.calls == 1


def test_unapproved_policy_cannot_become_ready(policy_factory) -> None:
    with pytest.raises(ValueError, match="unapproved"):
        PolicyRuntime(_loaded(policy_factory(), ServingFake(), approved=False))
    client = TestClient(_serving_app(PolicyRuntime()))
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


def _assembled_platform_app(tmp_path: Path, repository_root: Path, monkeypatch):
    from pixelgym.platform import bootstrap

    database = tmp_path / "state/control.db"
    database.parent.mkdir()
    control = ControlStore(database, reviewer_identity="local-reviewer")
    control.migrate()
    monkeypatch.setenv("PIXELGYM_REPOSITORY_ROOT", str(repository_root))
    monkeypatch.setenv("PIXELGYM_CONTROL_DB", str(database))
    monkeypatch.setenv("PIXELGYM_IMMUTABLE_ROOT", str(tmp_path / "immutable"))
    monkeypatch.setenv("PIXELGYM_CSRF_SECRET", "test-secret-at-least-sixteen")
    return bootstrap.create_app(), control


def _approved_candidate(control: ControlStore, policy, summary, report):
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )
    control.approve(
        candidate.candidate_id,
        actor="local-reviewer",
        reason="reviewed",
        gate_report_sha256=candidate.gate_report_sha256,
    )
    return candidate


def test_assembled_app_deploys_only_the_isolated_smoke_tested_runtime(
    tmp_path: Path, repository_root: Path, monkeypatch, passing_evidence, policy_factory
) -> None:
    app, control = _assembled_platform_app(tmp_path, repository_root, monkeypatch)
    policy, summary, report = passing_evidence
    candidate = _approved_candidate(control, policy, summary, report)

    deployed = app.state.deployment_coordinator.deploy(
        candidate.candidate_id, actor="local-reviewer", reason="first deploy"
    )

    runtime = app.state.policy_runtime.loaded
    assert runtime is not None
    assert runtime.manifest.policy_id == candidate.policy.policy_id
    assert runtime.deployment_id == deployed.deployment_id
    assert runtime.exact_policy_version == candidate.candidate_id
    response = TestClient(app).get("/api/v1/policy")
    assert response.json()["policy_id"] == candidate.policy.policy_id
    assert response.json()["deployment_id"] == deployed.deployment_id

    rollback_policy = policy_factory(1)
    rollback_summary = dataclasses.replace(
        summary, run_id="run-rollback", policy_id=rollback_policy.policy_id
    )
    rollback_report = dataclasses.replace(
        report, run_id="run-rollback", policy_id=rollback_policy.policy_id
    )
    rollback_candidate = _approved_candidate(
        control, rollback_policy, rollback_summary, rollback_report
    )
    second = app.state.deployment_coordinator.deploy(
        rollback_candidate.candidate_id, actor="local-reviewer", reason="second deploy"
    )
    restored = app.state.deployment_coordinator.rollback(
        actor="local-reviewer", reason="deterministic rollback"
    )
    runtime = app.state.policy_runtime.loaded
    assert runtime is not None
    assert restored.candidate_id == candidate.candidate_id
    assert runtime.manifest.policy_id == candidate.policy.policy_id
    assert runtime.deployment_id == restored.deployment_id
    assert second.deployment_id != restored.deployment_id
    # A rollback is an append-only event, so another previous event remains
    # available and the operator control must not disappear after the first one.
    deployment_page = TestClient(app).get("/deployment")
    assert "Rollback to previous approved version" in deployment_page.text
    repeated = app.state.deployment_coordinator.rollback(
        actor="local-reviewer", reason="repeat deterministic rollback"
    )
    assert repeated.candidate_id == rollback_candidate.candidate_id


@pytest.mark.parametrize("failure", ["provider", "invalid-output", "identity"])
def test_assembled_app_pre_activation_failures_preserve_active_pointer_and_runtime(
    tmp_path: Path, repository_root: Path, monkeypatch, passing_evidence, failure: str
) -> None:
    app, control = _assembled_platform_app(tmp_path, repository_root, monkeypatch)
    policy, summary, report = passing_evidence
    first = _approved_candidate(control, policy, summary, report)
    active = app.state.deployment_coordinator.deploy(
        first.candidate_id, actor="local-reviewer", reason="first deploy"
    )
    # Rebuild a valid, distinct policy instead of mutating identity fields.
    from pixelgym.platform.policy import build_policy_manifest, prompt_template

    prompt_version = 3 if failure == "identity" else policy.prompt_version
    prompt = "identity-mismatch fixture" if failure == "identity" else prompt_template(prompt_version) + " second"
    second_policy = build_policy_manifest(
        provider=policy.provider,
        model=policy.model + "-second",
        prompt_name=policy.prompt_name,
        prompt_version=prompt_version,
        prompt=prompt,
        condition=policy.condition,
        parameters=policy.parameters,
        parser_version=policy.parser_version,
        scorer_version=policy.scorer_version,
        overlay_version=policy.overlay_version,
        target_semantics=policy.target_semantics,
        source_provenance=__import__("pixelgym.platform.source_provenance", fromlist=["SourceProvenance"]).SourceProvenance(
            "pixelgym-source-provenance-v1", policy.code_revision, policy.source_tree_sha256,
            policy.code_state, "git-build-inputs-v1"
        ),
        dependency_lock_sha256=policy.dependency_lock_sha256,
    )
    second_summary = dataclasses.replace(
        summary, run_id="run-2", policy_id=second_policy.policy_id
    )
    second_report = dataclasses.replace(
        report, run_id="run-2", policy_id=second_policy.policy_id
    )
    second = _approved_candidate(control, second_policy, second_summary, second_report)
    smoke = app.state.deployment_coordinator.load_and_smoke
    if failure == "provider":
        smoke.provider = ServingFake(failure="timeout")
    elif failure == "invalid-output":
        smoke.provider = ServingFake("not-json")

    with pytest.raises((DeploymentSmokeError, TransitionError)):
        app.state.deployment_coordinator.deploy(
            second.candidate_id, actor="local-reviewer", reason="must not activate"
        )

    assert control.active()[0] == active
    runtime = app.state.policy_runtime.loaded
    assert runtime is not None
    assert runtime.deployment_id == active.deployment_id
    assert runtime.manifest.policy_id == first.policy.policy_id


def _csrf(text: str) -> str:
    return re.search(r'<meta name="csrf-token" content="([0-9a-f]+)">', text).group(1)


def test_api_transcript_html_redaction_excludes_csrf_and_page_chrome() -> None:
    body = """<h1>Action blocked</h1><p>only an eligible candidate can be approved</p>
    <meta name="csrf-token" content="secret-token"><footer>private host</footer>"""
    assert _safe_body(body, "text/html") == {
        "title": "Action blocked",
        "detail": "only an eligible candidate can be approved",
    }


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
    artifacts = [
        ArtifactRef(
            logical_key="raw-responses/a.json",
            uri="https://evidence.test/raw/a.json",
            version_id="v1",
            sha256="a" * 64,
            size=1,
            media_type="application/json",
            retention_status="locked",
        ),
        ArtifactRef(
            logical_key="runs/submission-a/predictions.jsonl",
            uri="https://evidence.test/runs/a/predictions.jsonl",
            version_id="v1",
            sha256="b" * 64,
            size=1,
            media_type="application/x-ndjson",
            retention_status="locked",
        ),
        ArtifactRef(
            logical_key="runs/submission-a/gate-report.json",
            uri="https://evidence.test/runs/a/gate-report.json",
            version_id="v1",
            sha256="c" * 64,
            size=1,
            media_type="application/json",
            retention_status="locked",
        ),
    ]
    first = control.register_candidate(
        source_run_id=summary.run_id, policy=policy, gate_report=report, artifacts=artifacts, summary=summary
    )
    second_policy = policy_factory(model="another-exact-model")
    second_report = replace(
        report,
        policy_id=second_policy.policy_id,
        run_id="run-2",
        accuracy=replace(report.accuracy, observed=0.9),
    )
    second_summary = replace(summary, run_id="run-2", policy_id=second_policy.policy_id, correct_count=90, accuracy=0.9)
    second = control.register_candidate(
        source_run_id="run-2", policy=second_policy, gate_report=second_report, artifacts=[], summary=second_summary
    )
    client = TestClient(create_control_app(control, csrf_secret="test-secret-at-least-sixteen"))
    response = client.get(f"/compare?candidate={first.candidate_id}&candidate={second.candidate_id}")
    assert "COMPATIBLE" in response.text
    assert "Cost / 100" in response.text
    assert "Provider p95" in response.text
    assert "Accuracy" in response.text
    assert "80.0%" in response.text
    assert "Δ +10.0 pp" in response.text
    assert "Raw-response index (1)" in response.text
    assert "Per-example errors" in response.text
    assert "Gate report" in response.text
    assert "MLflow run" in response.text
    assert "Prompt diff (recorded versions)" in response.text
    assert "Raw-response index unavailable" in response.text

    raw = client.get(f"/candidates/{first.candidate_id}/evidence/raw-responses")
    assert raw.status_code == 200
    assert "raw-responses/a.json" in raw.text
    assert "https://evidence.test/raw/a.json" in raw.text
    diff = client.get(f"/compare/prompt-diff?candidate={first.candidate_id}&candidate={second.candidate_id}")
    assert diff.status_code == 200
    assert "no separate immutable prompt-diff artifact was recorded" in diff.text


def test_runs_render_recorded_badges_filters_summary_and_fixture_disclosure(
    tmp_path: Path, passing_evidence, policy_factory
) -> None:
    _, summary, report = passing_evidence
    from dataclasses import replace

    policy = policy_factory(
        model="recorded-real-model",
        provider="recorded-real-provider",
        code_state="dirty",
    )
    summary = replace(summary, policy_id=policy.policy_id, synthetic_provider=False, dirty_code=True)
    report = replace(report, policy_id=policy.policy_id)

    incomplete = replace(
        report,
        cost_usd_per_100=replace(report.cost_usd_per_100, observed=None, passed=False),
        completeness=replace(report.completeness, scored=99, unique=99, passed=False),
        overall_passed=False,
        reasons=("incomplete and unpriced",),
    )
    summary = replace(summary, invalid_count=3, unpriced_call_count=1)
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=incomplete,
        artifacts=[],
        summary=summary,
    )
    client = TestClient(create_control_app(control, csrf_secret="test-secret-at-least-sixteen"))
    runs = client.get(
        f"/runs?provider=recorded-real-provider&lifecycle=GateFailed&dataset={report.dataset_fingerprint.removeprefix('sha256:')[:8]}&code_revision={policy.code_revision[:8]}"
    )
    assert runs.status_code == 200
    assert "REAL PROVIDER" in runs.text
    assert "INCOMPLETE" in runs.text
    assert "DIRTY CODE" in runs.text
    assert "UNPRICED" in runs.text
    assert "invalid 3" in runs.text
    assert report.dataset_fingerprint.removeprefix("sha256:")[:12] in runs.text
    assert policy.code_revision[:12] in runs.text
    assert "Filter stored runs" in runs.text
    detail = client.get(f"/candidates/{candidate.candidate_id}")
    assert "Synthetic fixture disclosure" not in detail.text
    replay_policy, replay_summary, replay_report = passing_evidence
    replay = control.register_candidate(
        source_run_id="replay-run",
        policy=replay_policy,
        gate_report=replace(replay_report, run_id="replay-run"),
        artifacts=[],
        summary=replace(replay_summary, run_id="replay-run"),
    )
    detail = client.get(f"/candidates/{replay.candidate_id}")
    assert "Synthetic fixture disclosure" in detail.text
    assert 'condition == "marks"' in detail.text
    assert "relabeled" in detail.text
    assert detail.text.index('name="csrf-token"') < detail.text.index("</head>")


def test_runs_render_safe_source_provenance_diagnostic(
    tmp_path: Path, passing_evidence, gate_policy
) -> None:
    _policy, summary, _report = passing_evidence
    policy = build_policy_manifest(
        provider="scripted-demo",
        model="day3-replay-revised-v2",
        prompt_name="pixelgym-grounding",
        prompt_version=2,
        prompt=prompt_template(2),
        condition="raw",
        parameters={"deterministic": True, "hidden_retries": 0},
        parser_version="pixelgym-grounding-parser-v1",
        scorer_version=gate_policy.required_scorer_version,
        overlay_version="none-raw-coordinate-policy",
        target_semantics=gate_policy.required_target_semantics,
        source_provenance=SourceProvenance(
            SOURCE_PROVENANCE_SCHEMA_VERSION,
            None,
            None,
            "unverifiable",
            "none",
            "manifest_malformed_json",
        ),
        dependency_lock_sha256="a" * 64,
    )
    summary = dataclasses.replace(
        summary,
        policy_id=policy.policy_id,
        code_state="unverifiable",
        code_provenance_verified=False,
    )
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=evaluate_gates(gate_policy, summary),
        artifacts=[],
        summary=summary,
    )
    client = TestClient(create_control_app(control, csrf_secret="test-secret-at-least-sixteen"))
    page = client.get("/runs")
    assert "PROVENANCE: MANIFEST MALFORMED JSON" in page.text
