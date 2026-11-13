# OSWorld guest check at `248097d`, 2026-11-12

This is not the [guest check on merged `main`](../osworld-guest-check-2026-11-12/README.md) from the
same day; that one ran revision `d4e711e`. One run of [`scripts/osworld_guest_check.py`](../../scripts/osworld_guest_check.py) on the pinned
OSWorld-V2 guest (`v2026.06.24`, provider `docker-local`, 1024x768, no KVM). The run began at
`2026-11-12T22:38:30Z` (`provenance.run_started_utc` in [`results.json`](results.json)), from
commit `248097d` with a clean worktree. It exercises that revision's guest bundle: input contract
`pixelgym-vendor-form-input-v3`, setup with no acknowledged input, and a pointer park that does not
click.

**Files.**

- [`results.json`](results.json): everything the driver recorded (resets, golden replay, input
  sequences, oracle frame/action pairs, and each provider close). Read the values there; this note
  restates none of them.
- [`raw/real-golden-episode.json`](raw/real-golden-episode.json): the golden replay's step trace,
  written by `scripts/osworld_golden_trajectory.py`.
- [`real-golden/contact-sheet.png`](real-golden/contact-sheet.png): the golden replay's contact
  sheet, at the path the episode file names. The frame PNGs are listed in
  [`images.manifest.json`](images.manifest.json) but are not checked in.
- [`park-output-supplement.json`](park-output-supplement.json): the pointer-park output, copied from
  the host setup cache after the run.
- [`redaction.json`](redaction.json): the local paths removed before check-in.

**Recording defect.** The driver at `248097d` read the pointer-park output from `desktop-env/`, while
OSWorld writes setup output to `desktop-env/<task id>/`, so every `resets.records[].pointer_park`
value in `results.json` is null. That field is left as recorded. Setup itself reads the same files
and raises unless the reported position is the park target, so a reset that returned had passed
that check. The supplement holds the files left on the host, which are only the last park output
per task id; its `collection_method` states this. The driver was fixed in `5d3e6d5`.

**Redaction.** Three fields in `results.json` and one in `raw/real-golden-episode.json` held absolute
paths under the operator's home directory. They were replaced with repository-relative paths by
exact string replacement before check-in. `redaction.json` lists each changed field and the
SHA-256 of each file before and after. The original files are not checked in.

**Relocation.** The run wrote to `artifacts/osworld-guest-check-2026-11-12/`. PR #280 then merged a
different run into that folder name, so this run moved here. The recorded artifact paths in
`raw/real-golden-episode.json` were rewritten to the new folder, after the redaction. The
`relocation` section of `redaction.json` gives that file's SHA-256 before and after.

**Limits.** This is one run on one host. The input sequences are hand-picked, not a randomized
differential test, and the oracle pairs cover one seed.
