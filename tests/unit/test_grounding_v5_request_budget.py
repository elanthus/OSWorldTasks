"""Request-sized holds, price routing and durable unknown-hold revisions."""

import copy
import io
import json
import urllib.error
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.request_budget import (
    ReboundedMemoryLedger,
    RequestBoundTransport,
    request_bound,
)
from pixelgym.grounding.v5.screenshot_memory import ScreenshotMemoryPolicy
from scripts.run_grounding_v5_gemini38_calibration import config_from_snapshot

ROOT = Path(__file__).parents[2]
SNAPSHOT = json.loads(
    (ROOT / "artifacts/grounding-v5-d58-gemini38-calibration/price-recheck.json").read_text()
)
CONFIG = config_from_snapshot(SNAPSHOT)


def request(images=1):
    policy = ScreenshotMemoryPolicy(CONFIG, retain_screenshots=False)
    result = policy.build_request(policy.reset("Complete the workflow."), bytes(1024 * 768 * 3))
    picture = next(part for part in result["messages"][1]["content"] if part["type"] == "image_url")
    result["messages"][1]["content"] += [copy.deepcopy(picture) for _ in range(images - 1)]
    return result


def test_bounded_images_do_not_change_request_and_prices_use_selected_route():
    body = request(32)
    before = copy.deepcopy(body)
    bound = request_bound(body, CONFIG)
    assert body == before
    assert bound["images"] == 32
    assert Decimal(bound["request_maximum_usd"]) < CONFIG.request_maximum_usd < Decimal("0.14")
    assert CONFIG.model == "google/gemini-3.8-flash"
    assert CONFIG.prompt_price_per_token_usd == Decimal("0.00000075")
    assert CONFIG.completion_price_per_token_usd == Decimal("0.00000375")
    assert request_bound(request(1), CONFIG)["input_units_bound"] < bound["input_units_bound"]


@pytest.mark.parametrize(
    "mutation", ["too_many_images", "tools", "larger_output", "oversized_text", "remote_image"]
)
def test_unsupported_request_cannot_reach_wire(tmp_path, mutation):
    body = request(33 if mutation == "too_many_images" else 1)
    if mutation == "tools":
        body["tools"] = []
    elif mutation == "larger_output":
        body["max_tokens"] = 10000
    elif mutation == "oversized_text":
        body["messages"][0]["content"] = "x" * 200000
    elif mutation == "remote_image":
        body["messages"][1]["content"][1]["image_url"]["url"] = "https://example.com/a.png"
    calls = []
    with closing(V5AttemptJournal(tmp_path / "ledger.sqlite")) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        transport = RequestBoundTransport(
            CONFIG,
            ledger=ledger,
            environment={"OPENROUTER_API_KEY": "fixture"},
            urlopen=lambda *a, **kw: calls.append(a),
        )
        result = transport.send(body, idempotency_key="bad", deadline_seconds=210)
        assert result.status == "pre_send_failure"
        assert not calls and ledger.wire_requests_sent == 0


def test_unknown_hold_uses_exact_request_bound_and_replays(tmp_path):
    body = request(10)
    cost_bound = Decimal(request_bound(body, CONFIG)["request_maximum_usd"])
    path = tmp_path / "ledger.sqlite"

    def failure(*args, **kwargs):
        raise urllib.error.URLError("fixture transport failure")

    with closing(V5AttemptJournal(path)) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        transport = RequestBoundTransport(
            CONFIG, ledger=ledger, environment={"OPENROUTER_API_KEY": "fixture"}, urlopen=failure
        )
        result = transport.send(body, idempotency_key="unknown", deadline_seconds=210)
        assert result.status == "transport_fault"
        assert transport.config == CONFIG
        assert ledger.unknown_reservation_usd == cost_bound
        assert ledger.spent_usd == 0
    with closing(V5AttemptJournal(path)) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        assert ledger.unknown_reservation_usd == cost_bound
        assert ledger.record_cost("unknown", Decimal("0.01"), cost_bound)
        assert ledger.unknown_reservation_usd == 0


def test_hold_revision_is_idempotent_and_later_charge_replays(tmp_path):
    path = tmp_path / "ledger.sqlite"
    proof = {
        "reservation_id": content_digest("prior"),
        "old_bound_usd": "1.44",
        "new_bound_usd": "0.10",
    }
    with closing(V5AttemptJournal(path)) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        assert ledger.reserve_wire("prior", Decimal("1.44"))
        ledger.reserve_unknown_charge("prior", Decimal("1.44"))
        prefix = journal.integrity_report()
        ledger.revise_unknown_bound(proof, plan_digest="frozen")
        count = len(journal.events())
        ledger.revise_unknown_bound(proof, plan_digest="frozen")
        assert len(journal.events()) == count
        assert ledger.unknown_reservation_usd == Decimal("0.10")
        assert ledger.spent_usd == 0 and ledger.unknown_charge_outcomes == 1
        assert prefix["event_count"] + 1 == count
        with pytest.raises(ValueError, match="different approval"):
            ledger.revise_unknown_bound(proof, plan_digest="changed")
    with closing(V5AttemptJournal(path)) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        assert ledger.unknown_reservation_usd == Decimal("0.10")
        assert ledger.record_cost("prior", Decimal("0.005"), Decimal("1.44"))
    with closing(V5AttemptJournal(path)) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        assert ledger.unknown_reservation_usd == 0 and ledger.spent_usd == Decimal("0.005")


def test_price_bound_violation_blocks_further_spend(tmp_path):
    body = request()
    response = {
        "id": "fixture",
        "model": CONFIG.model,
        "provider": "Google",
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {"cost": 1},
    }
    with closing(V5AttemptJournal(tmp_path / "ledger.sqlite")) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        transport = RequestBoundTransport(
            CONFIG,
            ledger=ledger,
            environment={"OPENROUTER_API_KEY": "fixture"},
            urlopen=lambda *a, **kw: io.BytesIO(json.dumps(response).encode()),
        )
        transport.send(body, idempotency_key="overpriced", deadline_seconds=210)
        assert ledger.blocked and ledger.spent_usd == Decimal(1)
        assert not ledger.reserve_wire("another", Decimal("0.01"))


def test_gemini38_full_episode_with_request_bound_transport(tmp_path):
    from pixelgym.grounding.v5.contracts import CallCaps
    from pixelgym.grounding.v5.memory_backend import MemoryBackend
    from pixelgym.grounding.v5.memory_calibration import run_episode
    from pixelgym.grounding.v5.policies import _append_golden_stage
    from pixelgym.grounding.v5.screenshot_memory import build_screenshot_policy_manifest

    backend = MemoryBackend()
    backend.reset(5112)
    task = backend.task
    actions = []
    try:
        for stage in task.stages:
            _append_golden_stage(backend, stage, actions)
    finally:
        backend.close()
    pending = iter(actions)

    def respond(wire, **kwargs):
        incoming = json.loads(wire.data)
        assert incoming["model"] == CONFIG.model
        assert incoming["provider"]["max_price"] == {"prompt": 0.75, "completion": 3.75}
        action = dict(next(pending))
        if action["action_type"] == 1:
            action.update(x=int(action["x"] * 1000 / 1024), y=int(action["y"] * 1000 / 768))
        return io.BytesIO(
            json.dumps(
                {
                    "id": "fixture",
                    "model": CONFIG.model,
                    "provider": "Google",
                    "choices": [
                        {"message": {"content": json.dumps(action)}, "finish_reason": "stop"}
                    ],
                    "usage": {"cost": 0.001},
                }
            ).encode()
        )

    with closing(V5AttemptJournal(tmp_path / "ledger.sqlite")) as journal:
        ledger = ReboundedMemoryLedger(Decimal(20), Decimal(0), journal=journal)
        transport = RequestBoundTransport(
            CONFIG, ledger=ledger, environment={"OPENROUTER_API_KEY": "fixture"}, urlopen=respond
        )
        job = {
            "trial_id": "gemini38-test",
            "mode": "history",
            "seed": 5112,
            "task_id": task.task_id,
            "task_digest": content_digest(task.canonical_dict()),
            "action_limit": task.max_episode_steps,
        }
        manifest = build_screenshot_policy_manifest(
            ROOT, config=CONFIG, code_revision="fixture", retain_screenshots=True
        )
        row = run_episode(
            journal,
            job=job,
            manifest=manifest,
            policy=ScreenshotMemoryPolicy(CONFIG, retain_screenshots=True),
            transport=transport,
            ledger=ledger,
            caps=CallCaps(100, 100, 0, 100),
            plan_digest="frozen",
        )
        assert row["success"] and row["reached_both_consumers"]
        assert row["model_attempts"] == ledger.wire_requests_sent == len(actions)
        assert ledger.spent_usd == Decimal("0.001") * len(actions)
        assert ledger.unknown_reservation_usd == 0
        assert len([e for e in journal.events() if e.kind == "request_budget_bound"]) == len(
            actions
        )
