"""Fast checks for the frozen integration-only public-action trajectory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pixelgym.tasks.vendor_form import generator
from scripts.osworld_golden_trajectory import verify_fixture

FIXTURE = Path("tests/integration/fixtures/osworld_golden_trajectory_seed7.json")


def test_integration_golden_fixture_replays_blind_with_exact_reward_timing():
    fixture = verify_fixture(FIXTURE)

    assert fixture["action_count"] == len(fixture["actions"])
    assert fixture["fake_backend_timeline"][-1] == {
        "step": fixture["action_count"],
        "action_type": 2,
        "reward": 1.0,
        "terminated": True,
        "truncated": False,
    }


def test_integration_golden_fixture_identity_matches_canonical_seed_7_spec():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    record = generator.generate_task(7)
    body = {key: value for key, value in record.items() if key != "task_id"}

    assert fixture["task_id"] == record["task_id"]
    assert (
        fixture["task_spec_sha256"]
        == hashlib.sha256(generator.canonical_json(body).encode("utf-8")).hexdigest()
    )
