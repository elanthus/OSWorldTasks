"""Haiku HTTP successor preserves observations and reserves the full paid bound."""

from decimal import Decimal

import pytest

from pixelgym.grounding.v5.haiku_openrouter import (
    MODEL,
    HaikuOpenRouterPolicy,
    config_from_snapshot,
    full_context_bound,
)
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_calibration import MemoryCalibrationLedger
from pixelgym.grounding.v5.reliable_transport import ReliableTransport


def config():
    return config_from_snapshot(
        {
            "data": {
                "id": MODEL,
                "endpoints": [
                    {
                        "tag": "anthropic",
                        "provider_name": "Anthropic",
                        "context_length": 200000,
                        "supported_parameters": [
                            "reasoning",
                            "response_format",
                            "structured_outputs",
                            "max_tokens",
                        ],
                        "pricing": {"prompt": "0.000001", "completion": "0.000005"},
                    }
                ],
            }
        }
    )


def test_matched_initial_observations_and_full_context_bound():
    requests = []
    for history in (True, False):
        p = HaikuOpenRouterPolicy(config(), retain_screenshots=history)
        frame = bytes(1024 * 768 * 3)
        state = p.observe_screenshot(p.reset("test"), frame)
        requests.append(p.build_request(state, frame))
    assert requests[0] == requests[1]
    request = requests[0]
    assert not {"seed", "temperature", "tools"} & request.keys()
    assert request["provider"]["only"] == ["anthropic"]
    assert request["provider"]["allow_fallbacks"] is False
    assert request["reasoning"] == {"enabled": True}
    assert Decimal(full_context_bound(request, config())["request_maximum_usd"]) == Decimal(
        ".220480"
    )


def test_budget_stops_before_wire(tmp_path):
    class Wire:
        idle = True

        def perform(self, *args, **kwargs):
            pytest.fail("over-budget request must never reach wire")

        def abort(self):
            pass

    journal = V5AttemptJournal(tmp_path / "attempts.sqlite")
    ledger = MemoryCalibrationLedger(Decimal(20), Decimal("19.80"), journal=journal)
    transport = ReliableTransport(
        config(),
        lifecycle_id="budget-test",
        ledger=ledger,
        wire=Wire(),
        environment={"OPENROUTER_API_KEY": "test"},
        phase_spend_limit=Decimal(20),
        request_bounder=full_context_bound,
    )
    p = HaikuOpenRouterPolicy(config(), retain_screenshots=False)
    request = p.build_request(p.reset("test"), bytes(1024 * 768 * 3))
    try:
        outcome = transport.send(request, idempotency_key="test-request", deadline_seconds=190)
        assert outcome.status == "pre_send_failure"
        assert ledger.wire_requests_sent == 0
        assert ledger.budget_accounted_spend_usd == Decimal("19.80")
    finally:
        transport._retire("test-end")
        journal.close()
