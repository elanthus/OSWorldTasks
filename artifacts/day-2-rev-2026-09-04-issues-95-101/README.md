# Day 2 evidence revision — issues #95 and #101

This directory is a new, immutable evidence revision. It does not replace the historical
`artifacts/day-2` evidence. The automated report's status is not a D2.11 human verdict;
`validation-report.json` deliberately contains `human_gate: null`.

The real guest used local Docker, the pinned OSWorld-V2 guest artifact, and no cloud or paid
provider. `raw/browser-boundary.json` records the exact Chromium renderer contract, the app-mode
fallback decision, the structural window state, the pixel anchors, and provider closure. The
failed kiosk attempts and retained kiosk screenshot are indexed by
`raw/presentation-mode-selection.json`.

The real reset records report raw differing-pixel counts and maximum per-channel deltas before
the separately named SSIM metric. No mask or tolerance was applied.

## Commands

```text
.venv/bin/python scripts/validate_vendor_form_browser_boundary.py --output artifacts/day-2-rev-2026-09-04-issues-95-101/raw/browser-boundary-local.json
.venv/bin/python -m pixelgym.validation.browser_boundary --local-evidence artifacts/day-2-rev-2026-09-04-issues-95-101/raw/browser-boundary-local.json --output artifacts/day-2-rev-2026-09-04-issues-95-101/raw/browser-boundary.json --screenshot artifacts/day-2-rev-2026-09-04-issues-95-101/screenshots/guest-app.png --guest-image .cache/osworld/osworld-v2-ubuntu-x86.qcow2 --stop-loss-seconds 5400
.venv/bin/python scripts/validate_day2.py fake --raw-dir artifacts/day-2-rev-2026-09-04-issues-95-101/raw --report-json artifacts/day-2-rev-2026-09-04-issues-95-101/validation-report.json
.venv/bin/python -c "import signal,sys; signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('real-reset validation exceeded 5400 seconds'))); signal.alarm(5400); from scripts.validate_day2 import main; raise SystemExit(main(sys.argv[1:]))" real-resets --raw-dir artifacts/day-2-rev-2026-09-04-issues-95-101/raw --report-json artifacts/day-2-rev-2026-09-04-issues-95-101/validation-report.json --guest-image .cache/osworld/osworld-v2-ubuntu-x86.qcow2
.venv/bin/python -c "import signal,sys; signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('real-space smoke exceeded 5400 seconds'))); signal.alarm(5400); from scripts.osworld_space_smoke import main; raise SystemExit(main(sys.argv[1:]))" --guest-image .cache/osworld/osworld-v2-ubuntu-x86.qcow2 --output artifacts/day-2-rev-2026-09-04-issues-95-101/raw/real-space-smoke.json
.venv/bin/python -c "import signal,sys; signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('real golden episode exceeded 5400 seconds'))); signal.alarm(5400); from scripts.osworld_golden_trajectory import main; raise SystemExit(main(sys.argv[1:]))" record --fixture tests/integration/fixtures/osworld_golden_trajectory_seed7.json --guest-image .cache/osworld/osworld-v2-ubuntu-x86.qcow2 --output-dir artifacts/day-2-rev-2026-09-04-issues-95-101/real-golden
.venv/bin/python scripts/validate_day2.py audit --raw-dir artifacts/day-2-rev-2026-09-04-issues-95-101/raw --report-json artifacts/day-2-rev-2026-09-04-issues-95-101/validation-report.json
.venv/bin/python scripts/validate_day2.py assemble --raw-dir artifacts/day-2-rev-2026-09-04-issues-95-101/raw --report-json artifacts/day-2-rev-2026-09-04-issues-95-101/validation-report.json
```
