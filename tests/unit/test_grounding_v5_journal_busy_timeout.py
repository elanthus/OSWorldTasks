"""Two real SQLite connections contend without network access or sleeps."""

import threading
from concurrent.futures import ThreadPoolExecutor

from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps
from pixelgym.grounding.v5.journal import WRITER_BUSY_TIMEOUT_SECONDS, V5AttemptJournal


def test_writer_busy_timeout_is_explicit_and_waits_for_a_held_lock(tmp_path):
    first = V5AttemptJournal(tmp_path / "shared.sqlite")
    second = V5AttemptJournal(tmp_path / "shared.sqlite")
    attempting = threading.Event()
    second._connection.set_trace_callback(
        lambda sql: attempting.set() if sql == "BEGIN IMMEDIATE" else None
    )
    try:
        assert first._connection.execute("PRAGMA busy_timeout").fetchone()[0] == 60000
        assert second._connection.execute("PRAGMA busy_timeout").fetchone()[0] == (
            WRITER_BUSY_TIMEOUT_SECONDS * 1000
        )
        with ThreadPoolExecutor(1) as pool:
            with first._write_transaction():
                future = pool.submit(
                    second.reserve_attempt_started,
                    AttemptIdentity("second", 0, 0),
                    provider_endpoint_identity="scripted",
                    request_digest="sha256:" + "1" * 64,
                    idempotency_key="second",
                    model_attempt_reservation=1,
                    control_request_reservation=0,
                    pre_call_checkpoint=b"{}",
                    approved_caps=CallCaps(1, 1, 0, 1),
                )
                assert attempting.wait(timeout=5)
                assert not future.done()
            assert future.result(timeout=5)[0].trial_id == "second"
        assert first.events()[0].trial_id == "second"
    finally:
        second.close()
        first.close()
