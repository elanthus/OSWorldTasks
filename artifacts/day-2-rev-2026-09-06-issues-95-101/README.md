# Day 2 evidence revision — 2026-09-06, issues #95 and #101

This is a new evidence revision against the current source after merging `origin/main`. It does not
rewrite `artifacts/day-2/` or the exhausted 2026-09-04 revision. The automated report's status is
not a D2.11 human verdict; `validation-report.json` retains `human_gate: null`, and the repository
owner re-grades D2.11 from these raw artifacts.

The local Docker run used the pinned OSWorld-V2 guest artifact, no cloud provider, and no paid model
calls. All four serialized real-guest commands completed within 1,687.20 seconds of the authorized
5,400-second allocation, and each stored result records provider closure. The browser probe records
the app-mode fallback selected after the earlier kiosk-first attempts failed to preserve the required
full-frame 1024x768 observation. `raw/presentation-mode-selection.json` links the retained kiosk
evidence from the superseded first allocation and the current-source app-mode evidence.

The current Playwright and guest launches record renderer contract
`sha256:70b4931f4af8ce4aae562c34d70992c34e51f4aa9e9f05ff084a65717b82eba5`.
The five real-reset frames were byte-identical PNGs. The guest navigation frame re-emitted after the
source-pin review differed from each reset frame by a raw 113,094 pixels with maximum per-channel
delta 255 before separately naming SSIM 0.8362671387854251. No mask or tolerance was applied.

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

The first restricted local Playwright attempt failed because macOS denied browser process control;
its redacted transcript is retained. The identical command succeeded through the normal approval
path. The 112-action integration fixture remained valid under blind fake replay and its real episode;
it was not replaced by the separate 110-action coordinate-based unit fixture.

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

## Scope notes

The README Limitations change in this branch is presented for owner review and is not approved public
wording. Historical `artifacts/day-2/raw/reward-timing.json`,
`artifacts/day-2/raw/browser-boundary.json`, `artifacts/validation-report.json`, and the frozen
grounding captures describe earlier source or renderer conditions; they were not modified. The
grounding captures remain explicitly marked as superseded-source artifacts pending a separately
authorized versioned recapture.
