# D5.8 review corrections

These corrections address PR #196's CodeRabbit review without replacing any frozen calibration
bundle, changing task difficulty, or authorizing another model call.

The full offline suite also exposed a concurrent journal-read failure in the existing timeout
test. Keyed event reads now take the journal's transaction lock, so timeout settlement and a late
response cannot query the shared connection during one another's writes. A coordinated two-thread
regression demonstrates that a reader cannot observe a row rolled back by another thread; it
failed before the fix. This changes no journal schema or stored evidence.

| Finding | Correction | Verification |
|---|---|---|
| Missing checkpoint fields could raise `KeyError`; restore ignored an injected task factory. | Validate the fields used by memory-choice restoration first, then use the backend's configured factory. | Missing-field fixtures preserve backend state; a checkpoint with custom task controls restores exactly. |
| A task could exceed the history arm's 32-frame capacity. | Reject an assigned action limit above 32 before reset or model dispatch in both arms. | A valid 50-step task is rejected in both modes before reset, journal events, or requests; a 32-step assignment passes preflight. |
| An admission or policy manifest could hash dirty source while claiming a clean revision. | Reject staged and unstaged tracked changes before hashing; recheck source, worktree, and HEAD before publishing admission evidence. | Real temporary Git repositories cover staged changes, unstaged changes, and permitted untracked output. |
| The pilot analysis's `provider_calls: 0` was ambiguous. | Publish [analysis v2](../artifacts/grounding-v5-d58-review-corrections/pilot-analysis-v2.json) with 20 wire requests, zero provider control requests, and zero calls made by the analysis process. | Counts come from the stored pilot summary. The original analysis and its hashes remain intact. |
| Verification assertions disappear under `python -O`. | Use the [successor verifier](../scripts/verify_grounding_v5_full_calibration.py), whose checks raise explicit errors. | Optimized-Python tests reject changed file hashes, inconsistent reports, and an incorrect private-journal amendment. |
| Report denominators assumed 24 tasks and 48 choices. | The admission builder records task and choice counts; the renderer uses those counts. A [summary correction](../artifacts/grounding-v5-d58-review-corrections/admission-summary-v2.json) derives historical counts from stored rows. | A smaller fixture with unequal task and choice counts verifies the rendered denominators. |

## Verify the corrections

From the repository root:

```sh
.venv/bin/python -m scripts.prepare_grounding_v5_review_corrections --verify
.venv/bin/python -O -m scripts.verify_grounding_v5_full_calibration
```

Both commands read stored evidence and make no provider calls. The
[correction record](../artifacts/grounding-v5-d58-review-corrections/correction.json) binds the
original inputs and successor scripts. The new full-calibration verifier checks public hashes and
report consistency by default. Its optional `--journal` audit additionally requires the original
runtime and private journal at that phase's closure; the current, extended aggregate journal
cannot reproduce an earlier closure. The verifier never rewrites a receipt.

The original verifier inside `grounding-v5-d58-full-calibration` is retained only for historical
reproduction. Use the successor for verification, including under Python optimization. Existing
runtime manifests and admission source hashes still describe their recorded revisions. Current
source changes produce different manifest identities and require fresh admission before any
future execution; they do not retroactively alter the policies used for calibration.

The final sample-size and spend-cap decisions remain open as recorded in the
[final-design package](grounding-v5-d58-final-design.md). These repairs generate no confirmatory
tasks and add no paid observations.

## Second review: closed evidence and interruption recovery

The second review's corrections preserve the original artifact files and their manifests.
Use these successors for new verification:

- `scripts.verify_d58_at_revision` copies the complete D5.8 artifact family, including historical
  predecessor packages. It removes `PYTHONOPTIMIZE` from analyzer environments, uses the corrected
  owner-budget and reliable-continuation analyzers, and continues to support standalone owner
  reconciliation through its recorded `driver_code_revision` and `--verify-reconciliation`.
- `scripts.verify_d58_owner_budget_continuation` and `scripts.verify_d58_reliable_continuation`
  replace every verification assertion with an explicit exception and require an exact manifest
  file set. Their journal checks still require the executed source and original journal.
- `scripts.verify_d58_gemini38_calibration` replaces the frozen analyzer for future verification
  or recording. Recording refuses an existing `analysis.json`, `verification.json`, or `files.json`
  before writing anything. The historical analyzer remains reproduction-only.
- The [corrected power report](../artifacts/grounding-v5-d58-review-corrections-v2/power-report-v2.md)
  derives its numerical prose from stored `power.json`, including the smallest listed option that
  meets the upper-sensitivity target. Reliability episode counts appear per option. Verify it with
  `.venv/bin/python -m scripts.prepare_grounding_v5_power_report --verify`. This correction changes
  neither the calculation nor the owner's outstanding decisions.

Journal event lists, terminal lookups, digest-version reads, and the complete integrity report now
share the transaction lock. The completed episode row is durable before the publication interruption
boundary, preserving budget and time stops on recovery without replay. The focus diagnostic records
measured renderer comparisons, enforces them under optimized Python, and reports an escaping exception
as an interruption. The reliable diagnostic binds the declared phase cap to the transport and stops
when that cap is reached.

Regression fixtures cover transaction rollback, durable stop recovery, changed report inputs,
partial recordings, extra/missing/corrupt manifest files, optimized-Python corruption rejection,
renderer measurements, interrupted phase publication, phase-cap enforcement, and historical
artifact dependencies. These are offline repairs; no new paid execution or milestone verdict is
included. Current source identities require fresh admission before future execution.
