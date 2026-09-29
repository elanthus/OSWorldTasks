"""Journal readers never observe a writer's uncommitted transaction."""

from __future__ import annotations

import threading
from contextlib import closing
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import AttemptIdentity
from pixelgym.grounding.v5.journal import JournalEvent, V5AttemptJournal


def test_event_lookup_waits_for_another_threads_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Rollback(Exception):
        pass

    reader_reached = threading.Event()
    owner = threading.get_ident()
    results: list[JournalEvent | None] = []
    with closing(V5AttemptJournal(tmp_path / "isolation.sqlite")) as journal:
        lock = journal._lock

        class ObservedLock:
            def __enter__(self):
                if threading.get_ident() != owner:
                    reader_reached.set()
                lock.acquire()

            def __exit__(self, *args):
                lock.release()

        monkeypatch.setattr(journal, "_lock", ObservedLock())

        def read() -> None:
            try:
                results.append(journal.event("uncommitted"))
            finally:
                # Also release the writer if a broken reader skips the lock.
                reader_reached.set()

        worker = threading.Thread(target=read)
        try:
            with pytest.raises(Rollback), journal._write_transaction():
                journal._connection.execute(
                    "INSERT INTO events(event_key, kind, trial_id, step_index, payload) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("uncommitted", "fixture", "fixture", 0, b"{}"),
                )
                worker.start()
                assert reader_reached.wait(5)
                raise Rollback
        finally:
            worker.join(5)
        assert not worker.is_alive()
        assert results == [None]  # The other thread must never see the rolled-back row.


@pytest.mark.parametrize(
    "reader", ["events", "terminal_attempt", "integrity_report", "_digest_version"]
)
def test_all_journal_readers_wait_for_transaction_rollback(tmp_path, monkeypatch, reader):
    class Rollback(Exception):
        pass

    reached = threading.Event()
    owner = threading.get_ident()
    results, errors = [], []
    with closing(V5AttemptJournal(tmp_path / "isolation.sqlite")) as journal:
        lock = journal._lock

        class ObservedLock:
            def __enter__(self):
                if threading.get_ident() != owner:
                    reached.set()
                lock.acquire()

            def __exit__(self, *args):
                lock.release()

        monkeypatch.setattr(journal, "_lock", ObservedLock())
        identity = AttemptIdentity("trial", 0, 0)
        query = lambda: getattr(journal, reader)(
            *([identity] if reader == "terminal_attempt" else [])
        )
        baseline = query()

        def read():
            try:
                results.append(query())
            except RuntimeError as error:
                errors.append(error)
            finally:
                reached.set()

        worker = threading.Thread(target=read)
        try:
            with pytest.raises(Rollback), journal._write_transaction():
                journal._connection.execute(
                    "INSERT INTO events(event_key, kind, trial_id, step_index, attempt_index, payload) VALUES (?, ?, ?, ?, ?, ?)",
                    ("uncommitted", "attempt_completed", "trial", 0, 0, b"{}"),
                )
                journal._connection.execute(
                    "INSERT INTO objects VALUES (?, ?, ?)", ("bad-digest", "fixture", b"bad")
                )
                journal._connection.execute("UPDATE journal_metadata SET value='invalid'")
                worker.start()
                assert reached.wait(5)
                raise Rollback
        finally:
            worker.join(5)
        assert not worker.is_alive()
        assert not errors
        assert results == [baseline]
