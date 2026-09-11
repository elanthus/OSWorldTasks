"""Request-local budgets and durable retirement after an outstanding deadline.

The executed v1 wrapper remains unchanged for historical reproduction. Each send
here owns a separate transport with a fixed config. Only the ledger is shared.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from decimal import Decimal
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.memory_plan import ScreenshotPriceConfig
from pixelgym.grounding.v5.panel_policy import OpenRouterPanelTransport
from pixelgym.grounding.v5.request_budget import request_bound
from pixelgym.grounding.v5.runner import TransportOutcome

TRANSPORT_VERSION = "gemini-request-local-transport-v2"


class IsolatedRequestBoundTransport(OpenRouterPanelTransport):
    """Serialize sends; a runner-abandoned worker permanently retires this lifecycle.

    An underlying HTTP call is not cancelled by the runner deadline. Its late result
    may settle its own charge, but the runner never receives a replacement action.
    Restarting the same lifecycle cannot clear its retirement record.
    """

    def __init__(self, *args: Any, lifecycle_id: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not isinstance(self.config, ScreenshotPriceConfig) or self.ledger.journal is None:
            raise ValueError("isolated budgeting requires screenshot prices and a journal")
        self._lifecycle_key = f"transport/{content_digest(lifecycle_id)}/retired"
        self._state_lock = threading.RLock()
        self._active: str | None = None
        self._idle = threading.Event()
        self._idle.set()
        self._retired = self.ledger.journal.event(self._lifecycle_key) is not None

    @property
    def retired(self) -> bool:
        with self._state_lock:
            return self._retired

    def wait_until_idle(self, timeout_seconds: float = 0) -> bool:
        return self._idle.wait(timeout_seconds)

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        with self._state_lock:
            if self._retired or self._active is not None:
                return TransportOutcome(
                    "pre_send_failure",
                    failure_code="transport_retired" if self._retired else "transport_busy",
                )
            self._active = idempotency_key
            self._idle.clear()
        worker = None
        try:
            config = self.config
            assert isinstance(config, ScreenshotPriceConfig)
            try:
                proof = request_bound(request, config)
            except (ValueError, KeyError, TypeError) as exc:
                return TransportOutcome(
                    "pre_send_failure", failure_code=f"request_bound_{type(exc).__name__}"
                )
            journal = self.ledger.journal
            assert journal is not None
            rid = content_digest(idempotency_key)
            with self._state_lock:
                if self._retired:
                    return TransportOutcome("pre_send_failure", failure_code="transport_retired")
                journal.append_event(
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
                worker = OpenRouterPanelTransport(
                    replace(config, upstream_context_length=proof["input_units_bound"]),
                    ledger=self.ledger,
                    environment={"OPENROUTER_API_KEY": self._api_key},
                    timeout_seconds=self.timeout_seconds,
                    urlopen=self._urlopen,
                    monotonic=self._monotonic,
                    wall_time=self._wall_time,
                    sleep=self._sleep,
                )
                worker._cooldown_until = self._cooldown_until
                worker._consecutive_rate_limits = self._consecutive_rate_limits
                worker._consecutive_transport_faults = self._consecutive_transport_faults
            return worker.send(
                request, idempotency_key=idempotency_key, deadline_seconds=deadline_seconds
            )
        finally:
            with self._state_lock:
                if worker is not None:
                    self.records.extend(worker.records)
                    self._cooldown_until = worker._cooldown_until
                    self._consecutive_rate_limits = worker._consecutive_rate_limits
                    self._consecutive_transport_faults = worker._consecutive_transport_faults
                # Retirement can precede a delayed wire reservation. Retain that
                # hold too, unless the worker already supplied a priced result.
                if self._retired:
                    self._hold_request(idempotency_key)
                self._active = None
                self._idle.set()

    def _hold_request(self, idempotency_key: str) -> None:
        if self.ledger.has_in_flight_reservation(idempotency_key):
            journal = self.ledger.journal
            assert journal is not None
            event = journal.event(f"spend/{content_digest(idempotency_key)}/request-bound")
            if event is None:
                raise ValueError("in-flight request lacks its recorded bound")
            self.ledger.reserve_unknown_charge(
                idempotency_key, Decimal(event.payload["request_maximum_usd"])
            )

    def settle_unknown_spend(self, *, idempotency_key: str) -> None:
        with self._state_lock:
            if self._active == idempotency_key and not self._retired:
                journal = self.ledger.journal
                assert journal is not None
                journal.append_event(
                    event_key=self._lifecycle_key,
                    kind="transport_retired",
                    trial_id="__spend_ledger__",
                    step_index=0,
                    payload={
                        "reservation_id": content_digest(idempotency_key),
                        "reason": "runner_abandoned_active_send",
                        "transport_version": TRANSPORT_VERSION,
                    },
                )
                self._retired = True
            self._hold_request(idempotency_key)
