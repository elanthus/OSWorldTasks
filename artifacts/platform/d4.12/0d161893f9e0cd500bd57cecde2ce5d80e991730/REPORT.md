# D4.12 raw evidence index

Evidence revision: `0d161893f9e0cd500bd57cecde2ce5d80e991730`

Evidence branch: `agent/complete-d4-platform-contract`

Observation window: `2026-08-18T19:10:37.814676+00:00` to `2026-08-18T19:23:49.199744+00:00`

Verdict: `null`

Verdict owner: project owner

This index copies and links stored observations only. It does not rerun tests, reinterpret gate semantics, declare a milestone verdict, or change human-owned checklist state.

## Checklist evidence

| # | Checklist line | Stored raw observation | Evidence |
| ---: | --- | --- | --- |
| 1 | Existing PixelGym fast suite still passes from the documented clean install. | {"environment_pytest":{"passed":1,"runtime":0.04,"skipped":0,"warnings":0},"fast_pytest":{"passed":679,"runtime":79.38,"skipped":5,"warnings":3},"golden_trajectory_output_contains_OK":true,"ruff_output_contains_all_checks_passed":true} | `commands/07-install-dev.json`, `commands/08-fast-suite.json`, `commands/09-environment-checker.json`, `commands/10-golden-trajectory.json`, `commands/13-ruff.json` |
| 2 | Platform unit tests run without network, OSWorld, provider credentials, or wall-clock sleeps. | {"boundary_inventory":["osworld_installed=false","provider_credentials_present_before_sanitization=false","provider_credentials_removed=true"],"platform_unit_pytest":{"passed":300,"runtime":44.63,"skipped":0,"warnings":3},"sleep_scan_exit_status":1,"sleep_scan_output":""} | `commands/11-platform-unit-boundaries.json`, `commands/15-boundary-inventory.json`, `commands/16-platform-sleep-scan.json` |
| 3 | Marked platform integration tests pass against a fresh local stack. | {"compose_pytest":{"passed":2,"runtime":137.51,"skipped":0,"warnings":0},"post_run_docker_inventory":{"containers":[],"images":[],"networks":[],"volumes":[]}} | `commands/30-compose-browser-sandbox-retry.json`, `commands/37-compose-cleanup-filtered.json` |
| 4 | Metaflow resume tests prove no duplicate fixture-provider calls. | {"boundary_names":["candidate_registered","evidence_persisted","mlflow_finalized","provider_response_received","raw_responses_persisted","run_linked"],"hard_kill_signal_recorded":true,"local_runtime_pytest":{"passed":10,"runtime":184.18,"skipped":0,"warnings":0},"normalized_evidence_equal":{"candidate_registered":true,"evidence_persisted":true,"hard_kill":true,"mlflow_finalized":true,"provider_response_received":true,"raw_responses_persisted":true,"run_linked":true,"uninterrupted":true},"provider_ledgers":{"candidate_registered":{"active":0,"attempts":100,"billable_calls":100,"cache_hits":0,"max_active":2,"unique_request_ids":100},"evidence_persisted":{"active":0,"attempts":100,"billable_calls":100,"cache_hits":0,"max_active":2,"unique_request_ids":100},"hard_kill":{"active":0,"attempts":100,"billable_calls":100,"cache_hits":0,"max_active":2,"unique_request_ids":100},"mlflow_finalized":{"active":0,"attempts":100,"billable_calls":100,"cache_hits":0,"max_active":2,"unique_request_ids":100},"provider_response_received":{"active":0,"attempts":101,"billable_calls":100,"cache_hits":1,"max_active":2,"unique_request_ids":100},"raw_responses_persisted":{"active":0,"attempts":100,"billable_calls":100,"cache_hits":0,"max_active":2,"unique_request_ids":100},"run_linked":{"active":0,"attempts":100,"billable_calls":100,"cache_hits":0,"max_active":2,"unique_request_ids":100},"uninterrupted":{"active":0,"attempts":100,"billable_calls":100,"cache_hits":0,"max_active":2,"unique_request_ids":100}}} | `commands/27-platform-local-runtime.json`, `commands/26-integration-test-inventory.json`, `commands/41-metaflow-resume-ledgers.json` |
| 5 | MLflow contains the complete run contract and links back to Metaflow. | {"compose_pytest":{"passed":2,"runtime":137.51,"skipped":0,"warnings":0},"identity_matches":true,"local_runtime_pytest":{"passed":10,"runtime":184.18,"skipped":0,"warnings":0},"mlflow_lineage_records":3,"run_manifests":3} | `commands/27-platform-local-runtime.json`, `commands/30-compose-browser-sandbox-retry.json`, `artifacts/platform/demo-mlflow-lineage.jsonl` |
| 6 | Dataset and raw-response hashes verify from immutable storage. | {"immutable_failures":0,"immutable_verified":310,"manifest_artifact_union":310,"manifest_union_matches_verification":true} | `commands/30-compose-browser-sandbox-retry.json`, `artifacts/platform/immutable-artifact-verification.json`, `identity-reconciliation.json` |
| 7 | Missing cost or latency blocks promotion. | {"mechanical_pytest":{"passed":30,"runtime":4.65,"skipped":0,"warnings":3}} | `commands/31-mechanical-boundaries.json` |
| 8 | Gate failure blocks both UI and direct approval API. | {"blocked_approval_statuses":[409],"mechanical_pytest":{"passed":30,"runtime":4.65,"skipped":0,"warnings":3},"screenshot_hashes_and_sizes_match":true,"screenshot_identity_references_match":true} | `commands/30-compose-browser-sandbox-retry.json`, `commands/31-mechanical-boundaries.json`, `artifacts/platform/demo-api-transcript.jsonl`, `artifacts/platform/screenshots/02-candidate-a-gate-blocked.png` |
| 9 | Passing gates do not bypass human approval. | {"approval_events":2,"approval_tuples_match":true,"d411_confirmation_count":4,"d412_verdict":null,"failed_tuples_have_no_approval":true} | `commands/30-compose-browser-sandbox-retry.json`, `commands/31-mechanical-boundaries.json`, `artifacts/platform/demo-approval-events.jsonl`, `artifacts/platform/screenshots/06-candidate-b-eligible.png`, `d411-human-confirmation.json` |
| 10 | Serving rejects an unapproved exact version. | {"mechanical_pytest":{"passed":30,"runtime":4.65,"skipped":0,"warnings":3}} | `commands/30-compose-browser-sandbox-retry.json`, `commands/31-mechanical-boundaries.json` |
| 11 | Failed deployment leaves the current version active. | {"mechanical_pytest":{"passed":30,"runtime":4.65,"skipped":0,"warnings":3}} | `commands/30-compose-browser-sandbox-retry.json`, `commands/31-mechanical-boundaries.json` |
| 12 | Rollback restores the previous approved exact version. | {"deployment_actions":["deploy","deploy","rollback"],"deployment_generations":[1,2,3],"rollback_restores_generation_1_policy":true} | `commands/30-compose-browser-sandbox-retry.json`, `artifacts/platform/demo-deployment-events.jsonl`, `artifacts/platform/demo-api-transcript.jsonl` |
| 13 | Concurrent transition tests produce one active deployment. | {"compose_pytest":{"passed":2,"runtime":137.51,"skipped":0,"warnings":0},"mechanical_pytest":{"passed":30,"runtime":4.65,"skipped":0,"warnings":3}} | `commands/30-compose-browser-sandbox-retry.json`, `commands/31-mechanical-boundaries.json` |
| 14 | Demo evidence clearly distinguishes scripted and real provider results. | {"provider":{"external_deployment":false,"network_model_calls":false,"paid_calls":0,"type":"deterministic scripted replay"}} | `artifacts/platform/EVIDENCE_REVIEW.md`, `artifacts/platform/rehearsal-environment.json`, `artifacts/platform/demo-mlflow-lineage.jsonl` |
| 15 | No credentials, private payloads, or privileged benchmark data appear in artifacts. | {"pre_generation_scan":{"files_scanned":65,"final_deliverables_scanned":["REPORT.md","evidence-manifest.json"],"finding_counts":{"aws_access_keys":0,"host_identity_paths_or_emails":0,"local_absolute_paths":0,"local_demo_credentials":0,"private_key_markers":0,"privileged_json_fields":0,"unredacted_image_payloads":0},"schema_version":"pixelgym-d412-redaction-scan-v1"}} | `commands/40-redaction-scan.json`, `redaction-scan.json`, `artifacts/platform/screenshots/manifest.json` |
| 16 | Retention, backup, recovery, and known limitations are documented. | {"indexed_documents":["deploy/README.md","artifacts/platform/architecture.md","artifacts/platform/known-limitations.md"]} | `deploy/README.md`, `artifacts/platform/architecture.md`, `artifacts/platform/known-limitations.md` |

## Raw command inventory

Each JSON record stores the exact argv/command, cwd, public environment, UTC timestamps, process runtime, exit status, and complete combined output. Expected sandbox and no-match observations remain visible.

| Record | Exit | Runtime (s) | Parsed pytest summary |
| --- | ---: | ---: | --- |
| `commands/00-git-revision.json` | 0 | 0.010777 | `null` |
| `commands/01-worktree-status.json` | 0 | 0.020561 | `null` |
| `commands/02-python-version.json` | 0 | 0.008924 | `null` |
| `commands/03-os-architecture.json` | 0 | 0.003384 | `null` |
| `commands/04-docker-version.json` | 0 | 0.011585 | `null` |
| `commands/05-compose-version.json` | 0 | 0.06885 | `null` |
| `commands/06-create-dev-venv.json` | 0 | 0.770146 | `null` |
| `commands/07-install-dev.json` | 0 | 1.683529 | `null` |
| `commands/08-fast-suite.json` | 0 | 79.753683 | `{"passed": 679, "runtime": 79.38, "skipped": 5, "warnings": 3}` |
| `commands/09-environment-checker.json` | 0 | 0.308907 | `{"passed": 1, "runtime": 0.04}` |
| `commands/10-golden-trajectory.json` | 0 | 2.356237 | `null` |
| `commands/11-platform-unit-boundaries.json` | 0 | 45.655774 | `{"passed": 300, "runtime": 44.63, "warnings": 3}` |
| `commands/12-dependency-lock-check.json` | 0 | 0.051617 | `null` |
| `commands/13-ruff.json` | 0 | 0.095366 | `null` |
| `commands/14-pip-check.json` | 0 | 0.291111 | `null` |
| `commands/15-boundary-inventory.json` | 0 | 0.029445 | `null` |
| `commands/16-platform-sleep-scan.json` | 1 | 0.009659 | `null` |
| `commands/17-source-provenance.json` | 0 | 0.159637 | `null` |
| `commands/18-lock-sha256.json` | 0 | 0.033483 | `null` |
| `commands/19-dev-package-versions.json` | 0 | 0.087355 | `null` |
| `commands/20-create-integration-venv.json` | 0 | 0.900392 | `null` |
| `commands/21-install-platform-lock.json` | 0 | 0.57723 | `null` |
| `commands/22-install-repository-no-deps.json` | 0 | 2.088156 | `null` |
| `commands/23-playwright-chromium.json` | 0 | 0.637026 | `null` |
| `commands/24-integration-package-versions.json` | 0 | 0.071506 | `null` |
| `commands/25-integration-pip-check.json` | 0 | 0.286901 | `null` |
| `commands/26-integration-test-inventory.json` | 0 | 2.330692 | `null` |
| `commands/27-platform-local-runtime.json` | 0 | 184.887452 | `{"passed": 10, "runtime": 184.18}` |
| `commands/29-compose-browser.json` | 1 | 2.533275 | `null` |
| `commands/30-compose-browser-sandbox-retry.json` | 0 | 138.178497 | `{"passed": 2, "runtime": 137.51}` |
| `commands/31-mechanical-boundaries.json` | 0 | 5.92222 | `{"passed": 30, "runtime": 4.65, "warnings": 3}` |
| `commands/37-compose-cleanup-filtered.json` | 0 | 0.233022 | `null` |
| `commands/38-evidence-branch.json` | 0 | 0.018366 | `null` |
| `commands/40-redaction-scan.json` | 0 | 0.035229 | `null` |
| `commands/41-metaflow-resume-ledgers.json` | 0 | 182.17168 | `null` |

## Identity and redaction indexes

See `identity-reconciliation.json` for stored cross-file identities and counts, `redaction-scan.json` for prohibited-pattern counts, and `evidence-manifest.json` for SHA-256 and size metadata.
