"""Submission history, live status, and rollback-label coverage for the control web app (#165)."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.web import app as web_app
from pixelgym.platform.web import create_control_app

SECRET = "test-secret-at-least-sixteen"
MLFLOW = "http://mlflow.test"


def _request(model: str) -> dict[str, str]:
    return {
        "dataset": "day3-frozen-v1",
        "prompt_version": "2",
        "model": model,
        "condition": "raw",
        "maximum_calls": "100",
        "price_catalog": "pixelgym-demo-prices-v1",
    }


def _control(tmp_path: Path) -> ControlStore:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    return control


def _client(control: ControlStore, **kwargs: Any) -> TestClient:
    return TestClient(
        create_control_app(control, csrf_secret=SECRET, mlflow_base_url=MLFLOW, **kwargs),
        client=("127.0.0.1", 50000),
    )


def _history(tmp_path: Path, passing_evidence) -> tuple[ControlStore, dict[str, str], Any]:
    """One submission per terminal and non-terminal status, only one with a candidate."""
    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    ids = {
        name: control.submit(_request(f"model-{name}"))
        for name in ("submitted", "running", "failed", "cancelled_queued", "cancelled_running")
    }
    control.link_run(ids["running"], metaflow_pathspec="Flow/11", mlflow_run_id="run-running")
    control.link_run(ids["failed"], metaflow_pathspec="Flow/12", mlflow_run_id="run-failed")
    control.mark_submission(ids["failed"], "Failed")
    control.cancel_submission(ids["cancelled_queued"], actor="local-reviewer", reason="queued")
    control.link_run(
        ids["cancelled_running"], metaflow_pathspec="Flow/13", mlflow_run_id="run-cancelled"
    )
    control.cancel_submission(ids["cancelled_running"], actor="local-reviewer", reason="stop")
    ids["complete"] = control.submit(_request(policy.model))
    control.link_run(ids["complete"], metaflow_pathspec="Flow/14", mlflow_run_id=summary.run_id)
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
        summary=summary,
        submission_id=ids["complete"],
    )
    return control, ids, candidate


def _row(html: str, submission_id: str) -> str:
    match = re.search(
        rf"<tr><td><a href=\"/submissions/{submission_id}\">.*?</tr>", html, re.DOTALL
    )
    assert match is not None, submission_id
    return match.group(0)


def test_submission_history_lists_every_submission_with_lineage_and_partial_evidence(
    tmp_path: Path, passing_evidence
) -> None:
    control, ids, candidate = _history(tmp_path, passing_evidence)
    client = _client(control)

    runs = client.get("/runs")
    assert runs.status_code == 200
    # /runs remains the candidate table; non-candidate submissions are absent from it but the
    # page links to the history view where they are listed.
    assert ids["failed"] not in runs.text
    assert '<a href="/submissions">submission history</a>' in runs.text

    page = client.get("/submissions")
    assert page.status_code == 200
    assert '<a href="/submissions">Submissions</a>' in page.text  # primary navigation
    for submission_id in ids.values():
        assert f'href="/submissions/{submission_id}"' in page.text

    submitted = _row(page.text, ids["submitted"])
    assert "badge--neutral" in submitted and ">Submitted<" in submitted
    assert ">pending<" in submitted
    assert "No run linked" in submitted
    assert "No candidate registered" in submitted

    running = _row(page.text, ids["running"])
    assert ">Running<" in running and ">Flow/11<" in running
    assert f'href="{MLFLOW}/#/experiments/0/runs/run-running"' in running
    assert "Partial evidence retained" not in running  # still in progress

    for name, pathspec, run_id in (
        ("failed", "Flow/12", "run-failed"),
        ("cancelled_running", "Flow/13", "run-cancelled"),
    ):
        row = _row(page.text, ids[name])
        assert "badge--bad" in row
        assert f">{pathspec}<" in row
        assert f'href="{MLFLOW}/#/experiments/0/runs/{run_id}"' in row
        assert "Partial evidence retained" in row
        assert "No candidate registered" in row

    queued = _row(page.text, ids["cancelled_queued"])
    assert ">Cancelled<" in queued and "No run linked" in queued
    assert "Partial evidence retained" not in queued

    complete = _row(page.text, ids["complete"])
    assert "badge--good" in complete
    assert f'<a href="/candidates/{candidate.candidate_id}">' in complete
    assert "No candidate registered" not in complete


def test_submission_history_status_filter_and_pagination_are_bounded(
    tmp_path: Path, passing_evidence, monkeypatch: pytest.MonkeyPatch
) -> None:
    control, ids, _ = _history(tmp_path, passing_evidence)
    client = _client(control)

    failed = client.get("/submissions", params={"status": "Failed"})
    assert failed.status_code == 200
    assert ids["failed"] in failed.text
    assert ids["running"] not in failed.text
    assert '<option value="Failed" selected>' in failed.text

    cancelled = client.get("/submissions", params={"status": "Cancelled"}).text
    assert ids["cancelled_queued"] in cancelled and ids["cancelled_running"] in cancelled
    assert ids["failed"] not in cancelled

    assert "No submissions match" in client.get("/submissions?status=Complete&page=2").text
    for invalid in ("Unknown", "<script>"):
        rejected = client.get("/submissions", params={"status": invalid})
        assert rejected.status_code == 422
        assert "<script>" not in rejected.text

    monkeypatch.setattr(web_app, "SUBMISSIONS_PAGE_SIZE", 2)
    first = client.get("/submissions")
    assert "/submissions?page=2" in first.text
    assert "Previous page" not in first.text
    seen: set[str] = set()
    for page in (1, 2, 3):
        body = client.get("/submissions", params={"page": page}).text
        seen.update(value for value in ids.values() if f'href="/submissions/{value}"' in body)
    assert seen == set(ids.values())
    assert client.get("/submissions", params={"page": 0}).status_code == 422


def test_submission_history_reads_one_bounded_page(tmp_path: Path) -> None:
    control = _control(tmp_path)
    for index in range(7):
        control.submit(_request(f"model-{index}"))
    page = control.submission_history(limit=3, offset=3)
    assert len(page) == 3
    assert all(row["candidate_id"] is None for row in page)
    assert control.submission_history(limit=3, status="Failed") == []
    with pytest.raises(ValueError, match="limit"):
        control.submission_history(limit=0)
    with pytest.raises(ValueError, match="offset"):
        control.submission_history(limit=1, offset=-1)
    assert control.candidate_id_for_run(None) is None
    assert control.candidate_id_for_run("run-without-candidate") is None


def test_submission_detail_shows_candidate_or_retained_partial_evidence(
    tmp_path: Path, passing_evidence
) -> None:
    control, ids, candidate = _history(tmp_path, passing_evidence)
    client = _client(control)
    partial = '<p class="muted" data-live="partial-evidence">'
    hidden_partial = '<p class="muted" data-live="partial-evidence" hidden>'

    failed = client.get(f"/submissions/{ids['failed']}").text
    assert partial in failed
    assert "No candidate registered" in failed
    assert "data-submission-status-url" not in failed  # terminal: no polling
    assert "Cancellation is no longer available" in failed

    queued = client.get(f"/submissions/{ids['cancelled_queued']}").text
    assert hidden_partial in queued  # never ran, so there is no partial evidence to claim
    assert ">not recorded<" in queued
    assert "No MLflow run recorded" in queued

    running = client.get(f"/submissions/{ids['running']}").text
    assert hidden_partial in running
    assert f'data-submission-status-url="/api/submissions/{ids["running"]}/status"' in running
    assert 'data-live="cancel-form"' in running
    assert '<p class="muted" data-live="cancel-closed" hidden>' in running

    complete = client.get(f"/submissions/{ids['complete']}").text
    assert f'<a href="/candidates/{candidate.candidate_id}">' in complete
    assert hidden_partial in complete


def test_submission_status_api_reports_live_transitions_without_waiting(
    tmp_path: Path, passing_evidence
) -> None:
    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    client = _client(control)
    submission_id = control.submit(_request(policy.model))
    url = f"/api/submissions/{submission_id}/status"

    assert client.get(url).json() == {
        "submission_id": submission_id,
        "status": "Submitted",
        "metaflow_pathspec": None,
        "mlflow_uri": None,
        "cancellable": True,
        "candidate_id": None,
    }
    control.link_run(submission_id, metaflow_pathspec="Flow/7", mlflow_run_id=summary.run_id)
    running = client.get(url).json()
    assert running["status"] == "Running"
    assert running["metaflow_pathspec"] == "Flow/7"
    assert running["mlflow_uri"] == f"{MLFLOW}/#/experiments/0/runs/{summary.run_id}"
    assert running["cancellable"] is True
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
        summary=summary,
        submission_id=submission_id,
    )
    complete = client.get(url).json()
    assert complete["status"] == "Complete"
    assert complete["cancellable"] is False
    assert complete["candidate_id"] == candidate.candidate_id
    assert client.get("/api/submissions/submission-missing/status").status_code == 404


def test_status_api_never_returns_a_scriptable_mlflow_link(tmp_path: Path) -> None:
    control = _control(tmp_path)
    submission_id = control.submit(_request("m"))
    control.link_run(submission_id, metaflow_pathspec="Flow/1", mlflow_run_id="run-1")
    client = TestClient(
        create_control_app(control, csrf_secret=SECRET, mlflow_base_url="javascript:alert(1)")
    )
    assert client.get(f"/api/submissions/{submission_id}/status").json()["mlflow_uri"] is None


def test_runs_submission_notice_links_only_to_an_existing_submission(tmp_path: Path) -> None:
    control = _control(tmp_path)
    submission_id = control.submit(_request("m"))
    client = _client(control)
    linked = client.get("/runs", params={"submitted": submission_id}).text
    assert (
        f'<div class="notice"><a href="/submissions/{submission_id}">'
        f"Submission {submission_id} accepted. Follow its status →</a></div>"
    ) in linked
    unknown = client.get("/runs", params={"submitted": "submission-unknown"}).text
    assert '<div class="notice">Submission submission-unknown accepted.</div>' in unknown
    assert 'href="/submissions/submission-unknown"' not in unknown


class _StubCoordinator:
    """Only presence matters to the deployment view; rollback is never invoked here."""


def test_rollback_form_states_that_validation_happens_on_action(
    tmp_path: Path, passing_evidence, policy_factory
) -> None:
    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    first = control.register_candidate(
        source_run_id=summary.run_id, policy=policy, gate_report=report, artifacts=[]
    )
    second_policy = policy_factory(model="second-exact-model")
    second = control.register_candidate(
        source_run_id="run-2",
        policy=second_policy,
        gate_report=replace(report, policy_id=second_policy.policy_id, run_id="run-2"),
        artifacts=[],
    )
    previous = None
    for item in (first, second):
        control.approve(
            item.candidate_id,
            actor="local-reviewer",
            reason="reviewed",
            gate_report_sha256=item.gate_report_sha256,
        )
        previous = control.activate(
            item.candidate_id,
            actor="local-reviewer",
            reason="deploy",
            action="deploy",
            expected_deployment_id=None if previous is None else previous.deployment_id,
            expected_generation=0 if previous is None else previous.generation,
        )
    page = _client(control, coordinator=_StubCoordinator()).get("/deployment").text
    assert 'action="/rollback"' in page
    assert "Load and health validation happens on action" in page
    assert 'aria-describedby="rollback-validation"' in page
    assert '<p class="muted" id="rollback-validation">' in page
    # Without a coordinator the form (and its label) is not offered at all.
    assert 'action="/rollback"' not in _client(control).get("/deployment").text


def test_enhancement_script_is_external_and_error_summary_is_focusable(tmp_path: Path) -> None:
    control = _control(tmp_path)
    client = _client(control)
    home = client.get("/")
    assert '<script src="/static/platform.js" defer></script>' in home.text
    # The CSP forbids inline script; every script element must load from /static.
    assert re.findall(r"<script(?![^>]*\bsrc=\"/static/)", home.text) == []
    assert home.headers["content-security-policy"] == "default-src 'self'; style-src 'self'"
    script = client.get("/static/platform.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "innerHTML" not in script.text
    submission_id = control.submit(_request("m"))
    control.mark_submission(submission_id, "Failed")
    detail = client.get(f"/submissions/{submission_id}")
    token = re.search(r'name="csrf-token" content="([0-9a-f]+)"', detail.text).group(1)
    blocked = client.post(
        f"/submissions/{submission_id}/cancel", data={"csrf_token": token, "reason": "late"}
    )
    assert blocked.status_code == 409
    assert (
        '<section class="error-summary" role="alert" tabindex="-1" '
        'aria-labelledby="error-summary-title"><h1 id="error-summary-title">Action blocked</h1>'
    ) in blocked.text
    css = client.get("/static/platform.css").text
    assert ".error-summary:focus" in css
