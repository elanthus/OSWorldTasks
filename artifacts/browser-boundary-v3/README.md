# Browser-boundary evidence, local half (v3 folder)

[`browser-boundary-local.json`](browser-boundary-local.json) is new local browser-boundary
evidence (schema `pixelgym-browser-boundary-v2`, validator `vendor-form-browser-boundary`). None of
the source hashes in the earlier stored files still matched the code:
[`day-2/raw/browser-boundary.json`](../day-2/raw/browser-boundary.json) hashes six files and none
matches, and the two `day-2-rev-2026-09-*` files hash eight and one matches. Those files are
unchanged.

- Command: `.venv/bin/python scripts/validate_vendor_form_browser_boundary.py --output artifacts/browser-boundary-v3/browser-boundary-local.json`
- Revision: `2c8d8031bba2ebcd1ab81059b373545a1410c234`, clean tracked worktree (only this folder
  was untracked when it ran).
- Host: Darwin 27.0.0 arm64, Python 3.12.15, Playwright 1.62.0, headless Chromium 151.0.7922.34.

The JSON itself records the browser, its arguments, the renderer contract, the checks, and the
SHA-256 of every source file it covers, so a reader can verify the hashes against any checkout.

**Not covered here.** The guest half (`GUEST_SOURCE_PATHS`, validator
`vendor-form-guest-navigation-boundary`) needs the OSWorld guest and has not been re-recorded for
the current bundle. Until it is, the audit's navigation surfaces stay as they are in the frozen
audit and the 2026-11 addendum.
