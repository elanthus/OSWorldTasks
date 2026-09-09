# Evidence note for revision 4c4a7fb2fcb983e81084542365abc0ec2d7df538

Recorded 2026-09-09 on local branch `evidence/d412-4c4a7fb2` from a clean detached
checkout of the `origin/main` head. The command layout, redaction scheme, and step
numbering follow the committed August 2026 runs. No provider, network model, or paid
calls were made at any step.

## Deviations from the August 2026 recording recipe

- `commands/16-platform-sleep-scan.json` uses a Python scan with the same pattern and
  exit semantics as the earlier `rg` invocation; `rg` is not installed on the recording host.
- `commands/23-playwright-chromium.json` additionally installs `hypothesis==6.167.1`, the
  dev-extra pin, because `tests/conftest.py` imports it at this revision.
- `commands/31-mechanical-boundaries.json` selects
  `test_deploy_failure_preserves_active_and_repeated_rollback_refuses_bad_source`, the
  successor introduced by PR #131 to the test named in the August runs.
- `commands/29-compose-browser.json` exited 0: the recording host allowed loopback port
  reservation, so the first fresh-stack attempt passed. The August runs recorded a
  sandbox `PermissionError` at this step before their retry passed.
- `commands/15-boundary-inventory.json` records no provider credentials present before
  sanitization; none were set in the recording environment.

## Re-recorded steps

- `16-platform-sleep-scan` was re-recorded once because the first scan wrote a bare
  newline where the generator requires empty no-match output.
- `40-redaction-scan` was re-recorded once because the preliminary scan file it reads had
  not been written (a wrong function signature in the recording script). Both reruns
  observed the same repository and evidence state; no other record was replaced.

## Generator changes landed with this evidence

`scripts/generate_d412_evidence_report.py` now keys the first Compose attempt's expected
status, the exact pytest skipped/warning counts, and the renamed mechanical test on the
evidence revision. The four August revisions keep their previously frozen expectations.

## Static rows

Checklist row 14 and the demo-bundle portions of rows 5, 6, 8, 9, and 12 index the
D4.11 rehearsal export from revision `fa4f414db15bf4a9f46dfcf0781828d2bf78afe9`
(2026-08-17), unchanged by this run. `d411-human-confirmation.json` is the same record
carried by every committed run.

## Owner decisions recorded

- 2026-09-09: the project owner chose to record this run with the generator adapted per
  revision rather than reproducing the August sandbox failure.
- 2026-09-09: the project owner approved checking this evidence in.

This note is a post-generation annotation, added after `REPORT.md`, `evidence-manifest.json`,
and `redaction-scan.json` were produced. It is therefore outside the stored final redaction
scan and manifest, matching how the unit tests treat `EVIDENCE-NOTE.md`.

The D4.12 milestone verdict remains `null` and belongs to the project owner.
