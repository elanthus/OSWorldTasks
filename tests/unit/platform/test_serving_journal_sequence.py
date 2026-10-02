"""Pin the serving journal event sequence across the V5Runner composition refactor.

The fixture was generated at revision 3314ccb (when ``_ServingTransaction`` still
subclassed ``V5Runner``) by running exactly the scenarios below.  Each entry is the
event key, kind, and SHA-256 of the canonical payload, in journal order.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import canonical_json_bytes, sha256_bytes
from pixelgym.grounding.v5.runner import InjectedInterruption, ScriptedTransport, V5Runner
from pixelgym.platform.serving_episode import ServingEpisodeHost
from tests.unit.platform.test_serving_episode_host import EPISODE_ID, _host, _result

FIXTURE = Path(__file__).parent / "fixtures" / "serving_journal_sequence_3314ccb.json"


def _sequence(host: ServingEpisodeHost) -> list[list[str]]:
    return [
        [event.event_key, event.kind, sha256_bytes(canonical_json_bytes(event.payload))]
        for event in host.journal.events(EPISODE_ID)
    ]


def _expected() -> dict[str, list[list[str]]]:
    return json.loads(FIXTURE.read_text())


def test_scripted_episode_journal_matches_pre_refactor_sequence(tmp_path: Path) -> None:
    host = _host(tmp_path)
    host.create_episode(task_instruction="Complete the form", client_episode_ref="client-1")
    first = host.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    host.act(
        episode_id=EPISODE_ID,
        screenshot=b"screen-1",
        previous_intent_id=first.intent_id,
        previous_result=_result(b"screen-1"),
    )
    assert _sequence(host) == _expected()["plain"]


@pytest.mark.parametrize("boundary", ["parsed_action_candidate", "intent_issued"])
def test_recovered_episode_journal_matches_pre_refactor_sequence(
    tmp_path: Path, boundary: str
) -> None:
    transport = ScriptedTransport()
    interrupted = _host(tmp_path, transport=transport, interrupt_after=boundary)
    interrupted.create_episode(
        task_instruction="Complete the form", client_episode_ref="client-1"
    )
    with pytest.raises(InjectedInterruption, match=boundary):
        interrupted.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    recovered = _host(tmp_path, transport=transport)
    recovered.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    assert _sequence(recovered) == _expected()[boundary]


def test_serving_dispatch_is_composed_not_inherited() -> None:
    from pixelgym.platform import serving_episode

    for value in vars(serving_episode).values():
        if isinstance(value, type) and value is not V5Runner:
            assert not issubclass(value, V5Runner), value
