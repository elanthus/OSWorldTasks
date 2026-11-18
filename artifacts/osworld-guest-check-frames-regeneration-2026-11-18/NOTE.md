# Golden-replay frames regenerated and copied off-host, 2026-11-18 (UTC)

The frame PNGs of two guest checks were listed in checked-in manifests but no copy of them could
be found on 2026-11-17:

- [2026-11-11 guest check](../osworld-guest-check-2026-11-11/README.md), revision `627e09b`,
  [manifest](../osworld-guest-check-2026-11-11/images.manifest.json)
- [2026-11-12 guest check, attempt 3](../osworld-guest-check-2026-11-12/README.md), revision
  `d4e711e`, [manifest](../osworld-guest-check-2026-11-12/attempt-3/images.manifest.json)

This note records one rerun of the golden replay at each revision, the comparison of its output
with those manifests, an off-host copy, and the retention lock on that copy. It changes no result.
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
[`scripts/record_gate_command.py`](../../scripts/record_gate_command.py) at revision `1f62145e`
([00](commands/00-git-revision.json)).

| Rerun output | Manifest | Files identical by size and SHA-256 | Raw record |
|---|---|---|---|
| `627e09b` | 2026-11-11 | 114 of 114, 0 different, 0 missing | [02](commands/02-regenerated-vs-manifest-2026-11-11.json) |
| `d4e711e` | 2026-11-12 attempt 3 | 114 of 114, 0 different, 0 missing | [07](commands/07-regenerated-vs-manifest-2026-11-12-attempt-3.json) |

The comparison is between whole PNG files, so it is a bitwise comparison with no tolerance. It
covers one rerun per revision on the host that made the original runs.

The two manifests list the same 114 digests although the runs used different guest bundles. Both
reruns reproduced those digests, each from its own revision's bundle.

## Off-host copy

Each set was copied with `rclone copy --checksum --immutable` to the owner's private Backblaze B2
bucket `OSWorld`, under a prefix named after its evidence folder, with the manifest's relative
paths.

| Set | Prefix | Upload | `rclone check` | Fresh download against the SHA-256 manifest |
|---|---|---|---|---|
| 2026-11-11 | `osworld-guest-check-2026-11-11/` | 114 new files, exit 0 ([03](commands/03-upload-2026-11-11.json)) | 114 matching, 0 differences ([04](commands/04-check-2026-11-11.json)) | 114 of 114 ([05](commands/05-download-to-fresh-directory-2026-11-11.json), [06](commands/06-downloaded-vs-manifest-2026-11-11.json)) |
| 2026-11-12 attempt 3 | `osworld-guest-check-2026-11-12/attempt-3/` | 114 new files, exit 0 ([08](commands/08-upload-2026-11-12-attempt-3.json)) | 114 matching, 0 differences ([09](commands/09-check-2026-11-12-attempt-3.json)) | 114 of 114 ([10](commands/10-download-to-fresh-directory-2026-11-12-attempt-3.json), [11](commands/11-downloaded-vs-manifest-2026-11-12-attempt-3.json)) |

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

## Limits

- **The frames are regenerated, not the original files.** They equal the originals in size and
  SHA-256 according to the manifests, which is the only surviving record of the originals.
- **Frames only.** The reruns stored the frames, the contact sheet and the printed summary. Their
  step traces were not stored, and the original runs' result files are the record of rewards.
- **Same-owner custody.** The bucket, its key and the local copies belong to one owner account. A
  governance-mode lock can be removed by a key with the `bypassGovernance` capability.
