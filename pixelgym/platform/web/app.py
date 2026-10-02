"""Server-rendered local control plane with allowlisted submissions and CSRF protection."""

from __future__ import annotations

import hashlib
import hmac
import html
import ipaddress
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import date
from difflib import HtmlDiff
from numbers import Real
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup
from pydantic import BaseModel, ConfigDict, Field

from pixelgym.platform.contracts import CandidateState, GateObservation
from pixelgym.platform.control_store import (
    ACTOR_VERIFICATION_SOURCE_KEY,
    RESERVED_ACTOR_NAMES,
    AuthorizationError,
    ConflictError,
    ContentionError,
    ControlStore,
    SyntheticDemoPrincipal,
    TransitionError,
    VerifiedPrincipal,
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
PRINCIPAL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@:/+-]{0,254}")


class ApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reason: str = Field(min_length=1, max_length=1000)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _finalize(value: object) -> object:
    """Escape every non-markup template expression with ``html.escape(..., quote=True)``.

    Autoescape stays on; this only keeps the entity spelling (``&#x27;``, ``&quot;``) that the
    pre-template renderer emitted, so rendered pages stay byte-identical. Markup values (macro
    output and the explicitly trusted disclosure) pass through unchanged.
    """
    if isinstance(value, Markup):
        return value
    return Markup(_escape(value))


def _actor_label(actor: object, details: dict[str, Any]) -> str:
    value = str(actor)
    source = details.get(ACTOR_VERIFICATION_SOURCE_KEY, "legacy_unverified")
    if source == "synthetic_demo":
        return f"{value} (synthetic demo)"
    if source == "legacy_unverified":
        return f"{value} (legacy/unverified)"
    return value


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


def _token(secret: bytes, session: str) -> bytes:
    return hmac.new(secret, session.encode(), hashlib.sha256).digest()


def _short_digest(value: str | None, *, width: int = 12) -> str:
    if not value:
        return "missing"
    return value.removeprefix("sha256:")[:width]


def _is_linkable_uri(uri: object) -> bool:
    """Only stored external evidence URIs with a non-scriptable scheme become links."""
    return urlsplit(str(uri)).scheme in {"http", "https", "s3"}


def _page_href(request: Request, path: str, page: int) -> str:
    parameters = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key != "page"
    ]
    parameters.append(("page", str(page)))
    return f"{path}?{urlencode(parameters)}"


def _mlflow_run_uri(mlflow_base_url: str, run_id: object) -> str:
    return f"{mlflow_base_url}/#/experiments/0/runs/{run_id}"


def _artifact(candidate: Any, suffix: str) -> Any | None:
    return next((item for item in candidate.artifacts if item.logical_key.endswith(suffix)), None)


def _candidate_badges(candidate: Any) -> list[tuple[str, str]]:
    """Return the candidate's (label, tone) badges in display order."""
    report = candidate.gate_report
    summary = candidate.summary
    # A candidate imported without a run summary falls back to its recorded provider;
    # never infer that an unattributed provider is the scripted demo.
    synthetic = (
        summary.synthetic_provider
        if summary is not None
        else candidate.policy.provider == "scripted-demo"
    )
    badges = [("DEMO PROVIDER", "demo") if synthetic else ("REAL PROVIDER", "real")]
    if not report.completeness.passed:
        badges.append(("INCOMPLETE", "bad"))
    if candidate.policy.code_state == "dirty":
        badges.append(("UNCOMMITTED CHANGES", "bad"))
    elif candidate.policy.code_state != "clean":
        # "unverifiable" and any value outside the schema: never claim a dirty tree.
        badges.append(("UNVERIFIED SOURCE", "bad"))
    if candidate.policy.source_provenance_failure_reason is not None:
        badges.append(
            (
                f"PROVENANCE: {candidate.policy.source_provenance_failure_reason.replace('_', ' ').upper()}",
                "bad",
            )
        )
    unpriced = (
        report.cost_usd_per_100.observed is None
        or (summary is not None and summary.unpriced_call_count > 0)
    )
    if unpriced:
        badges.append(("UNPRICED", "bad"))
    return badges


def _invalid_count(candidate: Any) -> str:
    return "missing" if candidate.summary is None else str(candidate.summary.invalid_count)


def _candidate_row(candidate: Any, mlflow_base_url: str) -> dict[str, Any]:
    state_tone = "good" if candidate.state in {CandidateState.ELIGIBLE, CandidateState.APPROVED} else "bad"
    return {
        "candidate": candidate,
        "badges": _candidate_badges(candidate),
        "state_tone": state_tone,
        "invalid_count": _invalid_count(candidate),
        "mlflow_uri": _mlflow_run_uri(mlflow_base_url, candidate.source_run_id),
    }


def _evidence_links(candidate: Any, mlflow_base_url: str) -> dict[str, Any]:
    predictions = _artifact(candidate, "/predictions.jsonl")
    gate = _artifact(candidate, "/gate-report.json")
    return {
        "candidate_id": candidate.candidate_id,
        "raw_count": sum(item.logical_key.startswith("raw-responses/") for item in candidate.artifacts),
        "predictions_uri": None if predictions is None else predictions.uri,
        "gate_uri": None if gate is None else gate.uri,
        "mlflow_uri": _mlflow_run_uri(mlflow_base_url, candidate.source_run_id),
    }


# Trusted, source-controlled HTML keyed by (provider, model); never populated from runtime input.
CANDIDATE_DISCLOSURE_TRUSTED_HTML: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        ("scripted-demo", "day3-replay-revised-v2"): (
            "<strong>Synthetic fixture disclosure:</strong> Candidate B's scripted revised responses "
            'are derived from the frozen Day 3 <code>condition == "marks"</code> rows, then relabeled '
            "for this policy's raw-condition demonstration. They are not results from the recorded "
            "raw prompt."
        ),
    }
)


def _candidate_disclosure(policy: Any) -> Markup | None:
    body = CANDIDATE_DISCLOSURE_TRUSTED_HTML.get((policy.provider, policy.model))
    # The single place the disclosure bypasses autoescape: it is source-controlled HTML.
    return None if body is None else Markup(body)


def _gate_metric(
    label: str, observation: GateObservation, bound: str, formatter: Callable[[object], str]
) -> dict[str, str]:
    return {
        "label": label,
        "observed": formatter(observation.observed),
        "bound": bound,
        "threshold": formatter(observation.threshold),
    }


def _gate_metrics(candidate: Any) -> list[dict[str, str]]:
    report = candidate.gate_report
    return [
        _gate_metric("Accuracy", report.accuracy, "minimum", _percentage),
        _gate_metric("Cost / 100", report.cost_usd_per_100, "maximum", _money),
        _gate_metric("Provider p95", report.provider_latency_p95_ms, "maximum", _milliseconds),
    ]


def _delta(value: object, baseline: object, *, kind: str) -> str:
    if value is None or baseline is None:
        return "Δ unavailable"
    difference = _numeric(value) - _numeric(baseline)
    if kind == "accuracy":
        return f"Δ {difference * 100:+.1f} pp"
    if kind == "money":
        return f"Δ ${difference:+.2f}"
    return f"Δ {difference:+.1f} ms"


def _comparison_card(candidate: Any, baseline: Any, mlflow_base_url: str) -> dict[str, Any]:
    report = candidate.gate_report
    baseline_report = baseline.gate_report
    return {
        "candidate": candidate,
        "accuracy_delta": _delta(
            report.accuracy.observed, baseline_report.accuracy.observed, kind="accuracy"
        ),
        "cost_delta": _delta(
            report.cost_usd_per_100.observed,
            baseline_report.cost_usd_per_100.observed,
            kind="money",
        ),
        "latency_delta": _delta(
            report.provider_latency_p95_ms.observed,
            baseline_report.provider_latency_p95_ms.observed,
            kind="latency",
        ),
        "evidence": _evidence_links(candidate, mlflow_base_url),
    }


def _timeline_events(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "created_at_utc": event["created_at_utc"],
            "event_type": event["event_type"],
            "subject_id": event["subject_id"],
            "actor_label": _actor_label(event["actor"], event["details"]),
        }
        for event in events
    ]


TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).with_name("templates")),
    autoescape=True,
    finalize=_finalize,
    undefined=StrictUndefined,
    keep_trailing_newline=False,
)
TEMPLATES.filters.update(
    percentage=_percentage,
    money=_money,
    milliseconds=_milliseconds,
    short_digest=_short_digest,
)
TEMPLATES.tests["linkable_uri"] = _is_linkable_uri


def create_control_app(
    control: ControlStore,
    *,
    coordinator: DeploymentCoordinator[Any] | None = None,
    csrf_secret: str,
    loopback_deployment: bool = True,
    principal_header: str = "X-Forwarded-User",
    trusted_proxy_addresses: Sequence[str] = (),
    session_cookie_secure: bool = False,
    submit_callback: Callable[[str, dict[str, str]], None] | None = None,
    cancel_callback: Callable[[str], bool] | None = None,
    tracking: Tracking | None = None,
    mlflow_base_url: str = "http://localhost:5000",
) -> FastAPI:
    if len(csrf_secret) < 16:
        raise ValueError("CSRF secret must be at least 16 characters")
    secret = csrf_secret.encode()
    if re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", principal_header) is None:
        raise ValueError("principal header must be a valid HTTP field name")
    try:
        trusted_proxies = tuple(
            ipaddress.ip_network(address, strict=False) for address in trusted_proxy_addresses
        )
    except ValueError as exc:
        raise ValueError("trusted proxy allowlist must contain IP addresses or CIDR networks") from exc
    if any(network.prefixlen == 0 for network in trusted_proxies):
        raise ValueError("trusted proxy allowlist must not contain a default route")
    app = FastAPI(title="PixelGym Grounding Control Plane", docs_url=None, redoc_url=None)
    static = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.middleware("http")
    async def session_cookie(request: Request, call_next: Callable[..., Any]) -> Any:
        supplied_principals = request.headers.getlist(principal_header)
        client_address = request.client.host if request.client is not None else ""
        try:
            client_ip = ipaddress.ip_address(client_address)
        except ValueError:
            client_ip = None
        if isinstance(client_ip, ipaddress.IPv6Address) and client_ip.ipv4_mapped is not None:
            client_ip = client_ip.ipv4_mapped
        trusted_source = client_ip is not None and any(
            client_ip in network for network in trusted_proxies
        )
        principal: VerifiedPrincipal | SyntheticDemoPrincipal | None = None
        if trusted_source:
            if len(supplied_principals) == 1:
                supplied_principal = supplied_principals[0]
                if (
                    PRINCIPAL_PATTERN.fullmatch(supplied_principal) is not None
                    and supplied_principal not in RESERVED_ACTOR_NAMES
                ):
                    principal = VerifiedPrincipal(supplied_principal)
        elif loopback_deployment:
            # Untrusted identity headers cannot override the fixed local-demo identity.
            principal = SyntheticDemoPrincipal()
        request.state.reviewer_principal = principal
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

    def require_principal(
        request: Request,
    ) -> VerifiedPrincipal | SyntheticDemoPrincipal:
        principal: VerifiedPrincipal | SyntheticDemoPrincipal | None = (
            request.state.reviewer_principal
        )
        if principal is None:
            raise HTTPException(403, "verified reviewer principal is required")
        return principal

    def render(request: Request, template: str, title: str, **context: Any) -> str:
        principal: VerifiedPrincipal | SyntheticDemoPrincipal | None = (
            request.state.reviewer_principal
        )
        label = (
            "unverified"
            if principal is None
            else _actor_label(
                principal,
                {ACTOR_VERIFICATION_SOURCE_KEY: principal.verification_source},
            )
        )
        return TEMPLATES.get_template(template).render(
            title=title, csrf=request.state.csrf, principal=label, **context
        )

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
        return render(
            request,
            "submit.html",
            "Submit experiment",
            dataset_options=DATASET_OPTIONS,
            prompt_options=PROMPT_OPTIONS,
            model_options=MODEL_OPTIONS,
        )

    @app.post("/experiments")
    async def submit_experiment(request: Request) -> RedirectResponse:
        principal = require_principal(request)
        fields = await csrf_form_fields(request)
        fields.pop("csrf_token")
        if set(fields) != set(ALLOWED_SUBMISSION_FIELDS):
            raise HTTPException(422, "submission fields do not match the fixed flow contract")
        payload = fields
        invalid = [key for key, value in payload.items() if value not in ALLOWED_SUBMISSION_FIELDS[key]]
        if invalid:
            raise HTTPException(422, f"submission contains non-allowlisted options: {', '.join(invalid)}")
        submission_id = await run_in_threadpool(control.submit, payload, actor=principal)
        if submit_callback is not None:
            submit_callback(submission_id, payload)
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)

    @app.get("/submissions/{submission_id}", response_class=HTMLResponse)
    def submission_view(submission_id: str, request: Request) -> str:
        try:
            submission = control.get_submission(submission_id)
        except KeyError as exc:
            raise HTTPException(404, "submission does not exist") from exc
        mlflow_uri = (
            _mlflow_run_uri(mlflow_base_url, submission["mlflow_run_id"])
            if submission["mlflow_run_id"]
            else None
        )
        return render(
            request,
            "submission.html",
            "Submission",
            submission_id=submission_id,
            submission=submission,
            mlflow_uri=mlflow_uri,
            cancellable=submission["status"] in {"Submitted", "Running"},
        )

    @app.post("/submissions/{submission_id}/cancel")
    async def cancel_submission(submission_id: str, request: Request) -> RedirectResponse:
        principal = require_principal(request)
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
            actor=principal,
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
                if item.gate_report.dataset_fingerprint.removeprefix("sha256:").startswith(dataset)
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
                item for item in candidates if item.gate_report.overall_passed is expected
            ]
        if filters_active:
            offset = (page - 1) * RUNS_PAGE_SIZE
            candidate_window = candidates[offset : offset + RUNS_PAGE_SIZE + 1]
            has_next_page = len(candidate_window) > RUNS_PAGE_SIZE
            candidates = candidate_window[:RUNS_PAGE_SIZE]
        return render(
            request,
            "runs.html",
            "Runs",
            submitted=submitted,
            rows=[_candidate_row(item, mlflow_base_url) for item in candidates],
            provider_options=provider_options,
            lifecycle_options=[item.value for item in CandidateState],
            filters={
                "provider": provider,
                "lifecycle": lifecycle,
                "dataset": dataset,
                "code_revision": code_revision,
                "prompt_version": prompt_version,
                "model": model,
                "status": status,
                "date_from": date_from,
                "date_to": date_to,
                "gate_result": gate_result,
            },
            previous_href=_page_href(request, "/runs", page - 1) if page > 1 else None,
            next_href=_page_href(request, "/runs", page + 1) if has_next_page else None,
        )

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
        selected = [candidate_or_404(item) for item in selected_ids]
        comparison_keys = {
            (
                item.gate_report.dataset_fingerprint,
                item.policy.scorer_version,
                item.policy.target_semantics,
                item.summary.primary_metric if item.summary is not None else "unknown",
            )
            for item in selected
        }
        compatible = len(selected) >= 2 and len(comparison_keys) == 1 and all(
            item.summary is not None for item in selected
        )
        prompt_diff_query = (
            urlencode([("candidate", item.candidate_id) for item in selected[:2]])
            if len(selected) >= 2
            else None
        )
        cards = [
            _comparison_card(item, selected[0], mlflow_base_url) for item in selected
        ]
        return render(
            request,
            "compare.html",
            "Compare",
            all_candidates=all_candidates,
            selected_ids=selected_ids,
            cards=cards,
            compatible=compatible,
            prompt_diff_query=prompt_diff_query,
        )

    def _packaged_prompt_text(item: Any) -> tuple[str, bool]:
        """Return (prompt text, is_legacy) reading the candidate's own immutable package.

        A candidate whose policy carries renderer identity stores the exact prompt bytes
        used to build it (``prompt_template_text``); that packaged text is read directly,
        with no re-render. A candidate packaged before renderer identity existed carries
        no such artifact, so its side of the diff is re-rendered from the frozen local
        templates and labelled legacy.
        """
        if item.policy.prompt_template_text is not None:
            return item.policy.prompt_template_text, False
        try:
            return prompt_template(item.policy.prompt_version), True
        except ValueError as exc:
            raise HTTPException(409, "recorded prompt version is unavailable for rendering") from exc

    @app.get("/compare/prompt-diff", response_class=HTMLResponse)
    def prompt_diff_view(
        request: Request, candidate: Annotated[list[str] | None, Query()] = None
    ) -> str:
        selected_ids = candidate or []
        if len(selected_ids) != 2:
            raise HTTPException(422, "prompt diff requires exactly two candidates")
        before, after = [candidate_or_404(item) for item in selected_ids]
        before_prompt, before_legacy = _packaged_prompt_text(before)
        after_prompt, after_legacy = _packaged_prompt_text(after)

        def _label(item: Any, is_legacy: bool) -> str:
            # is_legacy mirrors _packaged_prompt_text's own (text, is_legacy) return:
            # tag the diff column so a reader can tell a packaged side from a
            # re-rendered one without re-deriving it from the candidate.
            tag = " (legacy · re-rendered, no packaged prompt artifact)" if is_legacy else " (packaged)"
            return _escape(f"{item.candidate_id} · v{item.policy.prompt_version}{tag}")

        diff = HtmlDiff(wrapcolumn=88).make_table(
            before_prompt.splitlines(),
            after_prompt.splitlines(),
            fromdesc=_label(before, before_legacy),
            todesc=_label(after, after_legacy),
            context=True,
        )
        return render(
            request,
            "prompt_diff.html",
            "Prompt diff",
            includes_legacy=before_legacy or after_legacy,
            # difflib.HtmlDiff output is library-generated markup: difflib escapes every
            # prompt line itself and the column labels are escaped above.
            diff_table=Markup(diff),
        )

    @app.get("/candidates/{candidate_id}/evidence/raw-responses", response_class=HTMLResponse)
    def raw_response_index(candidate_id: str, request: Request) -> str:
        item = candidate_or_404(candidate_id)
        raw = [reference for reference in item.artifacts if reference.logical_key.startswith("raw-responses/")]
        return render(
            request,
            "raw_responses.html",
            "Raw-response index",
            candidate_id=candidate_id,
            references=raw,
        )

    @app.get("/candidates/{candidate_id}", response_class=HTMLResponse)
    def candidate_view(candidate_id: str, request: Request) -> str:
        item = candidate_or_404(candidate_id)
        action = "blocked"
        active_deployment_id = ""
        generation = 0
        if item.state is CandidateState.ELIGIBLE:
            action = "approve"
        elif item.state is CandidateState.APPROVED and coordinator is not None:
            action = "deploy"
            active, generation = control.active()
            active_deployment_id = active.deployment_id if active is not None else ""
        return render(
            request,
            "candidate.html",
            "Candidate",
            candidate_id=candidate_id,
            candidate=item,
            invalid_count=_invalid_count(item),
            badges=_candidate_badges(item),
            disclosure=_candidate_disclosure(item.policy),
            gate_metrics=_gate_metrics(item),
            evidence=_evidence_links(item, mlflow_base_url),
            action=action,
            active_deployment_id=active_deployment_id,
            generation=generation,
        )

    def _approve(
        candidate_id: str,
        reason: str,
        actor: VerifiedPrincipal | SyntheticDemoPrincipal,
    ) -> None:
        item = candidate_or_404(candidate_id)
        control.approve(
            candidate_id,
            actor=actor,
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
        principal = require_principal(request)
        fields = await csrf_form_fields(request)
        if set(fields) != {"csrf_token", "reason"}:
            raise HTTPException(422, "approval fields do not match the fixed contract")
        await run_in_threadpool(_approve, candidate_id, fields["reason"], principal)
        return RedirectResponse(f"/candidates/{candidate_id}", status_code=303)

    @app.post("/api/candidates/{candidate_id}/approve")
    def approve_api(candidate_id: str, request: Request, body: ApprovalBody) -> dict[str, str]:
        principal = require_principal(request)
        require_csrf(request, request.headers.getlist("x-csrf-token"))
        _approve(candidate_id, body.reason, principal)
        return {"candidate_id": candidate_id, "state": "Approved"}

    @app.post("/candidates/{candidate_id}/deploy")
    async def deploy_form(candidate_id: str, request: Request) -> RedirectResponse:
        principal = require_principal(request)
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
            actor=principal,
            reason=fields["reason"],
            expected_deployment_id=expected_deployment_id,
            expected_generation=expected_generation,
        )
        return RedirectResponse("/deployment", status_code=303)

    @app.post("/rollback")
    async def rollback_form(request: Request) -> RedirectResponse:
        principal = require_principal(request)
        fields = await csrf_form_fields(request)
        expected_fields = {"csrf_token", "reason", "expected_deployment_id", "expected_generation"}
        if set(fields) != expected_fields:
            raise HTTPException(422, "rollback fields do not match the fixed contract")
        if coordinator is None:
            raise HTTPException(503, "deployment coordinator is unavailable")
        expected_deployment_id, expected_generation = active_precondition(fields)
        await run_in_threadpool(
            coordinator.rollback,
            actor=principal,
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
        rollback_available = False
        if active:
            try:
                control.previous_target(active)
            except TransitionError:
                has_rollback_target = False
            else:
                has_rollback_target = True
            rollback_available = has_rollback_target and coordinator is not None
        return render(
            request,
            "deployment.html",
            "Deployment",
            active=active,
            generation=generation,
            rollback_available=rollback_available,
            events=_timeline_events(events),
        )

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
        return render(
            request,
            "deployment_audit.html",
            "Audit history",
            events=_timeline_events(events),
            previous_href=(
                _page_href(request, "/deployment/audit", page - 1) if page > 1 else None
            ),
            next_href=(
                _page_href(request, "/deployment/audit", page + 1) if has_next_page else None
            ),
        )

    @app.exception_handler(TransitionError)
    @app.exception_handler(ConflictError)
    @app.exception_handler(ContentionError)
    @app.exception_handler(AuthorizationError)
    @app.exception_handler(ImmutableStoreError)
    @app.exception_handler(DeploymentSmokeError)
    async def lifecycle_error(request: Request, exc: Exception) -> HTMLResponse:
        if isinstance(exc, AuthorizationError):
            status = 403
        elif isinstance(exc, ContentionError):
            status = 503
        else:
            status = 409
        return HTMLResponse(
            render(request, "action_blocked.html", "Action blocked", message=str(exc)),
            status_code=status,
        )

    return app
