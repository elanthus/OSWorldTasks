# Fresh no-call validation for the D5.10 owner review

This supplement adds fresh browser, OSWorld and CLI sandbox observations and a four-copy
restore drill. It preserves the completed parser-sensitivity results and every frozen plan.
The D5.10 verdict remains `null`; the owner decides the checklist and public wording.

Read the [generated raw-command index](REPORT.md), [integration provenance](integration-provenance.json),
[skip investigation](skip-investigation.json), [restore receipt](restore-drill.json), and
[46-file preservation receipt](preserved-inputs.json). The original
[results review guide](../../grounding-v5-d59-action-extraction-results/review-guide.md)
continues to describe the experiment and its statistical limits.

## Fresh observations

All commands below exited 0. Test runtimes are pytest's reported runtimes; the full recorder
runtime and output remain in the linked command records.

| Check | Stored observation | Runtime | Raw record |
|---|---|---:|---|
| Browser | 18 passed, no skips | 136.67 s | [04](commands/04-browser.json) |
| CLI/kernel sandbox | 5 passed, no skips | 75.21 s | [05](commands/05-cli-sandbox.json) |
| OSWorld live reconnect | 1 passed, 6 warnings, no skips | 329.13 s | [06](commands/06-osworld.json) |
| Whole unit suite, clean committed source | 3,294 passed, 6 skipped, 16 warnings | 151.14 s | [11](commands/11-unit-clean-committed-source.json) |
| Restore helper regressions, network/sleep guard | 12 passed | 2.32 s | [20](commands/20-receipt-path-regressions.json) |
| Four-copy evidence restore | 4 exact published projections reproduced | 216.818686 s | [09](commands/09-restore-drill.json) |

The browser observations cover repeatable development golden/recovery traces, fake/browser
geometry and recovery equivalence, focus, fonts, readiness, submission and reset behavior.
OSWorld checks exact live reconnect and rejection of stale state. These are synthetic-form
checks on one Darwin arm64 host. They do not establish portability or full desktop coverage.
OSWorld was pinned to `v2026.06.24`, commit `2b9b7b4eb73243d557bdbf2998fe18d8e18e19c6`,
Python 3.12.15, Docker provider, 1024×768. The selected local image ID was
`sha256:60fe3501e5f5e16c5eaeb75b5b13b843c490891a1f24b86244b06c0c9d32d9f3`.
Each integration record is hash-bound to this provenance in [integration observations](integration-observations.json).
No gated assets were fetched. The Docker provider was closed afterward ([17](commands/17-provider-closed.json)).

CLI tests use a synthetic placeholder token and a proxy that refuses provider targets.
They exercise the real CLI and transport sandbox without provider/model requests or a real
credential. Generic/stateful sandbox tests check allowed fake traffic, denied peer traffic
and credential exclusion. Fresh enforcement evidence does not retroactively sandbox
historical runs that used the recorded exception.

## The six skips

- One optional legacy v4 PNG-byte check: release-hosted PNGs are absent.
- One optional historical Day 2 PNG-byte check: release-hosted PNGs are absent.
- Two decision-record revision checks: D2.11 and D3 pilot name no reviewed commit.
- Two decision-record tag checks: those same records have `tag: null`. The legacy message
  says “tag None not present”; there is no named missing tag to fetch.

No requested integration skipped. No historical metadata was invented and no optional images
were fetched. The raw `-rs` output and per-test reasons are retained in the skip investigation.

## Restore scope and preservation

Pilot and full archives were each restored from both local retained stores, into fresh private
directories. Pilot copies each contained 10 files / 112,061,945 bytes; full copies each contained
11 files / 15,244,025,902 bytes. Every archive manifest, file digest and SQLite integrity check
was verified. Existing auditors then reproduced each exact published projection, including
journal, assignment, accounting, frozen identity/sandbox and extraction-lineage bindings.

Archive capture precedes the final retention receipt. The helper proves the only summary
difference is that published, hash-bound receipt, then places it in a separate audit view.
Actual restored files are not altered. Before/after fingerprints match for all four copies.
Regression tests reject altered outcomes, counts, receipts, final bytes and bindings, plus
unsafe destination paths before any destination write. Four additional CLI tests reject receipt
outputs inside each protected evidence root before creating the destination. The drill ran during helper preparation;
the committed helper additionally validates disjoint, non-symlink roots and separate receipt
outputs before writing.

This is an evidence restore and audit, not task/action replay or provider-usage settlement.
All five full-run confirmed-stopped timeouts retain unknown provider completion and usage.
Their counted same-request retries, all outcomes and denominators remain unchanged. Both stores
and restores occupy the same local disk; no off-host recovery was performed. No source evidence,
retained copy or restored copy was deleted, and no cleanup authorization was issued.

The final no-call candidate verifier reproduced frozen plan digest
`sha256:c24d121809409217f11009308956eef4d0b41f2d4666f468066893409386f4d6`
([14](commands/14-verify-frozen-candidate.json)); the analysis reproduced without writing changes
([15](commands/15-reproduce-sensitivity-analysis.json)). All 46 preserved inputs match their
pre-check hashes. No provider/model request reached a service. No real-credential authentication probe or new
execution approval was used.

## Retained failed validation observations

Two preparation runs are retained in full, rather than overwritten:

- [07](commands/07-unit-skip-investigation.json): 9 failed, 3,277 passed, 6 skipped in 188.82 s.
  Source-provenance tests overlapped helper edits, and the new script initially lacked its
  inventory entry. Both conditions were corrected.
- [10](commands/10-unit-stable-source.json): 64 failed, 3,228 passed, 6 skipped in 136.13 s.
  Tracked README edits triggered the clean-worktree evidence guard. The source was committed
  before the final run; no guard was weakened.

The collection base is `168598c243df24ba4f773cbc5696a1572532f532`. The final unit run used
committed source `f3fdf5f2aff9b4d7eae7556d92ba3596ebc13d98`; final package hashes bind the
subsequent evidence and documentation changes. Records 04–10 used relative cwd `.`; the existing
recorder also redacted standalone periods as `<path-0>`. Later records use absolute cwd.

## Decisions and limits still belonging to the owner

The fresh checks and restore receipts are now available for the checklist review. They do not
resolve the five unknown timeout usages, historical lost Haiku journals, absent historical PNG
bytes/provenance, unspecified secondary estimators or off-host recovery. The results remain
post-hoc same-task parser sensitivity, not independent confirmation or an isolated causal parser
effect. The owner still decides the D5.10 verdict, acceptable limitations and public wording.
