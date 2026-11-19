# Golden-replay frames regenerated and copied off-host, 2026-11-18 (UTC)

The frame PNGs of two guest checks were listed in checked-in manifests but no copy of them could
be found on 2026-11-17:

- [2026-11-11 guest check](../osworld-guest-check-2026-11-11/README.md), revision `627e09b`,
  [manifest](../osworld-guest-check-2026-11-11/images.manifest.json)
- [2026-11-12 guest check, attempt 3](../osworld-guest-check-2026-11-12/README.md), revision
  `d4e711e`, [manifest](../osworld-guest-check-2026-11-12/attempt-3/images.manifest.json)

This note records one rerun of the golden replay at each revision, the comparison of its output
with those manifests, an off-host copy, and the retention lock on that copy. It also records a
comparison of each rerun's episode file with the original run's files. It changes no result.
Both guest-check folders are left as checked in, and no PNG is added to git.

## The reruns

Each rerun used a detached worktree at the original revision with a clean `git status`, and ran
that revision's committed entry point:

```bash
PYTHONPATH=. .venv/bin/python scripts/osworld_golden_trajectory.py record --output-dir <regeneration-output>/<revision>/real-golden
```

The original runs called the same function, `record_real`, through a driver that also ran seven
review sequences. The reruns did not run those sequences. The guest image in each worktree was a
copy-on-write clone of the host's `osworld-v2-ubuntu-x86.qcow2`.

| Revision | Started (UTC) | Seconds | Exit status | Guest bundle built | Run record |
|---|---|---|---|---|---|
| `627e09b` | 2026-11-18T04:22:21Z | 625.3 | 0 | `…-4067dc638dbaaa8e.zip` | [`runs/627e09b.json`](runs/627e09b.json) |
| `d4e711e` | 2026-11-18T04:32:47Z | 570.9 | 0 | `…-505a15bf40bac294.zip` | [`runs/d4e711e.json`](runs/d4e711e.json) |

Both guest bundle digests equal the `guest_bundle_sha256_recorded` values in the original runs'
provenance files. Each run printed `provider_closed: true`, and no container was running before or
after either run. Both ran on the pinned OSWorld-V2 guest (`v2026.06.24`, provider `docker-local`,
1024x768, no KVM); the run records hold the image identifiers.

## Comparison with the checked-in manifests

Each row is a raw command record under [`commands/`](commands/), written by
[`scripts/record_gate_command.py`](../../scripts/record_gate_command.py). Records 00 to 15 were
written at revision `1f62145e` ([00](commands/00-git-revision.json)). Records 16 to 31 were added
during review of the pull request, with the checkout at `6b1c75f2`
([16](commands/16-git-revision-at-review.json)). Records 32 to 34 were added later in that
review. No revision record was stored for them; they started after the commit time of `7338ca2c`
and before that of `a0fa54d6`. Records 35 to 37 were written with the checkout at `a0fa54d6`
([35](commands/35-git-revision-before-rerun-output-retention.json)). Records 38 and 39 were
written with the checkout at `c6eb00cb` ([38](commands/38-git-revision-at-rate-read.json)).
Records 40 and 41 were written with the checkout at `a293538b`
([40](commands/40-git-revision-at-episode-comparison.json)). Records 42 to 44 were written with
the checkout at `fa51317f` ([42](commands/42-git-revision-at-repeat-with-intact-text.json)).

| Compared files | Manifest | Files identical by size and SHA-256 | Raw record |
|---|---|---|---|
| `627e09b` rerun output, in the run's output directory | 2026-11-11 | 114 of 114, 0 different, 0 missing | [17](commands/17-rerun-output-vs-manifest-2026-11-11.json) |
| `d4e711e` rerun output, in the run's output directory | 2026-11-12 attempt 3 | 114 of 114, 0 different, 0 missing | [18](commands/18-rerun-output-vs-manifest-2026-11-12-attempt-3.json) |
| Copy of the `627e09b` output at the manifest paths | 2026-11-11 | 114 of 114, 0 different, 0 missing | [02](commands/02-regenerated-vs-manifest-2026-11-11.json) |
| Copy of the `d4e711e` output at the manifest paths | 2026-11-12 attempt 3 | 114 of 114, 0 different, 0 missing | [07](commands/07-regenerated-vs-manifest-2026-11-12-attempt-3.json) |

Records 17 and 18 hash the directory that each run record names as its `--output-dir`. They read
each manifest path relative to that directory, dropping a leading `real-golden/`: the run writes
its contact sheet inside the directory, and these two manifests list the sheet at the set root.
The modification times of the hashed files lie between each run's recorded start and end.

The rerun output was then copied to the manifest paths under each evidence folder. That copy was
not recorded as a command. Records 02 and 07 compare the copied files, which are the files that
were uploaded.

The comparison is between whole PNG files, so it is a bitwise comparison with no tolerance. It
covers one rerun per revision on the host that made the original runs.

The two manifests list the same 114 digests although the runs used different guest bundles. Both
reruns reproduced those digests, each from its own revision's bundle.

## Off-host copy

Each set was copied with `rclone copy --checksum --immutable` to the owner's private Backblaze B2
bucket `OSWorld`, under a prefix named after its evidence folder, with the manifest's relative
paths.

| Set | Prefix | Upload | `rclone check` | Download and check against the SHA-256 manifest, in one command |
|---|---|---|---|---|
| 2026-11-11 | `osworld-guest-check-2026-11-11/` | 114 new files, exit 0 ([03](commands/03-upload-2026-11-11.json)) | 114 matching, 0 differences ([04](commands/04-check-2026-11-11.json)) | 114 of 114 ([32](commands/32-download-and-check-in-one-command-2026-11-11.json)) |
| 2026-11-12 attempt 3 | `osworld-guest-check-2026-11-12/attempt-3/` | 114 new files, exit 0 ([08](commands/08-upload-2026-11-12-attempt-3.json)) | 114 matching, 0 differences ([09](commands/09-check-2026-11-12-attempt-3.json)) | 114 of 114 ([33](commands/33-download-and-check-in-one-command-2026-11-12-attempt-3.json)) |

Records 32 and 33 each run the download and the check as one command in one working directory.
The command first requires that the target directory does not exist, then downloads into it,
then checks it, so the checked directory is the one downloaded into.

Two earlier pairs of records hold the same download and check as separate commands, each
reporting 114 of 114. In records 05, 06, 10 and 11 the download directory appears only as a
redaction placeholder. In records 19 to 22 it is a relative path, but each record redacts its
working directory separately. Neither pair shows that the two commands used one directory.

The first bucket read-back ([12](commands/12-object-lock-readback.json)) lists 228 files and
14,571,126 bytes under the two prefixes, with no retention set on any of them. After these uploads
the whole bucket held 433 files and 26,107,365,320 bytes, old versions included
([13](commands/13-bucket-size-all-versions.json)).

## Retention lock

The owner then approved a governance-mode retention of one year on these 228 files.
[`apply_governance_retention.py`](apply_governance_retention.py) set it per file with
`b2_update_file_retention`, in one pass.

| Step | Stored result | Raw record |
|---|---|---|
| Apply the retention | 228 files listed, 228 changed, 0 already set, exit 0 | [14](commands/14-apply-governance-retention.json) |
| Bucket read-back afterwards | 228 files, 14,571,126 bytes, every file governance mode, one retain-until time, 2027-11-18T06:13:22Z | [15](commands/15-object-lock-readback-after-retention.json), also stored as [`object-lock-observation.json`](object-lock-observation.json) |

The file names and sizes in the second read-back equal those in the first.

Record 14 was produced by the script as committed in `1c84009`. Review later added a check that
stops the script when the key is not authorized to read a file's retention, because an unreadable
retention is not an unset one. The script was not run again on these 228 files. The read-backs
used the same credentials as the lock, and read-back 15 returned a retention for every file.

## Rerun output and guest bundle archives

Each rerun also wrote a run record, its standard output and error, and an episode file that holds
the 112-step trace (`raw/real-golden-episode.json`). These files hold absolute local paths and
are not checked in; [`runs/`](runs/) holds a redacted projection of each run record. During
review they were copied off-host with the runner script and two guest bundle archives, under the
prefix `osworld-guest-check-frames-regeneration-2026-11-18/`, in `rerun-output/` and
`guest-bundles/`.

The archives that the reruns built were in their worktrees and were removed with them. The two
archives copied are the files of the same names in the host checkout's bundle cache. Their
SHA-256 digests equal the `guest_bundle_sha256_recorded` values in the original runs' provenance
files, and each file name ends in the first 16 hex digits of its digest.

| Step | Stored result | Raw record |
|---|---|---|
| Size and SHA-256 of the local files | 11 files, 1,675,324 bytes | [23](commands/23-rerun-output-and-bundle-manifest.json), also stored as [`rerun-output.manifest.json`](rerun-output.manifest.json) |
| Upload with `rclone copy --checksum --immutable` | 9 new files and 2 new files, exit 0 | [24](commands/24-upload-rerun-output.json), [25](commands/25-upload-guest-bundles.json) |
| `rclone check` | 9 matching and 2 matching, 0 differences | [26](commands/26-check-rerun-output.json), [27](commands/27-check-guest-bundles.json) |
| Download and check against that manifest, in one command | 11 of 11 by size and SHA-256 | [34](commands/34-download-and-check-in-one-command-rerun-output.json); earlier as two commands in [28](commands/28-download-rerun-output-and-bundles.json), [29](commands/29-named-directory-vs-rerun-output-manifest.json) |
| Bucket read-back under the prefix | 11 files, 1,675,324 bytes, no retention set | [30](commands/30-object-lock-readback-rerun-output.json) |
| Whole bucket afterwards | 444 files and 26,109,040,644 bytes, old versions included | [31](commands/31-bucket-size-all-versions.json) |
| Estimated storage charge for that size | about $0.11 per 30 days after the free 10 GB, about $0.18 with no free allowance, at $6.95 per TB per 30 days, read from Backblaze's pricing page on 2026-11-18 | [39](commands/39-backblaze-rate-and-storage-estimate.json) |

The owner later approved the same one-year governance-mode retention on these 11 files. The same
script set it, this time with the unreadable-retention check in place.

| Step | Stored result | Raw record |
|---|---|---|
| Apply the retention | 11 files listed, 0 with unreadable retention, 0 with a different retention, 11 changed, 0 already set, exit 0 | [36](commands/36-apply-governance-retention-rerun-output.json) |
| Bucket read-back afterwards | 11 files, 1,675,324 bytes, every file governance mode, one retain-until time, 2027-11-18T19:36:10Z | [37](commands/37-object-lock-readback-rerun-output-after-retention.json) |

The file names, sizes and SHA-1 values in read-back 37 equal those in read-back 30. This lock
ends about 13 hours after the lock on the frames, because each is one year from when it was
applied.

Record 39 reads <https://www.backblaze.com/cloud-storage/pricing> once. It takes the rate from
the page's billing sentence and the allowance from the page's statement that the first 10 GB of
storage is free, and multiplies the size in record 31 by that rate. Record 31 is the last size
recorded; the bucket was not measured again after the retention locks, which add no stored
bytes. Taking 1 TB as 10^12 bytes is the record's own assumption. The page also shows the
figures $5 and $15 per TB in other sentences; the estimate does not use them. The estimate
covers storage only and is not a bill.

## Rerun episode files compared with the original runs

Record [41](commands/41-rerun-episodes-vs-original-runs.json) compares each rerun's episode file
with the original run's checked-in episode file and `results.json`. It also checks that the
local rerun file has the size and SHA-256 listed for it in `rerun-output.manifest.json`, the
manifest of the off-host copy, and exits non-zero if it does not.

| Compared | `627e09b` rerun and the 2026-11-11 run | `d4e711e` rerun and 2026-11-12 attempt 3 |
|---|---|---|
| Leaf values in each episode file | 2,418 and 2,418, same keys | 2,418 and 2,418, same keys |
| Leaf values that differ | 226 | 226 |
| Steps equal apart from the frame path and the episode id | 112 of 112 | 112 of 112 |
| Rerun rewards and the original episode file | equal, 112 values, sum 1.0 | equal, 112 values, sum 1.0 |
| Rerun rewards and the original `results.json` | equal | equal |
| Summary and reward-timing subset | equal | equal |

The 226 differing values are the same four fields in both pairs: the episode id on each of the
112 steps, the frame path on each of the 112 steps, the contact-sheet path, and
`backend_metadata.python_version`. Each file holds one episode id, and no id is shared between a
rerun and its original run. The frame file names are equal on all 112 steps; the rerun files
store the frame paths as absolute paths and the original files store none as absolute. The
original runs recorded Python 3.12.15 and the reruns recorded 3.12.16.

In both reruns the only positive reward is at step 112, which is also the terminal step, as in
the original runs.

The comparison reads stored files and reruns nothing. It adds no result: the original runs'
files remain the record of rewards.

## Program text stored in records 39 and 41

The recorder was given its working directory as `.`, and it redacts its working directory in
what it stores. In records 39 and 41 it therefore also replaced each standalone `.` in the
stored program text with `<path-0>`: the decimal point in two patterns of record 39, and the key
separator in record 41. The recorder runs the command as given and redacts only the stored copy,
so the stored outputs are not affected. The program text stored in those two records is not the
text that ran.

Records 43 and 44 repeat the two commands with the working directory given as an absolute path,
which leaves the stored program text as it ran. Neither earlier record was edited.

| Repeat | Stored result | Raw record |
|---|---|---|
| The comparison of record 41 | the same output as record 41 | [43](commands/43-rerun-episodes-vs-original-runs-intact-text.json) |
| The pricing-page read of record 39, on 2026-11-19 | every value equal to record 39's except the time of the read, including the page's size and SHA-256 | [44](commands/44-backblaze-rate-and-storage-estimate-intact-text.json) |

The command stored in record 41 equals the command in record 43 with that replacement applied,
and the same holds for records 39 and 44.

[`tests/unit/test_osworld_guest_check_frames_regeneration.py`](../../tests/unit/test_osworld_guest_check_frames_regeneration.py)
checks these statements against the stored files.

## Restoring the frames

With an rclone B2 remote named `bb` built from `BACKBLAZE_KEY_ID` and `BACKBLAZE_APPLICATION_KEY`:

```bash
rclone copy bb:OSWorld/osworld-guest-check-2026-11-11 artifacts/osworld-guest-check-2026-11-11 --checksum
```

```bash
rclone copy bb:OSWorld/osworld-guest-check-2026-11-12/attempt-3 artifacts/osworld-guest-check-2026-11-12/attempt-3 --checksum
```

Both `real-golden/frames/` folders are git-ignored, and each `contact-sheet.png` is already tracked
with the same bytes.

The rerun output and guest bundle archives restore into any git-ignored folder, for example:

```bash
rclone copy bb:OSWorld/osworld-guest-check-frames-regeneration-2026-11-18 .cache/guest-check-regen-restore --checksum
```

## Limits

- **The frames are regenerated, not the original files.** They equal the originals in size and
  SHA-256 according to the manifests, which is the only surviving record of the originals.
- **The rerun episode files are compared but not checked in.** Each holds its rerun's step trace
  and rewards and is copied off-host. Record 41 compares it with the original run's files, which
  remain the record of rewards. The reruns recorded Python 3.12.16 where the original runs
  recorded 3.12.15.
- **The copied guest bundle archives are not the files the reruns built.** They are tied to the
  reruns by file name, and to the original runs by full SHA-256.
- **Same-owner custody.** The bucket, its key and the local copies belong to one owner account. A
  governance-mode lock can be removed by a key with the `bypassGovernance` capability.
