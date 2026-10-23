# D5.10 raw evidence index

Evidence revision: `8327aac872715febe05d7c8e5534e68c2f39c2c2`

Evidence branch: `evidence/d59-extraction-results`

Observation window: `2026-10-23T20:19:43.394535+00:00` to `2026-10-23T20:25:30.661491+00:00`

Verdict: `null`

Verdict owner: project owner

This index copies and links stored observations only. It does not rerun commands, reinterpret gate semantics, declare a milestone verdict, approve public wording, or change human-owned checklist state.

## Path placeholders

| Placeholder | Meaning |
| --- | --- |
| `<home>` | Recording account home. |
| `<private-archive>` | Verified private archive root. |
| `<private-backup>` | Verified backup root on the same local disk. |
| `<private-run>` | Retained execution directory. |
| `<repository>` | Analysis checkout. |

## Recording notes

- This is a post-hoc same-task parser-sensitivity supplement. Original replication and historical D5.9 results, freezes and evidence indexes remain unchanged; 40 input files were checked byte for byte. No independent confirmatory claim is made.
- The directory names the merged-main base revision used to begin this analysis branch. Collection observed the developing exporter, tests, docs and generated artifacts as working-tree changes. It is not represented as a clean checkout of a later commit. The final manifest binds the delivered code and artifacts.
- The private recorder captured exact argv, combined output, exits, UTC times and runtime. Only local/private path prefixes were replaced by listed placeholders. No raw responses, secrets or authentication values were recorded. No provider, authentication, model, browser or OSWorld execution was requested.
- Record 01 streamed and checked stored journal objects/events, every assignment and its host result, frozen source/identity/sandbox bindings, per-invocation enforcement, counted retries, caps/runtime and both archive copies. Extraction receipt hashes, selected spans and dispatch order were checked without action reparsing or rescoring.
- Five confirmed-stopped timeouts retain unknown provider completion and usage. Each received one counted same-request retry. Login did not settle them. Token totals cover only reported responses. All 432 full outcomes remain; the four pilot assignments and reliability repetitions have separate denominators.
- Record 07 contains an initial incorrect test filename: exit 4, no tests run. Record 08 retains the corrected selection: 109 passed in 2.00 s under a guard rejecting network, OSWorld and wall-clock sleeps. Both observations are preserved.
- Checklist text and linked integration/full-gate observations are inherited historical records at their original revisions. Fresh observations are the offline stored-evidence audit and targeted tests. No checklist state is checked off and the verdict remains null.
- Both retained copies were rehashed and SQLite integrity checked. They occupy the same local disk. No new restore drill or off-host backup is claimed. Archive snapshots precede the final retention receipt in the working summary.

## Checklist evidence

Checklist text is copied verbatim from `f0bcbd6f3e379f880baeca233d4b7e05528bcfc5:plans/grounding-v5-agent-benchmark.md`. The observation column copies exit statuses and parsed pytest counts from the listed records.

| # | Checklist line | Source | Stored raw observation | Records | Artifacts |
| ---: | --- | --- | --- | --- | --- |
| 1 | Review raw commands, outputs, evidence integrity, limitations, benchmark verdict, and any public wording | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:658` | `00-git-revision`: exit 0<br>`01-audit-retained-campaign`: exit 0<br>`02-evidence-branch`: exit 0<br>`03-checklist-source`: exit 0<br>`04-verify-frozen-candidate`: exit 0<br>`05-frozen-sensitivity-analysis`: exit 0<br>`06-reproduce-sensitivity-analysis`: exit 0<br>`07-targeted-offline-tests`: exit 4; pytest {"no_tests_ran": true, "runtime": 0.0}<br>`08-targeted-offline-tests`: exit 0; pytest {"passed": 109, "runtime": 2.0}<br>`09-lint`: exit 0<br>`10-preserved-inputs`: exit 0<br>`11-public-wording-inventory`: exit 0<br>`12-worktree-status`: exit 0<br>`13-redaction-scan`: exit 0<br>`14-original-replication-analysis`: exit 0<br>`15-historical-analysis`: exit 0 | `commands/00-git-revision.json`, `commands/01-audit-retained-campaign.json`, `commands/02-evidence-branch.json`, `commands/03-checklist-source.json`, `commands/04-verify-frozen-candidate.json`, `commands/05-frozen-sensitivity-analysis.json`, `commands/06-reproduce-sensitivity-analysis.json`, `commands/07-targeted-offline-tests.json`, `commands/08-targeted-offline-tests.json`, `commands/09-lint.json`, `commands/10-preserved-inputs.json`, `commands/11-public-wording-inventory.json`, `commands/12-worktree-status.json`, `commands/13-redaction-scan.json`, `commands/14-original-replication-analysis.json`, `commands/15-historical-analysis.json` | `docs/v5-benchmark-design.md`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/00-git-revision.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/01-worktree-status.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/52-worktree-status-after-verifiers.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/82-worktree-status-after-integration.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/84-public-wording-inventory.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/85-worktree-status-before-redaction-scan.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/86-redaction-scan-collected-records.json`, `artifacts/grounding-v5-d59-replication-results/review-guide.md`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/REPORT.md`, `docs/v5-policy-sandbox.md`, `docs/d59-subscription-verification.md`, `scripts/README.md`, `artifacts/grounding-v5-d59-action-extraction-results/review-guide.md`, `artifacts/grounding-v5-d59-action-extraction-results/report.json`, `artifacts/grounding-v5-d59-action-extraction-results/sensitivity-analysis.md`, `artifacts/grounding-v5-d59-action-extraction-results/pilot-report.json`, `artifacts/grounding-v5-d59-action-extraction-results/preserved-inputs.json`, `artifacts/grounding-v5-d59-action-extraction-results/sensitivity-analysis.json`, `scripts/report_grounding_v5_d59_extraction.py`, `tests/unit/test_grounding_v5_d59_extraction_report.py`, `docs/d59-action-extraction-successor.md` |
| 2 | The human explicitly approves D5.1 and sequencing relative to the separate D4.12 gate. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:785` | no stored observation | none | `artifacts/grounding-v5-d56-calibration-approval.md`, `artifacts/decisions/d4.12.json` |
| 3 | Versioned schemas and disjoint development, calibration, and confirmatory seed lists are committed before task capture or model calls. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:786` | no stored observation | none | `artifacts/grounding-v5-contracts.md`, `artifacts/grounding-v5-manifests/development.json`, `artifacts/grounding-v5-manifests/calibration.json`, `artifacts/grounding-v5-manifests/calibration-d56.json`, `artifacts/grounding-v5-manifests/confirmatory.json`, `pixelgym/grounding/v5/seeds.py`, `pixelgym/grounding/v5/schemas/task.schema.json`, `pixelgym/grounding/v5/schemas/policy.schema.json`, `pixelgym/grounding/v5/schemas/attempt-event.schema.json`, `pixelgym/grounding/v5/schemas/memory-task.schema.json`, `pixelgym/grounding/v5/schemas/memory-task-v3.schema.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/53-v5-group-generator-partitions-admission.json` |
| 4 | Six generator families produce the frozen allocations and satisfy every admission rule. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:788` | no stored observation | none | `artifacts/grounding-v5-development-admission.json`, `artifacts/grounding-v5-d59-freeze/admission.json`, `artifacts/grounding-v5-d59-freeze/task-manifest.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/53-v5-group-generator-partitions-admission.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/43-verify-d59-freeze.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/48-verify-v5-at-historical-revisions.json` |
| 5 | Every declared valid and adversarial trace replays twice with identical semantic state and bitwise-identical corresponding screenshots. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:789` | no stored observation | none | `artifacts/grounding-v5-development-admission.json`, `artifacts/grounding-v5-development-sample/manifest.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/59-browser-integration.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/53-v5-group-generator-partitions-admission.json` |
| 6 | Golden policies reach reward `1.0` exactly once; all declared near-miss and reward-hacking mutations remain at `0.0`. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:791` | no stored observation | none | `artifacts/grounding-v5-development-admission.json`, `artifacts/grounding-v5-d59-freeze/admission.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/53-v5-group-generator-partitions-admission.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/15-golden-trajectory-check.json` |
| 7 | Stateful policy reset, policy-egress isolation, pre-send attempt persistence, unknown-attempt reconciliation, bounded deadline/cancellation settlement, post-attempt and post-dispatch state reconstruction, canonical parser-input recovery, atomic candidate/checkpoint persistence, sealed parser failure, exact backend-state restore or reconnect, durable dispatch ordering, invalid output, call caps, resume, authoritative replay, credential-free policy inputs, secret exclusion, redaction binding, and evidence integrity have no-cost failure-path tests. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:793` | no stored observation | none | `artifacts/grounding-v5-contracts.md`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/54-v5-group-stateful-harness-journal-resume-dispatch-restore.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/55-v5-group-credential-secret-redaction-integrity.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/57-v5-group-call-cap-planning.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/70-osworld-v5-integration-without-guest-image.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/74-osworld-v5-integration-linked-guest-image.json` |
| 8 | No-cost OS-level integration evidence denies unauthorized policy egress and preserves required provider-fake, backend, application, and controller traffic without internet or real-provider calls. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:799` | no stored observation | none | `artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/58-v5-sandbox-integration.json` |
| 9 | The documented fast suite passes without browser, network, OSWorld, provider credentials, or model calls. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:801` | no stored observation | none | `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/10-boundary-inventory.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/06-create-dev-venv.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/07-install-dev.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/80-fast-suite-ci-invocation.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/81-unit-suite-serial.json` |
| 10 | A free plan-only command reports exact environment-action, model-attempt, provider-control, and total wire-request caps for each approved phase. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:803` | no stored observation | none | `artifacts/grounding-v5-scripted-call-cap-plan.json`, `artifacts/grounding-v5-d59-freeze/execution-plan.json`, `artifacts/grounding-v5-d59-haiku-freeze/execution-plan.json`, `artifacts/grounding-v5-d59-haiku-api-retry-successor/execution-plan.json`, `artifacts/grounding-v5-d59-haiku-network-retry/execution-plan.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/57-v5-group-call-cap-planning.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/42-d59-recorded-source-revisions.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/43-verify-d59-freeze.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/45-verify-d59-haiku-freeze.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/46-verify-d59-haiku-retry-successor.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/47-verify-d59-haiku-network-retry.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/48-verify-v5-at-historical-revisions.json` |
| 11 | The agent stops for explicit calibration approval and again for explicit confirmatory approval. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:805` | no stored observation | none | `artifacts/grounding-v5-d56-calibration-approval.md`, `artifacts/grounding-v5-d58-final-design/decision.json`, `artifacts/grounding-v5-d59-haiku-selection/owner-selection.json`, `artifacts/grounding-v5-d59-haiku-execution/owner-approval.json`, `artifacts/grounding-v5-d59-haiku-api-retry-execution/owner-approval.json`, `artifacts/grounding-v5-d59-haiku-results/authorization-history.json` |
| 12 | The confirmatory report is generated only from sealed evidence, retains every failure, and separates primary, diagnostic, ablation, reliability, and exploratory results. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:807` | no stored observation | none | `artifacts/grounding-v5-d59-haiku-results/report.md`, `artifacts/grounding-v5-d59-haiku-results/report.json`, `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md`, `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.json`, `artifacts/grounding-v5-d59-haiku-results/publication-relation.json`, `artifacts/grounding-v5-d59-haiku-results/authorization-history.json`, `artifacts/grounding-v5-d59-freeze/analysis-plan.md`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/50-check-d59-confirmatory-analysis.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/49-report-d59-haiku-help.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/51-d59-private-run-directories-present.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/56-v5-group-metrics-statistics.json`, `artifacts/grounding-v5-d59-replication-results/report.json`, `artifacts/grounding-v5-d59-replication-results/pilot-report.json`, `artifacts/grounding-v5-d59-replication-results/confirmatory-analysis.json`, `artifacts/grounding-v5-d59-replication-results/confirmatory-analysis.md` |
| 13 | The agent reports raw milestone-gate commands, exit statuses, counts, runtimes, and full output without declaring the human gate passed or approving public wording. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:809` | no stored observation | none | none |

## Recorded exceptions, waivers, and limitations

Checklist row 1: `docs/v5-benchmark-design.md` line 151

> | D5.10: milestone verdict and public wording | Not declared; human-owned | — |

Checklist row 1: `artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json`

> "claim_boundary":"The successor may support the matched model-quality comparison but cannot claim OS-enforced policy isolation or independent denial of local process capabilities."

Checklist row 1: `docs/evidence-index.md` lines 85-89

> - **2026-09-30, D5.9 private journals.** The attempt and invocation journals for the D5.9 Haiku
>   execution were not retained after the public projection was published. They were untracked
>   files in a temporary checkout that was later removed. The digests recorded in `report.json`
>   under `source_artifacts` and `journal_integrity` were computed from those journals at
>   publication and cannot be re-verified against them. D5.9 closes on the published projection.

Checklist row 1: `artifacts/grounding-v5-d59-haiku-results/report.md` line 14

> The owner authorized continuation after malformed output and, later, after exhausted connection resets. Each failed assignment remains in the results. Prior assignments were not replayed; retries kept the same per-action and aggregate caps. These are disclosed changes to the original campaign stop rule, made after observing failures.

Checklist row 1: `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md` lines 26-35

> ## Executed policies
>
> The bound execution plan (digest `sha256:3f50d15f72244cfc8fd422cd16ab8dc6ab8edd873f6c9a92375472671fdcd092`) records the policies that ran, read from its `policy_manifests`:
>
> | Condition | Executed policy ID | Pre-registered policy ID | Code revision | Transport retry rule |
> |---|---|---|---|---|
> | history | `policy-ebadd1b6c8462c939495` | `policy-79441db33362e00a1ac6` | `b3167ff49576aec4001444ba3166d37cfc8dfda4` | `claude-one-stopped-timeout-or-connection-retry-v1` |
> | stateless | `policy-392778a54a9f0d242a93` | `policy-e1d746cd6a83ffb12a20` | `b3167ff49576aec4001444ba3166d37cfc8dfda4` | `claude-one-stopped-timeout-or-connection-retry-v1` |
>
> The executed policy IDs differ from the pre-registered policy IDs. The pre-registration record names the frozen Haiku pair; the plan that ran is a later versioned successor. The field-level differences are recorded in the successor execution plans, not in this analysis.

Checklist row 2: `artifacts/grounding-v5-d56-calibration-approval.md` line 32

> | D5.1 scope and sequencing | Explicit owner decision | Approved to proceed with v5 readiness work while D4.12 remains null |

Checklist row 8: `artifacts/grounding-v5-d56-calibration-approval.md` line 36

> | OS-level policy isolation | Real OS enforcement test with an allowed fake endpoint and denied unauthorized channels | Implemented and exercised on Darwin |

Checklist row 8: `artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json`

> "decision":"Waive OS-level sandbox enforcement for the exact selected Haiku D5.9 policy pair and retain the calibrated Claude Code CLI controls."

Checklist row 8: `artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json`

> "claim_boundary":"The successor may support the matched model-quality comparison but cannot claim OS-enforced policy isolation or independent denial of local process capabilities."

Checklist row 8: `docs/v5-benchmark-design.md` lines 153-156

> Calibration results are descriptive and are not a model ranking. The D5.9 Haiku policies ran under
> a recorded exception that waived OS-level sandbox enforcement, so that evidence cannot support an
> OS-enforced isolation claim
> ([exception](../artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json)).

Checklist row 11: `artifacts/grounding-v5-d59-haiku-results/report.md` line 14

> The owner authorized continuation after malformed output and, later, after exhausted connection resets. Each failed assignment remains in the results. Prior assignments were not replayed; retries kept the same per-action and aggregate caps. These are disclosed changes to the original campaign stop rule, made after observing failures.

Checklist row 12: `docs/evidence-index.md` lines 85-89

> - **2026-09-30, D5.9 private journals.** The attempt and invocation journals for the D5.9 Haiku
>   execution were not retained after the public projection was published. They were untracked
>   files in a temporary checkout that was later removed. The digests recorded in `report.json`
>   under `source_artifacts` and `journal_integrity` were computed from those journals at
>   publication and cannot be re-verified against them. D5.9 closes on the published projection.

Checklist row 12: `artifacts/grounding-v5-d59-haiku-results/report.md` line 14

> The owner authorized continuation after malformed output and, later, after exhausted connection resets. Each failed assignment remains in the results. Prior assignments were not replayed; retries kept the same per-action and aggregate caps. These are disclosed changes to the original campaign stop rule, made after observing failures.

Checklist row 12: `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md` line 137

> The stop rule was amended after repeated intermittent network failures to allow for retries in the case of intermittent network issues. Each failed assignment remains in the results. No prior assignment was replayed.

Checklist row 12: `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md` lines 122-133

> ## Diagnostics not computable from the public projection
>
> These are not estimated.
>
> - critical-decision accuracy: needs per-step decision annotations held in the private journals.
> - dependency retention: needs per-step state and response records held in the private journals.
> - recovery success: needs error-injection and recovery annotations held in the private journals.
> - irreversible-error rate: the projection does not distinguish wrong commits from other unsuccessful terminations.
> - path overhead: the projection carries action totals but not the action trace the diagnostic is defined over.
> - loop rate: needs the per-episode action trace held in the private journals.
> - latency: the projection carries no timing fields.
> - tokens: the projection carries no token counts.

Checklist row 12: `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md` lines 26-35

> ## Executed policies
>
> The bound execution plan (digest `sha256:3f50d15f72244cfc8fd422cd16ab8dc6ab8edd873f6c9a92375472671fdcd092`) records the policies that ran, read from its `policy_manifests`:
>
> | Condition | Executed policy ID | Pre-registered policy ID | Code revision | Transport retry rule |
> |---|---|---|---|---|
> | history | `policy-ebadd1b6c8462c939495` | `policy-79441db33362e00a1ac6` | `b3167ff49576aec4001444ba3166d37cfc8dfda4` | `claude-one-stopped-timeout-or-connection-retry-v1` |
> | stateless | `policy-392778a54a9f0d242a93` | `policy-e1d746cd6a83ffb12a20` | `b3167ff49576aec4001444ba3166d37cfc8dfda4` | `claude-one-stopped-timeout-or-connection-retry-v1` |
>
> The executed policy IDs differ from the pre-registered policy IDs. The pre-registration record names the frozen Haiku pair; the plan that ran is a later versioned successor. The field-level differences are recorded in the successor execution plans, not in this analysis.

## Records withdrawn before packaging

None.

## Items not run

- `Full gate refresh and integration`: No fresh whole-suite, browser, OSWorld or live sandbox integration execution. Earlier observations remain historical at their recorded revisions.
- `Restore drill and independent off-host backup`: Not performed; both existing local copies verified, no evidence deleted.
- `Undefined trace estimators and complete provider usage`: No post-result estimator or usage imputation invented. Five timeouts retain unknown completion/usage.

## Public wording inventory

Copied verbatim from `commands/11-public-wording-inventory.json` (exit 0). Each line is `file:line:text` as printed by:

`rg -n 'D5.10|parser.sensitivity|post.hoc|model ranking' README.md docs/v5-benchmark-design.md docs/evidence-index.md docs/d59-action-extraction-successor.md`

```
docs/d59-action-extraction-successor.md:9:This was prepared as a disabled candidate for a **post-hoc parser-sensitivity study on
docs/d59-action-extraction-successor.md:93:post-hoc reporting rule, and assignment namespaces. Caps, allocation, statistical
docs/d59-action-extraction-successor.md:107:validate against its new digest. D5.10 remains a human decision.
docs/d59-action-extraction-successor.md:135:Original replication evidence and the D5.10 human decision remain separate.
docs/d59-action-extraction-successor.md:140:[separate D5.10 review package](../artifacts/grounding-v5-d59-action-extraction-results/review-guide.md)
docs/d59-action-extraction-successor.md:143:candidate remain unchanged. This is post-hoc sensitivity on inspected tasks;
docs/d59-action-extraction-successor.md:144:the D5.10 verdict and any public wording remain human-owned.
docs/evidence-index.md:21:| What did the PR196 Luna/Haiku calibration record? | [Consolidated report](../artifacts/grounding-v5-pr196-calibration/report.md), [structured results](../artifacts/grounding-v5-pr196-calibration/results.json), and [snapshot bindings](../artifacts/grounding-v5-pr196-calibration/sources.json) retain final outcomes, paired results, memory observations, interrupted attempts, all earlier Haiku cohorts, and cost. Haiku combines CLI and API execution; these are descriptive calibration results, not a model ranking or final evaluation. |
docs/evidence-index.md:22:| What did the fresh Haiku CLI replication record? | [Response-free report](../artifacts/grounding-v5-haiku-cli-replication/report.md), [structured results](../artifacts/grounding-v5-haiku-cli-replication/results.json), and [audited snapshot](../artifacts/grounding-v5-haiku-cli-replication/snapshot.json) retain all 100 fresh PR196-panel outcomes from exact `claude-haiku-4-5-20251001` through Claude Code CLI: history 28/50 successes and stateless 5/50, with every invalid output retained. The single-route replication is calibration evidence, not D5.9 execution, a model ranking, or a human gate. |
docs/evidence-index.md:23:| What did the D5.9 parser-sensitivity successor record? | [Owner review guide](../artifacts/grounding-v5-d59-action-extraction-results/review-guide.md), [frozen-mechanics sensitivity analysis](../artifacts/grounding-v5-d59-action-extraction-results/sensitivity-analysis.md), and [raw commands](../artifacts/grounding-v5-d510/8327aac872715febe05d7c8e5534e68c2f39c2c2/REPORT.md) retain all 432 outcomes and five unresolved timeouts. This is post-hoc sensitivity on inspected tasks, not independent confirmation; original results are preserved and D5.10 remains human-owned. |
docs/evidence-index.md:27:| What did the D5.9 Haiku execution record? | The [generated report](../artifacts/grounding-v5-d59-haiku-results/report.md) and [per-assignment projection](../artifacts/grounding-v5-d59-haiku-results/report.json) retain all 432 outcomes, with primary and reliability cases separated. The [authorization history](../artifacts/grounding-v5-d59-haiku-results/authorization-history.json) discloses the owner-approved continuations after malformed output and exhausted connection resets. Prior failures, reservations, and the original runtime deadline were retained. Raw journals remain local; D5.10 and public model-quality claims remain human-owned. |
docs/evidence-index.md:28:| What does the D5.9 confirmatory analysis show? | The [confirmatory analysis](../artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md) and its [JSON source](../artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.json) are generated by `scripts/analyze_grounding_v5_d59.py` from the public projection and the bound execution plan. They report the pre-registered paired McNemar test and family-stratified logical-cluster bootstrap for history minus stateless on the primary phase, the reliability subset separately, and labelled exploratory rates, termination profiles, and resource use. They name the executed policy IDs read from the bound execution plan beside the pre-registered IDs, which differ. Diagnostics that need the private journals are listed as not computable. D5.10 is the owner's review. |
docs/evidence-index.md:30:| Where is the completed replication prepared for D5.10 review? | Start with the [review guide](../artifacts/grounding-v5-d59-replication-results/review-guide.md), [generated analysis](../artifacts/grounding-v5-d59-replication-results/confirmatory-analysis.md), and [raw command index](../artifacts/grounding-v5-d510/c398557ca05b8cc87d2f4371fe6b1335d427e5c5/REPORT.md). The response-free full projection retains all 432 assignments; the operational pilot remains separate. The package discloses the unresolved confirmed-stopped timeout, incomplete provider usage, disagreement with historical D5.9, undefined secondary estimators, and two archive copies on the same local disk. Historical full-gate observations remain at their recorded revision; this supplement adds the offline replication audit and targeted regression checks, not a fresh full-gate run or a D5.10 verdict. A separately labelled exploratory [parse-failure signature](../artifacts/grounding-v5-d59-replication-results/prose-signature-diagnostic.md), regenerated read-only from the retained journal by `scripts/analyze_grounding_v5_d59_replication_prose_signature.py`, publishes counts only and is never pooled with the primary result. |
docs/v5-benchmark-design.md:4:- **Status:** in progress; no v5 milestone verdict (D5.10) has been declared
docs/v5-benchmark-design.md:150:| D5.9 replication: pre-registered successor beside D5.9 | All 432 assignments recorded; frozen analysis and retained-journal audit prepared for owner review. One timeout retains unknown provider completion/usage. The historical and replication differences exceed the frozen disagreement threshold; both are reported. An exploratory [parse-failure signature](../artifacts/grounding-v5-d59-replication-results/prose-signature-diagnostic.md) from the retained journal is reported separately. D5.10 remains human-owned. | [replication review guide](../artifacts/grounding-v5-d59-replication-results/review-guide.md), [analysis](../artifacts/grounding-v5-d59-replication-results/confirmatory-analysis.md), [replication plan](../artifacts/grounding-v5-d59-replication/execution-plan.json), [addendum](../artifacts/grounding-v5-d59-freeze/analysis-plan.md) |
docs/v5-benchmark-design.md:151:| D5.10: milestone verdict and public wording | Not declared; human-owned | — |
docs/v5-benchmark-design.md:153:Calibration results are descriptive and are not a model ranking. The D5.9 Haiku policies ran under
README.md:58:| v5 agent benchmark and calibration | In progress; v5 benchmark verdict (D5.10) not declared | [v5 design](docs/v5-benchmark-design.md), [evidence index](docs/evidence-index.md) |
```

## Raw command inventory

Each JSON record stores the exact argv/command, cwd, public environment, UTC timestamps, process runtime, exit status, and complete combined output. Failed and skipped observations remain in the inventory.

| Record | Exit | Runtime (s) | Parsed pytest summary |
| --- | ---: | ---: | --- |
| `commands/00-git-revision.json` | 0 | 0.015805 | `null` |
| `commands/01-audit-retained-campaign.json` | 0 | 46.252012 | `null` |
| `commands/02-evidence-branch.json` | 0 | 0.017062 | `null` |
| `commands/03-checklist-source.json` | 0 | 0.020232 | `null` |
| `commands/04-verify-frozen-candidate.json` | 0 | 9.555637 | `null` |
| `commands/05-frozen-sensitivity-analysis.json` | 0 | 0.721932 | `null` |
| `commands/06-reproduce-sensitivity-analysis.json` | 0 | 0.769021 | `null` |
| `commands/07-targeted-offline-tests.json` | 4 | 0.794386 | `{"no_tests_ran": true, "runtime": 0.0}` |
| `commands/08-targeted-offline-tests.json` | 0 | 2.836937 | `{"passed": 109, "runtime": 2.0}` |
| `commands/09-lint.json` | 0 | 0.020548 | `null` |
| `commands/10-preserved-inputs.json` | 0 | 0.04883 | `null` |
| `commands/11-public-wording-inventory.json` | 0 | 0.010266 | `null` |
| `commands/12-worktree-status.json` | 0 | 0.038466 | `null` |
| `commands/13-redaction-scan.json` | 0 | 0.073239 | `null` |
| `commands/14-original-replication-analysis.json` | 0 | 1.019113 | `null` |
| `commands/15-historical-analysis.json` | 0 | 0.681798 | `null` |

## Integrity and redaction indexes

`evidence-manifest.json` lists the SHA-256 and size of every other file in this directory and of each referenced repository artifact. `redaction-scan.json` lists the prohibited-pattern counts for every file in this directory except itself.
