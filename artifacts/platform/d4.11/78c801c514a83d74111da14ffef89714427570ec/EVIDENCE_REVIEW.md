# D4.11 generated-evidence review index — revision 78c801c514a83d74111da14ffef89714427570ec

Generated from a fresh isolated local lifecycle rehearsal at the clean `origin/main` head
`78c801c514a83d74111da14ffef89714427570ec` (2026-09-09), run from a detached checkout with an
empty worktree. Metrics in this bundle are synthetic; they are not the real Day 3 model results.
This directory is revision-scoped; the August 2026 bundle at `artifacts/platform/` (revision
`fa4f414d`) and every `artifacts/platform/d4.12/<revision>/` directory are unchanged.

The rehearsal used Compose project `pixelgym-d411-78c801c` with three newly created volumes.
`rehearsal-environment.json` records the redacted service, image, health, port, Python,
dependency-lock, and source-provenance facts. The isolated stack, its volumes, and its locally
built images were removed after export and verification; no other Compose project was touched.

## Suggested review order

1. `demo-api-transcript.jsonl`: event 1 is the failed candidate's direct approval (HTTP 409);
   events 2–3 show the B policy/deployment identity before rollback; events 4–5 show the seed
   identity after rollback.
2. `demo-approval-events.jsonl`: exactly two approvals (seed, then B); none for A.
3. `demo-deployment-events.jsonl`: generations 1, 2, 3 = deploy seed, deploy B, rollback to seed.
4. `demo-audit-events.jsonl` (15 events) with `actor` and `actor_verification_source`.
5. `demo-mlflow-lineage.jsonl`, `demo-run-manifests.jsonl`, `screenshots/manifest.json`, and
   `commands/35-mlflow-run-contract.json` to trace each UI state to MLflow and immutable objects.
6. `immutable-artifact-verification.json`: 334 objects verified, 0 failures.
7. `redaction-scan.json`: zero findings across the bundle.
8. `commands/` for every raw command, exit status, and redacted output, in execution order.
9. Screenshots in numeric order.

## D4.11 human confirmations

- [x] The failed candidate cannot be approved through the API. (owner, 2026-09-09; see `d411-human-confirmation.json`)
- [x] Passing gates still require reviewer approval. (owner, 2026-09-09)
- [x] Deployment and rollback change the policy ID shown by `/api/v1`. (owner, 2026-09-09)
- [x] Immutable hashes verify after the demo. (owner, 2026-09-09)

These confirmations and the milestone verdict belong to the project owner. Public wording
requires separate approval and is not part of this evidence review.

## Structured evidence

| File | Contents |
| --- | --- |
| `rehearsal-environment.json` | Redacted fresh-stack namespace, volumes, services, health, ports, runtime versions, lock digest, clean source provenance, initial/final counts, teardown. |
| `demo-api-transcript.jsonl` | Five redacted API exchanges: blocked approval; policy and ground before rollback; policy and ground after rollback. |
| `demo-approval-events.jsonl` | Two append-only approval events. |
| `demo-audit-events.jsonl` | Fifteen lifecycle audit events. |
| `demo-comparison.json` | Stored-metric comparison of the three candidates. |
| `demo-deployment-events.jsonl` | Three deployment events: seed deploy, B deploy, rollback to seed. |
| `demo-gate-reports.jsonl` | Three stored gate reports: failed A and two passing candidates. |
| `demo-mlflow-lineage.jsonl` | Three MLflow lineage records with run IDs, Metaflow pathspecs, prompt/policy identities, and pinned immutable references. |
| `demo-run-manifests.jsonl` | Four run manifests, including the cancelled fourth submission (no candidate, no artifacts). |
| `immutable-artifact-verification.json` | Verification of 334 pinned immutable objects; zero failures. |
| `driver-log.jsonl`, `driver-state.json` | Browser-driver observations: `/api/v1/policy` bodies at each step, HTTP statuses of blocked actions, screenshot provenance. |
| `commands/*.json` | Raw command records (schema `pixelgym-d412-command-record-v1`) for environment capture, stack start, each lifecycle phase, export, verification, teardown. |
| `redaction-scan.json` | Prohibited-pattern counts over this bundle. |
| `tooling/` | The browser driver and screenshot-manifest builder used, for reproduction. The redaction scanner (same pattern set as `scripts/generate_d412_evidence_report.py`, plus a provider-private-field pattern) is recorded in `commands/38-redaction-scan.json`. |

## Identity cross-check

| Role | Submission | Candidate | MLflow run | Policy |
| --- | --- | --- | --- | --- |
| Failed candidate A | `submission-450fea074cb82b1a8ab216ec` | `candidate-256bf4b72d198f142f31785e` | `95a33816718c47b0b0bd53b973a79523` | `sha256:256bf4b72d198f142f31785e79897aa380484373f500ec0be26fa64ac6b5c382` |
| Rollback seed | `submission-b46e213e11321d817bf6509f` | `candidate-580afb9f3e74ee75204d71f1` | `1f61bf467a3d49e88b7702f4ebd76fbe` | `sha256:580afb9f3e74ee75204d71f15260d1fb75be7401a231b0ba31e1d743fa60c9b6` |
| Passing candidate B | `submission-685e994b3d7c4b950bcefbea` | `candidate-9d78e49832fc7226fae5f5ce` | `139367b1a304475e8649ae08c1913f6f` | `sha256:9d78e49832fc7226fae5f5ce63c8c7300c1cdc212e4597f69766ca101496296b` |
| Cancelled submission C | `submission-351120d2f4d727b837c0cc42` | none | none | none |

## Deployment cross-check

| Generation | Action | Deployment | Active candidate |
| --- | --- | --- | --- |
| 1 | deploy | `deployment-a6bc20d85b53b06974ff60d1` | `candidate-580afb9f3e74ee75204d71f1` |
| 2 | deploy | `deployment-093b797f378e92feed765c44` | `candidate-9d78e49832fc7226fae5f5ce` |
| 3 | rollback | `deployment-9b9ba47f999d129231f655d5` | `candidate-580afb9f3e74ee75204d71f1` |

The tables above index stored evidence only. They do not rerun or reinterpret the recorded gates.
