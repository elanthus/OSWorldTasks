"""Fresh-stack browser and real-service lifecycle acceptance coverage."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from threading import Barrier
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

import pytest

from pixelgym.platform.contracts import ArtifactRef, GatePolicy, PolicyManifest
from pixelgym.platform.evaluation import EvaluationRunner, ScriptedReplayProvider
from pixelgym.platform.immutable_store import ImmutableStoreError, S3ImmutableStore
from pixelgym.platform.mlflow_tracking import RUN_PARAM_KEYS

playwright_api = pytest.importorskip("playwright.sync_api")
boto3 = pytest.importorskip("boto3")
mlflow = pytest.importorskip("mlflow")
psycopg2 = pytest.importorskip("psycopg2")

pytestmark = [
    pytest.mark.platform_compose_integration,
    pytest.mark.browser_integration,
]


def _csrf(page) -> str:
    value = page.locator('meta[name="csrf-token"]').get_attribute("content")
    assert value
    return value


def _submit(page, stack, *, prompt_version: str, model: str) -> None:
    page.goto(stack.platform_url)
    page.select_option('select[name="prompt_version"]', prompt_version)
    page.select_option('select[name="model"]', model)
    page.get_by_role("button", name="Submit fixed evaluation").click()
    page.wait_for_url(f"{stack.platform_url}/submissions/submission-*")
    playwright_api.expect(page.locator("h1")).to_contain_text("submission-")
    playwright_api.expect(page.get_by_text("Metaflow pathspec", exact=True)).to_be_visible()


def _candidate(page, stack, model: str, state: str) -> str:
    deadline = time.monotonic() + 300
    last_table = ""
    while time.monotonic() < deadline:
        page.goto(f"{stack.platform_url}/runs")
        row = page.locator("tbody tr", has_text=model)
        if row.count() == 1 and state in row.inner_text():
            href = row.locator("a").first.get_attribute("href")
            assert href and href.startswith("/candidates/")
            return href.removeprefix("/candidates/")
        last_table = page.locator("table").inner_text()
        page.wait_for_timeout(1_000)
    raise AssertionError(
        f"candidate for {model!r} did not reach {state!r}; last runs table:\n{last_table}"
    )


def _approve_and_deploy(page, stack, candidate_id: str, label: str) -> dict[str, object]:
    _approve(page, stack, candidate_id, label)
    return _deploy(page, stack, candidate_id, label)


def _approve(page, stack, candidate_id: str, label: str) -> None:
    page.goto(f"{stack.platform_url}/candidates/{candidate_id}")
    playwright_api.expect(page.locator("main")).to_contain_text("Eligible")
    page.locator('form[action$="/approve"] textarea[name="reason"]').fill(
        f"integration approval for {label}"
    )
    page.locator('form[action$="/approve"] button').click()
    playwright_api.expect(page.locator("main")).to_contain_text("Approved")


def _deploy(page, stack, candidate_id: str, label: str) -> dict[str, object]:
    page.goto(f"{stack.platform_url}/candidates/{candidate_id}")
    playwright_api.expect(page.locator("main")).to_contain_text("Approved")
    page.locator('form[action$="/deploy"] textarea[name="reason"]').fill(
        f"integration deployment for {label}"
    )
    page.locator('form[action$="/deploy"] button').click()
    page.wait_for_url(f"{stack.platform_url}/deployment")
    policy = page.context.request.get(f"{stack.platform_url}/api/v1/policy")
    assert policy.status == 200
    return policy.json()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _concurrent_form_post(
    stack,
    *,
    csrf_page: str,
    form_path: str,
    fields: dict[str, str],
    barrier: Barrier,
) -> int:
    opener = build_opener(HTTPCookieProcessor(CookieJar()), _NoRedirect())
    with opener.open(f"{stack.platform_url}{csrf_page}", timeout=10) as response:
        body = response.read().decode("utf-8")
    match = re.search(r'<meta name="csrf-token" content="([0-9a-f]+)">', body)
    assert match is not None
    expected_deployment = re.search(r'name="expected_deployment_id" value="([^"]*)"', body)
    expected_generation = re.search(r'name="expected_generation" value="([0-9]+)"', body)
    payload = {"csrf_token": match.group(1), **fields}
    if form_path.endswith("/deploy") or form_path == "/rollback":
        assert expected_deployment is not None and expected_generation is not None
        payload["expected_deployment_id"] = expected_deployment.group(1)
        payload["expected_generation"] = expected_generation.group(1)
    barrier.wait(timeout=10)
    request = Request(
        f"{stack.platform_url}{form_path}",
        data=urlencode(payload).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=120) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def _race_forms(stack, requests: list[tuple[str, str, dict[str, str]]]) -> list[int]:
    barrier = Barrier(len(requests))
    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        futures = [
            executor.submit(
                _concurrent_form_post,
                stack,
                csrf_page=csrf_page,
                form_path=form_path,
                fields=fields,
                barrier=barrier,
            )
            for csrf_page, form_path, fields in requests
        ]
    return [future.result(timeout=1) for future in futures]


def _s3_client(stack):
    return boto3.client(
        "s3",
        endpoint_url=stack.minio_url,
        aws_access_key_id=stack.environment["PIXELGYM_MINIO_USER"],
        aws_secret_access_key=stack.environment["PIXELGYM_MINIO_PASSWORD"],
        region_name="us-east-1",
    )


def _control_count(stack, table: str) -> int:
    if table not in {"candidates", "approvals", "deployments"}:
        raise ValueError("control table is not allowlisted")
    completed = stack.compose(
        "exec", "-T", "platform", "python", "-c",
        "import sqlite3,sys; c=sqlite3.connect('/state/control.db'); print(c.execute(f'SELECT COUNT(*) FROM {sys.argv[1]}').fetchone()[0])",
        table, timeout=30,
    )
    return int(completed.stdout.strip())


def _candidate_evidence(stack, candidate_id: str) -> tuple[PolicyManifest, list[ArtifactRef]]:
    completed = stack.compose(
        "exec", "-T", "platform", "python", "-c",
        "import json,sqlite3,sys; c=sqlite3.connect('/state/control.db'); r=c.execute('SELECT policy_json,artifacts_json FROM candidates WHERE candidate_id=?',(sys.argv[1],)).fetchone(); print(json.dumps({'policy':json.loads(r[0]),'artifacts':json.loads(r[1])}))",
        candidate_id, timeout=30,
    )
    payload = json.loads(completed.stdout)
    return PolicyManifest(**payload["policy"]), [ArtifactRef(**item) for item in payload["artifacts"]]


def _assert_real_s3_raw_tamper_blocks_before_registration(stack, candidate_id: str) -> None:
    policy, artifacts = _candidate_evidence(stack, candidate_id)
    store = S3ImmutableStore(
        bucket="pixelgym-immutable", prefix="platform", client=_s3_client(stack),
        object_lock=True, retention_days=30,
    )
    raw_reference = next(
        item for item in artifacts if item.logical_key.startswith("raw-responses/")
    )
    example_id = json.loads(store.get_verified(raw_reference))["example_id"]
    gate_policy = GatePolicy(
        **json.loads((stack.repository_root / "config/promotion-gates.demo-v1.json").read_text())
    )
    runner = EvaluationRunner(
        repository_root=stack.repository_root,
        store=store,
        tracking=None,
        provider=ScriptedReplayProvider(
            stack.repository_root / "artifacts/grounding-predictions.jsonl",
            variant="revised", model=policy.model,
        ),
        policy=policy,
        gate_policy=gate_policy,
        dataset_fingerprint=gate_policy.required_dataset_fingerprint,
        submission_id="real-minio-tamper-verification-only",
        metaflow_pathspec="GroundingEvaluationFlow/real-minio-tamper-verification-only",
    )
    candidate_count = _control_count(stack, "candidates")
    s3 = store.client
    key = f"platform/{raw_reference.logical_key}"
    created_versions: list[str] = []

    def put_version(data: bytes) -> str:
        result = s3.put_object(
            Bucket="pixelgym-immutable", Key=key, Body=data,
            ContentType=raw_reference.media_type,
            Metadata={"sha256": hashlib.sha256(data).hexdigest(), "media-type": raw_reference.media_type},
            ObjectLockMode="GOVERNANCE",
            ObjectLockRetainUntilDate=datetime.now(UTC) + timedelta(days=1),
        )
        created_versions.append(result["VersionId"])
        return result["VersionId"]

    def row(reference: ArtifactRef) -> list[dict[str, object]]:
        return [{"example_id": example_id, "reference": reference.to_dict()}]

    try:
        changed = b"X" * raw_reference.size
        changed_reference = ArtifactRef(
            **{**raw_reference.to_dict(), "version_id": put_version(changed)}
        )
        with pytest.raises(ImmutableStoreError, match="size or digest verification"):
            runner.verify_raw_artifacts(row(changed_reference), require_complete=False)

        corrupt = b"{not-valid-json"
        corrupt_reference = ArtifactRef(
            **{
                **raw_reference.to_dict(),
                "version_id": put_version(corrupt),
                "sha256": hashlib.sha256(corrupt).hexdigest(),
                "size": len(corrupt),
            }
        )
        with pytest.raises(json.JSONDecodeError):
            runner.verify_raw_artifacts(row(corrupt_reference), require_complete=False)

        missing_reference = ArtifactRef(
            **{**raw_reference.to_dict(), "version_id": "missing-integration-version"}
        )
        with pytest.raises(ImmutableStoreError, match="pinned immutable S3 object is missing"):
            runner.verify_raw_artifacts(row(missing_reference), require_complete=False)
        assert _control_count(stack, "candidates") == candidate_count
    finally:
        for version_id in created_versions:
            s3.delete_object(
                Bucket="pixelgym-immutable", Key=key, VersionId=version_id,
                BypassGovernanceRetention=True,
            )


def test_fresh_compose_browser_lifecycle_and_real_service_integrity(compose_stack) -> None:
    stack = compose_stack
    seeded = stack.compose(
        "exec",
        "-T",
        "platform",
        "python",
        "scripts/platform_demo.py",
        "--database",
        "/state/control.db",
        "--fixture",
        "rollback-seed",
        timeout=360,
    )
    assert "rollback-seed" in seeded.stdout

    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            seed_id = _candidate(
                page, stack, "day3-replay-revised-rollback-seed-v1", "Eligible"
            )
            seed_policy = _approve_and_deploy(page, stack, seed_id, "rollback seed")

            _submit(
                page,
                stack,
                prompt_version="1",
                model="day3-replay-baseline-v1",
            )
            candidate_a = _candidate(
                page, stack, "day3-replay-baseline-v1", "GateFailed"
            )
            page.goto(f"{stack.platform_url}/candidates/{candidate_a}")
            playwright_api.expect(page.get_by_text("Approval unavailable")).to_be_visible()
            assert page.locator('form[action$="/approve"]').count() == 0
            blocked = page.context.request.post(
                f"{stack.platform_url}/api/candidates/{candidate_a}/approve",
                headers={"X-CSRF-Token": _csrf(page)},
                data={"reason": "must remain blocked"},
            )
            assert blocked.status == 409

            _submit(
                page,
                stack,
                prompt_version="2",
                model="day3-replay-revised-v2",
            )
            candidate_b = _candidate(
                page, stack, "day3-replay-revised-v2", "Eligible"
            )
            page.goto(f"{stack.platform_url}/deployment")
            generation_match = re.search(r"generation ([0-9]+)", page.locator("main").inner_text())
            assert generation_match is not None
            page.goto(f"{stack.platform_url}/candidates/{candidate_b}")
            csrf = _csrf(page)
            blocked_deploy = page.context.request.post(
                f"{stack.platform_url}/candidates/{candidate_b}/deploy",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=urlencode(
                    {
                        "csrf_token": csrf, "reason": "approval must come first",
                        "expected_deployment_id": seed_policy["deployment_id"],
                        "expected_generation": generation_match.group(1),
                    }
                ),
                max_redirects=0,
            )
            assert blocked_deploy.status == 409

            page.goto(
                f"{stack.platform_url}/compare?"
                + urlencode(
                    [("candidate", candidate_a), ("candidate", candidate_b)]
                )
            )
            playwright_api.expect(page.get_by_text("COMPATIBLE", exact=True)).to_be_visible()
            playwright_api.expect(page.locator("main")).to_contain_text("Accuracy")
            playwright_api.expect(page.locator("main")).to_contain_text("Cost / 100")
            playwright_api.expect(page.locator("main")).to_contain_text("Provider p95")

            _approve(page, stack, candidate_b, "candidate B")
            duplicate_approvals = _race_forms(
                stack,
                [
                    (
                        f"/candidates/{candidate_b}",
                        f"/candidates/{candidate_b}/approve",
                        {"reason": f"concurrent duplicate approval {index}"},
                    )
                    for index in range(2)
                ],
            )
            assert duplicate_approvals == [409, 409]

            concurrent_deploys = _race_forms(
                stack,
                [
                    (
                        f"/candidates/{candidate_b}",
                        f"/candidates/{candidate_b}/deploy",
                        {"reason": f"concurrent deployment {index}"},
                    )
                    for index in range(2)
                ],
            )
            assert sorted(concurrent_deploys) == [303, 409]
            race_deployed = page.context.request.get(
                f"{stack.platform_url}/api/v1/policy"
            ).json()
            assert race_deployed["exact_policy_version"] == candidate_b

            concurrent_rollbacks = _race_forms(
                stack,
                [
                    (
                        "/deployment",
                        "/rollback",
                        {"reason": f"concurrent rollback {index}"},
                    )
                    for index in range(2)
                ],
            )
            assert sorted(concurrent_rollbacks) == [303, 409]
            race_active = page.context.request.get(
                f"{stack.platform_url}/api/v1/policy"
            ).json()
            assert race_active["exact_policy_version"] == seed_id
            active_rows = stack.compose(
                "exec",
                "-T",
                "platform",
                "python",
                "-c",
                (
                    "import sqlite3; c=sqlite3.connect('/state/control.db'); "
                    "print(c.execute('SELECT COUNT(*) FROM active_pointer WHERE singleton=1').fetchone()[0])"
                ),
                timeout=30,
            )
            assert active_rows.stdout.strip() == "1"

            b_policy = _deploy(page, stack, candidate_b, "candidate B")
            assert b_policy["policy_id"] != seed_policy["policy_id"]
            assert b_policy["deployment_id"] != seed_policy["deployment_id"]

            example = json.loads(
                (stack.repository_root / "artifacts/grounding-dataset.jsonl")
                .read_text()
                .splitlines()[0]
            )
            image_bytes = (stack.repository_root / example["image_path"]).read_bytes()
            ground = page.context.request.post(
                f"{stack.platform_url}/api/v1/ground",
                data={
                    "image_base64": base64.b64encode(image_bytes).decode("ascii"),
                    "media_type": "image/png",
                    "target": example["target"],
                },
            )
            assert ground.status == 200
            ground_body = ground.json()
            assert ground_body["policy_id"] == b_policy["policy_id"]
            assert ground_body["deployment_id"] == b_policy["deployment_id"]

            page.goto(f"{stack.platform_url}/deployment")
            page.locator('form[action="/rollback"] textarea[name="reason"]').fill(
                "integration rollback"
            )
            page.locator('form[action="/rollback"] button').click()
            rolled_back = page.context.request.get(
                f"{stack.platform_url}/api/v1/policy"
            ).json()
            assert rolled_back["policy_id"] == seed_policy["policy_id"]
            assert rolled_back["deployment_id"] not in {
                seed_policy["deployment_id"],
                b_policy["deployment_id"],
            }

            stack.compose("restart", "platform", timeout=180)
            stack.wait_http("/health/live")
            restored = page.context.request.get(
                f"{stack.platform_url}/api/v1/policy"
            ).json()
            assert restored == rolled_back
        finally:
            browser.close()

    s3 = _s3_client(stack)
    policy_key = f"platform/policies/{b_policy['policy_id'].removeprefix('sha256:')}.json"
    head = s3.head_object(Bucket="pixelgym-immutable", Key=policy_key)
    assert head["VersionId"] != "null"
    assert head["ObjectLockMode"] == "GOVERNANCE"
    policy_bytes = s3.get_object(
        Bucket="pixelgym-immutable", Key=policy_key, VersionId=head["VersionId"]
    )["Body"].read()
    assert hashlib.sha256(policy_bytes).hexdigest() == head["Metadata"]["sha256"]
    _assert_real_s3_raw_tamper_blocks_before_registration(stack, candidate_b)

    tracking = mlflow.MlflowClient(tracking_uri=stack.mlflow_url)
    experiment = tracking.get_experiment_by_name("pixelgym-grounding")
    assert experiment is not None
    runs = tracking.search_runs([experiment.experiment_id], max_results=20)
    revised = next(
        run for run in runs if run.data.params.get("model") == "day3-replay-revised-v2"
    )
    assert set(revised.data.params) == set(RUN_PARAM_KEYS)
    assert revised.data.tags["mlflow.run_id"] == revised.info.run_id
    assert revised.data.tags["metaflow.pathspec"].startswith("GroundingEvaluationFlow/")

    with psycopg2.connect(
        host="127.0.0.1",
        port=stack.postgres_port,
        dbname="mlflow",
        user=stack.environment["PIXELGYM_POSTGRES_USER"],
        password=stack.environment["PIXELGYM_POSTGRES_PASSWORD"],
        connect_timeout=5,
    ) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM runs WHERE run_uuid = %s", (revised.info.run_id,)
        )
        assert cursor.fetchone()[0] == 1

    stack.compose("stop", "platform", timeout=60)
    stack.compose(
        "run",
        "--rm",
        "--no-deps",
        "migrate",
        "python",
        "-c",
        (
            "import sqlite3; "
            "connection=sqlite3.connect('/state/control.db'); "
            "connection.execute(\"ALTER TABLE deployments ADD COLUMN "
            "previous_deployment_id TEXT REFERENCES deployments(deployment_id)\"); "
            "connection.commit(); connection.close()"
        ),
        timeout=120,
    )
    stack.compose("run", "--rm", "--no-deps", "migrate", timeout=120)
    stack.compose(
        "up", "-d", "--wait", "--wait-timeout", "120", "platform", timeout=180
    )
    stack.wait_http("/health/live")
    with playwright_api.sync_playwright() as playwright:
        request = playwright.request.new_context()
        try:
            migrated_policy = request.get(f"{stack.platform_url}/api/v1/policy")
            assert migrated_policy.status == 200
            assert migrated_policy.json() == restored
        finally:
            request.dispose()

    s3.delete_object(
        Bucket="pixelgym-immutable",
        Key=policy_key,
        VersionId=head["VersionId"],
        BypassGovernanceRetention=True,
    )
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"{stack.platform_url}/candidates/{candidate_b}")
            page.locator('form[action$="/deploy"] textarea[name="reason"]').fill(
                "missing package must fail"
            )
            page.locator('form[action$="/deploy"] button').click()
            playwright_api.expect(page.locator("main")).to_contain_text("Action blocked")
            still_active = page.context.request.get(
                f"{stack.platform_url}/api/v1/policy"
            ).json()
            assert still_active == restored

            page.goto(f"{stack.platform_url}/deployment")
            assert page.locator('form[action="/rollback"]').count() == 0
            assert page.context.request.get(
                f"{stack.platform_url}/api/v1/policy"
            ).json() == restored
        finally:
            browser.close()


def test_compose_diagnostics_redact_credentials_and_host_paths(compose_stack) -> None:
    assert compose_stack.environment["PIXELGYM_POSTGRES_PASSWORD"] == (
        "local_demo_postgres_only"
    )
    assert compose_stack.environment["PIXELGYM_MINIO_PASSWORD"] == "local_demo_minio_only"
    assert compose_stack.environment["PIXELGYM_CSRF_SECRET"] == (
        "local-demo-csrf-secret-change-before-any-shared-use"
    )
    raw = (
        f"{compose_stack.repository_root} {Path.home()} "
        "local_demo_postgres_only local_demo_minio_only "
        "local-demo-csrf-secret-change-before-any-shared-use"
    )
    redacted = compose_stack.redact(raw)
    assert str(compose_stack.repository_root) not in redacted
    assert str(Path.home()) not in redacted
    assert "local_demo_postgres_only" not in redacted
    assert "local_demo_minio_only" not in redacted
    assert "local-demo-csrf-secret" not in redacted
