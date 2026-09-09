# D4.11 lifecycle rehearsal script — revision 78c801c514a83d74111da14ffef89714427570ec

Target runtime: 2–4 minutes. Everything shown is local, no-cost, and driven by the deterministic
scripted provider. Accuracy, cost, and latency in this rehearsal are synthetic governance
fixtures, not model-quality evidence, and are distinct from the real Sprint 3 / Day 3 results.

1. **Fresh stack and empty history — 15 s.** Start the isolated Compose project
   `pixelgym-d411-78c801c` from new volumes at the recorded clean revision. Open **Runs** and show
   `No evaluated candidates` (`screenshots/01`). Point out the footer: `synthetic metrics are not
   model-quality evidence`. `/api/v1/policy` returns 503 because nothing is active.
2. **Candidate A — 20 s.** Submit the fixed baseline evaluation (prompt v1, scripted baseline
   replay) from the UI; show the submission page in `Submitted` state with its cancellation form
   (`02`). When it completes, open the candidate page: 56% synthetic accuracy, $0.00 per 100,
   25 ms provider p95, the frozen 100-example dataset, state `GateFailed`, and no approval control
   (`03`).
3. **Blocked approval — 15 s.** Replay the direct approval request from
   `demo-api-transcript.jsonl` (event `blocked-approval`): HTTP 409,
   `only an eligible candidate can be approved`, and `demo-approval-events.jsonl` records no
   approval for A.
4. **Candidate B and comparison — 25 s.** Submit the revised evaluation (prompt v2, scripted
   revised replay). Compare A and B (`04`): dataset fingerprint, scorer, target semantics, and
   primary metric are `COMPATIBLE`; B shows 100% synthetic accuracy, $0.00, 25 ms, `Eligible`.
   A direct deploy request for the unapproved B returns 409 (`commands/18`, driver log).
5. **MLflow lineage — 20 s.** Open B's MLflow run (`05`): run ID, Metaflow pathspec tag, exact
   code revision, dataset fingerprint, prompt version, `pixelgym.synthetic: true`, metrics, and the
   immutable-artifact index (`demo-mlflow-lineage.jsonl`).
6. **Rollback seed, then approve B — 25 s.** Approve and deploy the distinct revised-response
   rollback seed first so an older approved exact policy exists at generation 1 (`06`). Show B
   still `Eligible` (`07`). Approve B with a recorded reason; B becomes `Approved` while the seed
   remains the active deployment (`08`, and the `/api/v1/policy` capture in `driver-log.jsonl`).
7. **Deploy exact policy — 15 s.** Deploy B: generation 2, new policy ID and deployment ID (`09`).
8. **Versioned serving API — 20 s.** Replay `/api/v1/policy` and one bounded `/api/v1/ground`
   call with the frozen screenshot `vendor-form-0001.png` (transcript events 2 and 3): API
   `v1`, exact B policy ID, exact candidate version, deployment ID, provider request ID, and the
   prediction.
9. **Rollback — 20 s.** Roll back with a separate reason. `/api/v1/policy` and `/api/v1/ground`
   (transcript events 4 and 5) keep schema `v1` while policy ID, exact version, and deployment ID
   change back to the seed under generation 3 (`10`, `11`).
10. **Audit and integrity — 25 s.** Open **Deployment → full audit history** (`14`): append-only
    submission, gate, approval, deploy, rollback, resubmission, and cancellation events, each with
    `actor_verification_source`. Show `immutable-artifact-verification.json`: 334 pinned objects
    verify by version and SHA-256 with zero failures.

Separately from the canonical A/B story, the submission status and cancellation UI was exercised
on a fourth request (prompt v2 with the baseline model): `Submitted` with a cancel form (`12`),
then `Cancelled` with no cancellation offered and the active policy unchanged (`13`). An earlier
attempt that re-submitted A's exact request was deduplicated by the control plane: no new
submission was created and one `submission.resubmitted` audit event was appended to A.

The reviewer separately confirms the blocked approval, the human-only transition from eligible to
approved, the serving identity changes, and immutable verification. Public wording, paid calls,
external deployment, and the D4.12 milestone verdict remain separate human gates.
