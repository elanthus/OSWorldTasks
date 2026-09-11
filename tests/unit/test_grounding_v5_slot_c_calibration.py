"""Full Slot C planning requires intact reviewed smoke evidence, without paid calls."""

import json
import shutil
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import slot_c
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.panel_policy import MISTRAL_STATEFUL_CALIBRATION
from pixelgym.grounding.v5.slot_c import build_slot_c_plan, verify_mistral_smoke_review

ROOT = Path(__file__).resolve().parents[2]
PLAN = 'artifacts/grounding-v5-slot-c-mistral-smoke-plan.json'
REVIEW = 'plans/slot-c-mistral-smoke-review.json'
SUMMARY = 'artifacts/review-fixture/summary.json'
JOURNAL = 'artifacts/review-fixture/attempts.sqlite'


@pytest.fixture
def reviewed_root(tmp_path):
    # The receipt validator checks the provenance of an already-reviewed journal;
    # journal content validation itself is covered by the journal/runner suites.
    for name in ('pixelgym', 'pyproject.toml', 'requirements'):
        (tmp_path / name).symlink_to(ROOT / name, target_is_directory=(ROOT / name).is_dir())
    manifests = tmp_path / 'artifacts/grounding-v5-manifests/v2'
    manifests.mkdir(parents=True)
    for name in ('development.json', 'calibration-d56.json'):
        shutil.copyfile(ROOT / 'artifacts/grounding-v5-manifests/v2' / name, manifests / name)
    smoke = build_slot_c_plan(
        tmp_path, code_revision='a' * 40, candidate='mistral', phase='smoke',
        maximum_spend_usd='0.45', output_directory='artifacts/review-fixture',
    )
    (tmp_path / PLAN).write_text(json.dumps(smoke.raw))
    (tmp_path / SUMMARY).parent.mkdir(parents=True)
    summary = {
        'approved_plan_sha256': smoke.digest, 'code_revision': smoke.code_revision,
        'classifications': {'pilot_action_limit': 10}, 'completed_all_assigned_pairs': True,
        'execution_error': None,
        'provider_accounting': {'provider_calls_made': 20, 'unknown_charge_outcomes': 0},
        'spend': {'blocked': False, 'unknown_reservation_usd': '0'},
        'cleanup': {'journal_closed': True, 'provider_adapter_closed': True},
    }
    (tmp_path / SUMMARY).write_text(json.dumps(summary))
    (tmp_path / JOURNAL).write_bytes(b'fixture for an already-audited journal hash')
    review = {
        'summary_validation': 'valid', 'original_files_unchanged': True,
        'approved_plan_sha256': smoke.digest, 'source_code_revision': smoke.code_revision,
        'reconstructed_requests_matching_journal': 20, 'validated_responses': 20,
        'episodes_with_verified_history_carryover': 10,
        'source_file_sha256': {
            name: 'sha256:' + sha256((tmp_path / name).read_bytes()).hexdigest()
            for name in (PLAN, SUMMARY, JOURNAL)
        },
    }
    (tmp_path / REVIEW).parent.mkdir()
    (tmp_path / REVIEW).write_text(json.dumps(review))
    return tmp_path


def rewrite_reviewed_file(root, name, value):
    path = root / name
    path.write_text(json.dumps(value))
    review = json.loads((root / REVIEW).read_text())
    review['source_file_sha256'][name] = 'sha256:' + sha256(path.read_bytes()).hexdigest()
    (root / REVIEW).write_text(json.dumps(review))


def test_full_plan_uses_all_frozen_tasks_and_binds_review(reviewed_root):
    p = build_slot_c_plan(
        reviewed_root, code_revision='b' * 40, candidate='mistral', phase='calibration',
        maximum_spend_usd='2.00', output_directory='artifacts/calibration-fixture',
    )
    assert len(p.assignments) == 50
    assert p.manifest_path.name == 'calibration-d56.json'
    assert {a.slot for a in p.assignments} == {MISTRAL_STATEFUL_CALIBRATION.slot}
    manifest = json.loads((reviewed_root / p.manifest_path).read_text())
    assert [(a.task_id, a.action_limit) for a in p.assignments] == [
        (r['task_id'], r['max_episode_steps']) for r in manifest['records']
    ]
    assert p.budgets.caps.environment_action_cap == 1431
    assert p.budgets.caps.model_attempt_cap == 1431
    assert p.budgets.caps.provider_wire_request_cap == 1431
    assert p.budgets.caps.provider_control_request_cap == 0
    assert p.retry_breaker.max_bounded_retries_per_action == 0
    assert p.outputs.resume_mode == 'forbid'
    digest = content_digest(json.loads((reviewed_root / REVIEW).read_text()))
    assert any(digest in stop for stop in p.stop_conditions)


@pytest.mark.parametrize('name', [PLAN, SUMMARY, JOURNAL])
def test_review_rejects_changed_source_bytes(reviewed_root, name):
    with (reviewed_root / name).open('ab') as f:
        f.write(b' ')
    with pytest.raises(ValueError, match='source hash mismatch'):
        verify_mistral_smoke_review(reviewed_root)


def test_calibration_refuses_missing_review(reviewed_root):
    (reviewed_root / REVIEW).unlink()
    with pytest.raises(FileNotFoundError):
        build_slot_c_plan(
            reviewed_root, code_revision='b' * 40, candidate='mistral', phase='calibration',
            maximum_spend_usd='2', output_directory='artifacts/calibration-fixture',
        )


@pytest.mark.parametrize('field,value', [
    ('classifications', {'infrastructure_failure': 1}),
    ('completed_all_assigned_pairs', False),
    ('provider_accounting', {'provider_calls_made': 19, 'unknown_charge_outcomes': 0}),
    ('spend', {'blocked': True, 'unknown_reservation_usd': '0.02'}),
    ('cleanup', {'journal_closed': False, 'provider_adapter_closed': True}),
    ('approved_plan_sha256', 'sha256:' + '0' * 64),
])
def test_review_rejects_incomplete_or_mismatched_outcome(reviewed_root, field, value):
    summary = json.loads((reviewed_root / SUMMARY).read_text())
    summary[field] = value
    rewrite_reviewed_file(reviewed_root, SUMMARY, summary)
    with pytest.raises(ValueError, match='complete compatibility'):
        verify_mistral_smoke_review(reviewed_root)


def test_review_must_bind_the_journal(reviewed_root):
    review = json.loads((reviewed_root / REVIEW).read_text())
    del review['source_file_sha256'][JOURNAL]
    (reviewed_root / REVIEW).write_text(json.dumps(review))
    with pytest.raises(ValueError, match='bind plan, summary, and journal'):
        verify_mistral_smoke_review(reviewed_root)


def test_changed_policy_requires_another_smoke(reviewed_root, monkeypatch):
    monkeypatch.setattr(
        slot_c, 'MISTRAL_STATEFUL_SMOKE', replace(slot_c.MISTRAL_STATEFUL_SMOKE, temperature=1),
    )
    with pytest.raises(ValueError, match='policy contract differs'):
        verify_mistral_smoke_review(reviewed_root)
