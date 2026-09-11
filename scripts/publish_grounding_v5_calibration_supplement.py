"""Reproduce response-free calibration results without journals or provider calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from pixelgym.grounding.v5.plan import CalibrationPlan
from scripts.publish_grounding_v5_d56_completed_calibrations import _redaction_is_safe

DIRECTORY = Path('artifacts/grounding-v5-calibration-supplement')


def build(directory: Path) -> tuple[dict, str]:
    sources = json.loads((directory / 'sources.json').read_text())
    rows = []
    runs = []
    task_sets = []
    for name in ('mistral.json', 'qwen-pair.json'):
        raw = (directory / name).read_bytes()
        if 'sha256:' + hashlib.sha256(raw).hexdigest() != sources['snapshot_sha256'][name]:
            raise ValueError('snapshot digest mismatch')
        snapshot = json.loads(raw)
        if not _redaction_is_safe(snapshot, {}):
            raise ValueError('unsafe publication content')
        plan = CalibrationPlan.from_dict(snapshot['plan'])
        summary = snapshot['summary_without_transport_records']
        if summary['approved_plan_sha256'] != plan.digest:
            raise ValueError('plan binding mismatch')
        if summary['code_revision'] != plan.code_revision:
            raise ValueError('revision mismatch')
        if not summary['completed_all_assigned_pairs'] or summary['execution_error'] is not None:
            raise ValueError('incomplete calibration')
        assignments = {(a.slot, a.task_id): a for a in plan.assignments}
        episodes = summary['episode_results']
        if len(episodes) != len(assignments) or {(e['slot'], e['task_id']) for e in episodes} != set(assignments):
            raise ValueError('assignment denominator mismatch')
        if dict(Counter(e['classification'] for e in episodes)) != summary['classifications']:
            raise ValueError('classification count mismatch')
        if sum(e['success'] for e in episodes) != summary['successful_policy_task_pairs']:
            raise ValueError('success count mismatch')
        spend = summary['spend']
        accounted = Decimal(spend['known_spend_usd']) + Decimal(spend['unknown_reservation_usd'])
        if accounted != Decimal(spend['budget_accounted_spend_usd']) or accounted > plan.budgets.maximum_spend_usd:
            raise ValueError('spend accounting mismatch')
        for policy in plan.policies:
            items = [dict(e, family=assignments[(e['slot'], e['task_id'])].family) for e in episodes if e['slot'] == policy.slot]
            task_sets.append({e['task_id'] for e in items})
            rows.append({'slot': policy.slot, 'assigned': len(items), 'success': sum(e['success'] for e in items),
                         'classifications': dict(Counter(e['classification'] for e in items)), 'items': items,
                         'policy_manifest_digest': policy.policy_manifest_digest})
        runs.append({'source': name, 'plan_sha256': plan.digest, 'code_revision': plan.code_revision,
                     'spend': spend, 'provider_accounting': summary['provider_accounting'],
                     'journal_integrity': summary['journal_integrity'], 'cleanup': summary['cleanup']})
    if any(ids != task_sets[0] or len(ids) != 50 for ids in task_sets):
        raise ValueError('task panels differ')
    result = {'schema_version': 'pixelgym-calibration-supplement-v1', 'provider_calls_made': 0,
              'rows': rows, 'runs': runs, 'source_snapshot_sha256': sources['snapshot_sha256']}
    lines = ['# Completed calibration supplement', '',
             'Generated from committed response-free snapshots. Calibration evidence only; no confirmatory score or human-gate verdict.', '',
             '| Policy | Tasks | Success | Action-limit failures |', '|---|---:|---:|---:|']
    for row in rows:
        lines.append(f"| `{row['slot']}` | {row['assigned']} | {row['success']} | {row['classifications'].get('step_limit_truncation', 0)} |")
    qwen_rows = [row for row in rows if row['slot'].startswith('qwen-controlled-')]
    memory_note = (' Both Qwen arms scored zero, so this comparison does not establish a memory benefit.'
                   if len(qwen_rows) == 2 and all(row['success'] == 0 for row in qwen_rows) else '')
    lines += ['', f'All {len(rows)} published policies attempted the same fifty task IDs. The Qwen arms are a matched memory comparison.{memory_note} Mistral is a separate model/provider/runtime run and is not a controlled comparison with Qwen or historical Gemini.', '',
              '## Spend and reliability', '', '| Run | Calls | Unknown outcomes | Known USD | Reserved USD | Accounted USD |', '|---|---:|---:|---:|---:|---:|']
    for run in runs:
        a, s = run['provider_accounting'], run['spend']
        lines.append(f"| {run['source']} | {a['provider_calls_made']} | {a['unknown_charge_outcomes']} | {s['known_spend_usd']} | {s['unknown_reservation_usd']} | {s['budget_accounted_spend_usd']} |")
    lines += ['', 'Spend for Qwen covers its published arms; it is not a per-arm estimate. Unknown outcomes above are counts reported by the source summaries. Transport rows are excluded, so this public verifier does not establish which failures recovered or exhausted retries. Older stopped runs are not pooled into these results.', '',
              '## Provenance and reproduction', '',
              'The snapshots retain exact approved plans, summaries with transport rows removed, per-task outcomes, and selected local audit receipts. Their original-source hashes bind the restricted originals. The public verifier checks snapshot hashes, plan identity, allocation, classifications, spend, and deterministic report generation. It does not independently repeat the journal audit or verify source files absent from a public clone.', '',
              'Restricted journals, raw responses, screenshots, checkpoints, credentials, and operator paths are excluded. Historical Gemini/Qwen evidence remains in the [earlier report](../grounding-v5-d56-completed-calibrations-report.md).', '',
              'From the repository root:', '', '```sh', '.venv/bin/python -m scripts.publish_grounding_v5_calibration_supplement --verify', '```', '',
              'The local audit receipts report immutable source bytes and completed journal validation. Selected local diagnostic receipts are retained as audit data, not independently recomputed by this public verifier. No new paid calls are authorized by publication.']
    return result, '\n'.join(lines) + '\n'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = root / DIRECTORY
    result, report = build(directory)
    outputs = {'results.json': json.dumps(result, indent=2, sort_keys=True) + '\n', 'report.md': report}
    for name, text in outputs.items():
        path = directory / name
        if args.verify:
            if path.read_text() != text:
                raise ValueError(f'generated artifact differs: {name}')
        else:
            path.write_text(text)
    print(json.dumps({'verified': True, 'policies': len(result['rows']), 'provider_calls_made': 0}))


if __name__ == '__main__':
    main()
