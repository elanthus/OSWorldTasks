"""Versioned request-local transport, durable cooldowns, and observable retries.

The runner owns retries. A send here starts at most one curl process and always
settles its own request bound. Historical transports remain byte-for-byte intact.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, Literal

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.curl_wire import CurlWire, WireReceipt
from pixelgym.grounding.v5.memory_plan import ScreenshotPriceConfig
from pixelgym.grounding.v5.openrouter_policy import _usage_cost
from pixelgym.grounding.v5.panel_policy import SpendLedger
from pixelgym.grounding.v5.request_budget import request_bound
from pixelgym.grounding.v5.runner import TransportOutcome

TRANSPORT_VERSION = "curl-request-local-transport-v3"
RETRY_RULE = "same-request-transient-only-two-retries-5s-15s-v1"
TRANSIENT_CURL_ERRORS = frozenset({5, 6, 7, 18, 28, 35, 52, 55, 56, 92, 95})
TRANSIENT_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})
TRANSIENT_TYPES = frozenset(
    {"rate_limit_exceeded", "provider_overloaded", "provider_unavailable", "server", "timeout"}
)


def retry_delay(raw: str | None, *, failures: int, now: float) -> tuple[float, str]:
    """A valid server minimum is never shortened to our fallback ceiling."""
    if raw:
        try:
            delay = float(raw)
        except ValueError:
            try:
                target = parsedate_to_datetime(raw)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=UTC)
                delay = (target - datetime.fromtimestamp(now, UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = float("nan")
        if math.isfinite(delay) and delay >= 0:
            return delay, "retry_after"
    return (5.0, 15.0, 45.0, 60.0)[min(max(failures - 1, 0), 3)], "exponential_fallback"


def error_signal(body: dict[str, Any]) -> tuple[int | None, str | None]:
    candidates = [body.get("error")]
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        candidates.append(choices[0].get("error"))
    for error in candidates:
        if not isinstance(error, dict):
            continue
        code = error.get("code")
        code = code if type(code) is int else None
        metadata = error.get("metadata")
        category = metadata.get("error_type") if isinstance(metadata, dict) else None
        category = category if isinstance(category, str) and category in TRANSIENT_TYPES else None
        if code is not None or category is not None:
            return code, category
    return None, None


class ReliableTransport:
    def __init__(
        self,
        config: ScreenshotPriceConfig,
        *,
        lifecycle_id: str,
        ledger: SpendLedger,
        wire: Any = None,
        environment: Mapping[str, str] | None = None,
        wall_time: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        phase_deadline: float = float("inf"),
        phase_spend_limit: Decimal = Decimal(1),
        phase_start_accounted: Decimal | None = None,
        phase_wire_limit: int = 20,
        phase_start_wire: int | None = None,
        request_bounder: Callable | None = None,
    ) -> None:
        if ledger.journal is None:
            raise ValueError("reliable transport requires a durable journal")
        self.config, self.ledger, self.journal = config, ledger, ledger.journal
        self._request_bounder = request_bounder or request_bound
        self._key = (os.environ if environment is None else environment).get(
            "OPENROUTER_API_KEY", ""
        )
        if not self._key:
            raise ValueError("OpenRouter credential is unavailable")
        self.wire = wire if wire is not None else CurlWire()
        self._wall, self._mono, self._sleep = wall_time, monotonic, sleep
        self.phase_deadline = phase_deadline
        self.phase_spend_limit = phase_spend_limit
        self.phase_start_accounted = (
            ledger.budget_accounted_spend_usd
            if phase_start_accounted is None
            else phase_start_accounted
        )
        self.phase_wire_limit = phase_wire_limit
        self.phase_start_wire = (
            ledger.wire_requests_sent if phase_start_wire is None else phase_start_wire
        )
        self._prefix = f"reliable/{content_digest(lifecycle_id)}"
        existing = self.journal.event(f"{self._prefix}/binding")
        if existing is not None:
            if phase_start_accounted is None:
                self.phase_start_accounted = Decimal(existing.payload["phase_start_accounted"])
            if phase_start_wire is None:
                self.phase_start_wire = existing.payload["phase_start_wire"]
        binding = {
            "transport_version": TRANSPORT_VERSION,
            "phase_start_accounted": str(self.phase_start_accounted),
            "phase_spend_limit": str(phase_spend_limit),
            "phase_start_wire": self.phase_start_wire,
            "phase_wire_limit": phase_wire_limit,
            "phase_deadline": phase_deadline if math.isfinite(phase_deadline) else None,
            "config_digest": content_digest({k: str(v) for k, v in vars(config).items()}),
        }
        if existing is not None and existing.payload != binding:
            raise ValueError("transport lifecycle binding changed")
        self.journal.append_event(
            event_key=f"{self._prefix}/binding",
            kind="reliable_transport_binding",
            trial_id=self._prefix,
            step_index=0,
            payload=binding,
        )
        self._lock = threading.RLock()
        self._idle = threading.Event()
        self._idle.set()
        self._active: tuple[str, Decimal] | None = None
        self._retired = self.journal.event(f"{self._prefix}/retired") is not None
        self._cooldown_until, self._failures = 0.0, 0
        for event in self.journal.events():
            if event.kind == "reliable_transport_schedule" and event.trial_id == self._prefix:
                self._cooldown_until = event.payload["cooldown_until"]
                self._failures = event.payload["consecutive_failures"]
        self.records: list[dict[str, Any]] = []

    @property
    def retired(self) -> bool:
        with self._lock:
            return self._retired

    @property
    def wire_requests_sent(self) -> int:
        return self.ledger.wire_requests_sent

    def wait_until_idle(self, timeout_seconds: float = 0) -> bool:
        return self._idle.wait(timeout_seconds)

    def await_ready(self) -> bool:
        """Wait between attempts, outside the per-request network deadline."""
        wait = max(0.0, self._cooldown_until - self._wall())
        if wait + 10 >= self.phase_deadline - self._wall():
            return False
        while wait > 0:
            if self.retired:
                return False
            self._sleep(min(wait, 1.0))
            wait = max(0.0, self._cooldown_until - self._wall())
        return not self.retired and self._wall() + 10 < self.phase_deadline

    def bind_spend_journal(self, journal: Any) -> None:
        if journal is not self.journal:
            raise ValueError("transport cannot switch journals")

    def _retire(self, reason: str) -> None:
        with self._lock:
            if not self._retired:
                self.journal.append_event(
                    event_key=f"{self._prefix}/retired",
                    kind="transport_retired",
                    trial_id=self._prefix,
                    step_index=0,
                    payload={"reason": reason, "transport_version": TRANSPORT_VERSION},
                )
                self._retired = True
            self.wire.abort()

    def settle_unknown_spend(self, *, idempotency_key: str) -> None:
        with self._lock:
            if self._active is not None and self._active[0] == idempotency_key:
                self._retire("runner_abandoned_active_send")
            if self.ledger.has_in_flight_reservation(idempotency_key):
                event = self.journal.event(f"spend/{content_digest(idempotency_key)}/request-bound")
                assert event is not None
                self.ledger.reserve_unknown_charge(
                    idempotency_key, Decimal(event.payload["request_maximum_usd"])
                )

    def settle_zero_charge_spend(self, *, idempotency_key: str, reason: str) -> None:
        if self.ledger.has_in_flight_reservation(idempotency_key):
            self.ledger.release_wire(idempotency_key, reason=reason)

    def cancel(self, *, idempotency_key: str, mode: str) -> Literal["cancelled", "unknown"]:
        del mode
        self.settle_unknown_spend(idempotency_key=idempotency_key)
        return "unknown"  # Killing our client cannot cancel remote generation.

    def reconcile(self, *, idempotency_key: str, deadline_seconds: float) -> TransportOutcome:
        del deadline_seconds
        self.settle_unknown_spend(idempotency_key=idempotency_key)
        return TransportOutcome("unknown", failure_code="reconciliation_disabled")

    def _schedule(
        self, rid: str, *, failed: bool, retry_after: str | None = None
    ) -> tuple[float, str]:
        self._failures = self._failures + 1 if failed else 0
        delay, source = (
            retry_delay(retry_after, failures=self._failures, now=self._wall())
            if failed
            else (0.0, "none")
        )
        self._cooldown_until = max(self._cooldown_until, self._wall() + delay) if failed else 0.0
        self.journal.append_event(
            event_key=f"{self._prefix}/{rid}/schedule",
            kind="reliable_transport_schedule",
            trial_id=self._prefix,
            step_index=0,
            payload={
                "cooldown_until": self._cooldown_until,
                "consecutive_failures": self._failures,
                "retry_after_seconds": delay,
                "backoff_source": source,
            },
        )
        return delay, source

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        with self._lock:
            if self._retired or self._active is not None:
                return TransportOutcome(
                    "pre_send_failure",
                    failure_code="transport_retired" if self._retired else "transport_busy",
                )
            self._active = (idempotency_key, Decimal(0))
            self._idle.clear()
        try:
            if (
                request.get("model") != self.config.model
                or request.get("provider") != self.config.provider_parameters()
            ):
                return TransportOutcome(
                    "pre_send_failure", failure_code="request_identity_mismatch"
                )
            try:
                proof = self._request_bounder(request, self.config)
            except (ValueError, TypeError, KeyError):
                return TransportOutcome("pre_send_failure", failure_code="request_bound_invalid")
            bound = Decimal(proof["request_maximum_usd"])
            self._active = (idempotency_key, bound)
            started = self._mono()
            wait = max(0.0, self._cooldown_until - self._wall())
            if wait >= min(deadline_seconds - 10, self.phase_deadline - self._wall()):
                return TransportOutcome(
                    "pre_send_failure", failure_code="cooldown_exceeds_remaining_time"
                )
            while wait > 0:
                self._sleep(min(wait, 1.0))
                if self.retired:
                    return TransportOutcome("pre_send_failure", failure_code="transport_retired")
                wait = max(0.0, self._cooldown_until - self._wall())
            remaining = min(
                deadline_seconds - (self._mono() - started) - 10,
                self.phase_deadline - self._wall() - 10,
            )
            if remaining <= 0:
                return TransportOutcome("pre_send_failure", failure_code="phase_time_stop")
            if (
                self.ledger.budget_accounted_spend_usd + bound
                > self.phase_start_accounted + self.phase_spend_limit
                or self.ledger.wire_requests_sent >= self.phase_start_wire + self.phase_wire_limit
            ):
                return TransportOutcome("pre_send_failure", failure_code="phase_cap_stop")
            rid = content_digest(idempotency_key)
            with self._lock:
                if self._retired:
                    return TransportOutcome("pre_send_failure", failure_code="transport_retired")
                self.journal.append_event(
                    event_key=f"spend/{rid}/request-bound",
                    kind="request_budget_bound",
                    trial_id="__spend_ledger__",
                    step_index=0,
                    payload={
                        **proof,
                        "reservation_id": rid,
                        "transport_version": TRANSPORT_VERSION,
                    },
                )
                if not self.ledger.reserve_wire(idempotency_key, bound):
                    return TransportOutcome(
                        "pre_send_failure", failure_code="aggregate_spend_guard"
                    )
            try:
                receipt = self.wire.perform(
                    json.dumps(request, separators=(",", ":")).encode(),
                    headers={
                        "Authorization": f"Bearer {self._key}",
                        "Content-Type": "application/json",
                        "Idempotency-Key": idempotency_key,
                        "X-OpenRouter-Metadata": "enabled",
                    },
                    timeout=min(180.0, remaining),
                )
            except BaseException:
                self.ledger.reserve_unknown_charge(idempotency_key, bound)
                self._retire("wire_client_failed")
                raise
            return self._receive(receipt, rid=rid, key=idempotency_key, bound=bound)
        finally:
            with self._lock:
                if not self.wire.idle:
                    self._retire("wire_not_reaped")
                self._active = None
                self._idle.set()

    def _receive(
        self, receipt: WireReceipt, *, rid: str, key: str, bound: Decimal
    ) -> TransportOutcome:
        body_digest = self.journal.put_object("provider_response_envelope", receipt.body)
        self.journal.append_event(
            event_key=f"{self._prefix}/{rid}/receipt",
            kind="reliable_transport_receipt",
            trial_id=self._prefix,
            step_index=0,
            payload={
                "reservation_id": rid,
                "response_envelope_digest": body_digest,
                "http_status": receipt.status,
                "curl_exit_code": receipt.exit_code,
                "metrics": dict(receipt.metrics),
            },
        )
        try:
            body = json.loads(receipt.body)
            if not isinstance(body, dict):
                raise TypeError("invalid envelope")
        except (ValueError, TypeError, UnicodeDecodeError):
            body = {}
        choices = body.get("choices")
        choice = (
            choices[0]
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else {}
        )
        message = choice.get("message")
        content = message.get("content", "") if isinstance(message, dict) else ""
        usage = dict(body["usage"]) if isinstance(body.get("usage"), dict) else {}
        try:
            cost = _usage_cost(usage)
        except ValueError:
            cost = None
        price_ok = self.ledger.record_cost(key, cost, bound) if cost is not None else True
        if cost is None:
            self.ledger.reserve_unknown_charge(key, bound)
        usage.update(
            upstream_provider=body.get("provider"),
            price_guard="ok" if cost is not None and price_ok else "missing_or_invalid_cost",
        )
        code, category = error_signal(body)
        rate_limit = receipt.status == 429 or code == 429 or category == "rate_limit_exceeded"
        transient = (
            receipt.exit_code in TRANSIENT_CURL_ERRORS
            or receipt.status in TRANSIENT_HTTP
            or code in TRANSIENT_HTTP
            or category in TRANSIENT_TYPES
        )
        # Contradictory identities, substantive output, and security/config errors
        # cannot be retried into an apparently successful model response.
        identity_ok = body.get("model") in (None, self.config.model) and body.get("provider") in (
            None,
            self.config.response_provider,
        )
        substantive = bool(content) or (
            type(usage.get("completion_tokens")) is int and usage["completion_tokens"] > 0
        )
        fatal = (
            not identity_ok
            or not price_ok
            or receipt.exit_code not in ({0} | TRANSIENT_CURL_ERRORS)
            or (receipt.status >= 300 and receipt.status not in TRANSIENT_HTTP)
        )
        if (
            not fatal
            and not substantive
            and (transient or (receipt.exit_code == 0 and 200 <= receipt.status < 300 and not body))
        ):
            delay, source = self._schedule(
                rid, failed=True, retry_after=receipt.headers.get("retry-after")
            )
            status: Literal["rate_limited", "transport_fault"] = (
                "rate_limited" if rate_limit and cost == 0 else "transport_fault"
            )
            reason = (
                "provider_rate_limit"
                if rate_limit
                else f"curl_{receipt.exit_code}"
                if receipt.exit_code
                else f"provider_transient_{code or receipt.status}"
            )
            outcome = TransportOutcome(
                status, failure_code=reason, retry_after_seconds=delay, backoff_source=source
            )
        else:
            self._schedule(rid, failed=False)
            canonical = {
                "response_id": str(body.get("id", "")),
                "model": str(body.get("model", "")),
                "content": content if isinstance(content, str) else "",
                "finish_reason": choice.get("finish_reason", ""),
                "usage": usage,
            }
            outcome = TransportOutcome(
                "policy_violation"
                if fatal or (substantive and (transient or choice.get("finish_reason") == "error"))
                else "response",
                canonical,
            )
            if fatal:
                self._retire("non_retryable_provider_or_transport_error")
        self.records.append(
            {
                "idempotency_key": key,
                "status": outcome.status,
                "failure_code": outcome.failure_code,
                "cost_usd": str(cost) if cost is not None else None,
            }
        )
        return outcome
