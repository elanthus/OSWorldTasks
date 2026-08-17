"""Fresh-stack browser and real-service lifecycle acceptance coverage."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest

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
    page.wait_for_url(f"{stack.platform_url}/runs**")
    assert "submitted=submission-" in page.url
    playwright_api.expect(page.locator(".notice")).to_contain_text("accepted")


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
    page.goto(f"{stack.platform_url}/candidates/{candidate_id}")
    playwright_api.expect(page.locator("main")).to_contain_text("Eligible")
    page.locator('form[action$="/approve"] textarea[name="reason"]').fill(
        f"integration approval for {label}"
    )
    page.locator('form[action$="/approve"] button').click()
    playwright_api.expect(page.locator("main")).to_contain_text("Approved")
    page.locator('form[action$="/deploy"] textarea[name="reason"]').fill(
        f"integration deployment for {label}"
    )
    page.locator('form[action$="/deploy"] button').click()
    page.wait_for_url(f"{stack.platform_url}/deployment")
    policy = page.context.request.get(f"{stack.platform_url}/api/v1/policy")
    assert policy.status == 200
    return policy.json()


def _s3_client(stack):
    return boto3.client(
        "s3",
        endpoint_url=stack.minio_url,
        aws_access_key_id="pixelgym_demo",
        aws_secret_access_key="local_demo_minio_only",
        region_name="us-east-1",
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
            page.goto(f"{stack.platform_url}/candidates/{candidate_b}")
            csrf = _csrf(page)
            blocked_deploy = page.context.request.post(
                f"{stack.platform_url}/candidates/{candidate_b}/deploy",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=urlencode(
                    {"csrf_token": csrf, "reason": "approval must come first"}
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

            b_policy = _approve_and_deploy(page, stack, candidate_b, "candidate B")
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
        user="pixelgym_demo",
        password="local_demo_postgres_only",
        connect_timeout=5,
    ) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM runs")
        assert cursor.fetchone()[0] == len(runs)

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
            page.locator('form[action="/rollback"] textarea[name="reason"]').fill(
                "missing previous package must block rollback"
            )
            page.locator('form[action="/rollback"] button').click()
            playwright_api.expect(page.locator("main")).to_contain_text("Action blocked")
            assert page.context.request.get(
                f"{stack.platform_url}/api/v1/policy"
            ).json() == restored
        finally:
            browser.close()


def test_compose_diagnostics_redact_credentials_and_host_paths(compose_stack) -> None:
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
