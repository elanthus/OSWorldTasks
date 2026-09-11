"""Real runner/ledger fixtures with deterministic wire faults and virtual time."""

import json
import threading
from contextlib import closing
from decimal import Decimal

import pytest

from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.curl_wire import WireReceipt
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.reliable_memory import (
    ROOT,
    ReliableMemoryPolicy,
    ReliableMemoryRunner,
    build_reliable_manifest,
    reliable_config,
)
from pixelgym.grounding.v5.reliable_transport import ReliableTransport, retry_delay
from pixelgym.grounding.v5.request_budget import ReboundedMemoryLedger, request_bound
from tests.unit.test_grounding_v5_request_budget import CONFIG, request

CONFIG = reliable_config(CONFIG)


class Clock:
    now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Wire:
    idle = True

    def __init__(self, receipts):
        self.receipts, self.calls = iter(receipts), []
        self.aborted = False

    def perform(self, body, **kwargs):
        self.calls.append((body, kwargs))
        return next(self.receipts)

    def abort(self):
        self.aborted = True


def reply(
    *,
    status=200,
    code=None,
    content='{"action_type":0,"x":0,"y":0,"key":0}',
    cost="0.001",
    tokens=1,
    **kwargs,
):
    body = {
        "id": "fixture",
        "model": CONFIG.model,
        "provider": "Google",
        "choices": [
            {"message": {"content": content}, "finish_reason": "error" if code else "stop"}
        ],
        "usage": {"cost": cost, "completion_tokens": tokens},
    }
    if code:
        body["error"] = {"code": code}
    return WireReceipt(status, json.dumps(body).encode(), **kwargs)


def make(journal, wire, clock, **kwargs):
    ledger = ReboundedMemoryLedger(Decimal(28), Decimal(0), journal=journal)
    transport = ReliableTransport(
        CONFIG,
        lifecycle_id="fixture",
        ledger=ledger,
        wire=wire,
        environment={"OPENROUTER_API_KEY": "fixture-secret"},
        wall_time=clock.time,
        monotonic=clock.time,
        sleep=clock.sleep,
        **kwargs,
    )
    return ledger, transport


@pytest.mark.parametrize("status", [200, 429])
def test_rate_limit_body_and_http_share_full_durable_cooldown(tmp_path, status):
    path = tmp_path / "journal.sqlite"
    clock = Clock()
    with closing(V5AttemptJournal(path)) as journal:
        wire = Wire(
            [
                reply(
                    status=status,
                    code=429,
                    content="",
                    cost="0",
                    tokens=0,
                    headers={"retry-after": "120"},
                )
            ]
        )
        ledger, transport = make(journal, wire, clock)
        first = transport.send(request(), idempotency_key="one", deadline_seconds=210)
        assert first.status == "rate_limited" and first.retry_after_seconds == 120
        assert ledger.unknown_reservation_usd == ledger.spent_usd == 0
    with closing(V5AttemptJournal(path)) as journal:
        wire = Wire([reply()])
        ledger, transport = make(journal, wire, clock)
        result = transport.send(request(), idempotency_key="two", deadline_seconds=210)
        assert result.status == "response" and clock.now == 1120
        assert ledger.wire_requests_sent == 2 and ledger.spent_usd == Decimal("0.001")


@pytest.mark.parametrize(
    "raw,expected",
    [("600", 600), ("Thu, 01 Jan 1970 00:20:00 GMT", 200), ("nan", 15), ("bad", 15), ("-1", 15)],
)
def test_retry_after_minimum_and_fallback(raw, expected):
    assert retry_delay(raw, failures=2, now=1000)[0] == expected


def test_unknown_fault_bound_survives_retry_and_phase_cap_restart(tmp_path):
    path, clock = tmp_path / "journal.sqlite", Clock()
    with closing(V5AttemptJournal(path)) as journal:
        wire = Wire([WireReceipt(0, b"", exit_code=56), reply()])
        ledger, transport = make(journal, wire, clock, phase_wire_limit=2)
        assert (
            transport.send(request(10), idempotency_key="fault", deadline_seconds=210).status
            == "transport_fault"
        )
        assert (
            transport.send(request(), idempotency_key="good", deadline_seconds=210).status
            == "response"
        )
        bound = Decimal(request_bound(request(10), CONFIG)["request_maximum_usd"])
        assert ledger.unknown_reservation_usd == bound and clock.now == 1005
        assert ledger.in_flight_reservation_usd == 0
    with closing(V5AttemptJournal(path)) as journal:
        wire = Wire([])
        ledger, transport = make(journal, wire, clock, phase_wire_limit=2)
        assert (
            transport.send(request(), idempotency_key="extra", deadline_seconds=210).failure_code
            == "phase_cap_stop"
        )
        assert not wire.calls and ledger.unknown_reservation_usd == bound


def test_long_cooldown_does_not_send_early(tmp_path):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        wire, clock = (
            Wire([reply(code=429, content="", cost="0", tokens=0, headers={"retry-after": "600"})]),
            Clock(),
        )
        _, transport = make(journal, wire, clock)
        transport.send(request(), idempotency_key="one", deadline_seconds=210)
        assert (
            transport.send(request(), idempotency_key="two", deadline_seconds=210).failure_code
            == "cooldown_exceeds_remaining_time"
        )
        assert len(wire.calls) == 1 and clock.now == 1000


def test_runner_waits_long_server_delay_outside_network_deadline(tmp_path):
    from pixelgym.grounding.v5.reliable_memory import CooldownExecutor

    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        wire, clock = (
            Wire(
                [
                    reply(code=429, content="", cost="0", tokens=0, headers={"retry-after": "600"}),
                    reply(),
                ]
            ),
            Clock(),
        )
        _, transport = make(journal, wire, clock, phase_deadline=6400)
        transport.send(request(), idempotency_key="first", deadline_seconds=210)
        executor = CooldownExecutor(transport)
        result = executor.execute(
            lambda: transport.send(request(), idempotency_key="retry", deadline_seconds=210),
            timeout_seconds=210,
        )
        assert not result.timed_out and result.value.status == "response"
        assert clock.now == 1600 and wire.calls[-1][1]["timeout"] == 180


def test_phase_dollars_cannot_reset_when_transport_is_recreated(tmp_path):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        wire, clock = Wire([reply(cost="0.01")]), Clock()
        _, first = make(journal, wire, clock, phase_spend_limit=Decimal("0.04"))
        first.send(request(), idempotency_key="first", deadline_seconds=210)
        _, again = make(journal, wire, clock, phase_spend_limit=Decimal("0.04"))
        assert again.phase_start_accounted == 0
        assert (
            again.send(request(10), idempotency_key="too_large", deadline_seconds=210).failure_code
            == "phase_cap_stop"
        )
        assert len(wire.calls) == 1


def test_malformed_model_action_is_kept_without_retry(tmp_path):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        wire = Wire([reply(content="not an action")])
        _, transport = make(journal, wire, Clock())
        policy = ReliableMemoryPolicy(CONFIG, retain_screenshots=False)
        runner = ReliableMemoryRunner(
            journal=journal,
            policy=policy,
            manifest=build_reliable_manifest(
                ROOT, config=CONFIG, code_revision="test", retain_screenshots=False
            ),
            transport=transport,
            approved_caps=CallCaps(100, 100, 0, 100),
        )
        result = runner.run(
            trial_id="invalid", task=generate_memory_task(5112), backend=FocusMemoryBackend()
        )
        assert result.classification == "invalid_output" and len(wire.calls) == 1
        assert not any(e.kind == "dispatch_committed" for e in journal.events("invalid"))


def test_diagnostic_large_history_recovers_without_repeating_prefix(tmp_path):
    from pixelgym.grounding.v5.reliable_diagnostic import diagnostic_jobs, run_condition

    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        wire = Wire([reply(code=429, content="", cost="0", tokens=0), reply()])
        ledger, transport = make(journal, wire, Clock())
        job = next(
            j
            for j in diagnostic_jobs("fixture")
            if j["case"]["state_name"] == "memory_7" and j["mode"] == "history"
        )
        row = run_condition(
            journal,
            job=job,
            transport=transport,
            revision="test",
            plan_digest="sha256:" + "a" * 64,
            caps=CallCaps(100, 100, 0, 100),
        )
        assert row["action_dispatched"] and row["model_attempts"] == 2
        assert len({body for body, _ in wire.calls}) == 1
        assert request_bound(json.loads(wire.calls[0][0]), CONFIG)["images"] > 10
        assert (
            len(
                [e for e in journal.events(job["trial_id"]) if e.kind == "reliable_prefix_prepared"]
            )
            == 1
        )
        with pytest.raises(ValueError, match="cannot be restarted"):
            run_condition(
                journal,
                job=job,
                transport=transport,
                revision="test",
                plan_digest="sha256:" + "a" * 64,
                caps=CallCaps(100, 100, 0, 100),
            )
        assert ledger.wire_requests_sent == 2


@pytest.mark.parametrize("receipt", [reply(code=429), reply(status=401), reply(exit_code=60)])
def test_substantive_output_and_security_errors_are_not_retried(tmp_path, receipt):
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        wire = Wire([receipt])
        _, transport = make(journal, wire, Clock())
        assert (
            transport.send(request(), idempotency_key="one", deadline_seconds=210).status
            == "policy_violation"
        )
        assert len(wire.calls) == 1


@pytest.mark.parametrize("mode", ["history", "stateless"])
@pytest.mark.parametrize("recover", [False, True])
def test_runner_retries_same_observation_and_dispatches_once(tmp_path, mode, recover):
    faults = [WireReceipt(0, b"", exit_code=56), reply(code=429, content="", cost="0", tokens=0)]
    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        wire, clock = Wire([*faults, reply() if recover else faults[0]]), Clock()
        ledger, transport = make(journal, wire, clock)
        policy = ReliableMemoryPolicy(CONFIG, retain_screenshots=mode == "history")
        runner = ReliableMemoryRunner(
            journal=journal,
            policy=policy,
            manifest=build_reliable_manifest(
                ROOT, config=CONFIG, code_revision="test", retain_screenshots=mode == "history"
            ),
            transport=transport,
            approved_caps=CallCaps(100, 100, 0, 100),
        )
        # Interrupt after the first GUI action, so only this request's recovery is exercised.
        from pixelgym.grounding.v5.runner import InjectedInterruption

        def boundary(name):
            if name == "dispatch_committed":
                raise InjectedInterruption(name)

        runner.boundary = boundary
        if recover:
            with pytest.raises(InjectedInterruption):
                runner.run(
                    trial_id=mode, task=generate_memory_task(5112), backend=FocusMemoryBackend()
                )
        else:
            result = runner.run(
                trial_id=mode, task=generate_memory_task(5112), backend=FocusMemoryBackend()
            )
            assert result.classification == "infrastructure_failure"
        assert len(wire.calls) == 3 and len({c[0] for c in wire.calls}) == 1
        assert len({c[1]["headers"]["Idempotency-Key"] for c in wire.calls}) == 3
        assert sum(e.kind == "dispatch_committed" for e in journal.events(mode)) == int(recover)
        assert ledger.in_flight_reservation_usd == 0
        assert clock.now == 1020


def test_abandoned_worker_is_killed_retired_and_cannot_overlap(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class Blocking(Wire):
        def perform(self, body, **kwargs):
            self.idle = False
            self.calls.append(body)
            entered.set()
            assert release.wait(5)
            self.idle = True
            return reply()

        def abort(self):
            self.aborted = True
            release.set()

    with closing(V5AttemptJournal(tmp_path / "journal.sqlite")) as journal:
        wire = Blocking([])
        ledger, transport = make(journal, wire, Clock())
        worker = threading.Thread(
            target=lambda: transport.send(request(), idempotency_key="late", deadline_seconds=210)
        )
        worker.start()
        try:
            assert entered.wait(5)
            assert (
                transport.send(request(), idempotency_key="busy", deadline_seconds=210).failure_code
                == "transport_busy"
            )
            transport.settle_unknown_spend(idempotency_key="late")
        finally:
            release.set()
            worker.join(5)
        assert not worker.is_alive() and wire.aborted and transport.retired
        assert (
            transport.send(request(), idempotency_key="after", deadline_seconds=210).failure_code
            == "transport_retired"
        )
        assert len(wire.calls) == 1 and ledger.spent_usd == Decimal("0.001")
        assert ledger.unknown_reservation_usd == 0
        assert journal.event(f"spend/{content_digest('late')}/charged")
