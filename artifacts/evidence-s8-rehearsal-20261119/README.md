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
- Both runs were later restored from the local archive and from Backblaze and re-verified; see
  finding 1 below. The proposal's 2026-11-20 amendment names Backblaze as the recovery copy.
- The bucket held 27,669,229,089 bytes including versions after the upload. At $6.95 per TB per
  month, read from Backblaze's pricing page on 2026-11-19, that is about $0.19 per month.

## Review findings

An owner-side review of this evidence raised the points below. Each was checked against the
code and the stored evidence. The sealed rehearsal state is unchanged.

1. **The recovery check did not follow the proposal as first written.** The
   [S7 proposal](../../docs/s7-serving-demo-proposal.md) required one local-archive restore and one
   fresh-cache private LFS restore. No private LFS copy was made. On 2026-11-20 the owner amended
   the proposal to make a Backblaze B2 restore the recovery copy
   ([amendment](../../docs/s7-serving-demo-proposal.md#amendment-2026-11-20-backblaze-b2-replaces-the-private-lfs-copy)).
   The amendment was written after the restores below had run.
   [`recovery-check-steps.jsonl`](recovery-check-steps.jsonl) records them: on 2026-11-20 both
   runs were restored from the local archive and from Backblaze into new directories, verified,
   and their reports re-rendered. All 14 steps exited 0, the verify digests equal the originals,
   and each re-rendered report has the same SHA-256 as the original. Three limits apply: the
   verifier ran from the working checkout, not in an isolated interpreter against the bundled
   source; each restored S8 state still reads the original qualification directory; and the
   restores ran on the same host. The owner grades whether the amended check is met.
2. **The `rollback_to_exhausted_B` observation does not show exhaustion.** It was refused with
   `TransitionError: there is no eligible known-good deployment to roll back to` and
   `candidate_id: null`. That is the rollback lineage rule, which would refuse the same way if B
   had capacity left. Exhaustion is shown only by `redeploy_exhausted_B` (`StatefulLoadError`,
   smoke sends 5 to 6).
3. **No live episode from an old deployment was refused.** Both `409 deployment_changed` probes
   used episodes that had already ended. The live case rests on unit tests only.
4. **The driver's acceptance record holds one command**, the stored-verification replay. The
   regression-backed rows had no raw command at the rehearsal revision.
   [`acceptance-regression-steps.jsonl`](acceptance-regression-steps.jsonl) now records three
   commands run at `578202a7` after the rehearsal: the serving-stack unit tests (130 passed), the
   S7 and S8 unit tests (133 passed), and the opt-in Darwin sandbox tests (4 passed, 2 skipped;
   the skipped two need the Claude CLI). These are regression runs, not graded checks.
5. **Sandbox enforcement is the launcher's own report.** The five worker proofs give a mechanism
   and five different profile digests. No profile text is retained in the S8 state, so the digests
   cannot be checked against content.
6. **Step 6 of the driver compares B's attempt count with a literal 24** instead of the plan's
   cap. The code is unchanged here.

## Limits

- The owner has not graded S8.
- `record.json` is assembled by the operator. No generator script or test binds it.
- The audit actor is a string recorded as `legacy_unverified`, not an authenticated identity. The
  approval digest is the only authority. The actor string is withheld from this public record.
- `approved_on` in the approval document reads `11.19.26`.
- The owner's approval of the fresh qualification, and of ceilings 381 and 415, is recorded as
  given in chat. Three qualifications followed the original bound; the 2026-10-27 one (77 sends)
  was never used.
- The run state lives outside the repository because the driver refuses an in-checkout path, while
  `AGENTS.md` section 7 asks for a git-ignored path inside it.
- No Object Lock retention is set on the uploaded files, and no private LFS copy was made.
- `s7-20261027-s8driver` has a local archive only; a copy is nested inside the
  `s7-20261119-main` archive as a predecessor.
- The results measure serving controls with a scripted policy. They do not measure a model,
  availability, or storage-enforced retention.
