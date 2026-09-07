# Day 2 evidence revision — 2026-09-06, issues #95 and #101

This is a new evidence revision against the current source after merging `origin/main`. It does not
rewrite `artifacts/day-2/` or the exhausted 2026-09-04 revision. The automated report's status is
not a D2.11 human verdict. The repository owner re-graded D2.11 from these raw artifacts on
2026-09-07 and declared `PASS`; the record is `raw/human-gate.json` and is embedded in
`validation-report.json` as `human_gate` by the `assemble` step.

The local Docker run used the pinned OSWorld-V2 guest artifact, no cloud provider, and no paid model
calls. The seven serialized real-guest commands, including one failed combined-evidence attempt,
consumed 2,204.14 seconds of the authorized 5,400-second allocation; every attempt records provider
closure. The browser probe records the app-mode fallback selected after the earlier kiosk-first
attempts failed to preserve the required full-frame 1024x768 observation.
`raw/presentation-mode-selection.json` links the retained kiosk evidence from the superseded first
allocation and the current-source app-mode evidence.

The current Playwright and guest launches record renderer contract
`sha256:70b4931f4af8ce4aae562c34d70992c34e51f4aa9e9f05ff084a65717b82eba5`.
The five real-reset frames were byte-identical PNGs. The retained `04b` guest frame was captured
before page initialization completed: its request-card values and dynamic form labels were empty.
That 113,094-pixel difference with maximum per-channel delta 255 was a readiness race, not a
renderer or cross-run stability observation. The launch now requires two consecutive
`GET /api/page-ready` responses equal to `{"ready": true}`; the evidence probe independently checks
that state after reset and captures a fresh stable frame. The final `04c` guest frame is
byte-identical to all five reset frames: 0 differing pixels, maximum per-channel delta 0, and SSIM
1.0. No mask or tolerance was applied.

After review widened `GUEST_SOURCE_PATHS` to cover `app.js`, `index.html`, `style.css`, and the
viewport schema, the guest probe was re-emitted as `commands/04b-guest-browser-boundary.txt` under
the remaining allocation. Both the original and re-emitted command records are retained. The
re-emitted record passed all eight navigation-boundary checks and records provider closure.
The first refreshed audit then failed closed because the local Playwright record also depended on
the changed validator source. Its restricted failure and successful approved rerun are retained as
`commands/02b-validate-browser-boundary-local.txt` and
`commands/03b-validate-browser-boundary-local-unsandboxed.txt`; the failed and successful audit
attempts are likewise retained as `commands/08b-validate-day2-audit-stale-local.txt` and
`commands/08c-validate-day2-audit.txt`.

The `04b` frame is retained as `screenshots/guest-app-04b-uninitialised.png`. After the readiness
fix, the first `04c` guest attempt passed all nine guest checks and closed its provider, but the
combined command failed closed because the local Playwright evidence had become stale with the
validator source. The restricted local refresh failure, approved local refresh, failed combined
attempt, and final successful guest command are retained separately. The final audit records 14/14
evidence-backed surfaces; both browser-navigation surfaces remain blocked rather than known
limitations. Commands 05, 06, and 07 were not rerun because neither audit nor assembly reported
their stored records stale.

The first restricted local Playwright attempt failed because macOS denied browser process control;
its redacted transcript is retained. The identical command succeeded through the normal approval
path. The 112-action integration fixture remained valid under blind fake replay and its real episode;
it was not replaced by the separate 110-action coordinate-based unit fixture.

Fake-backend reset frames are not retained because they can be regenerated in seconds without
Docker. Run `.venv/bin/python scripts/validate_day2.py fake --raw-dir <scratch>/raw --report-json
<scratch>/validation-report.json`, which writes `<scratch>/screenshots/fake-resets/`; those frames
must match the SHA-256 entries in `raw/fake-reset-frames.sha256` (compare the manifest with
`(cd <scratch> && sha256sum screenshots/fake-resets/*.png)`).

## Commands

Each transcript in `commands/` begins with the exact command and exit status and ends with
`/usr/bin/time -p` wall-time output. Commands ran in this order:

1. `.venv/bin/python scripts/validate_day2.py fake --raw-dir artifacts/day-2-rev-2026-09-06-issues-95-101/raw --report-json artifacts/day-2-rev-2026-09-06-issues-95-101/validation-report.json`
2. `.venv/bin/python scripts/validate_vendor_form_browser_boundary.py --output artifacts/day-2-rev-2026-09-06-issues-95-101/raw/browser-boundary-local.json` (restricted attempt, then normal approval path)
3. `.venv/bin/python -m pixelgym.validation.browser_boundary --local-evidence artifacts/day-2-rev-2026-09-06-issues-95-101/raw/browser-boundary-local.json --output artifacts/day-2-rev-2026-09-06-issues-95-101/raw/browser-boundary.json --screenshot artifacts/day-2-rev-2026-09-06-issues-95-101/screenshots/guest-app.png --guest-image .cache/osworld/osworld-v2-ubuntu-x86.qcow2 --stop-loss-seconds 5400`
4. `.venv/bin/python -c "<SIGALRM wrapper: 5198 seconds>" real-resets --raw-dir artifacts/day-2-rev-2026-09-06-issues-95-101/raw --report-json artifacts/day-2-rev-2026-09-06-issues-95-101/validation-report.json --guest-image .cache/osworld/osworld-v2-ubuntu-x86.qcow2`
5. `.venv/bin/python -c "<SIGALRM wrapper: 4268 seconds>" --guest-image .cache/osworld/osworld-v2-ubuntu-x86.qcow2 --output artifacts/day-2-rev-2026-09-06-issues-95-101/raw/real-space-smoke.json`
6. `.venv/bin/python -c "<SIGALRM wrapper: 4052 seconds>" record --fixture tests/integration/fixtures/osworld_golden_trajectory_seed7.json --guest-image .cache/osworld/osworld-v2-ubuntu-x86.qcow2 --output-dir artifacts/day-2-rev-2026-09-06-issues-95-101/real-golden`
7. `.venv/bin/python scripts/validate_day2.py audit --raw-dir artifacts/day-2-rev-2026-09-06-issues-95-101/raw --report-json artifacts/day-2-rev-2026-09-06-issues-95-101/validation-report.json`
8. `.venv/bin/python scripts/validate_day2.py assemble --raw-dir artifacts/day-2-rev-2026-09-06-issues-95-101/raw --report-json artifacts/day-2-rev-2026-09-06-issues-95-101/validation-report.json`
9. The same `assemble` command after recording `raw/provider-stop-loss.json`, so the report embeds
   that stored allocation record.
10. The guest navigation command in item 3 was repeated after widening the guest source pins, with
    `--stop-loss-seconds 3712`; its transcript is retained as
    `commands/04b-guest-browser-boundary.txt`.
11. The local browser-boundary command in item 2 was repeated after its source hash became stale,
    first in the restricted sandbox and then on the approved process-control path.
12. The audit retained its fail-closed stale-local attempt and successful retry, followed by one
    final assembly in `commands/09b-validate-day2-assemble.txt`.
13. After the readiness fix, the first guest attempt used the remaining 3,533-second limit. Its
    provider closed and all nine guest checks passed, but combination failed on stale local evidence;
    the transcript is `commands/04c-guest-browser-boundary-attempt-1-stale-local.txt`.
14. The local browser-boundary command was rerun in the restricted sandbox and then through the
    approved process-control path; transcripts are
    `commands/02c-validate-browser-boundary-local-restricted.txt` and
    `commands/03c-validate-browser-boundary-local-unsandboxed.txt`.
15. The final guest probe used the remaining 3,365-second limit and is retained as
    `commands/04c-guest-browser-boundary.txt`.
16. The audit was regenerated as `commands/08d-validate-day2-audit.txt`.
17. The report was reassembled after cumulative stop-loss accounting as
    `commands/09c-validate-day2-assemble.txt`.

## Scope notes

The README Limitations change in this branch is presented for owner review and is not approved public
wording. Historical `artifacts/day-2/raw/reward-timing.json`,
`artifacts/day-2/raw/browser-boundary.json`, `artifacts/validation-report.json`, and the frozen
grounding captures describe earlier source or renderer conditions; they were not modified. The
grounding captures remain explicitly marked as superseded-source artifacts pending a separately
authorized versioned recapture.
