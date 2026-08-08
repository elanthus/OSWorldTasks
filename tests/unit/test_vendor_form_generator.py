"""Determinism tests for the seeded vendor-onboarding task generator (D1.3)."""

import json

from pixelgym.tasks.vendor_form import generator


def _body(task: dict) -> dict:
    return {key: value for key, value in task.items() if key != "task_id"}


def test_same_seed_produces_byte_identical_canonical_json():
    task_a = generator.generate_task(7)
    task_b = generator.generate_task(7)

    assert generator.canonical_json(_body(task_a)) == generator.canonical_json(_body(task_b))
    assert task_a["task_id"] == task_b["task_id"]


def test_different_seed_changes_task():
    task_a = generator.generate_task(7)
    task_b = generator.generate_task(8)

    assert task_a["task_id"] != task_b["task_id"]
    assert task_a["fields"] != task_b["fields"]


def test_canonical_json_round_trips_and_is_deterministic():
    task = generator.generate_task(42)
    body = _body(task)

    first = generator.canonical_json(body)
    second = generator.canonical_json(body)

    assert first == second
    assert json.loads(first) == body


def test_task_id_derives_from_canonical_spec_hash():
    task = generator.generate_task(3)
    body = _body(task)

    assert task["task_id"] == generator.task_id_for(body)


def test_generated_fields_cover_required_controls():
    task = generator.generate_task(1)
    fields = task["fields"]

    assert set(fields) == set(generator.FIELD_NAMES)
    assert isinstance(fields["expedited_onboarding"], bool)
    assert fields["country"] in task["options"]["country"]
    assert fields["payment_terms"] in task["options"]["payment_terms"]
