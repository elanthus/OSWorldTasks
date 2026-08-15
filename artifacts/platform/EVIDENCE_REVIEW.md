# D4.11 generated-evidence review index

Generated from the fresh local lifecycle rehearsal at code revision
`29ab07c4bbea05e8d2f8fc5010335a765c941469`. Metrics in this bundle are synthetic;
they are not the real Day 3 model results.

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

Public wording requires separate approval and is not part of this evidence review.

## Structured evidence

| File | Contents |
| --- | --- |
| `demo-api-transcript.jsonl` | Five redacted API exchanges: blocked approval; policy and grounding before rollback; policy and grounding after rollback. |
| `demo-approval-events.jsonl` | Two append-only reviewer approval events. |
| `demo-audit-events.jsonl` | Eleven lifecycle audit events spanning gates, approvals, deploys, and rollback. |
| `demo-comparison.json` | Compatible candidate-A/candidate-B metric comparison used by the UI. |
| `demo-deployment-events.jsonl` | Three deployment events: seed deploy, candidate-B deploy, and seed rollback. |
| `demo-gate-reports.jsonl` | Three gate reports: failed candidate A and two passing candidates. |
| `demo-mlflow-lineage.jsonl` | Three MLflow lineage records with metrics, parameters, tags, Metaflow pathspecs, and registered policy identities. |
| `demo-run-manifests.jsonl` | Three complete evaluation run manifests. |
| `immutable-artifact-verification.json` | Hash verification results for 310 immutable objects; zero failures. |
| `test-results.txt` | Redacted raw output from the final platform unit test run. |

## Screenshots

| File | Captured state |
| --- | --- |
| `screenshots/01-empty-run-history.png` | Fresh stack with no evaluated candidates. |
| `screenshots/02-candidate-a-gate-blocked.png` | Candidate A failed its accuracy gate and exposes no approval control. |
| `screenshots/03-compatible-comparison.png` | Compatible candidate-A/candidate-B metric comparison. |
| `screenshots/04-mlflow-candidate-b-lineage.png` | Candidate B metrics, parameters, Metaflow tag, and registered policy in MLflow. |
| `screenshots/05-rollback-seed-deployed.png` | Earlier approved rollback seed active at generation 1. |
| `screenshots/06-candidate-b-eligible.png` | Passing candidate B still awaiting reviewer approval. |
| `screenshots/07-candidate-b-approved.png` | Candidate B approved but not yet deployed. |
| `screenshots/08-candidate-b-deployed.png` | Candidate B active at generation 2 with rollback available. |
| `screenshots/09-rollback-restored-seed.png` | Rollback restored the seed under generation 3. |
| `screenshots/10-rollback-restored-seed-viewport.png` | Readable viewport capture of the restored generation-3 policy identity. |
| `screenshots/manifest.json` | Screenshot SHA-256 hashes, sizes, lifecycle states, and linked candidate, MLflow, policy, and audit identities. |

## Identity cross-check

| Role | Candidate | MLflow run | Policy |
| --- | --- | --- | --- |
| Failed candidate A | `candidate-93d3400630f5051df89cb0ef` | `f14e530a42fd4a759bbc232385a30617` | `sha256:93d3400630f5051df89cb0efdeeb7aa3c0921e7ee851382ecaf88abd247f5f1b` |
| Rollback seed | `candidate-87cb41002aeaceb4bdb035d0` | `96090623a9c14f19aa976406b675afab` | `sha256:87cb41002aeaceb4bdb035d0721ed415247376747211fc1514c99b2a3af859d2` |
| Passing candidate B | `candidate-f3e4efb35c712a70b26e8d63` | `114fce5800024f58929caaa6cc2f79dd` | `sha256:f3e4efb35c712a70b26e8d630038a25dc97fe72593c7b6ce07a16aee9e491e33` |
