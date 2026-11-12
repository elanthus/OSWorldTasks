# OSWorld guest check, 2026-11-11

This is one run on the pinned OSWorld-V2 guest (`v2026.06.24`, provider `docker-local`, 1024x768,
no KVM). It ran during the work on the fake/browser parity and reward-timing fix. Read
[`provenance.json`](provenance.json) before citing it.

**Revision.** The run used commit `627e09b` with a clean worktree. A later commit in the same
pull request (`ded68da`) changed the guest form server and page script, so the merged guest
bundle differs from the one exercised here. The recorded bundle digest equals the digest rebuilt
at `627e09b`. This run is not evidence for the merged code.

**What ran.** [`driver.py`](driver.py) did two things:

1. It replayed the OSWorld golden trajectory
   (`tests/integration/fixtures/osworld_golden_trajectory_seed7.json`) on the guest.
2. It ran seven short input sequences that the code review had flagged (caret movement, label
   clicks, Tab into the radio group, keyboard selection), each on the guest and on the fake
   backend, and compared the submitted values.

Every result below is read from [`results.json`](results.json) and
[`real-golden-episode.json`](real-golden-episode.json):

- **Golden trajectory:** 112 steps. The only positive reward came at step 112, the terminal step.
  Step 111 had every field correct but no submission yet, and its reward was 0.
- **Seven sequences:** in all seven the guest's submitted values equalled the fake's. Every step
  read its input acknowledgement in one poll, and no step paid a reward.
- **Run:** about 49 minutes in total, of which the golden replay took about 13. The provider
  closed afterwards.

**Limits.** This is one run of seven hand-picked sequences, not a randomized differential test.
The frame images are listed in [`images.manifest.json`](images.manifest.json) but are not
checked in.
