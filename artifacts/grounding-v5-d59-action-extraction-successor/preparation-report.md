# Prepared D5.9 extraction successor

Owner approval is pending. The original replication plan, results and D5.10 evidence
are unchanged. No model call, live login, live subscription probe, or environment
action replay was performed during this task.

- Candidate digest: `sha256:c24d121809409217f11009308956eef4d0b41f2d4666f468066893409386f4d6`
- Bound implementation: `67b28a4073a8e26e4bd873f748929ccba11ded54`
- History policy: `policy-9f3e13279cacb221eb47`
- Stateless policy: `policy-20215e0a99ac9dfd59f8`
- Execution disabled; subscription execution unauthorized; approved attempt/wire caps zero.
- Proposed pilot: 4 assignments, 104 actions, 208 attempts / 208 wires, zero controls,
  6 active hours. Proposed full phase, separately approved after pilot: 432 assignments,
  12,134 actions, 24,268 attempts / 24,268 wires, zero controls, 168 active hours.
- Only drained owner-login waits pause the clock. Model, prompt, strict runtime identity,
  sandbox enforcement, retries and stop rule retain their registered values.

`plan-field-changes.json` lists all 56 changed JSON-pointer fields and value digests.
The candidate is a post-hoc same-task parser-sensitivity study; independent
confirmation is explicitly false. The D5.10 decision remains human-owned.

## Offline evidence

`offline-diagnostic.json` matches the original audited v2 journal chain and counts,
checks canonical-response hashes, and examines all 8,759 retained responses. All
8,572 previously valid actions remain unchanged. Of 187 terminal parse failures,
186 have a schema/bounds-valid fenced body; the conservative adapter accepts 185,
rejecting the bracketed outside content and the remaining schema failure. This does
not establish task success. The 157 original step limits and unknown timeout remain
in the original evidence.

The diagnostic opens SQLite read-only and reports unchanged input size/mtime. It
publishes response hashes and decisions, never raw prose or action coordinates.
See [the protocol explanation](../../docs/d59-action-extraction-successor.md) for
exact rejection rules, trial identities of the two retained failures, and limits.

## Validation actually run

Final targeted validation, exit 0: **278 passed in 12.78 seconds**. This command loads
a guard that rejects socket bind/connect/DNS operations and `time.sleep`:

```sh
.venv/bin/python -m pytest -q -p tests.support.d59_offline_guard \
  tests/unit/test_grounding_v5_action_extraction.py \
  tests/unit/test_grounding_v5_extraction_successor.py \
  tests/unit/test_grounding_v5_replication_driver.py \
  tests/unit/test_grounding_v5_claude_code_policy.py \
  tests/unit/test_grounding_v5_d59_replication.py \
  tests/unit/test_grounding_v5_cli_memory_retry.py \
  tests/unit/test_grounding_v5_replication_stop_options.py \
  tests/unit/test_grounding_v5_auth_transport.py \
  tests/unit/test_scripts_readme_inventory.py
```

Also executed successfully:

- `python -m mypy pixelgym`: no issues in 155 source files.
- `python -m ruff check .`: all checks passed.
- `python -m ruff format --check pixelgym flows scripts tests`: 454 files formatted.
- `python -m scripts.prepare_grounding_v5_d59_extraction --source-revision 67b28a4073a8e26e4bd873f748929ccba11ded54 --verify`:
  exact generated artifacts reproduced, execution disabled and provider calls zero.
- `python -m scripts.diagnose_grounding_v5_d59_action_extraction --journal <retained-full-journal>`:
  zero provider calls and the response counts above; no private path is published.
- `git diff --check`: exit 0. Original plan/results/gate and the other agent's
  documentation paths have no changes in this worktree.

An earlier broad selection included existing loopback-proxy tests; those were
excluded from final guarded validation. No provider request was sent. That run also
exposed a scheduling race in an existing drain test. Its fixture now releases the
in-flight assignment only after observing the stopping result; production campaign
scheduling is unchanged. Early new-test failures were corrected before the final run.

## Files and remaining work

Implementation: `action_extraction.py`, `d59_extraction_successor.py`, the default
policy's decoder hook, and the shared campaign driver's explicit `--plan` selection.
Scripts: offline diagnostic and no-call successor preparer, plus script inventory.
Tests: parser, receipt, identity/sandbox fixtures, plan/approval/inventory/diagnostic
regressions, offline guard, and the existing drain fixture synchronization.
Documentation: the dedicated protocol document and this separate artifact directory.
The concurrent current-results analysis has not been edited.

Remaining: owner review/approval of this exact successor and any later phase
execution; fresh no-call live identity/sandbox checks at that time; future pilot and
separately authorized full run. No task-success benefit or human milestone verdict
is claimed by this preparation.
