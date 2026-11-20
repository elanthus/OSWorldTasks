# OSWorld guest check on the merged code, 2026-11-12

This repeats the [2026-11-11 guest check](../osworld-guest-check-2026-11-11/README.md) on merged
`main` (`d4e711e`, clean worktree). It uses the pinned OSWorld-V2 guest (`v2026.06.24`, provider
`docker-local`, 1024x768, no KVM). The earlier run used commit `627e09b`, before `ded68da` changed
the guest form server and page script. This run's recorded guest bundle (`505a15bf…`) is the one
rebuilt at `d4e711e`. Read [`provenance.json`](provenance.json) before citing it.

**Attempts.** All three attempts are kept, the two incomplete ones included.

1. [`attempt-1`](attempt-1/results.json) ran the 2026-11-11 driver unchanged.
   - Six of the seven review sequences ran and matched the fake.
   - The golden replay and "caret click near left edge" did not run. Their VMs missed OSWorld's
     fixed 300 s readiness wait.
2. [`attempt-2`](attempt-2/results.json) retried those two steps and ran nothing. Docker Desktop
   was quit from its own window as the attempt started.
3. [`attempt-3`](attempt-3/results.json) retried the same two steps after Docker Desktop was
   restarted, and both ran.

The retries used [`driver_subset.py`](driver_subset.py). It is the 2026-11-11 driver with an
option to run only some steps.

**Results on the merged code.** Each result is read from the attempt files named in it:

- **Golden trajectory** (attempt 3): 112 steps. The only positive reward came at step 112, the
  terminal step. Step 111 had every field correct but no submission yet, and its reward was 0.
- **Seven review sequences** (six in attempt 1, one in attempt 3): in every one, the guest's
  submitted values equalled the fake's. Every step read its input acknowledgement in one poll,
  and no step paid a reward.
- **Provider:** closed after every attempt. Infrastructure time was about 61 minutes in total.

**Limits.** These are seven hand-picked sequences, not a randomized differential test. The
complete result comes from two attempts at the same revision, not from one uninterrupted run. The
frame images are listed in [`attempt-3/images.manifest.json`](attempt-3/images.manifest.json) but
are not checked in.
