# Off-host copy and restore drill for the retained D5.9 campaign archives

This supplement records the first copy of the two retained D5.9 campaign archives to a location
off the host disk, and a restore drill from that copy. It changes no result, no freeze and no
decision. D5.10 was declared separately in [`artifacts/decisions/d5.10.json`](../../decisions/d5.10.json)
against `fd54dc27…`; its gate record classified single-disk retention as a known limitation, and
that record is unchanged. The owner decides whether this evidence changes that classification.

Read the [generated raw-command index](REPORT.md), the [Object Lock read-back](object-lock-observation.json),
the [restore-drill receipt](offhost-restore-drill.json), and the [checklist map](checklist-map.json).

## What was copied, and where

Both private archive trees were copied with `rclone 1.75.2 --checksum` to a private Backblaze B2
bucket with Object Lock enabled, owned by the project owner. A first attempt on 2026-10-25 stopped
at the account's storage cap after 67 small files; rclone cancelled its partial multipart upload and
a read-back found zero unfinished parts. After the owner raised the cap, the rerun completed and both
`rclone check` passes reported zero differences. The owner then approved a one-year governance-mode
retention, applied per file by a one-off script after verification. Those upload logs are private;
the recorded evidence below starts at the independent read-back.

| Observation | Stored result | Raw record |
|---|---|---|
| Bucket state read back through committed `scripts/observe_b2_object_lock.py` | private bucket, Object Lock enabled, 91 files, 26,085,481,886 bytes, every file governance mode, earliest retain-until 2027-10-25T22:44:26Z | [03](commands/03-object-lock-readback.json) |
| Fresh download vs local original, parser-sensitivity archive | 68 matching files, 0 differences | [04](commands/04-downloaded-vs-local-extraction.json) |
| Fresh download vs local original, replication archive | 23 matching files, 0 differences | [05](commands/05-downloaded-vs-local-replication.json) |

The download went into a new private directory, so no local copy was overwritten.

## Restore drill from the downloaded copy

| Check | Stored result | Runtime | Raw record |
|---|---|---:|---|
| Parser-sensitivity pilot and full restored from the downloaded copy and from the local backup; published projections reproduced | 4 of 4 restores: projection matches published, restored bytes unchanged; full copies 11 files / 15,244,025,902 bytes with 5 unresolved timeouts retained; pilot copies 10 files | 230.6 s | [06](commands/06-offhost-restore-drill-extraction.json) |
| Replication full archive restored from the downloaded copy with manifest-digest verification | 11 files, 10,672,107,426 bytes, digest `99cff640…` verified | 16.6 s | [07](commands/07-offhost-restore-replication-full.json) |
| Replication pilot archive restored the same way | 10 files, 56,199,153 bytes, digest `ee1dd23e…` verified | 0.6 s | [08](commands/08-offhost-restore-replication-pilot.json) |

Record 06 used the existing `scripts/restore_grounding_v5_d510.py` with the downloaded copy as
`--archive` and the local backup as `--backup`. Its receipt field `off_host_restore_performed` reads
`false` because the helper hard-codes it; the record's argv shows the archive side was the download.
[`offhost-restore-drill.json`](offhost-restore-drill.json) wraps that receipt with this provenance
without altering it.

## Retained failed observations

Two records exit non-zero and are kept rather than removed:

- [09](commands/09-replication-audit-verify-offhost.json): the replication audit `--verify` with the
  downloaded copy as the archive fails in 0.5 s with `source file hash mismatch`.
- [14](commands/14-replication-audit-verify-local-control.json): the same audit with the local archive
  fails identically in 0.9 s.

`scripts/report_grounding_v5_d59_replication.py` checks the campaign's bound source files against the
current checkout, and those files changed after the campaign ran. The failure is the same for both
copies and does not depend on the off-host copy. The replication audit at its historical source
revision remains in the earlier `c398557` package; here, the replication off-host copy rests on
records 05, 07 and 08.

## Limits

This is one additional copy under the same owner account that controls the bucket, its key and both
local copies. Governance-mode retention resists deletion for one year; a holder of the account's
bypass right could still remove versions, and no third-party custody was used. The original D5.9
Haiku journals were never retained, so nothing exists to copy for that campaign. The five unknown
timeout usages are unchanged. Credentials were read from an ignored `.env` file into the environment
of the recorded processes and appear in no record. No model or authentication-probe call was made.
No original, retained, downloaded or restored evidence was deleted, and no cleanup was authorized.
