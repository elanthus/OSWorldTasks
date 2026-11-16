# Chromium reset determinism v1

Raw evidence of resetting the real vendor-form page in local Playwright Chromium, without the
OSWorld guest. The [generated report](REPORT.md) is produced from the two JSON files by
`scripts/generate_chromium_reset_determinism_report.py`; the recorder is
`scripts/run_chromium_reset_determinism.py`.

| File | Host | Recorded at revision | How it got here |
| --- | --- | --- | --- |
| `macos-arm64.json`, `frames-macos-arm64/` | macOS 27 arm64, local | `26a88f98…`, clean worktree | recorded locally on the PR branch |
| `linux-ci.json`, `frames-linux-ci/` | GitHub Actions `ubuntu-latest` x86_64 | `343eccfa…`, GitHub's merge commit of PR #285 into `main` | CI run 43794152455, job "Browser integration (Chromium)", artifact `chromium-reset-determinism-linux`, checked in unchanged |

The Linux file records its frames directory as `chromium-reset-determinism/frames-linux-ci`, the
path in the CI workspace. The report generator looks for a frames directory of that name beside
the evidence file, so the file is checked in byte for byte as downloaded.

What this does not cover: the OSWorld guest's Chrome, the guest bundle, and the guest's display
stack. The only stored guest bitwise reset evidence remains
[five resets of seed 7 on bundle `6a3fd2bd…`](../day-2-rev-2026-09-06-issues-95-101/raw/real-reset.json);
a guest driver for the current bundle exists (`scripts/osworld_reset_determinism.py`) but has not
been run. Perceptual similarity is not computed.
