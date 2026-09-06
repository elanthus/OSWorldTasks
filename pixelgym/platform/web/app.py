"""Server-rendered local control plane with allowlisted submissions and CSRF protection."""

from __future__ import annotations

import hashlib
import hmac
import html
import re
import secrets
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import date
from difflib import HtmlDiff
from numbers import Real
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from pixelgym.platform.contracts import CandidateState
from pixelgym.platform.control_store import (
    AuthorizationError,
    ConflictError,
    ControlStore,
    TransitionError,
)
from pixelgym.platform.deployment import DeploymentCoordinator
from pixelgym.platform.deployment_smoke import DeploymentSmokeError
from pixelgym.platform.immutable_store import ImmutableStoreError
from pixelgym.platform.mlflow_tracking import (
    CompatibleSearchCapacityError,
    Tracking,
    TrackingMirrorError,
)
from pixelgym.platform.policy import prompt_template

DATASET_OPTIONS = {
    "day3-frozen-v1": "Frozen Day 3 dataset · 100 examples",
}
PROMPT_OPTIONS = {
    "1": "Baseline prompt v1",
    "2": "Revised prompt v2",
}
MODEL_OPTIONS = {
    "day3-replay-baseline-v1": "Scripted baseline replay",
    "day3-replay-revised-v2": "Scripted revised replay",
}
ALLOWED_SUBMISSION_FIELDS = {
    "dataset": set(DATASET_OPTIONS),
    "prompt_version": set(PROMPT_OPTIONS),
    "model": set(MODEL_OPTIONS),
    "condition": {"raw"},
    "maximum_calls": {"100"},
    "price_catalog": {"pixelgym-demo-prices-v1"},
}
CSRF_TOKEN_BYTES = hashlib.sha256().digest_size
CSRF_TOKEN_HEX_LENGTH = CSRF_TOKEN_BYTES * 2
CSRF_TOKEN_PATTERN = re.compile(rf"[0-9a-fA-F]{{{CSRF_TOKEN_HEX_LENGTH}}}")
RUNS_PAGE_SIZE = 50
DEPLOYMENT_AUDIT_WINDOW = 25
AUDIT_HISTORY_PAGE_SIZE = 100


class ApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reason: str = Field(min_length=1, max_length=1000)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _badge(label: str, tone: str = "neutral") -> str:
    return f'<span class="badge badge--{_escape(tone)}">{_escape(label)}</span>'


def _numeric(value: object) -> float:
    if isinstance(value, Real) and not isinstance(value, bool):
        return float(value)
    raise TypeError("metric value must be numeric")


def _percentage(value: object) -> str:
    return "missing" if value is None else f"{_numeric(value):.1%}"


def _money(value: object) -> str:
    return "missing" if value is None else f"${_numeric(value):.2f}"


def _milliseconds(value: object) -> str:
    return "missing" if value is None else f"{_numeric(value):.1f} ms"


def _layout(title: str, body: str, *, csrf: str = "") -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape(title)} · PixelGym Control</title><link rel="stylesheet" href="/static/platform.css">
<meta name="csrf-token" content="{_escape(csrf)}"></head>
<body><header class="shell"><a class="brand" href="/">PIXELGYM <span>CONTROL</span></a>
<nav aria-label="Primary"><a href="/">Submit</a><a href="/runs">Runs</a><a href="/compare">Compare</a><a href="/deployment">Deployment</a></nav></header>
<main class="shell">{body}</main><footer class="shell">Local scripted-provider environment · synthetic metrics are not model-quality evidence.</footer>
</body></html>"""


def _token(secret: bytes, session: str) -> bytes:
    return hmac.new(secret, session.encode(), hashlib.sha256).digest()


def _short_digest(value: str | None, *, width: int = 12) -> str:
    if not value:
        return "missing"
    return value.removeprefix("sha256:")[:width]


def _safe_link(uri: str, label: str) -> str:
    """Render only stored external evidence URIs with a non-scriptable scheme."""
    if urlsplit(uri).scheme not in {"http", "https", "s3"}:
        return f'<span class="muted">{_escape(label)} unavailable</span>'
    return f'<a href="{_escape(uri)}">{_escape(label)} ↗</a>'


def _page_href(request: Request, path: str, page: int) -> str:
    parameters = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key != "page"
    ]
    parameters.append(("page", str(page)))
    return f"{path}?{urlencode(parameters)}"


def _artifact(candidate: Any, suffix: str) -> Any | None:
    return next((item for item in candidate.artifacts if item.logical_key.endswith(suffix)), None)


def _candidate_badges(candidate: Any) -> str:
    report = candidate.gate_report
    summary = candidate.summary
    # A candidate imported without a run summary falls back to its recorded provider;
    # never infer that an unattributed provider is the scripted demo.
    synthetic = (
        summary.synthetic_provider
        if summary is not None
        else candidate.policy.provider == "scripted-demo"
    )
    badges = [_badge("DEMO PROVIDER" if synthetic else "REAL PROVIDER", "demo" if synthetic else "real")]
    if not report["completeness"]["passed"]:
        badges.append(_badge("INCOMPLETE", "bad"))
    if candidate.policy.code_state != "clean":
        badges.append(_badge("DIRTY CODE", "bad"))
    if candidate.policy.source_provenance_failure_reason is not None:
        badges.append(
            _badge(
                f"PROVENANCE: {candidate.policy.source_provenance_failure_reason.replace('_', ' ').upper()}",
                "bad",
            )
        )
    unpriced = (
        report["cost_usd_per_100"]["observed"] is None
        or (summary is not None and summary.unpriced_call_count > 0)
    )
    if unpriced:
        badges.append(_badge("UNPRICED", "bad"))
    return " ".join(badges)


def _candidate_row(candidate: Any, mlflow_base_url: str) -> str:
    report = candidate.gate_report
    state_tone = "good" if candidate.state in {CandidateState.ELIGIBLE, CandidateState.APPROVED} else "bad"
    summary = candidate.summary
    invalid_count = "missing" if summary is None else str(summary.invalid_count)
    return f"""<tr><td><a href="/candidates/{_escape(candidate.candidate_id)}">{_escape(candidate.candidate_id)}</a><br><span class="muted">{_escape(candidate.policy.provider)}</span><br>{_candidate_badges(candidate)}</td>
<td>{_escape(candidate.policy.model)}<br><span class="muted">prompt v{candidate.policy.prompt_version}</span></td>
<td class="number">{_percentage(report['accuracy']['observed'])}</td><td class="number">{_money(report['cost_usd_per_100']['observed'])}</td>
<td class="number">{_milliseconds(report['provider_latency_p95_ms']['observed'])}</td><td>{_badge(candidate.state.value, state_tone)}</td>
<td><span class="mono">{_escape(_short_digest(report['dataset_fingerprint']))}</span><br><span class="muted">code {_escape(_short_digest(candidate.policy.code_revision))} · invalid {invalid_count}</span></td>
<td>{_safe_link(f'{mlflow_base_url}/#/experiments/0/runs/{candidate.source_run_id}', 'MLflow')}</td></tr>"""


def _evidence_links(candidate: Any, mlflow_base_url: str) -> str:
    raw_count = sum(item.logical_key.startswith("raw-responses/") for item in candidate.artifacts)
    predictions = _artifact(candidate, "/predictions.jsonl")
    gate = _artifact(candidate, "/gate-report.json")
    raw = (
        f'<a href="/candidates/{_escape(candidate.candidate_id)}/evidence/raw-responses">'
        f"Raw-response index ({raw_count})</a>"
        if raw_count
        else '<span class="muted">Raw-response index unavailable</span>'
    )
    per_example = (
        _safe_link(predictions.uri, "Per-example errors")
        if predictions is not None
        else '<span class="muted">Per-example errors unavailable</span>'
    )
    gate_link = (
        _safe_link(gate.uri, "Gate report")
        if gate is not None
        else '<span class="muted">Gate report unavailable</span>'
    )
    mlflow = _safe_link(
        f"{mlflow_base_url}/#/experiments/0/runs/{candidate.source_run_id}", "MLflow run"
    )
    return f'<div class="evidence-links">{raw} · {per_example} · {gate_link} · {mlflow}</div>'


def _delta(value: object, baseline: object, *, kind: str) -> str:
    if value is None or baseline is None:
        return "Δ unavailable"
    difference = _numeric(value) - _numeric(baseline)
    if kind == "accuracy":
        return f"Δ {difference * 100:+.1f} pp"
    if kind == "money":
        return f"Δ ${difference:+.2f}"
    return f"Δ {difference:+.1f} ms"


def _comparison_card(candidate: Any, baseline: Any, mlflow_base_url: str) -> str:
    report = candidate.gate_report
    baseline_report = baseline.gate_report
    return f"""<article class="metric-card"><p>{_escape(candidate.candidate_id)}</p><h3>{_percentage(report['accuracy']['observed'])}</h3><small>{_escape(_delta(report['accuracy']['observed'], baseline_report['accuracy']['observed'], kind='accuracy'))}</small><dl><dt>Cost / 100</dt><dd>{_money(report['cost_usd_per_100']['observed'])}<br><small>{_escape(_delta(report['cost_usd_per_100']['observed'], baseline_report['cost_usd_per_100']['observed'], kind='money'))}</small></dd><dt>Provider p95</dt><dd>{_milliseconds(report['provider_latency_p95_ms']['observed'])}<br><small>{_escape(_delta(report['provider_latency_p95_ms']['observed'], baseline_report['provider_latency_p95_ms']['observed'], kind='latency'))}</small></dd><dt>Gate state</dt><dd>{_escape(candidate.state.value)}</dd></dl>{_evidence_links(candidate, mlflow_base_url)}</article>"""


def create_control_app(
    control: ControlStore,
    *,
    coordinator: DeploymentCoordinator[Any] | None = None,
    csrf_secret: str,
    session_cookie_secure: bool = False,
    submit_callback: Callable[[str, dict[str, str]], None] | None = None,
    cancel_callback: Callable[[str], bool] | None = None,
    tracking: Tracking | None = None,
    mlflow_base_url: str = "http://localhost:5000",
) -> FastAPI:
    if len(csrf_secret) < 16:
        raise ValueError("CSRF secret must be at least 16 characters")
    secret = csrf_secret.encode()
    app = FastAPI(title="PixelGym Grounding Control Plane", docs_url=None, redoc_url=None)
    static = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.middleware("http")
    async def session_cookie(request: Request, call_next: Callable[..., Any]) -> Any:
        supplied_session = request.cookies.get("pixelgym_session")
        session_id, separator, supplied_tag = (supplied_session or "").rpartition(".")
        session_shape_is_valid = (
            bool(separator)
            and re.fullmatch(r"[A-Za-z0-9_-]{32}", session_id) is not None
            and re.fullmatch(r"[0-9a-f]{64}", supplied_tag) is not None
        )
        if session_shape_is_valid:
            expected_tag = hmac.new(
                secret,
                b"pixelgym-session-v1\0" + session_id.encode(),
                hashlib.sha256,
            ).hexdigest()
            session_is_valid = hmac.compare_digest(expected_tag, supplied_tag)
        else:
            session_is_valid = False
        if session_is_valid and supplied_session is not None:
            session = supplied_session
        else:
            session_id = secrets.token_urlsafe(24)
            session_tag = hmac.new(
                secret,
                b"pixelgym-session-v1\0" + session_id.encode(),
                hashlib.sha256,
            ).hexdigest()
            session = f"{session_id}.{session_tag}"
        request.state.pixelgym_session = session
        request.state.csrf_digest = _token(secret, session)
        request.state.csrf = request.state.csrf_digest.hex()
        response = await call_next(request)
        if not session_is_valid:
            response.set_cookie(
                "pixelgym_session",
                session,
                httponly=True,
                samesite="strict",
                secure=session_cookie_secure,
            )
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def require_csrf(request: Request, supplied: Sequence[str]) -> None:
        if len(supplied) != 1:
            raise HTTPException(403, "CSRF validation failed")
        token = supplied[0]
        if len(token) != CSRF_TOKEN_HEX_LENGTH or CSRF_TOKEN_PATTERN.fullmatch(token) is None:
            raise HTTPException(403, "CSRF validation failed")
        token_bytes = bytes.fromhex(token)
        if not hmac.compare_digest(request.state.csrf_digest, token_bytes):
            raise HTTPException(403, "CSRF validation failed")

    def candidate_or_404(candidate_id: str) -> Any:
        try:
            return control.get_candidate(candidate_id)
        except KeyError as exc:
            raise HTTPException(404, "candidate does not exist") from exc

    async def csrf_form_fields(request: Request) -> dict[str, str]:
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
            raise HTTPException(415, "forms must use application/x-www-form-urlencoded")
        body = await request.body()
        if len(body) > 32_768:
            raise HTTPException(413, "form body is too large")
        if not body:
            raise HTTPException(422, "form body is empty")
        try:
            values = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
        except (UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(422, "form body is malformed") from exc
        require_csrf(request, values.get("csrf_token", []))
        if any(len(items) != 1 for items in values.values()):
            raise HTTPException(422, "duplicate form fields are not allowed")
        return {key: items[0] for key, items in values.items()}

    def active_precondition(fields: dict[str, str]) -> tuple[str | None, int]:
        try:
            generation = int(fields["expected_generation"])
        except ValueError as exc:
            raise HTTPException(422, "expected generation must be an integer") from exc
        if generation < 0:
            raise HTTPException(422, "expected generation must be nonnegative")
        return fields["expected_deployment_id"] or None, generation

    @app.get("/", response_class=HTMLResponse)
    def submit_view(request: Request) -> str:
        body = f"""<section class="hero"><div><p class="eyebrow">GROUNDING EVALUATION</p><h1>Measure the policy.<br>Then earn the right to ship it.</h1>
<p class="lede">Submit one frozen, attributable evaluation. Every response is preserved before scoring; every gate must pass together.</p></div>
<aside><div class="signal"><span>DATASET</span><strong>100</strong><small>frozen examples</small></div><div class="signal"><span>SPEND CAP</span><strong>$0</strong><small>scripted demo only</small></div></aside></section>
<section class="panel"><div class="panel__heading"><div><p class="eyebrow">NEW EXPERIMENT</p><h2>Resolve every input before execution</h2></div>{_badge('DEMO PROVIDER', 'demo')}</div>
<form method="post" action="/experiments" class="form-grid"><input type="hidden" name="csrf_token" value="{request.state.csrf}">
<label>Dataset snapshot<select name="dataset">{''.join(f'<option value="{key}">{value}</option>' for key,value in DATASET_OPTIONS.items())}</select></label>
<label>Prompt version<select name="prompt_version">{''.join(f'<option value="{key}">{value}</option>' for key,value in PROMPT_OPTIONS.items())}</select></label>
<label>Provider model<select name="model">{''.join(f'<option value="{key}">{value}</option>' for key,value in MODEL_OPTIONS.items())}</select></label>
<label>Condition<input name="condition" value="raw" readonly></label><label>Maximum calls<input name="maximum_calls" value="100" readonly></label>
<label>Price catalog<input name="price_catalog" value="pixelgym-demo-prices-v1" readonly></label>
<div class="form-summary"><span>Maximum estimated spend</span><strong>$0.00</strong><small>No provider credentials or network calls.</small></div>
<button type="submit">Submit fixed evaluation →</button></form></section>"""
        return _layout("Submit experiment", body, csrf=request.state.csrf)

    @app.post("/experiments")
    async def submit_experiment(request: Request) -> RedirectResponse:
        fields = await csrf_form_fields(request)
        fields.pop("csrf_token")
        if set(fields) != set(ALLOWED_SUBMISSION_FIELDS):
            raise HTTPException(422, "submission fields do not match the fixed flow contract")
        payload = fields
        invalid = [key for key, value in payload.items() if value not in ALLOWED_SUBMISSION_FIELDS[key]]
        if invalid:
            raise HTTPException(422, f"submission contains non-allowlisted options: {', '.join(invalid)}")
        submission_id = await run_in_threadpool(control.submit, payload)
        if submit_callback is not None:
            submit_callback(submission_id, payload)
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)

    @app.get("/submissions/{submission_id}", response_class=HTMLResponse)
    def submission_view(submission_id: str, request: Request) -> str:
        try:
            submission = control.get_submission(submission_id)
        except KeyError as exc:
            raise HTTPException(404, "submission does not exist") from exc
        run_link = (
            _safe_link(
                f"{mlflow_base_url}/#/experiments/0/runs/{submission['mlflow_run_id']}",
                "MLflow run",
            )
            if submission["mlflow_run_id"]
            else '<span class="muted">MLflow run pending</span>'
        )
        pathspec = submission["metaflow_pathspec"] or "pending"
        cancellation = '<p class="muted">Cancellation is no longer available for this terminal run.</p>'
        if submission["status"] in {"Submitted", "Running"}:
            cancellation = f"""<form method="post" action="/submissions/{_escape(submission_id)}/cancel"><input type="hidden" name="csrf_token" value="{request.state.csrf}"><label>Cancellation reason<textarea name="reason" required minlength="1"></textarea></label><button class="secondary" type="submit">Cancel experiment</button><p class="muted">Cancellation records the authoritative state immediately; the local flow stops at its next durable step boundary while retaining partial immutable evidence.</p></form>"""
        request_rows = "".join(
            f"<dt>{_escape(key)}</dt><dd>{_escape(value)}</dd>"
            for key, value in submission["request"].items()
        )
        body = f"""<section class="page-title"><p class="eyebrow">SUBMISSION</p><h1>{_escape(submission_id)}</h1><p>{_badge(submission['status'], 'good' if submission['status'] == 'Complete' else 'neutral')}</p></section><div class="detail-grid"><section class="panel"><h2>Execution lineage</h2><dl><dt>Status</dt><dd>{_escape(submission['status'])}</dd><dt>Metaflow pathspec</dt><dd class="mono">{_escape(pathspec)}</dd><dt>Tracking</dt><dd>{run_link}</dd></dl><h3>Resolved request</h3><dl>{request_rows}</dl></section><aside class="panel action-panel"><h2>Cancellation</h2>{cancellation}</aside></div>"""
        return _layout("Submission", body, csrf=request.state.csrf)

    @app.post("/submissions/{submission_id}/cancel")
    async def cancel_submission(submission_id: str, request: Request) -> RedirectResponse:
        fields = await csrf_form_fields(request)
        if set(fields) != {"csrf_token", "reason"}:
            raise HTTPException(422, "cancellation fields do not match the fixed contract")
        try:
            submission = await run_in_threadpool(control.get_submission, submission_id)
        except KeyError as exc:
            raise HTTPException(404, "submission does not exist") from exc
        if submission["status"] == "Running" and cancel_callback is None:
            raise HTTPException(409, "running flow cancellation is unavailable")
        await run_in_threadpool(
            control.cancel_submission,
            submission_id,
            actor=control.reviewer_identity,
            reason=fields["reason"],
        )
        if cancel_callback is not None and submission["status"] != "Cancelled":
            await run_in_threadpool(cancel_callback, submission_id)
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)

    @app.get("/runs", response_class=HTMLResponse)
    def runs_view(
        request: Request,
        page: Annotated[int, Query(ge=1)] = 1,
        submitted: str | None = None,
        provider: str | None = None,
        lifecycle: str | None = None,
        dataset: str | None = None,
        code_revision: str | None = None,
        prompt_version: int | None = None,
        model: str | None = None,
        status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        gate_result: str | None = None,
    ) -> str:
        for label, value in (("date_from", date_from), ("date_to", date_to)):
            if value:
                try:
                    parsed = date.fromisoformat(value)
                except ValueError as exc:
                    raise HTTPException(
                        422, f"{label} must use a valid YYYY-MM-DD date"
                    ) from exc
                if parsed.isoformat() != value:
                    raise HTTPException(422, f"{label} must use a valid YYYY-MM-DD date")
        if gate_result is not None and gate_result not in {"passed", "failed"}:
            raise HTTPException(422, "gate_result must be passed or failed")
        filters_active = any(
            (
                provider,
                lifecycle,
                dataset,
                code_revision,
                prompt_version is not None,
                model,
                status,
                date_from,
                date_to,
                gate_result,
            )
        )
        if filters_active:
            candidates = control.list_candidates()
            has_next_page = False
        else:
            candidate_window = control.list_candidates(
                limit=RUNS_PAGE_SIZE + 1,
                offset=(page - 1) * RUNS_PAGE_SIZE,
            )
            has_next_page = len(candidate_window) > RUNS_PAGE_SIZE
            candidates = candidate_window[:RUNS_PAGE_SIZE]
        provider_options = control.list_candidate_providers()
        submissions = control.list_submissions()
        submission_by_run = {
            item["mlflow_run_id"]: item for item in submissions if item["mlflow_run_id"]
        }
        if provider:
            candidates = [item for item in candidates if item.policy.provider == provider]
        if lifecycle:
            candidates = [item for item in candidates if item.state.value == lifecycle]
        if dataset:
            candidates = [
                item
                for item in candidates
                if item.gate_report["dataset_fingerprint"].removeprefix("sha256:").startswith(dataset)
            ]
        if code_revision:
            candidates = [
                item
                for item in candidates
                if item.policy.code_revision.removeprefix("sha256:").startswith(code_revision)
            ]
        if prompt_version is not None:
            candidates = [
                item for item in candidates if item.policy.prompt_version == prompt_version
            ]
        if model:
            candidates = [item for item in candidates if item.policy.model == model]
        if status:
            candidates = [
                item
                for item in candidates
                if submission_by_run.get(item.source_run_id, {}).get("status") == status
            ]
        if date_from:
            candidates = [
                item
                for item in candidates
                if submission_by_run.get(item.source_run_id, {}).get("created_at_utc", "")[:10]
                >= date_from
            ]
        if date_to:
            candidates = [
                item
                for item in candidates
                if submission_by_run.get(item.source_run_id, {}).get("created_at_utc", "")[:10]
                <= date_to
            ]
        if gate_result:
            expected = gate_result == "passed"
            candidates = [
                item for item in candidates if item.gate_report["overall_passed"] is expected
            ]
        if filters_active:
            offset = (page - 1) * RUNS_PAGE_SIZE
            candidate_window = candidates[offset : offset + RUNS_PAGE_SIZE + 1]
            has_next_page = len(candidate_window) > RUNS_PAGE_SIZE
            candidates = candidate_window[:RUNS_PAGE_SIZE]
        notice = f'<div class="notice">Submission {_escape(submitted)} accepted.</div>' if submitted else ""
        rows = "".join(_candidate_row(item, mlflow_base_url) for item in candidates)
        if not rows:
            rows = '<tr><td colspan="8" class="empty">No evaluated candidates match these filters.</td></tr>'
        lifecycle_options = [item.value for item in CandidateState]
        filter_form = f"""<form method="get" action="/runs" class="filter-grid"><label>Provider<select name="provider"><option value="">All providers</option>{''.join(f'<option value="{_escape(value)}" {'selected' if value == provider else ''}>{_escape(value)}</option>' for value in provider_options)}</select></label>
<label>Lifecycle<select name="lifecycle"><option value="">All states</option>{''.join(f'<option value="{_escape(value)}" {'selected' if value == lifecycle else ''}>{_escape(value)}</option>' for value in lifecycle_options)}</select></label>
<label>Dataset fingerprint prefix<input name="dataset" value="{_escape(dataset or '')}" pattern="[0-9a-f]*" maxlength="64"></label>
<label>Prompt version<input name="prompt_version" type="number" min="1" value="{_escape(prompt_version or '')}"></label><label>Model<input name="model" value="{_escape(model or '')}"></label>
<label>Submission status<input name="status" value="{_escape(status or '')}"></label><label>From date<input name="date_from" type="date" value="{_escape(date_from or '')}"></label><label>Through date<input name="date_to" type="date" value="{_escape(date_to or '')}"></label><label>Gate result<select name="gate_result"><option value="">All results</option><option value="passed" {'selected' if gate_result == 'passed' else ''}>Passed</option><option value="failed" {'selected' if gate_result == 'failed' else ''}>Failed</option></select></label>
<label>Code revision prefix<input name="code_revision" value="{_escape(code_revision or '')}" pattern="[0-9a-f]*" maxlength="40"></label><button type="submit">Filter runs</button></form>"""
        previous_link = (
            f'<a href="{_escape(_page_href(request, "/runs", page - 1))}">← Previous page</a>'
            if page > 1
            else ""
        )
        next_link = (
            f'<a href="{_escape(_page_href(request, "/runs", page + 1))}">Next page →</a>'
            if has_next_page
            else ""
        )
        pagination = " · ".join(link for link in (previous_link, next_link) if link)
        body = f"""<section class="page-title"><p class="eyebrow">RUN HISTORY</p><h1>Every result stays visible.</h1><p>Failures, invalid outputs, and incomplete runs are retained—not repaired or hidden.</p></section>{notice}
<section class="panel"><h2>Filter stored runs</h2>{filter_form}</section><section class="panel table-panel"><table><thead><tr><th>Candidate</th><th>Policy</th><th>Accuracy</th><th>Cost / 100</th><th>Provider p95</th><th>Lifecycle</th><th>Dataset / code / invalid</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table>{f'<nav aria-label="Run pages">{pagination}</nav>' if pagination else ''}</section>"""
        return _layout("Runs", body, csrf=request.state.csrf)

    @app.get("/api/tracking/runs/compatible")
    def compatible_tracking_runs(
        dataset_fingerprint: str,
        scorer_version: str,
        target_semantics: str,
        primary_metric: str = "accuracy",
    ) -> dict[str, Any]:
        if tracking is None:
            raise HTTPException(503, "MLflow tracking is unavailable")
        values = {
            "dataset_fingerprint": dataset_fingerprint,
            "scorer_version": scorer_version,
            "target_semantics": target_semantics,
            "primary_metric": primary_metric,
        }
        if any(
            not value
            or len(value) > 256
            or not re.fullmatch(r"[A-Za-z0-9:._-]+", value)
            for value in values.values()
        ):
            raise HTTPException(422, "tracking compatibility filters contain unsafe values")
        try:
            runs = tracking.search_compatible_runs(
                **values,
                max_results=20,
                timeout_seconds=5.0,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except CompatibleSearchCapacityError as exc:
            # Capacity exhaustion is temporary service unavailability, not an MLflow deadline.
            raise HTTPException(
                503,
                {
                    "error": "MLflow compatible-run search capacity exhausted",
                    "capacity": exc.capacity,
                    "occupancy": exc.occupancy,
                },
            ) from exc
        except TimeoutError as exc:
            raise HTTPException(504, "MLflow compatible-run search timed out") from exc
        return {"runs": [asdict(run) for run in runs]}

    @app.get("/compare", response_class=HTMLResponse)
    def compare_view(
        request: Request, candidate: Annotated[list[str] | None, Query()] = None
    ) -> str:
        selected_ids = candidate or []
        if len(selected_ids) > 4:
            raise HTTPException(422, "compare accepts at most four candidates")
        all_candidates = control.list_candidates()
        chooser = "".join(
            f'<label class="check"><input type="checkbox" name="candidate" value="{_escape(item.candidate_id)}" {'checked' if item.candidate_id in selected_ids else ''}>{_escape(item.candidate_id)} · prompt v{item.policy.prompt_version}</label>'
            for item in all_candidates
        ) or '<p class="muted">Evaluate candidates to enable comparison.</p>'
        selected = [candidate_or_404(item) for item in selected_ids]
        comparison_keys = {
            (
                item.gate_report["dataset_fingerprint"],
                item.policy.scorer_version,
                item.policy.target_semantics,
                item.summary.primary_metric if item.summary is not None else "unknown",
            )
            for item in selected
        }
        compatible = len(selected) >= 2 and len(comparison_keys) == 1 and all(
            item.summary is not None for item in selected
        )
        comparison = ""
        if selected:
            baseline = selected[0]
            cards = "".join(
                _comparison_card(item, baseline, mlflow_base_url)
                for item in selected
            )
            warning = _badge("COMPATIBLE", "good") if compatible else _badge("PROMOTION COMPARISON BLOCKED", "bad")
            explanation = "Same dataset fingerprint, scorer, target semantics, and primary metric." if compatible else "Select 2–4 runs with the same dataset fingerprint, scorer, target semantics, and recorded primary metric."
            prompt_diff = ""
            if len(selected) >= 2:
                query = urlencode([( "candidate", item.candidate_id) for item in selected[:2]])
                prompt_diff = f'<p><a href="/compare/prompt-diff?{_escape(query)}">Prompt diff (recorded versions)</a></p>'
            comparison = f'<section class="comparison-head">{warning}<p>{explanation}</p>{prompt_diff}</section><div class="metric-grid">{cards}</div>'
        body = f"""<section class="page-title"><p class="eyebrow">COMPATIBLE COMPARISON</p><h1>No favorable metric gets to travel alone.</h1><p>Accuracy, cost, and latency always appear together.</p></section><section class="panel"><form method="get" action="/compare"><fieldset><legend>Select two to four candidates</legend>{chooser}</fieldset><button type="submit">Compare selected →</button></form></section>{comparison}"""
        return _layout("Compare", body, csrf=request.state.csrf)

    @app.get("/compare/prompt-diff", response_class=HTMLResponse)
    def prompt_diff_view(
        request: Request, candidate: Annotated[list[str] | None, Query()] = None
    ) -> str:
        selected_ids = candidate or []
        if len(selected_ids) != 2:
            raise HTTPException(422, "prompt diff requires exactly two candidates")
        before, after = [candidate_or_404(item) for item in selected_ids]
        try:
            before_prompt = prompt_template(before.policy.prompt_version)
            after_prompt = prompt_template(after.policy.prompt_version)
        except ValueError as exc:
            raise HTTPException(409, "recorded prompt version is unavailable for rendering") from exc
        diff = HtmlDiff(wrapcolumn=88).make_table(
            before_prompt.splitlines(),
            after_prompt.splitlines(),
            fromdesc=_escape(f"{before.candidate_id} · v{before.policy.prompt_version}"),
            todesc=_escape(f"{after.candidate_id} · v{after.policy.prompt_version}"),
            context=True,
        )
        body = f"""<section class="page-title"><p class="eyebrow">PROMPT COMPARISON</p><h1>Recorded prompt versions.</h1><p>This is rendered from each candidate's stored prompt version and digest using the frozen local templates; no separate immutable prompt-diff artifact was recorded.</p></section><section class="panel diff-table">{diff}</section>"""
        return _layout("Prompt diff", body, csrf=request.state.csrf)

    @app.get("/candidates/{candidate_id}/evidence/raw-responses", response_class=HTMLResponse)
    def raw_response_index(candidate_id: str, request: Request) -> str:
        item = candidate_or_404(candidate_id)
        raw = [reference for reference in item.artifacts if reference.logical_key.startswith("raw-responses/")]
        rows = "".join(
            f"<li><span class=\"mono\">{_escape(reference.logical_key)}</span> · {_safe_link(reference.uri, 'immutable object')}</li>"
            for reference in raw
        ) or "<li>No raw-response references were stored for this candidate.</li>"
        body = f"""<section class="page-title"><p class="eyebrow">IMMUTABLE EVIDENCE</p><h1>Raw-response index.</h1><p>{len(raw)} stored raw-response object references for {_escape(candidate_id)}.</p></section><section class="panel"><ul class="evidence-index">{rows}</ul></section>"""
        return _layout("Raw-response index", body, csrf=request.state.csrf)

    @app.get("/candidates/{candidate_id}", response_class=HTMLResponse)
    def candidate_view(candidate_id: str, request: Request) -> str:
        item = candidate_or_404(candidate_id)
        report = item.gate_report
        reasons = "".join(f"<li>{_escape(reason)}</li>" for reason in report["reasons"]) or "<li>All automated gates passed.</li>"
        controls = ""
        if item.state is CandidateState.ELIGIBLE:
            controls = f"""<form method="post" action="/candidates/{_escape(candidate_id)}/approve"><input type="hidden" name="csrf_token" value="{request.state.csrf}"><label>Approval reason<textarea name="reason" required minlength="1"></textarea></label><button type="submit">Approve exact candidate</button></form>"""
        elif item.state is CandidateState.APPROVED and coordinator is not None:
            active, generation = control.active()
            active_id = active.deployment_id if active is not None else ""
            controls = f"""<form method="post" action="/candidates/{_escape(candidate_id)}/deploy"><input type="hidden" name="csrf_token" value="{request.state.csrf}"><input type="hidden" name="expected_deployment_id" value="{_escape(active_id)}"><input type="hidden" name="expected_generation" value="{generation}"><label>Deployment reason<textarea name="reason" required minlength="1"></textarea></label><button type="submit">Deploy approved version</button></form>"""
        else:
            controls = '<div class="blocked"><strong>Approval unavailable</strong><p>A failed gate is terminal for this candidate. Revise the policy and create a new run.</p></div>'
        invalid = "missing" if item.summary is None else str(item.summary.invalid_count)
        disclosure = ""
        if item.policy.provider == "scripted-demo" and item.policy.model == "day3-replay-revised-v2":
            disclosure = '<p class="disclosure"><strong>Synthetic fixture disclosure:</strong> Candidate B\'s scripted revised responses are derived from the frozen Day 3 <code>condition == "marks"</code> rows, then relabeled for this policy\'s raw-condition demonstration. They are not results from the recorded raw prompt.</p>'
        body = f"""<section class="page-title"><p class="eyebrow">CANDIDATE</p><h1>{_escape(candidate_id)}</h1><p class="mono">{_escape(item.policy.policy_id)}</p><p>{_escape(item.policy.provider)} · code {_escape(_short_digest(item.policy.code_revision))} · invalid outputs {invalid}</p>{_candidate_badges(item)}{disclosure}</section><div class="detail-grid"><section class="panel"><h2>Gate report</h2><div class="metric-strip"><div><span>Accuracy</span><strong>{_percentage(report['accuracy']['observed'])}</strong><small>minimum {_percentage(report['accuracy']['threshold'])}</small></div><div><span>Cost / 100</span><strong>{_money(report['cost_usd_per_100']['observed'])}</strong><small>maximum {_money(report['cost_usd_per_100']['threshold'])}</small></div><div><span>Provider p95</span><strong>{_milliseconds(report['provider_latency_p95_ms']['observed'])}</strong><small>maximum {_milliseconds(report['provider_latency_p95_ms']['threshold'])}</small></div></div><h3>Evidence</h3>{_evidence_links(item, mlflow_base_url)}<h3>Decision details</h3><ul>{reasons}</ul></section><aside class="panel action-panel"><p class="eyebrow">HUMAN GATE</p><h2>{_escape(item.state.value)}</h2><p>Passing gates creates eligibility only. Approval and deployment remain separate attributed actions.</p>{controls}</aside></div>"""
        return _layout("Candidate", body, csrf=request.state.csrf)

    def _approve(candidate_id: str, reason: str) -> None:
        item = candidate_or_404(candidate_id)
        control.approve(
            candidate_id,
            actor=control.reviewer_identity,
            reason=reason,
            gate_report_sha256=item.gate_report_sha256,
        )
        if tracking is not None:
            try:
                tracking.mirror_candidate_status(
                    item.policy.policy_id,
                    gate_status="eligible",
                    approval_status="approved",
                )
            except TrackingMirrorError as exc:
                control.record_tracking_reconciliation(
                    subject_id=candidate_id,
                    operation="mirror_approval_tags",
                    error=f"{type(exc).__name__}: {exc}",
                    resolved=False,
                )

    @app.post("/candidates/{candidate_id}/approve")
    async def approve_form(candidate_id: str, request: Request) -> RedirectResponse:
        fields = await csrf_form_fields(request)
        if set(fields) != {"csrf_token", "reason"}:
            raise HTTPException(422, "approval fields do not match the fixed contract")
        await run_in_threadpool(_approve, candidate_id, fields["reason"])
        return RedirectResponse(f"/candidates/{candidate_id}", status_code=303)

    @app.post("/api/candidates/{candidate_id}/approve")
    def approve_api(candidate_id: str, request: Request, body: ApprovalBody) -> dict[str, str]:
        require_csrf(request, request.headers.getlist("x-csrf-token"))
        _approve(candidate_id, body.reason)
        return {"candidate_id": candidate_id, "state": "Approved"}

    @app.post("/candidates/{candidate_id}/deploy")
    async def deploy_form(candidate_id: str, request: Request) -> RedirectResponse:
        fields = await csrf_form_fields(request)
        expected_fields = {"csrf_token", "reason", "expected_deployment_id", "expected_generation"}
        if set(fields) != expected_fields:
            raise HTTPException(422, "deployment fields do not match the fixed contract")
        if coordinator is None:
            raise HTTPException(503, "deployment coordinator is unavailable")
        expected_deployment_id, expected_generation = active_precondition(fields)
        await run_in_threadpool(candidate_or_404, candidate_id)
        await run_in_threadpool(
            coordinator.deploy,
            candidate_id,
            actor=control.reviewer_identity,
            reason=fields["reason"],
            expected_deployment_id=expected_deployment_id,
            expected_generation=expected_generation,
        )
        return RedirectResponse("/deployment", status_code=303)

    @app.post("/rollback")
    async def rollback_form(request: Request) -> RedirectResponse:
        fields = await csrf_form_fields(request)
        expected_fields = {"csrf_token", "reason", "expected_deployment_id", "expected_generation"}
        if set(fields) != expected_fields:
            raise HTTPException(422, "rollback fields do not match the fixed contract")
        if coordinator is None:
            raise HTTPException(503, "deployment coordinator is unavailable")
        expected_deployment_id, expected_generation = active_precondition(fields)
        await run_in_threadpool(
            coordinator.rollback,
            actor=control.reviewer_identity,
            reason=fields["reason"],
            expected_deployment_id=expected_deployment_id,
            expected_generation=expected_generation,
        )
        return RedirectResponse("/deployment", status_code=303)

    @app.get("/deployment", response_class=HTMLResponse)
    def deployment_view(request: Request) -> str:
        active, generation = control.active()
        events = control.audit_events(
            limit=DEPLOYMENT_AUDIT_WINDOW,
            newest_first=True,
        )
        active_html = '<div class="empty">No policy is active.</div>'
        rollback = ""
        if active:
            active_html = f'<h2>{_escape(active.policy_id)}</h2><p>Deployment {_escape(active.deployment_id)} · generation {active.generation}</p>'
            try:
                control.previous_target(active)
            except TransitionError:
                has_rollback_target = False
            else:
                has_rollback_target = True
            if has_rollback_target and coordinator is not None:
                rollback = f'<form method="post" action="/rollback"><input type="hidden" name="csrf_token" value="{request.state.csrf}"><input type="hidden" name="expected_deployment_id" value="{_escape(active.deployment_id)}"><input type="hidden" name="expected_generation" value="{generation}"><label>Rollback reason<textarea name="reason" required></textarea></label><button class="secondary" type="submit">Rollback to previous approved version</button></form>'
        timeline = "".join(f'<li><span>{_escape(event["created_at_utc"])}</span><strong>{_escape(event["event_type"])}</strong><p>{_escape(event["subject_id"])}</p></li>' for event in events)
        body = f"""<section class="page-title"><p class="eyebrow">DELIVERY LEDGER</p><h1>One exact policy is active.</h1><p>Activation changes one transactional pointer. History is append-only.</p></section><div class="detail-grid"><section class="panel"><p class="eyebrow">ACTIVE DEPLOYMENT</p>{active_html}{rollback}</section><section class="panel"><h2>Recent audit trail</h2><ol class="timeline">{timeline or '<li>No lifecycle events yet.</li>'}</ol><p><a href="/deployment/audit">View full audit history →</a></p></section></div>"""
        return _layout("Deployment", body, csrf=request.state.csrf)

    @app.get("/deployment/audit", response_class=HTMLResponse)
    def deployment_audit_view(
        request: Request,
        page: Annotated[int, Query(ge=1)] = 1,
    ) -> str:
        event_window = control.audit_events(
            limit=AUDIT_HISTORY_PAGE_SIZE + 1,
            offset=(page - 1) * AUDIT_HISTORY_PAGE_SIZE,
            newest_first=True,
        )
        has_next_page = len(event_window) > AUDIT_HISTORY_PAGE_SIZE
        events = event_window[:AUDIT_HISTORY_PAGE_SIZE]
        timeline = "".join(
            f'<li><span>{_escape(event["created_at_utc"])}</span><strong>{_escape(event["event_type"])}</strong><p>{_escape(event["subject_id"])}</p></li>'
            for event in events
        )
        previous_link = (
            f'<a href="{_escape(_page_href(request, "/deployment/audit", page - 1))}">← Previous page</a>'
            if page > 1
            else ""
        )
        next_link = (
            f'<a href="{_escape(_page_href(request, "/deployment/audit", page + 1))}">Next page →</a>'
            if has_next_page
            else ""
        )
        pagination = " · ".join(link for link in (previous_link, next_link) if link)
        body = f"""<section class="page-title"><p class="eyebrow">DELIVERY LEDGER</p><h1>Full audit history.</h1><p>Newest lifecycle events appear first.</p></section><section class="panel"><ol class="timeline">{timeline or '<li>No lifecycle events on this page.</li>'}</ol>{f'<nav aria-label="Audit pages">{pagination}</nav>' if pagination else ''}<p><a href="/deployment">← Back to deployment</a></p></section>"""
        return _layout("Audit history", body, csrf=request.state.csrf)

    @app.exception_handler(TransitionError)
    @app.exception_handler(ConflictError)
    @app.exception_handler(AuthorizationError)
    @app.exception_handler(ImmutableStoreError)
    @app.exception_handler(DeploymentSmokeError)
    async def lifecycle_error(request: Request, exc: Exception) -> HTMLResponse:
        status = 403 if isinstance(exc, AuthorizationError) else 409
        return HTMLResponse(
            _layout("Action blocked", f'<section class="error-summary"><h1>Action blocked</h1><p>{_escape(exc)}</p><a href="/runs">Return to runs</a></section>', csrf=request.state.csrf),
            status_code=status,
        )

    return app
