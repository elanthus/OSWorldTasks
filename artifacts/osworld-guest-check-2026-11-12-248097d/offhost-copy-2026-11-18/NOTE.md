# Off-host copy of the golden-replay frames, 2026-11-18 (UTC)

This note records a copy of this run's 114 PNGs outside the host disk and outside git. It changes
no result. The run's files, including [`../README.md`](../README.md), are left as checked in; that
README says the frame PNGs "are not checked in", which is still true.

## What was copied, and where

The 114 files listed in [`../images.manifest.json`](../images.manifest.json) (113 frames and the
contact sheet, 7,312,308 bytes) were copied to the owner's private Backblaze B2 bucket `OSWorld`,
under the prefix `osworld-guest-check-2026-11-12-248097d/`, with the manifest's relative paths.
It is the bucket that holds the
[D5.9 campaign archives](../../grounding-v5-d510/290d3b626cd5f67e5ede1dd2eed086d06dd18b2f/REVIEW-NOTE.md).

The source was the one local copy of the frames then known, in a git-ignored folder of a
worktree. It was checked against the manifest by size and SHA-256 immediately before the upload
(114 of 114). The upload used `rclone copy --checksum --immutable` (rclone 1.76.0), followed by
`rclone check --one-way`. Neither command's output was stored as a file, so the recorded evidence
below starts at the read-back.

The owner then approved a governance-mode retention of one year, applied per file with
`b2_update_file_retention`. The first pass was interrupted after 69 files; a second pass gave the
remaining 45 the same retain-until time. That output was not stored either.

## Recorded evidence

Each row is a raw command record under [`commands/`](commands/), written by
[`scripts/record_gate_command.py`](../../../scripts/record_gate_command.py). Records 00 to 04 were
written at revision `1f62145e` ([00](commands/00-git-revision.json)). Records 05 to 07 were added
during review of the pull request, with the checkout at `6b1c75f2`
([05](commands/05-git-revision-at-review.json)).

| Observation | Stored result | Raw record |
|---|---|---|
| Bucket state under the prefix, read back through [`scripts/observe_b2_object_lock.py`](../../../scripts/observe_b2_object_lock.py) | private bucket, Object Lock enabled, 114 files, 7,312,308 bytes, every file governance mode, one retain-until time, 2027-11-18T04:15:08Z | [02](commands/02-object-lock-readback.json), also stored as [`object-lock-observation.json`](object-lock-observation.json) |
| Download of the prefix into a new directory whose path both records write out | 114 new files, exit status 0 | [06](commands/06-download-to-named-directory.json) |
| That directory against the SHA-256 manifest in git | 114 downloaded, 114 match by size and SHA-256, 0 missing, 0 extra | [07](commands/07-named-directory-vs-sha256-manifest.json) |
| Earlier download of the prefix, and its check | exit status 0, and 114 of 114 | [03](commands/03-download-to-fresh-directory.json), [04](commands/04-downloaded-vs-sha256-manifest.json) |

In records 03 and 04 the download directory appears only as a redaction placeholder. The same
placeholder in two records does not show that the checked directory is the one downloaded into,
so records 06 and 07 repeat both steps with the directory path written out.

[`tests/unit/test_osworld_guest_check_248097d_offhost_copy.py`](../../../tests/unit/test_osworld_guest_check_248097d_offhost_copy.py)
checks these statements against the stored files.

## Restoring the frames

With `BACKBLAZE_KEY_ID`, `BACKBLAZE_APPLICATION_KEY` and an rclone B2 remote named `bb` built from
them, this puts the frames back at the paths the manifest names:

```bash
rclone copy bb:OSWorld/osworld-guest-check-2026-11-12-248097d artifacts/osworld-guest-check-2026-11-12-248097d --checksum
```

`real-golden/frames/` is git-ignored, and `real-golden/contact-sheet.png` is already tracked with
the same bytes.

## Limits

- **Same-owner custody.** The bucket, its key and the local copy belong to one owner account. A
  governance-mode lock can be removed by a key with the `bypassGovernance` capability.
- **B2 records SHA-1, the manifest records SHA-256.** The read-back alone ties the remote files to
  the manifest by path and size only. The SHA-256 tie is the download in records 06 and 07.
- **Only this run.** The frames of the
  [2026-11-11](../../osworld-guest-check-2026-11-11/README.md) and
  [2026-11-12 attempt 3](../../osworld-guest-check-2026-11-12/README.md) guest checks are not part
  of this copy.
