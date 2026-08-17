# D4.12 raw evidence index

Evidence revision: `f92e307af7a3830347d50ca63f6a7d481489935c`  
Evidence branch: `agent/issue-47-d412-evidence`  
Observation window: `2026-08-17T20:10:46.298307+00:00` to `2026-08-17T20:31:02.486982+00:00`  
Verdict: `null`  
Verdict owner: project owner

This index copies and links stored observations only. It does not rerun tests, reinterpret gate semantics, declare a milestone verdict, or change human-owned checklist state.

## Checklist evidence

| # | Checklist line | Stored raw observation | Evidence |
| ---: | --- | --- | --- |
| 1 | Existing PixelGym fast suite still passes from the documented clean install. | 593 passed, 5 skipped, 3 warnings in 53.84s; environment checker 1 passed in 0.04s; golden trajectory reported OK; Ruff reported all checks passed. | `commands/07-install-dev.json`, `commands/08-fast-suite.json`, `commands/09-environment-checker.json`, `commands/10-golden-trajectory.json`, `commands/13-ruff.json` |
| 2 | Platform unit tests run without network, OSWorld, provider credentials, or wall-clock sleeps. | 216 passed, 3 warnings in 14.13s with closed proxy endpoints and provider credentials removed; osworld_installed=false; provider_credentials_present=false; the sleep scan returned zero matches (rg exit 1). | `commands/11-platform-unit-boundaries.json`, `commands/15-boundary-inventory.json`, `commands/16-platform-sleep-scan.json` |
| 3 | Marked platform integration tests pass against a fresh local stack. | 2 passed in 153.97s; the post-run filtered Docker inventory contained zero pixelgym-it containers, images, networks, or volumes. | `commands/30-compose-browser-sandbox-retry.json`, `commands/37-compose-cleanup-filtered.json` |
| 4 | Metaflow resume tests prove no duplicate fixture-provider calls. | The marked local runtime command recorded 9 passed in 86.30s; its collected cases include every side-effect boundary, hard-kill resume, call caps, and concurrency caps. | `commands/28-platform-local-runtime-retry.json`, `commands/39-integration-test-inventory.json` |
| 5 | MLflow contains the complete run contract and links back to Metaflow. | The local runtime and fresh Compose commands recorded 9 and 2 passing tests; three stored MLflow lineage records reconcile with three run manifests by run ID, Metaflow pathspec, and policy ID. | `commands/28-platform-local-runtime-retry.json`, `commands/30-compose-browser-sandbox-retry.json`, `artifacts/platform/demo-mlflow-lineage.jsonl` |
| 6 | Dataset and raw-response hashes verify from immutable storage. | The stored immutable verification has 310 verified pinned objects and failure_count=0; its reference set equals the three run-manifest artifact union; the fresh Compose case also exercised real MinIO version/digest/tamper paths. | `commands/30-compose-browser-sandbox-retry.json`, `artifacts/platform/immutable-artifact-verification.json`, `identity-reconciliation.json` |
| 7 | Missing cost or latency blocks promotion. | The named missing/non-finite cost and latency boundary cases are present in the 23-case command output; 23 passed in 1.30s. | `commands/31-mechanical-boundaries.json` |
| 8 | Gate failure blocks both UI and direct approval API. | The stored blocked-approval exchange is HTTP 409; the failed-candidate screenshot is hash verified; the named direct-approval case appears in the 23-case output; the fresh browser/API lifecycle recorded 2 passed. | `commands/30-compose-browser-sandbox-retry.json`, `commands/31-mechanical-boundaries.json`, `artifacts/platform/demo-api-transcript.jsonl`, `artifacts/platform/screenshots/02-candidate-a-gate-blocked.png` |
| 9 | Passing gates do not bypass human approval. | Two stored approval events belong only to the two stored passing-policy identities; the eligible screenshot precedes approval; the project owner separately confirmed all four D4.11 review items. | `commands/30-compose-browser-sandbox-retry.json`, `commands/31-mechanical-boundaries.json`, `artifacts/platform/demo-approval-events.jsonl`, `artifacts/platform/screenshots/06-candidate-b-eligible.png`, `d411-human-confirmation.json` |
| 10 | Serving rejects an unapproved exact version. | The named unapproved deployment and serving-restore cases appear in the 23-case output; the fresh browser lifecycle recorded the pre-approval deployment response as 409 within its passing case. | `commands/30-compose-browser-sandbox-retry.json`, `commands/31-mechanical-boundaries.json` |
| 11 | Failed deployment leaves the current version active. | The named deploy and pre-activation failure cases appear in the 23-case output; the fresh Compose lifecycle exercised missing-package deploy and rollback failures while retaining the stored active identity. | `commands/30-compose-browser-sandbox-retry.json`, `commands/31-mechanical-boundaries.json` |
| 12 | Rollback restores the previous approved exact version. | Stored generations are deploy/deploy/rollback with generation 3 restoring generation 1's candidate and policy; the fresh browser lifecycle recorded 2 passed. | `commands/30-compose-browser-sandbox-retry.json`, `artifacts/platform/demo-deployment-events.jsonl`, `artifacts/platform/demo-api-transcript.jsonl` |
| 13 | Concurrent transition tests produce one active deployment. | The fresh lifecycle's passing case contains exact [303, 409] deploy and rollback race assertions and an active-pointer count of one; the named stale compare-and-swap case is in the 23-case output. | `commands/30-compose-browser-sandbox-retry.json`, `commands/31-mechanical-boundaries.json` |
| 14 | Demo evidence clearly distinguishes scripted and real provider results. | The review index labels all demo metrics synthetic, and the stored environment records deterministic scripted replay, zero network model calls, zero paid calls, and no external deployment. | `artifacts/platform/EVIDENCE_REVIEW.md`, `artifacts/platform/rehearsal-environment.json`, `artifacts/platform/demo-mlflow-lineage.jsonl` |
| 15 | No credentials, private payloads, or privileged benchmark data appear in artifacts. | The recorded redaction scan reports zero findings in every prohibited category; the screenshot manifest states the excluded data classes. | `commands/40-redaction-scan.json`, `artifacts/platform/screenshots/manifest.json` |
| 16 | Retention, backup, recovery, and known limitations are documented. | Stored documentation distinguishes 30-day local governance retention from production WORM and identifies production backup, replication, recovery testing, and disaster-recovery work as unapproved/not demonstrated. | `deploy/README.md`, `artifacts/platform/architecture.md`, `artifacts/platform/known-limitations.md` |

## Raw command inventory

Each JSON record stores the exact argv/command, cwd, public environment, UTC timestamps, process runtime, exit status, and complete combined output. Expected sandbox/no-match attempts remain visible alongside their successful retries.

| Record | Exit | Runtime (s) | Parsed pytest summary |
| --- | ---: | ---: | --- |
| `commands/00-git-revision.json` | 0 | 0.014391 | `null` |
| `commands/01-worktree-status.json` | 0 | 0.0247 | `null` |
| `commands/02-python-version.json` | 0 | 0.010658 | `null` |
| `commands/03-os-architecture.json` | 0 | 0.004245 | `null` |
| `commands/04-docker-version.json` | 0 | 0.06134 | `null` |
| `commands/05-compose-version.json` | 0 | 0.13596 | `null` |
| `commands/06-create-dev-venv.json` | 0 | 1.263763 | `null` |
| `commands/07-install-dev.json` | 0 | 8.229202 | `null` |
| `commands/08-fast-suite.json` | 0 | 54.288559 | `{"passed": 593, "runtime": 53.84, "skipped": 5, "warnings": 3}` |
| `commands/09-environment-checker.json` | 0 | 0.404828 | `{"passed": 1, "runtime": 0.04}` |
| `commands/10-golden-trajectory.json` | 0 | 2.524121 | `null` |
| `commands/11-platform-unit-boundaries.json` | 0 | 14.579389 | `{"passed": 216, "runtime": 14.13, "warnings": 3}` |
| `commands/12-dependency-lock-check.json` | 0 | 0.086727 | `null` |
| `commands/13-ruff.json` | 0 | 0.601661 | `null` |
| `commands/14-pip-check.json` | 0 | 0.28383 | `null` |
| `commands/15-boundary-inventory.json` | 0 | 0.051109 | `null` |
| `commands/16-platform-sleep-scan.json` | 1 | 0.024168 | `null` |
| `commands/17-source-provenance.json` | 0 | 0.224472 | `null` |
| `commands/18-lock-sha256.json` | 0 | 0.045149 | `null` |
| `commands/19-dev-package-versions.json` | 0 | 0.18467 | `null` |
| `commands/20-create-integration-venv.json` | 0 | 1.341015 | `null` |
| `commands/21-install-platform.json` | 1 | 8.105314 | `null` |
| `commands/22-install-platform-network-retry.json` | 0 | 27.844906 | `null` |
| `commands/23-playwright-chromium.json` | 1 | 471.402338 | `null` |
| `commands/24-playwright-chromium-network-retry.json` | 0 | 0.212051 | `null` |
| `commands/25-integration-package-versions.json` | 0 | 0.09552 | `null` |
| `commands/26-integration-pip-check.json` | 0 | 0.350263 | `null` |
| `commands/27-platform-local-runtime.json` | 0 | 98.290659 | `{"passed": 9, "runtime": 97.6}` |
| `commands/28-platform-local-runtime-retry.json` | 0 | 86.915253 | `{"passed": 9, "runtime": 86.3}` |
| `commands/29-compose-browser.json` | 1 | 2.786788 | `null` |
| `commands/30-compose-browser-sandbox-retry.json` | 0 | 154.449144 | `{"passed": 2, "runtime": 153.97}` |
| `commands/31-mechanical-boundaries.json` | 0 | 1.892527 | `{"passed": 23, "runtime": 1.3, "warnings": 3}` |
| `commands/37-compose-cleanup-filtered.json` | 0 | 0.257344 | `null` |
| `commands/38-evidence-branch.json` | 0 | 0.0144 | `null` |
| `commands/39-integration-test-inventory.json` | 0 | 1.984707 | `null` |
| `commands/40-redaction-scan.json` | 0 | 0.031492 | `null` |

## Identity and redaction indexes

See `identity-reconciliation.json` for stored cross-file identities and counts, `redaction-scan.json` for prohibited-pattern counts, and `evidence-manifest.json` for SHA-256 and size metadata.
