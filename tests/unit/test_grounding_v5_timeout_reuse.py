"""Offline overlap fixtures: late results cannot borrow another request's bound."""

import io
import json
import threading
import urllib.error
from contextlib import closing
from decimal import Decimal

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger, request_bound
from pixelgym.grounding.v5.request_budget_v2 import IsolatedRequestBoundTransport
from tests.unit.test_grounding_v5_request_budget import CONFIG, request


def response(cost="0.01"):
    return io.BytesIO(
        json.dumps(
            {
                "id": "fixture",
                "model": CONFIG.model,
                "provider": "Google",
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"cost": cost},
            }
        ).encode()
    )


@pytest.mark.parametrize("late_failure", [False, True])
def test_active_timeout_retires_transport_and_late_settlement_keeps_own_bound(
    tmp_path, late_failure
):
    entered, release = threading.Event(), threading.Event()
    calls, outcomes = [], []

    def wire(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5), "fixture release was not delivered"
        if late_failure:
            raise urllib.error.URLError("fixture")
        return response()

    path = tmp_path / "journal.sqlite"
    bound = Decimal(request_bound(request(10), CONFIG)["request_maximum_usd"])
    with closing(V5AttemptJournal(path)) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        transport = IsolatedRequestBoundTransport(
            CONFIG,
            lifecycle_id="phase",
            ledger=ledger,
            environment={"OPENROUTER_API_KEY": "fixture"},
            urlopen=wire,
        )
        thread = threading.Thread(
            target=lambda: outcomes.append(
                transport.send(request(10), idempotency_key="first", deadline_seconds=210)
            )
        )
        thread.start()
        try:
            assert entered.wait(5)
            assert (
                transport.send(
                    request(1), idempotency_key="second", deadline_seconds=210
                ).failure_code
                == "transport_busy"
            )
            transport.settle_unknown_spend(idempotency_key="first")
            transport.settle_unknown_spend(idempotency_key="first")
            assert transport.retired and not transport.wait_until_idle()
            assert (
                transport.send(
                    request(1), idempotency_key="second", deadline_seconds=210
                ).failure_code
                == "transport_retired"
            )
            assert transport.config == CONFIG
            assert ledger.unknown_reservation_usd == bound
        finally:
            release.set()
            thread.join(5)
        assert not thread.is_alive() and transport.wait_until_idle()
        assert len(calls) == 1 and not ledger.blocked
        assert ledger.spent_usd == (Decimal(0) if late_failure else Decimal("0.01"))
        assert ledger.unknown_reservation_usd == (bound if late_failure else 0)
        assert (
            transport.send(request(), idempotency_key="third", deadline_seconds=210).failure_code
            == "transport_retired"
        )
        if not late_failure:
            charged = journal.event(f"spend/{content_digest('first')}/charged")
            assert Decimal(charged.payload["request_maximum_usd"]) == bound
            assert ledger.record_cost("first", Decimal("0.01"), bound)
            assert ledger.spent_usd == Decimal("0.01")
    with closing(V5AttemptJournal(path)) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        again = IsolatedRequestBoundTransport(
            CONFIG,
            lifecycle_id="phase",
            ledger=ledger,
            environment={"OPENROUTER_API_KEY": "fixture"},
            urlopen=wire,
        )
        assert again.retired
        assert (
            again.send(request(), idempotency_key="fourth", deadline_seconds=210).failure_code
            == "transport_retired"
        )
        assert ledger.spent_usd == (Decimal(0) if late_failure else Decimal("0.01"))


@pytest.mark.parametrize("images", [(1, 10), (10, 1)])
def test_sequential_requests_each_charge_their_reserved_bound(tmp_path, images):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        transport = IsolatedRequestBoundTransport(
            CONFIG,
            lifecycle_id="phase",
            ledger=ledger,
            environment={"OPENROUTER_API_KEY": "fixture"},
            urlopen=lambda *a, **kw: response(),
        )
        for index, count in enumerate(images):
            body = request(count)
            key = str(index)
            assert (
                transport.send(body, idempotency_key=key, deadline_seconds=210).status == "response"
            )
            reserved = journal.event(f"spend/{content_digest(key)}/reserved")
            charged = journal.event(f"spend/{content_digest(key)}/charged")
            assert (
                reserved.payload["request_maximum_usd"]
                == charged.payload["request_maximum_usd"]
                == request_bound(body, CONFIG)["request_maximum_usd"]
            )
        assert not ledger.blocked and not transport.retired
        assert ledger.spent_usd == Decimal("0.02") and transport.config == CONFIG
