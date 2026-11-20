# S8 serving rehearsal: evidence record

The S8 scripted serving rehearsal was executed once on 2026-11-20 (UTC) from revision
`578202a798459368eaf17c16cd6c1b1a0f756af5`. This folder records the digests, counts, commands,
and exit statuses of that run and of the S7 qualification it consumed. It declares no verdict:
the owner grades S8.

[`record.json`](record.json) is the structured record. The operator assembled it from the private
stored evidence and command logs. The qualification and rehearsal content (reports, packages,
gate observations, lifecycle observations) stays private and is not in this repository.

## What ran

| Run | Command | Exit status | Fake sends |
| --- | --- | ---: | --- |
| `s7-20261119-main` | `scripts.s7_serving prepare` | 0 | 0 |
| `s7-20261119-main` | `scripts.s7_serving qualify` | 0 | qualification 77 |
| `s7-20261119-main` | `scripts.s7_serving verify` | 0 | none |
| `s8-20261119-main` | `scripts.s8_rehearsal prepare` | 0 | 0 |
| `s8-20261119-main` | `scripts.s8_rehearsal run --listener-port 8765` | 0 | A 2, B 24, smoke 6 |
| `s8-20261119-main` | `scripts.s8_rehearsal verify` | 0 | none |

The rehearsal ledger holds 109 sends of the 111 campaign cap: qualification 77, A 2, B 24,
smoke 6. Real-provider requests and spending are 0 as declared constants of the fixture-only
boundary; they are not independent network or billing measurements. The caller reached `/api/v2`
through a listener on `127.0.0.1`. No `failure.json` was written.

The qualification declares five predecessors with 304 prior sends (0, 73, 77, 77, 77), a
cumulative qualification allowance of 381, and a campaign ceiling of 415.

## Why a fresh qualification

The 2026-10-27 qualification (`s7-20261027-s8driver`, revision `580df9dc`) bound Python 3.12.15
and 447 installed distributions. By 2026-11-19 the host had Python 3.12.16 and 450 distributions,
so that qualification's runtime lock no longer verified. The owner approved a fresh qualification
at current `main` on 2026-11-19 and then signed the approval document for its exact packages.

## Copies

- Local archives exist for `s7-20261027-s8driver`, `s7-20261119-main`, and `s8-20261119-main`;
  `record.json` holds each manifest digest.
- `s7-20261119-main` and `s8-20261119-main` were copied to the owner's private Backblaze B2 bucket
  with `rclone copy --checksum --immutable`. `rclone check --one-way` reported 0 differences
  for both (7,981 and 148 files), and `scripts/observe_b2_object_lock.py` listed the same counts.
- The S8 copy was downloaded again, restored against its manifest digest, and re-verified with
  `scripts.s8_rehearsal verify` (exit status 0, same verification and lifecycle digests).
- The bucket held 27,669,229,089 bytes including versions after the upload. At $6.95 per TB per
  month, read from Backblaze's pricing page on 2026-11-19, that is about $0.19 per month.

## Limits

- The owner has not graded S8.
- The audit actor is a string, not an authenticated identity. The approval digest is the recorded
  authority. The actor string is withheld from this public record.
- No Object Lock retention is set on the uploaded files, and no private LFS copy was made.
- The Backblaze restore drill covers the S8 state only. The S7 upload was checked but not restored.
- `s7-20261027-s8driver` has a local archive only; a copy is nested inside the
  `s7-20261119-main` archive as a predecessor.
- The results measure serving controls with a scripted policy. They do not measure a model,
  availability, or storage-enforced retention.
