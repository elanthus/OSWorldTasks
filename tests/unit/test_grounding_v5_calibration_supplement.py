"""Publication checks work using only response-free committed snapshots."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.publish_grounding_v5_calibration_supplement import DIRECTORY, build

ROOT = Path(__file__).resolve().parents[2]


def test_committed_supplement_reproduces_without_original_journals(tmp_path):
    shutil.copytree(ROOT / DIRECTORY, tmp_path / 'evidence')
    result, report = build(tmp_path / 'evidence')
    assert [(r['assigned'], r['success']) for r in result['rows']] == [(50, 0)] * 3
    assert report == (ROOT / DIRECTORY / 'report.md').read_text()
    assert result == json.loads((ROOT / DIRECTORY / 'results.json').read_text())


@pytest.mark.parametrize('mutation,rehash,message', [
    ('whitespace', False, 'snapshot digest'),
    ('missing_episode', True, 'denominator'),
    ('spend', True, 'spend accounting'),
    ('plan_binding', True, 'plan binding'),
    ('private_path', True, 'unsafe publication'),
    ('raw_response', True, 'unsafe publication'),
])
def test_rejects_tampering_and_unsafe_content(tmp_path, mutation, rehash, message):
    shutil.copytree(ROOT / DIRECTORY, tmp_path / 'evidence')
    directory = tmp_path / 'evidence'
    path = directory / 'mistral.json'
    value = json.loads(path.read_text())
    summary = value['summary_without_transport_records']
    if mutation == 'missing_episode':
        summary['episode_results'].pop()
    elif mutation == 'spend':
        summary['spend']['known_spend_usd'] = '100'
    elif mutation == 'plan_binding':
        summary['approved_plan_sha256'] = 'sha256:' + '0' * 64
    elif mutation == 'private_path':
        value['operator'] = '/Users/private/example'
    elif mutation == 'raw_response':
        value['raw_response'] = 'forbidden provider body'
    path.write_text(json.dumps(value) + '\n')
    if rehash:
        source_path = directory / 'sources.json'
        sources = json.loads(source_path.read_text())
        sources['snapshot_sha256']['mistral.json'] = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
        source_path.write_text(json.dumps(sources))
    with pytest.raises(ValueError, match=message):
        build(directory)
