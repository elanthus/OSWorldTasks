# D4.11 generated-evidence review index

Generated from the fresh isolated local lifecycle rehearsal at clean code revision
`fa4f414db15bf4a9f46dfcf0781828d2bf78afe9`. Metrics in this bundle are synthetic;
they are not the real Day 3 model results.

The rehearsal used Compose project `pixelgym-d411-fa4f414` with newly created volumes.
See `rehearsal-environment.json` for the redacted service, image, health, port, Python,
dependency-lock, and source-provenance record. The isolated stack was stopped and its
volumes were removed after the evidence was exported and verified.

## Suggested review order

1. Read `demo-api-transcript.jsonl` and confirm the failed candidate's approval request
   returned HTTP 409, then compare the policy IDs before and after rollback.
2. Read `demo-approval-events.jsonl` and confirm only the passing rollback seed and
   candidate B received reviewer approvals.
3. Read `demo-deployment-events.jsonl` and confirm generations 1, 2, and 3 record
   deploy, deploy, and rollback, restoring the generation-1 policy at generation 3.
4. Cross-check `demo-mlflow-lineage.jsonl`, `demo-run-manifests.jsonl`, and the
   screenshot manifest to trace each UI state to MLflow and immutable artifacts.
5. Read `immutable-artifact-verification.json` and confirm all 310 objects verified
   with zero failures.
6. Review the screenshots in numeric order.

## D4.11 human confirmations

- [ ] The failed candidate cannot be approved through the API.
- [ ] Passing gates still require reviewer approval.
- [ ] Deployment and rollback change the policy ID shown by `/api/v1`.
- [ ] Immutable hashes verify after the demo.

These confirmations and the milestone verdict belong to the human reviewer. Public wording
requires separate approval and is not part of this evidence review.

## Structured evidence

| File | Contents |
| --- | --- |
| `rehearsal-environment.json` | Redacted fresh-stack namespace, volumes, service images, health, ports, runtime versions, dependency lock, and clean source provenance. |
| `demo-api-transcript.jsonl` | Five redacted API exchanges: blocked approval; policy and grounding before rollback; policy and grounding after rollback. |
| `demo-approval-events.jsonl` | Two append-only reviewer approval events. |
| `demo-audit-events.jsonl` | Eleven lifecycle audit events spanning submissions, gates, approvals, deploys, and rollback. |
| `demo-comparison.json` | Compatible candidate-A/candidate-B metric comparison exported from stored values. |
| `demo-deployment-events.jsonl` | Three deployment events: seed deploy, candidate-B deploy, and seed rollback. |
| `demo-gate-reports.jsonl` | Three stored gate reports: failed candidate A and two passing candidates. |
| `demo-mlflow-lineage.jsonl` | Three MLflow lineage records with run IDs, Metaflow pathspecs, prompt identities, policy identities, and pinned immutable-artifact references. |
| `demo-run-manifests.jsonl` | Three complete evaluation run manifests. |
| `immutable-artifact-verification.json` | Hash verification results for 310 pinned immutable objects; zero failures. |
| `test-results.txt` | Redacted raw output from the final bounded platform unit test run. |

The report index and tables above summarize stored evidence only. They do not rerun or
reinterpret the recorded gates.

## Screenshots

| File | Captured state |
| --- | --- |
| `screenshots/01-empty-run-history.png` | Fresh stack with no evaluated candidates. |
| `screenshots/02-candidate-a-gate-blocked.png` | Candidate A failed its accuracy gate and exposes no approval control. |
| `screenshots/03-compatible-comparison.png` | Compatible candidate-A/candidate-B metric comparison. |
| `screenshots/04-mlflow-candidate-b-lineage.png` | Candidate B metrics, run ID, synthetic/Metaflow tags, and registered prompt in MLflow. |
| `screenshots/05-rollback-seed-deployed.png` | Earlier approved rollback seed active at generation 1. |
| `screenshots/06-candidate-b-eligible.png` | Passing candidate B still awaiting reviewer approval. |
| `screenshots/07-candidate-b-approved.png` | Candidate B approved but not yet deployed. |
| `screenshots/08-candidate-b-deployed.png` | Candidate B active at generation 2 with rollback available. |
| `screenshots/09-rollback-restored-seed.png` | Rollback restored the seed under generation 3. |
| `screenshots/10-rollback-restored-seed-viewport.png` | Readable viewport capture of the restored generation-3 policy identity. |
| `screenshots/manifest.json` | Screenshot SHA-256 hashes, sizes, dimensions, lifecycle states, and linked candidate, MLflow, policy, deployment, and audit identities. |

## Identity cross-check

| Role | Candidate | MLflow run | Policy |
| --- | --- | --- | --- |
| Failed candidate A | `candidate-b87ccb522e22600e0f1661b7` | `9677ee1dbac04cf1961514b240c49a13` | `sha256:b87ccb522e22600e0f1661b7bb8f29249667660e3d22dec0868342d2820d4c60` |
| Rollback seed | `candidate-cb167e4e9cca9cd71885b809` | `c346f46793d24abfa17374c85691d762` | `sha256:cb167e4e9cca9cd71885b8093660bb83df76cb6d1437e40c32e86ada0820fd25` |
| Passing candidate B | `candidate-ab125729e03ef236acbf1919` | `2bd26538769c40a982baa9bae1c8c144` | `sha256:ab125729e03ef236acbf1919d9acbc4c18496fc40b795c0a8705591e1b4aceea` |

## Deployment cross-check

| Generation | Action | Deployment | Active candidate |
| --- | --- | --- | --- |
| 1 | deploy | `deployment-fde93ffc76794c9ebb7efd2b` | `candidate-cb167e4e9cca9cd71885b809` |
| 2 | deploy | `deployment-683e80056223ec43fc1ea41f` | `candidate-ab125729e03ef236acbf1919` |
| 3 | rollback | `deployment-73f1a1a0f93bda946b0c1b98` | `candidate-cb167e4e9cca9cd71885b809` |
