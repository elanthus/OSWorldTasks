"""Transport diagnostics retain error identity while excluding sensitive messages."""

import io
import json
import ssl
import urllib.error
import urllib.request
from contextlib import closing

import pytest

from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.transport_diagnostics import DiagnosticUrlopen, safe_error_details


def request():
    return urllib.request.Request(
        "https://example.invalid",
        data=b"fixture",
        headers={"Authorization": "Bearer never-log-this", "Idempotency-Key": "fixture-request"},
    )


def test_success_preserves_request_arguments_response_bytes_and_single_call(tmp_path):
    calls = []
    wire = request()
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:

        def send(req, **kwargs):
            calls.append((req, kwargs))
            return io.BytesIO(b'{"ok":true}')

        transport = DiagnosticUrlopen(journal, phase_id="fixture", urlopen=send)
        with transport(wire, timeout=17) as response:
            assert json.load(response) == {"ok": True}
        assert calls == [(wire, {"timeout": 17})]
        assert not journal.events()


@pytest.mark.parametrize(
    "read_failure,nested", [(False, False), (False, True), (True, False), (True, True)]
)
def test_failure_records_stage_once_and_reraises_original_without_message(
    tmp_path, read_failure, nested
):
    ssl_error = ssl.SSLError(1, "secret credential and response body never-log-this")
    ssl_error.library, ssl_error.reason = "SSL", "BAD_LENGTH"
    error = urllib.error.URLError(ssl_error) if nested else ssl_error
    calls = []

    class FaultyResponse(io.BytesIO):
        def read(self, *args, **kwargs):
            raise error

    def send(req, **kwargs):
        calls.append(req)
        if not read_failure:
            raise error
        return FaultyResponse()

    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        transport = DiagnosticUrlopen(journal, phase_id="fixture", urlopen=send)
        with pytest.raises(type(error)) as observed, transport(request(), timeout=17) as response:
            json.load(response)
        assert observed.value is error and len(calls) == 1
        events = journal.events()
        assert len(events) == 1
        assert events[0].payload["request_stage"] == (
            "read_response" if read_failure else "open_connect_or_send"
        )
        assert events[0].payload["ssl_reason"] == "BAD_LENGTH"
        assert "never-log-this" not in json.dumps(events[0].payload)


@pytest.mark.parametrize("reason", ["https://secret:password@example.invalid", "SECRET"])
def test_freeform_url_reason_is_not_recorded(reason):
    details = safe_error_details(urllib.error.URLError(reason))
    assert details == {"exception_class": "URLError", "underlying_class": "URLError"}


def test_diagnostics_preserve_request_local_unknown_accounting(tmp_path):
    from decimal import Decimal

    from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger, request_bound
    from pixelgym.grounding.v5.request_budget_v2 import IsolatedRequestBoundTransport
    from tests.unit.test_grounding_v5_request_budget import CONFIG
    from tests.unit.test_grounding_v5_request_budget import request as model_request

    body = model_request(2)

    class BrokenRead(io.BytesIO):
        def read(self, *args, **kwargs):
            raise ssl.SSLError(1, "private error detail")

    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        ledger = ReboundedMemoryLedger(Decimal(28), Decimal(0), journal=journal)
        traced = DiagnosticUrlopen(
            journal, phase_id="fixture", urlopen=lambda *a, **kw: BrokenRead()
        )
        transport = IsolatedRequestBoundTransport(
            CONFIG,
            lifecycle_id="fixture",
            ledger=ledger,
            environment={"OPENROUTER_API_KEY": "fixture"},
            urlopen=traced,
        )
        result = transport.send(body, idempotency_key="fixture", deadline_seconds=210)
        assert result.status == "transport_fault" and result.failure_code == "SSLError"
        assert ledger.wire_requests_sent == 1 and ledger.spent_usd == 0
        assert ledger.unknown_reservation_usd == Decimal(
            request_bound(body, CONFIG)["request_maximum_usd"]
        )
        assert ledger.in_flight_reservation_usd == 0 and not transport.retired
        records = [e for e in journal.events() if e.kind == "transport_error_diagnostic"]
        assert len(records) == 1 and records[0].payload["request_stage"] == "read_response"
        assert "private error detail" not in json.dumps(records[0].payload)
