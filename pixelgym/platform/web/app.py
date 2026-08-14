"""Four-view local control plane with allowlisted submissions and CSRF protection."""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Query, Request
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


class ApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reason: str = Field(min_length=1, max_length=1000)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _badge(label: str, tone: str = "neutral") -> str:
    return f'<span class="badge badge--{_escape(tone)}">{_escape(label)}</span>'


def _percentage(value: object) -> str:
    return "missing" if value is None else f"{float(value):.1%}"


def _money(value: object) -> str:
    return "missing" if value is None else f"${float(value):.2f}"


def _milliseconds(value: object) -> str:
    return "missing" if value is None else f"{float(value):.1f} ms"


def _layout(title: str, body: str, *, csrf: str = "") -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape(title)} · PixelGym Control</title><link rel="stylesheet" href="/static/platform.css"></head>
<body><header class="shell"><a class="brand" href="/">PIXELGYM <span>CONTROL</span></a>
<nav aria-label="Primary"><a href="/">Submit</a><a href="/runs">Runs</a><a href="/compare">Compare</a><a href="/deployment">Deployment</a></nav></header>
<main class="shell">{body}</main><footer class="shell">Local scripted-provider environment · synthetic metrics are not model-quality evidence.</footer>
<meta name="csrf-token" content="{_escape(csrf)}"></body></html>"""


def _token(secret: bytes, session: str) -> str:
    return hmac.new(secret, session.encode(), hashlib.sha256).hexdigest()


def _candidate_row(candidate: Any, mlflow_base_url: str) -> str:
    report = candidate.gate_report
    state_tone = "good" if candidate.state in {CandidateState.ELIGIBLE, CandidateState.APPROVED} else "bad"
    return f"""<tr><td><a href="/candidates/{_escape(candidate.candidate_id)}">{_escape(candidate.candidate_id)}</a><br>{_badge('DEMO PROVIDER', 'demo')}</td>
<td>{_escape(candidate.policy.model)}<br><span class="muted">prompt v{candidate.policy.prompt_version}</span></td>
<td class="number">{_percentage(report['accuracy']['observed'])}</td><td class="number">{_money(report['cost_usd_per_100']['observed'])}</td>
<td class="number">{_milliseconds(report['provider_latency_p95_ms']['observed'])}</td><td>{_badge(candidate.state.value, state_tone)}</td>
<td><a href="{_escape(mlflow_base_url)}/#/experiments/0/runs/{_escape(candidate.source_run_id)}">MLflow ↗</a></td></tr>"""


def create_control_app(
    control: ControlStore,
    *,
    coordinator: DeploymentCoordinator | None = None,
    csrf_secret: str,
    submit_callback: Callable[[str, dict[str, str]], None] | None = None,
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
        session = request.cookies.get("pixelgym_session") or secrets.token_urlsafe(24)
        request.state.pixelgym_session = session
        request.state.csrf = _token(secret, session)
        response = await call_next(request)
        if "pixelgym_session" not in request.cookies:
            response.set_cookie(
                "pixelgym_session", session, httponly=True, samesite="strict", secure=False
            )
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def require_csrf(request: Request, supplied: str | None) -> None:
        if not supplied or not hmac.compare_digest(request.state.csrf, supplied):
            raise HTTPException(403, "CSRF validation failed")

    def candidate_or_404(candidate_id: str) -> Any:
        try:
            return control.get_candidate(candidate_id)
        except KeyError as exc:
            raise HTTPException(404, "candidate does not exist") from exc

    async def form_fields(request: Request) -> dict[str, str]:
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
        if any(len(items) != 1 for items in values.values()):
            raise HTTPException(422, "duplicate form fields are not allowed")
        return {key: items[0] for key, items in values.items()}

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
        fields = await form_fields(request)
        require_csrf(request, fields.pop("csrf_token", None))
        if set(fields) != set(ALLOWED_SUBMISSION_FIELDS):
            raise HTTPException(422, "submission fields do not match the fixed flow contract")
        payload = fields
        invalid = [key for key, value in payload.items() if value not in ALLOWED_SUBMISSION_FIELDS[key]]
        if invalid:
            raise HTTPException(422, f"submission contains non-allowlisted options: {', '.join(invalid)}")
        submission_id = control.submit(payload)
        if submit_callback is not None:
            submit_callback(submission_id, payload)
        return RedirectResponse(f"/runs?submitted={submission_id}", status_code=303)

    @app.get("/runs", response_class=HTMLResponse)
    def runs_view(request: Request, submitted: str | None = None) -> str:
        candidates = control.list_candidates()
        notice = f'<div class="notice">Submission {_escape(submitted)} accepted.</div>' if submitted else ""
        rows = "".join(_candidate_row(item, mlflow_base_url) for item in candidates)
        if not rows:
            rows = '<tr><td colspan="7" class="empty">No evaluated candidates yet.</td></tr>'
        body = f"""<section class="page-title"><p class="eyebrow">RUN HISTORY</p><h1>Every result stays visible.</h1><p>Failures, invalid outputs, and incomplete runs are retained—not repaired or hidden.</p></section>{notice}
<section class="panel table-panel"><table><thead><tr><th>Candidate</th><th>Policy</th><th>Accuracy</th><th>Cost / 100</th><th>Provider p95</th><th>Lifecycle</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table></section>"""
        return _layout("Runs", body, csrf=request.state.csrf)

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
        compatible = len(selected) >= 2 and len({(
            item.gate_report["dataset_fingerprint"], item.policy.scorer_version, item.policy.target_semantics
        ) for item in selected}) == 1
        comparison = ""
        if selected:
            cards = "".join(
                f"""<article class="metric-card"><p>{_escape(item.candidate_id)}</p><h3>{_percentage(item.gate_report['accuracy']['observed'])}</h3><dl><dt>Cost / 100</dt><dd>{_money(item.gate_report['cost_usd_per_100']['observed'])}</dd><dt>Provider p95</dt><dd>{_milliseconds(item.gate_report['provider_latency_p95_ms']['observed'])}</dd><dt>Gate state</dt><dd>{_escape(item.state.value)}</dd></dl></article>"""
                for item in selected
            )
            warning = _badge("COMPATIBLE", "good") if compatible else _badge("PROMOTION COMPARISON BLOCKED", "bad")
            explanation = "Same dataset fingerprint, scorer, target semantics, and primary metric." if compatible else "Select 2–4 runs with the same dataset fingerprint, scorer, and target semantics."
            comparison = f'<section class="comparison-head">{warning}<p>{explanation}</p></section><div class="metric-grid">{cards}</div>'
        body = f"""<section class="page-title"><p class="eyebrow">COMPATIBLE COMPARISON</p><h1>No favorable metric gets to travel alone.</h1><p>Accuracy, cost, and latency always appear together.</p></section><section class="panel"><form method="get" action="/compare"><fieldset><legend>Select two to four candidates</legend>{chooser}</fieldset><button type="submit">Compare selected →</button></form></section>{comparison}"""
        return _layout("Compare", body, csrf=request.state.csrf)

    @app.get("/candidates/{candidate_id}", response_class=HTMLResponse)
    def candidate_view(candidate_id: str, request: Request) -> str:
        item = candidate_or_404(candidate_id)
        report = item.gate_report
        reasons = "".join(f"<li>{_escape(reason)}</li>" for reason in report["reasons"]) or "<li>All automated gates passed.</li>"
        controls = ""
        if item.state is CandidateState.ELIGIBLE:
            controls = f"""<form method="post" action="/candidates/{_escape(candidate_id)}/approve"><input type="hidden" name="csrf_token" value="{request.state.csrf}"><label>Approval reason<textarea name="reason" required minlength="1"></textarea></label><button type="submit">Approve exact candidate</button></form>"""
        elif item.state is CandidateState.APPROVED and coordinator is not None:
            controls = f"""<form method="post" action="/candidates/{_escape(candidate_id)}/deploy"><input type="hidden" name="csrf_token" value="{request.state.csrf}"><label>Deployment reason<textarea name="reason" required minlength="1"></textarea></label><button type="submit">Deploy approved version</button></form>"""
        else:
            controls = '<div class="blocked"><strong>Approval unavailable</strong><p>A failed gate is terminal for this candidate. Revise the policy and create a new run.</p></div>'
        body = f"""<section class="page-title"><p class="eyebrow">CANDIDATE</p><h1>{_escape(candidate_id)}</h1><p class="mono">{_escape(item.policy.policy_id)}</p></section><div class="detail-grid"><section class="panel"><h2>Gate report</h2><div class="metric-strip"><div><span>Accuracy</span><strong>{_percentage(report['accuracy']['observed'])}</strong><small>minimum {_percentage(report['accuracy']['threshold'])}</small></div><div><span>Cost / 100</span><strong>{_money(report['cost_usd_per_100']['observed'])}</strong><small>maximum {_money(report['cost_usd_per_100']['threshold'])}</small></div><div><span>Provider p95</span><strong>{_milliseconds(report['provider_latency_p95_ms']['observed'])}</strong><small>maximum {_milliseconds(report['provider_latency_p95_ms']['threshold'])}</small></div></div><h3>Decision details</h3><ul>{reasons}</ul></section><aside class="panel action-panel"><p class="eyebrow">HUMAN GATE</p><h2>{_escape(item.state.value)}</h2><p>Passing gates creates eligibility only. Approval and deployment remain separate attributed actions.</p>{controls}</aside></div>"""
        return _layout("Candidate", body, csrf=request.state.csrf)

    def _approve(candidate_id: str, reason: str) -> None:
        item = candidate_or_404(candidate_id)
        control.approve(
            candidate_id,
            actor=control.reviewer_identity,
            reason=reason,
            gate_report_sha256=item.gate_report_sha256,
        )

    @app.post("/candidates/{candidate_id}/approve")
    async def approve_form(candidate_id: str, request: Request) -> RedirectResponse:
        fields = await form_fields(request)
        require_csrf(request, fields.get("csrf_token"))
        if set(fields) != {"csrf_token", "reason"}:
            raise HTTPException(422, "approval fields do not match the fixed contract")
        _approve(candidate_id, fields["reason"])
        return RedirectResponse(f"/candidates/{candidate_id}", status_code=303)

    @app.post("/api/candidates/{candidate_id}/approve")
    def approve_api(candidate_id: str, request: Request, body: ApprovalBody) -> dict[str, str]:
        require_csrf(request, request.headers.get("x-csrf-token"))
        _approve(candidate_id, body.reason)
        return {"candidate_id": candidate_id, "state": "Approved"}

    @app.post("/candidates/{candidate_id}/deploy")
    async def deploy_form(candidate_id: str, request: Request) -> RedirectResponse:
        fields = await form_fields(request)
        require_csrf(request, fields.get("csrf_token"))
        if set(fields) != {"csrf_token", "reason"}:
            raise HTTPException(422, "deployment fields do not match the fixed contract")
        if coordinator is None:
            raise HTTPException(503, "deployment coordinator is unavailable")
        candidate_or_404(candidate_id)
        coordinator.deploy(candidate_id, actor=control.reviewer_identity, reason=fields["reason"])
        return RedirectResponse("/deployment", status_code=303)

    @app.post("/rollback")
    async def rollback_form(request: Request) -> RedirectResponse:
        fields = await form_fields(request)
        require_csrf(request, fields.get("csrf_token"))
        if set(fields) != {"csrf_token", "reason"}:
            raise HTTPException(422, "rollback fields do not match the fixed contract")
        if coordinator is None:
            raise HTTPException(503, "deployment coordinator is unavailable")
        coordinator.rollback(actor=control.reviewer_identity, reason=fields["reason"])
        return RedirectResponse("/deployment", status_code=303)

    @app.get("/deployment", response_class=HTMLResponse)
    def deployment_view(request: Request) -> str:
        active, _generation = control.active()
        events = control.audit_events()
        active_html = '<div class="empty">No policy is active.</div>'
        rollback = ""
        if active:
            active_html = f'<h2>{_escape(active.policy_id)}</h2><p>Deployment {_escape(active.deployment_id)} · generation {active.generation}</p>'
            if active.previous_deployment_id and coordinator is not None:
                rollback = f'<form method="post" action="/rollback"><input type="hidden" name="csrf_token" value="{request.state.csrf}"><label>Rollback reason<textarea name="reason" required></textarea></label><button class="secondary" type="submit">Rollback to previous approved version</button></form>'
        timeline = "".join(f'<li><span>{_escape(event["created_at_utc"])}</span><strong>{_escape(event["event_type"])}</strong><p>{_escape(event["subject_id"])}</p></li>' for event in events)
        body = f"""<section class="page-title"><p class="eyebrow">DELIVERY LEDGER</p><h1>One exact policy is active.</h1><p>Activation changes one transactional pointer. History is append-only.</p></section><div class="detail-grid"><section class="panel"><p class="eyebrow">ACTIVE DEPLOYMENT</p>{active_html}{rollback}</section><section class="panel"><h2>Audit trail</h2><ol class="timeline">{timeline or '<li>No lifecycle events yet.</li>'}</ol></section></div>"""
        return _layout("Deployment", body, csrf=request.state.csrf)

    @app.exception_handler(TransitionError)
    @app.exception_handler(ConflictError)
    @app.exception_handler(AuthorizationError)
    async def lifecycle_error(request: Request, exc: Exception) -> HTMLResponse:
        status = 403 if isinstance(exc, AuthorizationError) else 409
        return HTMLResponse(
            _layout("Action blocked", f'<section class="error-summary"><h1>Action blocked</h1><p>{_escape(exc)}</p><a href="/runs">Return to runs</a></section>', csrf=request.state.csrf),
            status_code=status,
        )

    return app
