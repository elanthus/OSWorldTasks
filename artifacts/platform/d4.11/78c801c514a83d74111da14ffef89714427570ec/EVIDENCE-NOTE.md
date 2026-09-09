# Evidence note for the D4.11 rehearsal at 78c801c514a83d74111da14ffef89714427570ec

Recorded 2026-09-09 from a detached `git worktree` of the `origin/main` head with an empty
porcelain status, a fresh `python3.12 -m venv` installed with `pip install -e ".[dev,platform]"`,
and Playwright Chromium. No provider, network model, paid, or external-deployment calls were made.

## Deviations and re-recorded steps (originals retained except where stated)

- `commands/11-compose-ps.json` was re-recorded with a field-filtered table format. The first
  JSON-format capture included the MinIO image's vendor `maintainer` label, which contains an
  email address and would have counted as a redaction finding; the record was overwritten before
  the scan.
- `commands/17-lifecycle-phase1.json` exited 1 after screenshot 04: waiting for MLflow's SPA to
  reach network-idle timed out because the UI polls. `commands/18` resumed from the recorded
  candidate IDs and completed the MLflow capture and the blocked direct deploy of unapproved B
  without resubmitting anything.
- `commands/27-lifecycle-phase4-cancellation-ui.json` exited 1: the cancellation exercise
  resubmitted A's exact request and the control plane deduplicated it (no new submission; one
  `submission.resubmitted` audit event on A). `commands/28` reran the exercise with an unused
  request (prompt v2, baseline model), which created submission C and was cancelled. Both audit
  events are in `demo-audit-events.jsonl`; A's state stayed `Complete`.
- The flow-worker poll inside `commands/29`'s preceding wait loop matched its own scanner
  process; `commands/34` recorded the corrected check (no flow processes).
- Reviewer attribution at this revision is the fixed `synthetic-demo` actor with
  `actor_verification_source: synthetic_demo`, per the loopback attestation in Compose. The
  August bundle's `local-reviewer` identity no longer applies.

## Driver revisions behind each record

`tooling/d411_driver.py` is the final revision. It was edited twice during the rehearsal and
once after PR review; the pre-edit revisions were not preserved as separate files. The exact
differences are:

- **Record 17 (`phase1`)** ran the initial revision: `phase1` ended with an inline MLflow capture
  that called `page.wait_for_load_state("networkidle")` and then saved state; there was no
  `phase1b` function and no `"1b"` dispatch entry. The recorded traceback shows that revision's
  line numbers.
- **Records 18, 21, 24, 27** ran the second revision: the MLflow capture and the blocked-deploy
  check moved into `phase1b` (called from `phase1` and dispatchable as `"1b"`), the wait became
  `wait_for_load_state("load")` plus a fixed 6 s pause, and `phase1b` recovers identifiers from
  `driver-log.jsonl` when `driver-state.json` has none.
- **Record 28** ran the third revision: `phase4` submits prompt version `"2"` with the baseline
  model instead of `"1"`.
- **Post-review edits** (not executed): Ruff import ordering and unused-variable renames, and
  the `phase1b` log recovery now ignores unrecognised models and keeps the first occurrence of
  each phase-1 identifier.

No other lines changed between revisions. Records 22, 23, 25, 26 used `scripts/capture_platform_api.py`
at the recorded git revision, unmodified.

## What was not done

- `tests/integration/platform/test_compose_lifecycle.py` was not run as part of this rehearsal;
  the lifecycle was driven by `tooling/d411_driver.py` against the documented wrapper stack.
- No video was recorded; screenshots and the timed script stand in.
- No public wording, README numbers, or gate checklist boxes were changed.

The four D4.11 confirmations and the D4.12 verdict belong to the project owner.

## Post-PR adjustments (CI on PR #177)

- `commands/09-compose-up.json`: the Docker build log named the image's ephemeral pip wheel cache
  under the container's `/tmp`. That string was replaced with `<container-pip-ephem-wheel-cache>`
  after recording and the replacement is declared in the record's `post_recording_redaction`
  field. It was a container-internal path, not a host path; the record's exit status and all other
  output are unchanged.
- `tooling/*.py`: import ordering fixed and four unused unpacked variables renamed with a leading
  underscore so the repository Ruff configuration passes. Behaviour is unchanged; these copies are
  for reproduction and were not re-executed.
