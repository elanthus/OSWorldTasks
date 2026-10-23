# D5.10 raw evidence index

Evidence revision: `168598c243df24ba4f773cbc5696a1572532f532`

Evidence branch: `evidence/d510-fresh-validation`

Observation window: `2026-10-23T21:16:28.135091+00:00` to `2026-10-23T21:41:46.391970+00:00`

Verdict: `null`

Verdict owner: project owner

This index copies and links stored observations only. It does not rerun commands, reinterpret gate semantics, declare a milestone verdict, approve public wording, or change human-owned checklist state.

## Path placeholders

| Placeholder | Meaning |
| --- | --- |
| `<home>` | Recording account home. |
| `<path-0>` | Collection checkout; records 04–10 used relative cwd, so standalone periods are also redacted by the recorder. |
| `<path-1>` | Private retained execution directory. |
| `<path-2>` | Local private archive. |
| `<path-3>` | Backup on the same local disk. |
| `<path-4>` | Fresh private restore destination. |
| `<path-5>` | Private restore receipt. |
| `<system-temp>` | System temporary directory. |

## Recording notes

- This supplement refreshes no-call browser, OSWorld and CLI sandbox observations, investigates the six unit skips, and restores all four pilot/full archive and backup copies. It does not change any result or freeze, confer a milestone verdict, or authorize public wording.
- The directory names merged-main base 168598c243df24ba4f773cbc5696a1572532f532. Record 12 binds committed restore-helper source f3fdf5f2aff9b4d7eae7556d92ba3596ebc13d98. Integrations and the drill were collected during preparation of that source; the final whole-unit run used its clean tracked snapshot. The delivered manifest binds final artifacts.
- Raw recorder output is retained, including failed runs. Records 04–10 used cwd dot: automatic path redaction replaces standalone periods with <path-0> as well as local prefixes. Later records use absolute cwd. No response, secret or credential value was collected.
- Record 07: 9 failed, 3277 passed, 6 skipped; source-fingerprint tests overlapped helper edits, and the new script inventory entry was then missing. Record 10: 64 failed, 3228 passed, 6 skipped; tracked README edits violated clean-worktree evidence guards. Both observations remain visible. Record 11 reruns the suite on committed clean source; no evidence guard was relaxed.
- Requested integrations had no skips. Record 05 uses a synthetic placeholder token and refuses provider egress; it makes no model request. Browser checks use the synthetic form; OSWorld uses an existing pinned local guest and Docker image. No gated assets were fetched. Shared runtime provenance is hash-bound to each integration observation.
- Restore verifies archive manifests, file digests, SQLite integrity, journal/accounting/identity/extraction bindings and exact published projections. It does not replay tasks or agent actions. Archive snapshots precede the final retention receipt: a separate audit view supplies only the hash-bound published receipt after proving every other summary field identical. Actual restored bytes remain unchanged.
- Both pilot and full copies were restored from each store. All four audited projections match publication; five full-run unresolved timeouts remain unresolved. Restored files, source evidence and both retained copies were not deleted; no cleanup authorization was issued.
- Original result and freeze inputs (46 files) were rehashed unchanged. Primary/reliability/pilot denominators and post-hoc same-task parser-sensitivity interpretation remain unchanged. The original Haiku journal-loss and sandbox-exception disclosures remain historical limits; fresh sandbox tests do not retroactively sandbox those runs.
- Linked earlier checklist evidence remains historical at its recorded revision. New observations supplement it without checking checklist boxes. The owner retains the D5.10 verdict and public-wording decisions.
- Before final delivery the CLI was hardened to reject receipt output paths inside any run, archive, backup or restore root. Record 20 retains 12 guarded regression results; the completed four-copy drill used an already separate output path and its observations remain unchanged.

## Checklist evidence

Checklist text is copied verbatim from `f0bcbd6f3e379f880baeca233d4b7e05528bcfc5:plans/grounding-v5-agent-benchmark.md`. The observation column copies exit statuses and parsed pytest counts from the listed records.

| # | Checklist line | Source | Stored raw observation | Records | Artifacts |
| ---: | --- | --- | --- | --- | --- |
| 1 | Review raw commands, outputs, evidence integrity, limitations, benchmark verdict, and any public wording | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:658` | `00-git-revision`: exit 0<br>`02-evidence-branch`: exit 0<br>`03-checklist-source`: exit 0<br>`04-browser`: exit 0; pytest {"passed": 18, "runtime": 136.67}<br>`05-cli-sandbox`: exit 0; pytest {"passed": 5, "runtime": 75.21}<br>`06-osworld`: exit 0; pytest {"passed": 1, "runtime": 329.13, "warnings": 6}<br>`07-unit-skip-investigation`: exit 1; pytest {"failed": 9, "passed": 3277, "runtime": 188.82, "skipped": 6, "warnings": 16}<br>`08-restore-overlay-regressions`: exit 0; pytest {"passed": 6, "runtime": 0.11}<br>`09-restore-drill`: exit 0<br>`10-unit-stable-source`: exit 1; pytest {"failed": 64, "passed": 3228, "runtime": 136.13, "skipped": 6, "warnings": 16}<br>`11-unit-clean-committed-source`: exit 0; pytest {"passed": 3294, "runtime": 151.14, "skipped": 6, "warnings": 16}<br>`12-source-commit`: exit 0<br>`13-restore-regressions`: exit 0; pytest {"passed": 8, "runtime": 3.46}<br>`14-verify-frozen-candidate`: exit 0<br>`15-reproduce-sensitivity-analysis`: exit 0<br>`16-public-wording-inventory`: exit 0<br>`17-provider-closed`: exit 0<br>`18-lint`: exit 0<br>`19-worktree-status`: exit 0<br>`20-receipt-path-regressions`: exit 0; pytest {"passed": 12, "runtime": 2.32} | `commands/00-git-revision.json`, `commands/02-evidence-branch.json`, `commands/03-checklist-source.json`, `commands/04-browser.json`, `commands/05-cli-sandbox.json`, `commands/06-osworld.json`, `commands/07-unit-skip-investigation.json`, `commands/08-restore-overlay-regressions.json`, `commands/09-restore-drill.json`, `commands/10-unit-stable-source.json`, `commands/11-unit-clean-committed-source.json`, `commands/12-source-commit.json`, `commands/13-restore-regressions.json`, `commands/14-verify-frozen-candidate.json`, `commands/15-reproduce-sensitivity-analysis.json`, `commands/16-public-wording-inventory.json`, `commands/17-provider-closed.json`, `commands/18-lint.json`, `commands/19-worktree-status.json`, `commands/20-receipt-path-regressions.json` | `docs/v5-benchmark-design.md`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/00-git-revision.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/01-worktree-status.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/52-worktree-status-after-verifiers.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/82-worktree-status-after-integration.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/84-public-wording-inventory.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/85-worktree-status-before-redaction-scan.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/86-redaction-scan-collected-records.json`, `artifacts/grounding-v5-d59-replication-results/review-guide.md`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/REPORT.md`, `docs/v5-policy-sandbox.md`, `docs/d59-subscription-verification.md`, `scripts/README.md`, `artifacts/grounding-v5-d59-action-extraction-results/review-guide.md`, `artifacts/grounding-v5-d59-action-extraction-results/report.json`, `artifacts/grounding-v5-d59-action-extraction-results/sensitivity-analysis.md`, `artifacts/grounding-v5-d59-action-extraction-results/pilot-report.json`, `artifacts/grounding-v5-d59-action-extraction-results/preserved-inputs.json`, `artifacts/grounding-v5-d59-action-extraction-results/sensitivity-analysis.json`, `scripts/report_grounding_v5_d59_extraction.py`, `tests/unit/test_grounding_v5_d59_extraction_report.py`, `docs/d59-action-extraction-successor.md`, `scripts/restore_grounding_v5_d510.py`, `tests/unit/test_grounding_v5_d510_restore.py`, `artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/integration-provenance.json`, `artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/integration-observations.json`, `artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/skip-investigation.json`, `artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/restore-drill.json`, `artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/preserved-inputs.json`, `artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/REVIEW-NOTE.md` |
| 2 | The human explicitly approves D5.1 and sequencing relative to the separate D4.12 gate. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:785` | no stored observation | none | `artifacts/grounding-v5-d56-calibration-approval.md`, `artifacts/decisions/d4.12.json` |
| 3 | Versioned schemas and disjoint development, calibration, and confirmatory seed lists are committed before task capture or model calls. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:786` | no stored observation | none | `artifacts/grounding-v5-contracts.md`, `artifacts/grounding-v5-manifests/development.json`, `artifacts/grounding-v5-manifests/calibration.json`, `artifacts/grounding-v5-manifests/calibration-d56.json`, `artifacts/grounding-v5-manifests/confirmatory.json`, `pixelgym/grounding/v5/seeds.py`, `pixelgym/grounding/v5/schemas/task.schema.json`, `pixelgym/grounding/v5/schemas/policy.schema.json`, `pixelgym/grounding/v5/schemas/attempt-event.schema.json`, `pixelgym/grounding/v5/schemas/memory-task.schema.json`, `pixelgym/grounding/v5/schemas/memory-task-v3.schema.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/53-v5-group-generator-partitions-admission.json` |
| 4 | Six generator families produce the frozen allocations and satisfy every admission rule. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:788` | `04-browser`: exit 0; pytest {"passed": 18, "runtime": 136.67} | `commands/04-browser.json` | `artifacts/grounding-v5-development-admission.json`, `artifacts/grounding-v5-d59-freeze/admission.json`, `artifacts/grounding-v5-d59-freeze/task-manifest.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/53-v5-group-generator-partitions-admission.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/43-verify-d59-freeze.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/48-verify-v5-at-historical-revisions.json` |
| 5 | Every declared valid and adversarial trace replays twice with identical semantic state and bitwise-identical corresponding screenshots. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:789` | `04-browser`: exit 0; pytest {"passed": 18, "runtime": 136.67} | `commands/04-browser.json` | `artifacts/grounding-v5-development-admission.json`, `artifacts/grounding-v5-development-sample/manifest.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/59-browser-integration.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/53-v5-group-generator-partitions-admission.json` |
| 6 | Golden policies reach reward `1.0` exactly once; all declared near-miss and reward-hacking mutations remain at `0.0`. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:791` | `04-browser`: exit 0; pytest {"passed": 18, "runtime": 136.67} | `commands/04-browser.json` | `artifacts/grounding-v5-development-admission.json`, `artifacts/grounding-v5-d59-freeze/admission.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/53-v5-group-generator-partitions-admission.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/15-golden-trajectory-check.json` |
| 7 | Stateful policy reset, policy-egress isolation, pre-send attempt persistence, unknown-attempt reconciliation, bounded deadline/cancellation settlement, post-attempt and post-dispatch state reconstruction, canonical parser-input recovery, atomic candidate/checkpoint persistence, sealed parser failure, exact backend-state restore or reconnect, durable dispatch ordering, invalid output, call caps, resume, authoritative replay, credential-free policy inputs, secret exclusion, redaction binding, and evidence integrity have no-cost failure-path tests. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:793` | `06-osworld`: exit 0; pytest {"passed": 1, "runtime": 329.13, "warnings": 6}<br>`09-restore-drill`: exit 0<br>`13-restore-regressions`: exit 0; pytest {"passed": 8, "runtime": 3.46}<br>`20-receipt-path-regressions`: exit 0; pytest {"passed": 12, "runtime": 2.32} | `commands/06-osworld.json`, `commands/09-restore-drill.json`, `commands/13-restore-regressions.json`, `commands/20-receipt-path-regressions.json` | `artifacts/grounding-v5-contracts.md`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/54-v5-group-stateful-harness-journal-resume-dispatch-restore.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/55-v5-group-credential-secret-redaction-integrity.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/57-v5-group-call-cap-planning.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/70-osworld-v5-integration-without-guest-image.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/74-osworld-v5-integration-linked-guest-image.json` |
| 8 | No-cost OS-level integration evidence denies unauthorized policy egress and preserves required provider-fake, backend, application, and controller traffic without internet or real-provider calls. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:799` | `05-cli-sandbox`: exit 0; pytest {"passed": 5, "runtime": 75.21} | `commands/05-cli-sandbox.json` | `artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/58-v5-sandbox-integration.json` |
| 9 | The documented fast suite passes without browser, network, OSWorld, provider credentials, or model calls. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:801` | `07-unit-skip-investigation`: exit 1; pytest {"failed": 9, "passed": 3277, "runtime": 188.82, "skipped": 6, "warnings": 16}<br>`10-unit-stable-source`: exit 1; pytest {"failed": 64, "passed": 3228, "runtime": 136.13, "skipped": 6, "warnings": 16}<br>`11-unit-clean-committed-source`: exit 0; pytest {"passed": 3294, "runtime": 151.14, "skipped": 6, "warnings": 16} | `commands/07-unit-skip-investigation.json`, `commands/10-unit-stable-source.json`, `commands/11-unit-clean-committed-source.json` | `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/10-boundary-inventory.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/06-create-dev-venv.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/07-install-dev.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/80-fast-suite-ci-invocation.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/81-unit-suite-serial.json` |
| 10 | A free plan-only command reports exact environment-action, model-attempt, provider-control, and total wire-request caps for each approved phase. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:803` | `14-verify-frozen-candidate`: exit 0 | `commands/14-verify-frozen-candidate.json` | `artifacts/grounding-v5-scripted-call-cap-plan.json`, `artifacts/grounding-v5-d59-freeze/execution-plan.json`, `artifacts/grounding-v5-d59-haiku-freeze/execution-plan.json`, `artifacts/grounding-v5-d59-haiku-api-retry-successor/execution-plan.json`, `artifacts/grounding-v5-d59-haiku-network-retry/execution-plan.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/57-v5-group-call-cap-planning.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/42-d59-recorded-source-revisions.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/43-verify-d59-freeze.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/45-verify-d59-haiku-freeze.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/46-verify-d59-haiku-retry-successor.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/47-verify-d59-haiku-network-retry.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/48-verify-v5-at-historical-revisions.json` |
| 11 | The agent stops for explicit calibration approval and again for explicit confirmatory approval. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:805` | no stored observation | none | `artifacts/grounding-v5-d56-calibration-approval.md`, `artifacts/grounding-v5-d58-final-design/decision.json`, `artifacts/grounding-v5-d59-haiku-selection/owner-selection.json`, `artifacts/grounding-v5-d59-haiku-execution/owner-approval.json`, `artifacts/grounding-v5-d59-haiku-api-retry-execution/owner-approval.json`, `artifacts/grounding-v5-d59-haiku-results/authorization-history.json` |
| 12 | The confirmatory report is generated only from sealed evidence, retains every failure, and separates primary, diagnostic, ablation, reliability, and exploratory results. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:807` | `09-restore-drill`: exit 0<br>`15-reproduce-sensitivity-analysis`: exit 0 | `commands/09-restore-drill.json`, `commands/15-reproduce-sensitivity-analysis.json` | `artifacts/grounding-v5-d59-haiku-results/report.md`, `artifacts/grounding-v5-d59-haiku-results/report.json`, `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md`, `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.json`, `artifacts/grounding-v5-d59-haiku-results/publication-relation.json`, `artifacts/grounding-v5-d59-haiku-results/authorization-history.json`, `artifacts/grounding-v5-d59-freeze/analysis-plan.md`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/50-check-d59-confirmatory-analysis.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/49-report-d59-haiku-help.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/51-d59-private-run-directories-present.json`, `artifacts/grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/commands/56-v5-group-metrics-statistics.json`, `artifacts/grounding-v5-d59-replication-results/report.json`, `artifacts/grounding-v5-d59-replication-results/pilot-report.json`, `artifacts/grounding-v5-d59-replication-results/confirmatory-analysis.json`, `artifacts/grounding-v5-d59-replication-results/confirmatory-analysis.md` |
| 13 | The agent reports raw milestone-gate commands, exit statuses, counts, runtimes, and full output without declaring the human gate passed or approving public wording. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:809` | `00-git-revision`: exit 0<br>`02-evidence-branch`: exit 0<br>`03-checklist-source`: exit 0<br>`04-browser`: exit 0; pytest {"passed": 18, "runtime": 136.67}<br>`05-cli-sandbox`: exit 0; pytest {"passed": 5, "runtime": 75.21}<br>`06-osworld`: exit 0; pytest {"passed": 1, "runtime": 329.13, "warnings": 6}<br>`07-unit-skip-investigation`: exit 1; pytest {"failed": 9, "passed": 3277, "runtime": 188.82, "skipped": 6, "warnings": 16}<br>`08-restore-overlay-regressions`: exit 0; pytest {"passed": 6, "runtime": 0.11}<br>`09-restore-drill`: exit 0<br>`10-unit-stable-source`: exit 1; pytest {"failed": 64, "passed": 3228, "runtime": 136.13, "skipped": 6, "warnings": 16}<br>`11-unit-clean-committed-source`: exit 0; pytest {"passed": 3294, "runtime": 151.14, "skipped": 6, "warnings": 16}<br>`12-source-commit`: exit 0<br>`13-restore-regressions`: exit 0; pytest {"passed": 8, "runtime": 3.46}<br>`14-verify-frozen-candidate`: exit 0<br>`15-reproduce-sensitivity-analysis`: exit 0<br>`16-public-wording-inventory`: exit 0<br>`17-provider-closed`: exit 0<br>`18-lint`: exit 0<br>`19-worktree-status`: exit 0<br>`20-receipt-path-regressions`: exit 0; pytest {"passed": 12, "runtime": 2.32} | `commands/00-git-revision.json`, `commands/02-evidence-branch.json`, `commands/03-checklist-source.json`, `commands/04-browser.json`, `commands/05-cli-sandbox.json`, `commands/06-osworld.json`, `commands/07-unit-skip-investigation.json`, `commands/08-restore-overlay-regressions.json`, `commands/09-restore-drill.json`, `commands/10-unit-stable-source.json`, `commands/11-unit-clean-committed-source.json`, `commands/12-source-commit.json`, `commands/13-restore-regressions.json`, `commands/14-verify-frozen-candidate.json`, `commands/15-reproduce-sensitivity-analysis.json`, `commands/16-public-wording-inventory.json`, `commands/17-provider-closed.json`, `commands/18-lint.json`, `commands/19-worktree-status.json`, `commands/20-receipt-path-regressions.json` | none |

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

- `Off-host restore and independent backup`: Four restores verified both pilot/full copies on this host. Both retained copies remain on the same local disk; no off-host recovery was performed.
- `Undefined trace estimators and complete provider usage`: No post-result estimator or usage imputation invented. Five confirmed-stopped timeouts retain unknown provider completion/usage.
- `Historical optional PNG bytes and absent decision provenance`: Two release-hosted image sets were not fetched. Two original decision records have no reviewed revision or tag; these metadata were preserved rather than invented.

## Public wording inventory

Copied verbatim from `commands/16-public-wording-inventory.json` (exit 0). Each line is `file:line:text` as printed by:

`rg -n 'D5.10|parser-sensitivity|post-hoc|sandbox|isolation' README.md docs/v5-benchmark-design.md docs/d59-action-extraction-successor.md`

```
README.md:58:| v5 agent benchmark and calibration | In progress; v5 benchmark verdict (D5.10) not declared | [v5 design](docs/v5-benchmark-design.md), [evidence index](docs/evidence-index.md) |
docs/v5-benchmark-design.md:4:- **Status:** in progress; no v5 milestone verdict (D5.10) has been declared
docs/v5-benchmark-design.md:48:- Provider access is the only outbound network use allowed from the policy sandbox. The
docs/v5-benchmark-design.md:49:  kernel-enforced launch sandbox and its egress proxy are described in the
docs/v5-benchmark-design.md:50:  [policy sandbox design](v5-policy-sandbox.md); the D5.9 Haiku pair ran without it.
docs/v5-benchmark-design.md:78:digests, prompts, reducer, parser, memory policy, sandbox and endpoint allowlist, coordinate
docs/v5-benchmark-design.md:150:| D5.9 replication: pre-registered successor beside D5.9 | All 432 assignments recorded; frozen analysis and retained-journal audit prepared for owner review. One timeout retains unknown provider completion/usage. The historical and replication differences exceed the frozen disagreement threshold; both are reported. An exploratory [parse-failure signature](../artifacts/grounding-v5-d59-replication-results/prose-signature-diagnostic.md) from the retained journal is reported separately. D5.10 remains human-owned. | [replication review guide](../artifacts/grounding-v5-d59-replication-results/review-guide.md), [analysis](../artifacts/grounding-v5-d59-replication-results/confirmatory-analysis.md), [replication plan](../artifacts/grounding-v5-d59-replication/execution-plan.json), [addendum](../artifacts/grounding-v5-d59-freeze/analysis-plan.md) |
docs/v5-benchmark-design.md:151:| D5.10: milestone verdict and public wording | Not declared; human-owned | — |
docs/v5-benchmark-design.md:154:a recorded exception that waived OS-level sandbox enforcement, so that evidence cannot support an
docs/v5-benchmark-design.md:155:OS-enforced isolation claim
docs/d59-action-extraction-successor.md:9:This was prepared as a disabled candidate for a **post-hoc parser-sensitivity study on
docs/d59-action-extraction-successor.md:71:The existing stream parser, credential-output rejection, kernel sandbox, egress
docs/d59-action-extraction-successor.md:74:the parser identity and policy IDs. Its sandbox runtime digest changes to bind the
docs/d59-action-extraction-successor.md:93:post-hoc reporting rule, and assignment namespaces. Caps, allocation, statistical
docs/d59-action-extraction-successor.md:94:mechanics, model, prompt, route, stop rule, retry budgets, sandbox enforcement and
docs/d59-action-extraction-successor.md:107:validate against its new digest. D5.10 remains a human decision.
docs/d59-action-extraction-successor.md:119:phase approval before any authentication or sandbox probe. Future execution also
docs/d59-action-extraction-successor.md:120:requires fresh live identity and sandbox checks. Preparation here does not run those
docs/d59-action-extraction-successor.md:129:guard stop or infrastructure/request failure; strict identity/sandbox bindings;
docs/d59-action-extraction-successor.md:135:Original replication evidence and the D5.10 human decision remain separate.
docs/d59-action-extraction-successor.md:140:[separate D5.10 review package](../artifacts/grounding-v5-d59-action-extraction-results/review-guide.md)
docs/d59-action-extraction-successor.md:143:candidate remain unchanged. This is post-hoc sensitivity on inspected tasks;
docs/d59-action-extraction-successor.md:144:the D5.10 verdict and any public wording remain human-owned.
```

## Raw command inventory

Each JSON record stores the exact argv/command, cwd, public environment, UTC timestamps, process runtime, exit status, and complete combined output. Failed and skipped observations remain in the inventory.

| Record | Exit | Runtime (s) | Parsed pytest summary |
| --- | ---: | ---: | --- |
| `commands/00-git-revision.json` | 0 | 0.017355 | `null` |
| `commands/02-evidence-branch.json` | 0 | 0.013179 | `null` |
| `commands/03-checklist-source.json` | 0 | 0.015463 | `null` |
| `commands/04-browser.json` | 0 | 137.127408 | `{"passed": 18, "runtime": 136.67}` |
| `commands/05-cli-sandbox.json` | 0 | 76.470109 | `{"passed": 5, "runtime": 75.21}` |
| `commands/06-osworld.json` | 0 | 330.183847 | `{"passed": 1, "runtime": 329.13, "warnings": 6}` |
| `commands/07-unit-skip-investigation.json` | 1 | 189.790913 | `{"failed": 9, "passed": 3277, "runtime": 188.82, "skipped": 6, "warnings": 16}` |
| `commands/08-restore-overlay-regressions.json` | 0 | 1.376051 | `{"passed": 6, "runtime": 0.11}` |
| `commands/09-restore-drill.json` | 0 | 216.818686 | `null` |
| `commands/10-unit-stable-source.json` | 1 | 137.425686 | `{"failed": 64, "passed": 3228, "runtime": 136.13, "skipped": 6, "warnings": 16}` |
| `commands/11-unit-clean-committed-source.json` | 0 | 152.043089 | `{"passed": 3294, "runtime": 151.14, "skipped": 6, "warnings": 16}` |
| `commands/12-source-commit.json` | 0 | 0.016085 | `null` |
| `commands/13-restore-regressions.json` | 0 | 4.192578 | `{"passed": 8, "runtime": 3.46}` |
| `commands/14-verify-frozen-candidate.json` | 0 | 9.945798 | `null` |
| `commands/15-reproduce-sensitivity-analysis.json` | 0 | 0.833827 | `null` |
| `commands/16-public-wording-inventory.json` | 0 | 0.010801 | `null` |
| `commands/17-provider-closed.json` | 0 | 0.027396 | `null` |
| `commands/18-lint.json` | 0 | 0.024962 | `null` |
| `commands/19-worktree-status.json` | 0 | 0.047574 | `null` |
| `commands/20-receipt-path-regressions.json` | 0 | 3.086584 | `{"passed": 12, "runtime": 2.32}` |

## Integrity and redaction indexes

`evidence-manifest.json` lists the SHA-256 and size of every other file in this directory and of each referenced repository artifact. `redaction-scan.json` lists the prohibited-pattern counts for every file in this directory except itself.
