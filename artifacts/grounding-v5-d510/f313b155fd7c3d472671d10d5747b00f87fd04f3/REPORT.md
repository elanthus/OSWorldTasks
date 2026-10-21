# D5.10 raw evidence index

Evidence revision: `f313b155fd7c3d472671d10d5747b00f87fd04f3`

Evidence branch: `evidence/d510-f313b155`

Observation window: `2026-10-21T07:11:58.894398+00:00` to `2026-10-21T07:39:04.258992+00:00`

Verdict: `null`

Verdict owner: project owner

This index copies and links stored observations only. It does not rerun commands, reinterpret gate semantics, declare a milestone verdict, approve public wording, or change human-owned checklist state.

## Path placeholders

| Placeholder | Meaning |
| --- | --- |
| `.venv` | In-tree dev virtual environment at the documented location, ignored by Git (records 06-07). |
| `<home>` | Home directory of the recording account. |
| `<path-0>` | Evidence worktree (repository root) at the evidence revision. |
| `<path-1>` | Collection directory outside the worktree that held the command records, probe scripts, the integration virtual environment, and the recording TMPDIR during collection. |
| `<path-2>` | Integration virtual environment: python3.12 -m venv, then pip install -e ".[dev,osworld]" (records 61-62). |
| `<system-temp>` | System temporary directory of the recording process (the recording TMPDIR). |

## Recording notes

- Every record was written by scripts/record_gate_command.py at the evidence revision, run as /opt/homebrew/bin/python3.12 -m scripts.record_gate_command from <path-0> (schema pixelgym-d412-command-record-v1). Records are numbered in start order. The numbering has no gaps and no record was withdrawn.
- Every command, including the git commands, was recorded under env -i with only PATH, HOME, TMPDIR, LANG, and LC_ALL passed through; records 10 and 65 list the environment variable names a child process observed. Records 58, 59, 70, and 74 additionally pass the one documented opt-in variable for their suite through the recorder's --env flag; it is shown in each record's environment field.
- Record 03 runs uname -mprsv instead of uname -a. It prints every uname -a field except the network node hostname.
- Records 09, 10, 42, 63, and 65 run probe scripts from <path-1>/probes. Each probe prints its own source before its output. The three probes are the ones recorded in the superseded b0f00f8d package.
- Records 24, 27-29, 39-41, 43, and 45-47 are the documented current-source verifiers for frozen D5.8 and D5.9 evidence. Each exits 1 because a source file it binds changed after its evidence was frozen; the output names the first differing file. Record 48 runs scripts/verify_v5_at_revision all, which verifies the same packages as records 24, 39, 40, 41, 43, 45, 46, and 47 against their recorded historical source revisions; every observation exits 0 with no verification errors and no artifact changes. Its selectors (27, 42-44, 46, 48-50) are the record numbers of the superseded b0f00f8d package, retained in Git history. Records 33, 35, and 36 verify the packages behind records 27-29 at their executed revision.
- Record 59 ran the browser suites with Playwright 1.64.0 from the dev extra against browsers already present in the local Playwright cache. No browser was downloaded.
- OSWorld: record 69 shows no guest image in the worktree and record 70 ran the suite in that state. Records 71-72 created the ignored .cache/osworld directory and linked the existing local guest image file from the primary checkout's ignored .cache/osworld directory; record 73 lists its size and modification time. The installed OSWorld Docker provider bind-mounts the image with "mode": "ro" (desktop_env/providers/docker/provider.py in <path-2>). Record 74 ran the suite with the link, and record 79 removed the link. Records 66-68 and 75-78 list Docker containers, images, and volumes; record 77 has no before-run counterpart. No image, VM disk, or browser was pulled or downloaded.
- Records 53-57 select test node IDs chosen by reading test names in tests/unit. Each command lists its node IDs. Every test function in tests/unit/test_grounding_v5_runner.py (57) and tests/unit/test_grounding_v5.py (27) is listed in exactly one of the five groups; other files may also be covered by the fast-suite records.
- Record 83 is git show of the retired plan at f0bcbd6f3e379f880baeca233d4b7e05528bcfc5. The generator checks every checklist entry against that record's text and line numbers.
- Network use during collection: the pip installs in records 07 and 62. No provider credential was present (records 10 and 65), no paid model call was made, and no evaluation was rerun.
- The redaction scan (record 86, and redaction-scan.json for this directory) uses the seven D4.12 pattern categories from scripts/generate_d412_evidence_report.py plus system_temp_paths, provider_api_keys, and bearer_tokens.
- An earlier recording pass at this revision on the same day was discarded in full before packaging and is not retained: a driver defect gave its grouped test records (53-57) an empty node list, and its sandbox, browser, and OSWorld records (58, 59, 70, 74) were skipped because the opt-in variables were not passed. No record from that pass is in this package; the worktree, both virtual environments, and the cache directory were recreated before this pass.
- This package supersedes the b0f00f8d package and the 20261010 historical-verification supplement. Both were removed from the tree in the change that adds this package and remain in Git history.

## Checklist evidence

Checklist text is copied verbatim from `f0bcbd6f3e379f880baeca233d4b7e05528bcfc5:plans/grounding-v5-agent-benchmark.md`. The observation column copies exit statuses and parsed pytest counts from the listed records.

| # | Checklist line | Source | Stored raw observation | Records | Artifacts |
| ---: | --- | --- | --- | --- | --- |
| 1 | Review raw commands, outputs, evidence integrity, limitations, benchmark verdict, and any public wording | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:658` | `00-git-revision`: exit 0<br>`01-worktree-status`: exit 0<br>`52-worktree-status-after-verifiers`: exit 0<br>`82-worktree-status-after-integration`: exit 0<br>`84-public-wording-inventory`: exit 0<br>`85-worktree-status-before-redaction-scan`: exit 0<br>`86-redaction-scan-collected-records`: exit 0 | `commands/00-git-revision.json`, `commands/01-worktree-status.json`, `commands/52-worktree-status-after-verifiers.json`, `commands/82-worktree-status-after-integration.json`, `commands/84-public-wording-inventory.json`, `commands/85-worktree-status-before-redaction-scan.json`, `commands/86-redaction-scan-collected-records.json` | `docs/v5-benchmark-design.md` |
| 2 | The human explicitly approves D5.1 and sequencing relative to the separate D4.12 gate. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:785` | no stored observation | none | `artifacts/grounding-v5-d56-calibration-approval.md`, `artifacts/decisions/d4.12.json` |
| 3 | Versioned schemas and disjoint development, calibration, and confirmatory seed lists are committed before task capture or model calls. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:786` | `53-v5-group-generator-partitions-admission`: exit 0; pytest {"passed": 49, "runtime": 5.67} | `commands/53-v5-group-generator-partitions-admission.json` | `artifacts/grounding-v5-contracts.md`, `artifacts/grounding-v5-manifests/development.json`, `artifacts/grounding-v5-manifests/calibration.json`, `artifacts/grounding-v5-manifests/calibration-d56.json`, `artifacts/grounding-v5-manifests/confirmatory.json`, `pixelgym/grounding/v5/seeds.py`, `pixelgym/grounding/v5/schemas/task.schema.json`, `pixelgym/grounding/v5/schemas/policy.schema.json`, `pixelgym/grounding/v5/schemas/attempt-event.schema.json`, `pixelgym/grounding/v5/schemas/memory-task.schema.json`, `pixelgym/grounding/v5/schemas/memory-task-v3.schema.json` |
| 4 | Six generator families produce the frozen allocations and satisfy every admission rule. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:788` | `53-v5-group-generator-partitions-admission`: exit 0; pytest {"passed": 49, "runtime": 5.67}<br>`43-verify-d59-freeze`: exit 1<br>`48-verify-v5-at-historical-revisions`: exit 0 | `commands/53-v5-group-generator-partitions-admission.json`, `commands/43-verify-d59-freeze.json`, `commands/48-verify-v5-at-historical-revisions.json` | `artifacts/grounding-v5-development-admission.json`, `artifacts/grounding-v5-d59-freeze/admission.json`, `artifacts/grounding-v5-d59-freeze/task-manifest.json` |
| 5 | Every declared valid and adversarial trace replays twice with identical semantic state and bitwise-identical corresponding screenshots. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:789` | `59-browser-integration`: exit 0; pytest {"passed": 18, "runtime": 157.66}<br>`53-v5-group-generator-partitions-admission`: exit 0; pytest {"passed": 49, "runtime": 5.67} | `commands/59-browser-integration.json`, `commands/53-v5-group-generator-partitions-admission.json` | `artifacts/grounding-v5-development-admission.json`, `artifacts/grounding-v5-development-sample/manifest.json` |
| 6 | Golden policies reach reward `1.0` exactly once; all declared near-miss and reward-hacking mutations remain at `0.0`. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:791` | `53-v5-group-generator-partitions-admission`: exit 0; pytest {"passed": 49, "runtime": 5.67}<br>`15-golden-trajectory-check`: exit 0 | `commands/53-v5-group-generator-partitions-admission.json`, `commands/15-golden-trajectory-check.json` | `artifacts/grounding-v5-development-admission.json`, `artifacts/grounding-v5-d59-freeze/admission.json` |
| 7 | Stateful policy reset, policy-egress isolation, pre-send attempt persistence, unknown-attempt reconciliation, bounded deadline/cancellation settlement, post-attempt and post-dispatch state reconstruction, canonical parser-input recovery, atomic candidate/checkpoint persistence, sealed parser failure, exact backend-state restore or reconnect, durable dispatch ordering, invalid output, call caps, resume, authoritative replay, credential-free policy inputs, secret exclusion, redaction binding, and evidence integrity have no-cost failure-path tests. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:793` | `54-v5-group-stateful-harness-journal-resume-dispatch-restore`: exit 0; pytest {"passed": 124, "runtime": 5.54}<br>`55-v5-group-credential-secret-redaction-integrity`: exit 0; pytest {"passed": 183, "runtime": 3.81}<br>`57-v5-group-call-cap-planning`: exit 0; pytest {"passed": 27, "runtime": 14.44}<br>`70-osworld-v5-integration-without-guest-image`: exit 0; pytest {"runtime": 3.89, "skipped": 1}<br>`74-osworld-v5-integration-linked-guest-image`: exit 0; pytest {"passed": 1, "runtime": 210.72, "warnings": 9} | `commands/54-v5-group-stateful-harness-journal-resume-dispatch-restore.json`, `commands/55-v5-group-credential-secret-redaction-integrity.json`, `commands/57-v5-group-call-cap-planning.json`, `commands/70-osworld-v5-integration-without-guest-image.json`, `commands/74-osworld-v5-integration-linked-guest-image.json` | `artifacts/grounding-v5-contracts.md` |
| 8 | No-cost OS-level integration evidence denies unauthorized policy egress and preserves required provider-fake, backend, application, and controller traffic without internet or real-provider calls. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:799` | `58-v5-sandbox-integration`: exit 0; pytest {"passed": 2, "runtime": 1.62} | `commands/58-v5-sandbox-integration.json` | `artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json` |
| 9 | The documented fast suite passes without browser, network, OSWorld, provider credentials, or model calls. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:801` | `10-boundary-inventory`: exit 0<br>`06-create-dev-venv`: exit 0<br>`07-install-dev`: exit 0<br>`80-fast-suite-ci-invocation`: exit 0; pytest {"passed": 2776, "runtime": 144.62, "skipped": 6, "warnings": 12}<br>`81-unit-suite-serial`: exit 0; pytest {"deselected": 1, "passed": 2776, "runtime": 379.72, "skipped": 6, "warnings": 3} | `commands/10-boundary-inventory.json`, `commands/06-create-dev-venv.json`, `commands/07-install-dev.json`, `commands/80-fast-suite-ci-invocation.json`, `commands/81-unit-suite-serial.json` | none |
| 10 | A free plan-only command reports exact environment-action, model-attempt, provider-control, and total wire-request caps for each approved phase. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:803` | `57-v5-group-call-cap-planning`: exit 0; pytest {"passed": 27, "runtime": 14.44}<br>`42-d59-recorded-source-revisions`: exit 0<br>`43-verify-d59-freeze`: exit 1<br>`45-verify-d59-haiku-freeze`: exit 1<br>`46-verify-d59-haiku-retry-successor`: exit 1<br>`47-verify-d59-haiku-network-retry`: exit 1<br>`48-verify-v5-at-historical-revisions`: exit 0 | `commands/57-v5-group-call-cap-planning.json`, `commands/42-d59-recorded-source-revisions.json`, `commands/43-verify-d59-freeze.json`, `commands/45-verify-d59-haiku-freeze.json`, `commands/46-verify-d59-haiku-retry-successor.json`, `commands/47-verify-d59-haiku-network-retry.json`, `commands/48-verify-v5-at-historical-revisions.json` | `artifacts/grounding-v5-scripted-call-cap-plan.json`, `artifacts/grounding-v5-d59-freeze/execution-plan.json`, `artifacts/grounding-v5-d59-haiku-freeze/execution-plan.json`, `artifacts/grounding-v5-d59-haiku-api-retry-successor/execution-plan.json`, `artifacts/grounding-v5-d59-haiku-network-retry/execution-plan.json` |
| 11 | The agent stops for explicit calibration approval and again for explicit confirmatory approval. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:805` | no stored observation | none | `artifacts/grounding-v5-d56-calibration-approval.md`, `artifacts/grounding-v5-d58-final-design/decision.json`, `artifacts/grounding-v5-d59-haiku-selection/owner-selection.json`, `artifacts/grounding-v5-d59-haiku-execution/owner-approval.json`, `artifacts/grounding-v5-d59-haiku-api-retry-execution/owner-approval.json`, `artifacts/grounding-v5-d59-haiku-results/authorization-history.json` |
| 12 | The confirmatory report is generated only from sealed evidence, retains every failure, and separates primary, diagnostic, ablation, reliability, and exploratory results. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:807` | `50-check-d59-confirmatory-analysis`: exit 0<br>`49-report-d59-haiku-help`: exit 0<br>`51-d59-private-run-directories-present`: exit 1<br>`56-v5-group-metrics-statistics`: exit 0; pytest {"passed": 70, "runtime": 1.63} | `commands/50-check-d59-confirmatory-analysis.json`, `commands/49-report-d59-haiku-help.json`, `commands/51-d59-private-run-directories-present.json`, `commands/56-v5-group-metrics-statistics.json` | `artifacts/grounding-v5-d59-haiku-results/report.md`, `artifacts/grounding-v5-d59-haiku-results/report.json`, `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md`, `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.json`, `artifacts/grounding-v5-d59-haiku-results/publication-relation.json`, `artifacts/grounding-v5-d59-haiku-results/authorization-history.json`, `artifacts/grounding-v5-d59-freeze/analysis-plan.md` |
| 13 | The agent reports raw milestone-gate commands, exit statuses, counts, runtimes, and full output without declaring the human gate passed or approving public wording. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:809` | no stored observation | none | none |

## Recorded exceptions, waivers, and limitations

Checklist row 1: `docs/v5-benchmark-design.md` line 148

> | D5.10: milestone verdict and public wording | Not declared; human-owned | — |

Checklist row 1: `artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json`

> "claim_boundary":"The successor may support the matched model-quality comparison but cannot claim OS-enforced policy isolation or independent denial of local process capabilities."

Checklist row 1: `docs/evidence-index.md` lines 82-86

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

Checklist row 8: `docs/v5-benchmark-design.md` lines 150-153

> Calibration results are descriptive and are not a model ranking. The D5.9 Haiku policies ran under
> a recorded exception that waived OS-level sandbox enforcement, so that evidence cannot support an
> OS-enforced isolation claim
> ([exception](../artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json)).

Checklist row 11: `artifacts/grounding-v5-d59-haiku-results/report.md` line 14

> The owner authorized continuation after malformed output and, later, after exhausted connection resets. Each failed assignment remains in the results. Prior assignments were not replayed; retries kept the same per-action and aggregate caps. These are disclosed changes to the original campaign stop rule, made after observing failures.

Checklist row 12: `docs/evidence-index.md` lines 82-86

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

- `scripts/build_grounding_benchmark_v2.py`: Marked as a frozen reproduction entry point. It has no argument parser and no verify or check mode; any invocation, including --help, calls build_benchmark_v2 and writes the tracked v2 benchmark files. Not invoked.
- `scripts/generate_grounding_report.py (default mode)`: No verify or check mode; the default invocation writes the tracked report and results. --help is recorded; the read-only verifier scripts/verify_grounding_report.py is recorded separately.
  - Record: `commands/18-generate-grounding-report-v1-help.json`
  - Record: `commands/16-verify-grounding-report-v1.json`
- `scripts/generate_grounding_report_v2.py (default mode)`: No verify or check mode; without --output-dir it writes the tracked report and provenance. --help is recorded; scripts/verify_grounding_report_v2.py is recorded separately.
  - Record: `commands/19-generate-grounding-report-v2-help.json`
  - Record: `commands/17-verify-grounding-report-v2.json`
- `scripts/generate_grounding_clustered_analysis.py (default mode)`: No verify or check mode; without --output-dir it writes the tracked supplement files. --help is recorded.
  - Record: `commands/20-generate-grounding-clustered-analysis-help.json`
- `scripts/record_grounding_v5_d56_retained_development_runs.py (default mode)`: No verify or check mode; it reads ignored local originals and writes the registry. --help is recorded.
  - Record: `commands/21-record-d56-retained-development-runs-help.json`
- `scripts/report_grounding_v5_d59_haiku.py --verify`: --verify requires --input, the private D5.9 run directory. Record 53 shows the ignored D5.9 run directories absent from the worktree. --help is recorded. No input was substituted.
  - Record: `commands/49-report-d59-haiku-help.json`
  - Record: `commands/51-d59-private-run-directories-present.json`
  - `docs/evidence-index.md` lines 82-86:
    > - **2026-09-30, D5.9 private journals.** The attempt and invocation journals for the D5.9 Haiku
    >   execution were not retained after the public projection was published. They were untracked
    >   files in a temporary checkout that was later removed. The digests recorded in `report.json`
    >   under `source_artifacts` and `journal_integrity` were computed from those journals at
    >   publication and cannot be re-verified against them. D5.9 closes on the published projection.
- `D5.9 plan-only commands: scripts/run_grounding_v5_d59_haiku.py prepare, scripts/run_grounding_v5_d59_haiku_retry_successor.py prepare, scripts/run_grounding_v5_d59_haiku_network_retry.py prepare`: All three call the shared runner's prepare, which calls probe_claude_runtime (claude --version, claude auth status --json, claude --help) and therefore needs a logged-in Claude Code credential, reads an owner approval file (the network-retry approval path is ignored and absent), and writes an output directory. Not run. The D5.9 --verify commands that recompute the execution plans are recorded as 46 and 48-50.
- `D5.8 runner prepare subcommands (run_grounding_v5_memory_calibration.py, run_grounding_v5_gemini38_calibration.py, run_grounding_v5_focus_calibration.py, run_grounding_v5_focus_continuation.py, run_grounding_v5_focus_diagnostic.py, run_grounding_v5_reliable_diagnostic.py, run_grounding_v5_reliable_continuation.py, run_grounding_v5_reliable_extension.py, run_grounding_v5_owner_budget_continuation.py, run_grounding_v5_memory_pilot.py)`: Each prepare subcommand writes an execution plan into a tracked artifact directory; none is a no-write plan-only or validate-only mode. Not run. The D5.8 evidence verifiers are recorded as 27-44.
- `D5.6-era plan-only and validate-only modes (run_grounding_v5_calibration.py --validate-only, run_grounding_v5_provider_smoke.py, run_grounding_v5_calibration_pilot.py, run_grounding_v5_expanded_calibration.py, run_grounding_v5_panel_smoke.py --plan-only)`: Outside the requested D5.9-freeze and D5.8-plan scope for plan-only commands. Not run.
- `scripts/fetch_evidence_images.py, scripts/generate_validation_report.py, scripts/generate_d412_evidence_report.py`: Marked as frozen reproduction entry points but outside the grounding v1/v2 and v5 scope of this package; fetch_evidence_images.py also downloads a GitHub release. Not run. The unit-suite records report 6 skipped tests.
- `platform_compose_integration (tests/integration/platform/test_compose_lifecycle.py)`: D4.12 scope; excluded by the collection instructions. Not run.

## Public wording inventory

Copied verbatim from `commands/84-public-wording-inventory.json` (exit 0). Each line is `file:line:text` as printed by:

`grep -n -i -E 'v5|d5\.9|d5\.10' README.md docs/v5-benchmark-design.md docs/evidence-index.md`

```
README.md:58:| v5 agent benchmark and calibration | In progress; v5 benchmark verdict (D5.10) not declared | [v5 design](docs/v5-benchmark-design.md), [evidence index](docs/evidence-index.md) |
README.md:59:| v5 stateful serving | In progress | [Platform design](docs/platform-design.md#v5-stateful-serving-apiv2) |
README.md:172:to its reviewed revision and scripted-provider scope. The v5 confirmatory benchmark and v5
README.md:175:([v5 design](docs/v5-benchmark-design.md), [evidence index](docs/evidence-index.md)).
README.md:206:- Platform metrics are synthetic, local scripted-provider measurements. V5 calibration is
README.md:207:  descriptive, the confirmatory benchmark is unfinished, and no v5 gate is declared
README.md:235:- [Platform design](docs/platform-design.md) — Milestone 4 platform and v5 stateful serving.
README.md:236:- [v5 benchmark design](docs/v5-benchmark-design.md) — contract, analysis plan, and current status
docs/v5-benchmark-design.md:1:# v5 agent benchmark design
docs/v5-benchmark-design.md:3:- **Protocol identifier:** `pixelgym-agent-v5`
docs/v5-benchmark-design.md:4:- **Status:** in progress; no v5 milestone verdict (D5.10) has been declared
docs/v5-benchmark-design.md:17:([floor audit](../artifacts/grounding-v4c-pilot-floor-audit-qwen3-8-27b.json)), so v5 fixes
docs/v5-benchmark-design.md:20:v5 measures exact end-to-end task completion by a complete, versioned policy system (model,
docs/v5-benchmark-design.md:33:The [environment contract](environment-contract.md) is unchanged. v5 adds task generation,
docs/v5-benchmark-design.md:92:| Confirmatory | 96 (later enlarged to 192 for D5.9) | Frozen evaluation |
docs/v5-benchmark-design.md:124:The binding D5.9 pre-registration, copied verbatim from the frozen design, is the
docs/v5-benchmark-design.md:125:[D5.9 pre-registration record](../artifacts/grounding-v5-d59-freeze/analysis-plan.md).
docs/v5-benchmark-design.md:145:| D5.6–D5.7: calibration panel and runs | Delivered as descriptive calibration, including negative results | [D5.6 calibration report](../artifacts/grounding-v5-d56-completed-calibrations-report.md), [supplement](../artifacts/grounding-v5-calibration-supplement/report.md) |
docs/v5-benchmark-design.md:146:| D5.8: design decisions (difficulty, minimum difference, power, sample, caps) | Decided; Gemini and Haiku analyses retained | [Gemini power check](../artifacts/grounding-v5-d58-final-design/power.md), [Haiku successor analysis](../artifacts/grounding-v5-d58-haiku-successor/report.md) |
docs/v5-benchmark-design.md:147:| D5.9: confirmatory evaluation | Haiku execution recorded, 432 outcomes. The stop rule was amended after repeated intermittent network failures to allow for retries in the case of intermittent network issues. Confirmatory analysis: [confirmatory analysis](../artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md). | [D5.9 Haiku report](../artifacts/grounding-v5-d59-haiku-results/report.md), [pre-registration](../artifacts/grounding-v5-d59-freeze/analysis-plan.md) |
docs/v5-benchmark-design.md:148:| D5.10: milestone verdict and public wording | Not declared; human-owned | — |
docs/v5-benchmark-design.md:150:Calibration results are descriptive and are not a model ranking. The D5.9 Haiku policies ran under
docs/v5-benchmark-design.md:153:([exception](../artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json)).
docs/evidence-index.md:19:| What did the transport repair show? | [Reliability diagnostic](../artifacts/grounding-v5-d58-reliable-diagnostic/report.md) and [verification receipt](../artifacts/grounding-v5-d58-reliable-diagnostic/verification.json) record ten actions from ten calls, no network errors or retries, and USD 0.099531750 in new charges. These supplied-state checks add no end-to-end calibration observations. |
docs/evidence-index.md:20:| What happened in the completed Gemini calibration? | [Final continuation report](../artifacts/grounding-v5-d58-owner-budget-continuation/report.md) and [verification receipt](../artifacts/grounding-v5-d58-owner-budget-continuation/verification.json) record all 100 assignments, including ten earlier failures: history 42/50 terminal successes and stateless 6/50, with both consumers reached on 43/50 and 45/50. The final history episode was cut short at the authorized six-hour limit. Confirmed aggregate charges were USD 23.978227275 under the USD 28 cap; unresolved holds carry owner-authorized zero budget weight. The [D5.8 final-design decision](../artifacts/grounding-v5-d58-final-design/decision.json), recorded 2026-09-12 (commit `bacae4d`), retains the calibrated task mechanics and records 42/50 capable-arm success as an explicit exception to the proposed 40/50 ceiling; it records the reason as not tuning the generator to reduce success. |
docs/evidence-index.md:21:| What did the PR196 Luna/Haiku calibration record? | [Consolidated report](../artifacts/grounding-v5-pr196-calibration/report.md), [structured results](../artifacts/grounding-v5-pr196-calibration/results.json), and [snapshot bindings](../artifacts/grounding-v5-pr196-calibration/sources.json) retain final outcomes, paired results, memory observations, interrupted attempts, all earlier Haiku cohorts, and cost. Haiku combines CLI and API execution; these are descriptive calibration results, not a model ranking or final evaluation. |
docs/evidence-index.md:22:| What did the fresh Haiku CLI replication record? | [Response-free report](../artifacts/grounding-v5-haiku-cli-replication/report.md), [structured results](../artifacts/grounding-v5-haiku-cli-replication/results.json), and [audited snapshot](../artifacts/grounding-v5-haiku-cli-replication/snapshot.json) retain all 100 fresh PR196-panel outcomes from exact `claude-haiku-4-5-20251001` through Claude Code CLI: history 28/50 successes and stateless 5/50, with every invalid output retained. The single-route replication is calibration evidence, not D5.9 execution, a model ranking, or a human gate. |
docs/evidence-index.md:23:| What does the Haiku D5.8 successor calculate? | [Structured analysis](../artifacts/grounding-v5-d58-haiku-successor/analysis.json) and [generated report](../artifacts/grounding-v5-d58-haiku-successor/report.md) use the 44 predesignated independent representatives: 30 are discordant. Under the unchanged 20-point/80% planning assumptions, 168 independent pairs yield 87.0% power at observed discordance and 81.2% at the upper sensitivity endpoint. That analytical package left owner selection unset; the later Haiku selection records the decision. D5.9 execution was unset in that analytical package. |
docs/evidence-index.md:24:| What did the D5.9 Haiku execution candidate freeze? | The counted network retry candidate allows one runner-managed retry for a stopped timeout or connection reset. Recovery does not fail the episode; both requests remain counted within the unchanged caps. It binds [new source, policies, and trial IDs](../artifacts/grounding-v5-d59-haiku-network-retry/execution-plan.json), preserves prior evidence, and makes no provider calls during preparation. The earlier zero-internal-retry candidate's [first-run discard receipt](../artifacts/grounding-v5-d59-haiku-api-retry-successor/discarded-run.json) remains historical evidence. The new candidate is not an execution approval. The unchanged [OS-sandbox exception](../artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json) cannot support an OS-enforced isolation claim. |
docs/evidence-index.md:25:| What was pre-registered for D5.9? | The [D5.9 pre-registration record](../artifacts/grounding-v5-d59-freeze/analysis-plan.md) holds verbatim copies of the analysis-plan and stop-rule sections of the Gemini confirmatory freeze and the Haiku freeze successor: the exact McNemar primary test at alpha 0.05, the 20-point minimum relevant difference, 80% target power, and the family-stratified logical-cluster bootstrap with seed 20260911 and 10,000 resamples. Its header records the source commit and the sha256 of each source file. |
docs/evidence-index.md:26:| What did the D5.9 Haiku execution record? | The [generated report](../artifacts/grounding-v5-d59-haiku-results/report.md) and [per-assignment projection](../artifacts/grounding-v5-d59-haiku-results/report.json) retain all 432 outcomes, with primary and reliability cases separated. The [authorization history](../artifacts/grounding-v5-d59-haiku-results/authorization-history.json) discloses the owner-approved continuations after malformed output and exhausted connection resets. Prior failures, reservations, and the original runtime deadline were retained. Raw journals remain local; D5.10 and public model-quality claims remain human-owned. |
docs/evidence-index.md:27:| What does the D5.9 confirmatory analysis show? | The [confirmatory analysis](../artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md) and its [JSON source](../artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.json) are generated by `scripts/analyze_grounding_v5_d59.py` from the public projection and the bound execution plan. They report the pre-registered paired McNemar test and family-stratified logical-cluster bootstrap for history minus stateless on the primary phase, the reliability subset separately, and labelled exploratory rates, termination profiles, and resource use. They name the executed policy IDs read from the bound execution plan beside the pre-registered IDs, which differ. Diagnostics that need the private journals are listed as not computable. D5.10 is the owner's review. |
docs/evidence-index.md:28:| Can the reserved final sample meet the memory power target? | The historical Gemini [prospective power and cost check](../artifacts/grounding-v5-d58-final-design/power.md) gives 71.4% power for 120 independent pairs at the agreed 20-point difference. Under that calibration, the 168-pair design gives 85.9% at observed discordance and 80.3% at the upper sensitivity endpoint. The historical Gemini D5.9 freeze candidate ([execution plan](../artifacts/grounding-v5-d59-freeze/execution-plan.json)) binds 192 admitted tasks per arm and a USD 120 aggregate planning cap. Its budget and runtime proposal do not carry forward to the Haiku selection; its execution remains disabled and its approved call caps remain zero. |
docs/evidence-index.md:29:| What happened before the focus repair? | [Earlier Gemini 3.8 report](../artifacts/grounding-v5-d58-gemini38-calibration/report.md) and [verification receipt](../artifacts/grounding-v5-d58-gemini38-calibration/verification.json) preserve the separate cohort stopped at 51/100 episodes, including the stateless text-entry floor and account reconciliation. |
docs/evidence-index.md:30:| What did the focus and timeout repair diagnostic show? | [Twenty-call report](../artifacts/grounding-v5-d58-focus-diagnostic/report.md) and [journal verification](../artifacts/grounding-v5-d58-focus-diagnostic/verification.json) record 6/6 desired text transitions and 3/4 correct memory choices in each mode, including one zero-charge empty response. Supplied prefixes do not establish end-to-end memory exposure. |
docs/evidence-index.md:31:| What happened in the D5.6 supplemental calibration? | [Generated supplement](../artifacts/grounding-v5-calibration-supplement/report.md) publishes Mistral and the matched Qwen stateful/stateless pair, with task outcomes, accounting, source hashes, and local audit receipts. Public verification reproduces the derivative without restricted journals. |
docs/evidence-index.md:32:| What happened in historical v5 calibration? | [Generated calibration report](../artifacts/grounding-v5-d56-completed-calibrations-report.md) and [structured derivative](../artifacts/grounding-v5-d56-completed-calibrations-publishable.json) retain completed and negative results, incomplete runs, policy identities, spend and unknown-charge reservations, dates, taxonomy, and predecessor disclosures. |
docs/evidence-index.md:33:| What can a public clone verify? | [Integrity audit](../artifacts/grounding-v5-d56-completed-calibrations-integrity-audit.json), [publication relation](../artifacts/grounding-v5-d56-completed-calibrations-publication-relation.json), and [errata](../artifacts/grounding-v5-d56-gemini-qwen-v3-calibration-evidence-errata.json). Aggregate bindings are published; restricted journals, raw provider responses, screenshots, and private checkpoints remain `must_not_commit`. Journal file hashes and row-level contents cannot be verified from a public clone. |
docs/evidence-index.md:34:| Where are the design records? | The [environment contract](environment-contract.md), [platform design](platform-design.md), [v5 benchmark design](v5-benchmark-design.md), [development process](development-process.md), and [glossary](glossary.md) replace the retired sprint and milestone plans. The [million-episode design note](million-episode-design-note.md) is generated from the stored fan-out evidence. |
docs/evidence-index.md:43:[`evidence.py`](../pixelgym/grounding/v5/evidence.py); the historical withdrawal disclosure is in
docs/evidence-index.md:46:The older incomplete [Qwen report](../artifacts/grounding-v5-d56-qwen-full-calibration-report.md)
docs/evidence-index.md:49:not rewritten. These runs do not constitute a completed confirmatory benchmark or a v5 gate.
docs/evidence-index.md:51:Superseded v3/v4 calibration apps and v5 D5.6 experiment drivers remain reproducible at the
docs/evidence-index.md:82:- **2026-09-30, D5.9 private journals.** The attempt and invocation journals for the D5.9 Haiku
docs/evidence-index.md:86:  publication and cannot be re-verified against them. D5.9 closes on the published projection.
docs/evidence-index.md:92:records the new scripted cases and restore observations separately from D5.9.
docs/evidence-index.md:104:The planning directory was retired after the D5.9 execution. Four frozen reports still contain
docs/evidence-index.md:105:relative links into it: the [completed-calibrations report](../artifacts/grounding-v5-d56-completed-calibrations-report.md),
docs/evidence-index.md:106:the [incomplete Qwen report](../artifacts/grounding-v5-d56-qwen-full-calibration-report.md), the
docs/evidence-index.md:107:[calibration approval](../artifacts/grounding-v5-d56-calibration-approval.md), and the
docs/evidence-index.md:108:[PR196 report](../artifacts/grounding-v5-pr196-calibration/report.md). These links no longer
docs/evidence-index.md:110:digests. Two generators still emit those links: `scripts/publish_grounding_v5_d56_completed_calibrations.py`
docs/evidence-index.md:113:those links named is now summarized in the [v5 benchmark design](v5-benchmark-design.md).
```

## Raw command inventory

Each JSON record stores the exact argv/command, cwd, public environment, UTC timestamps, process runtime, exit status, and complete combined output. Failed and skipped observations remain in the inventory.

| Record | Exit | Runtime (s) | Parsed pytest summary |
| --- | ---: | ---: | --- |
| `commands/00-git-revision.json` | 0 | 0.015376 | `null` |
| `commands/01-worktree-status.json` | 0 | 0.035686 | `null` |
| `commands/02-python-version.json` | 0 | 0.011872 | `null` |
| `commands/03-os-architecture.json` | 0 | 0.00445 | `null` |
| `commands/04-docker-version.json` | 0 | 0.020521 | `null` |
| `commands/05-evidence-branch.json` | 0 | 0.015856 | `null` |
| `commands/06-create-dev-venv.json` | 0 | 1.560138 | `null` |
| `commands/07-install-dev.json` | 0 | 12.773495 | `null` |
| `commands/08-dev-pip-check.json` | 0 | 0.345653 | `null` |
| `commands/09-dev-package-versions.json` | 0 | 0.107415 | `null` |
| `commands/10-boundary-inventory.json` | 0 | 0.085972 | `null` |
| `commands/11-ruff-check.json` | 0 | 0.241155 | `null` |
| `commands/12-ruff-format-check.json` | 0 | 0.015199 | `null` |
| `commands/13-mypy-pixelgym.json` | 0 | 1.359497 | `null` |
| `commands/14-mypy-flows.json` | 0 | 0.221204 | `null` |
| `commands/15-golden-trajectory-check.json` | 0 | 2.450811 | `null` |
| `commands/16-verify-grounding-report-v1.json` | 0 | 0.38564 | `null` |
| `commands/17-verify-grounding-report-v2.json` | 0 | 0.060683 | `null` |
| `commands/18-generate-grounding-report-v1-help.json` | 0 | 0.137962 | `null` |
| `commands/19-generate-grounding-report-v2-help.json` | 0 | 0.055392 | `null` |
| `commands/20-generate-grounding-clustered-analysis-help.json` | 0 | 0.046042 | `null` |
| `commands/21-record-d56-retained-development-runs-help.json` | 0 | 0.180369 | `null` |
| `commands/22-verify-d56-completed-calibrations.json` | 0 | 3.431453 | `null` |
| `commands/23-verify-calibration-supplement.json` | 0 | 0.75565 | `null` |
| `commands/24-verify-d58-memory-admission.json` | 1 | 0.2281 | `null` |
| `commands/25-verify-d58-full-calibration.json` | 0 | 0.21931 | `null` |
| `commands/26-verify-d58-gemini38-calibration.json` | 0 | 0.215179 | `null` |
| `commands/27-verify-d58-reliable-continuation.json` | 1 | 0.226445 | `null` |
| `commands/28-verify-d58-owner-budget-continuation.json` | 1 | 0.216778 | `null` |
| `commands/29-verify-d58-owner-budget-reconciliation.json` | 1 | 0.215841 | `null` |
| `commands/30-verify-d58-at-revision-focus-calibration.json` | 0 | 0.482018 | `null` |
| `commands/31-verify-d58-at-revision-focus-continuation.json` | 0 | 0.473608 | `null` |
| `commands/32-verify-d58-at-revision-reliable-diagnostic.json` | 0 | 0.455226 | `null` |
| `commands/33-verify-d58-at-revision-reliable-continuation.json` | 0 | 0.49616 | `null` |
| `commands/34-verify-d58-at-revision-reliable-extension.json` | 0 | 0.502058 | `null` |
| `commands/35-verify-d58-at-revision-owner-budget-continuation.json` | 0 | 0.557497 | `null` |
| `commands/36-verify-d58-at-revision-owner-budget.json` | 0 | 0.481305 | `null` |
| `commands/37-verify-pr196-calibration.json` | 0 | 0.223684 | `null` |
| `commands/38-verify-haiku-cli-replication.json` | 0 | 0.215255 | `null` |
| `commands/39-verify-d58-review-corrections.json` | 1 | 0.194836 | `null` |
| `commands/40-verify-d58-power-report.json` | 1 | 0.163363 | `null` |
| `commands/41-verify-d58-haiku-successor.json` | 1 | 0.195869 | `null` |
| `commands/42-d59-recorded-source-revisions.json` | 0 | 0.055041 | `null` |
| `commands/43-verify-d59-freeze.json` | 1 | 201.609672 | `null` |
| `commands/44-verify-d59-haiku-selection.json` | 0 | 0.055612 | `null` |
| `commands/45-verify-d59-haiku-freeze.json` | 1 | 0.238522 | `null` |
| `commands/46-verify-d59-haiku-retry-successor.json` | 1 | 0.399834 | `null` |
| `commands/47-verify-d59-haiku-network-retry.json` | 1 | 0.398056 | `null` |
| `commands/48-verify-v5-at-historical-revisions.json` | 0 | 230.132032 | `null` |
| `commands/49-report-d59-haiku-help.json` | 0 | 0.197454 | `null` |
| `commands/50-check-d59-confirmatory-analysis.json` | 0 | 0.413804 | `null` |
| `commands/51-d59-private-run-directories-present.json` | 1 | 0.006276 | `null` |
| `commands/52-worktree-status-after-verifiers.json` | 0 | 0.050992 | `null` |
| `commands/53-v5-group-generator-partitions-admission.json` | 0 | 7.060937 | `{"passed": 49, "runtime": 5.67}` |
| `commands/54-v5-group-stateful-harness-journal-resume-dispatch-restore.json` | 0 | 6.20564 | `{"passed": 124, "runtime": 5.54}` |
| `commands/55-v5-group-credential-secret-redaction-integrity.json` | 0 | 4.406791 | `{"passed": 183, "runtime": 3.81}` |
| `commands/56-v5-group-metrics-statistics.json` | 0 | 2.201176 | `{"passed": 70, "runtime": 1.63}` |
| `commands/57-v5-group-call-cap-planning.json` | 0 | 15.038532 | `{"passed": 27, "runtime": 14.44}` |
| `commands/58-v5-sandbox-integration.json` | 0 | 1.954234 | `{"passed": 2, "runtime": 1.62}` |
| `commands/59-browser-integration.json` | 0 | 158.035663 | `{"passed": 18, "runtime": 157.66}` |
| `commands/60-curl-wire-integration.json` | 0 | 1.160417 | `{"passed": 1, "runtime": 0.76}` |
| `commands/61-create-integration-venv.json` | 0 | 1.37317 | `null` |
| `commands/62-install-integration-dev-osworld.json` | 0 | 214.667318 | `null` |
| `commands/63-integration-package-versions.json` | 0 | 0.321723 | `null` |
| `commands/64-integration-pip-check.json` | 0 | 0.536931 | `null` |
| `commands/65-integration-boundary-inventory.json` | 0 | 0.03534 | `null` |
| `commands/66-docker-osworld-containers-before.json` | 0 | 0.027298 | `null` |
| `commands/67-docker-all-containers-before.json` | 0 | 0.045502 | `null` |
| `commands/68-docker-osworld-images.json` | 0 | 0.027909 | `null` |
| `commands/69-osworld-guest-image-in-worktree.json` | 1 | 0.005005 | `null` |
| `commands/70-osworld-v5-integration-without-guest-image.json` | 0 | 5.003028 | `{"runtime": 3.89, "skipped": 1}` |
| `commands/71-create-worktree-osworld-cache-dir.json` | 0 | 0.004664 | `null` |
| `commands/72-link-local-guest-image.json` | 0 | 0.004661 | `null` |
| `commands/73-linked-guest-image-size.json` | 0 | 0.005589 | `null` |
| `commands/74-osworld-v5-integration-linked-guest-image.json` | 0 | 211.501131 | `{"passed": 1, "runtime": 210.72, "warnings": 9}` |
| `commands/75-docker-osworld-containers-after.json` | 0 | 0.024673 | `null` |
| `commands/76-docker-all-containers-after.json` | 0 | 0.047629 | `null` |
| `commands/77-docker-dangling-volumes-after.json` | 0 | 0.017105 | `null` |
| `commands/78-osworld-image-declared-volumes.json` | 0 | 0.027376 | `null` |
| `commands/79-unlink-local-guest-image.json` | 0 | 0.006093 | `null` |
| `commands/80-fast-suite-ci-invocation.json` | 0 | 145.532573 | `{"passed": 2776, "runtime": 144.62, "skipped": 6, "warnings": 12}` |
| `commands/81-unit-suite-serial.json` | 0 | 381.09958 | `{"deselected": 1, "passed": 2776, "runtime": 379.72, "skipped": 6, "warnings": 3}` |
| `commands/82-worktree-status-after-integration.json` | 0 | 0.031019 | `null` |
| `commands/83-checklist-source.json` | 0 | 0.017807 | `null` |
| `commands/84-public-wording-inventory.json` | 0 | 0.008339 | `null` |
| `commands/85-worktree-status-before-redaction-scan.json` | 0 | 0.037065 | `null` |
| `commands/86-redaction-scan-collected-records.json` | 0 | 0.09186 | `null` |

## Integrity and redaction indexes

`evidence-manifest.json` lists the SHA-256 and size of every other file in this directory and of each referenced repository artifact. `redaction-scan.json` lists the prohibited-pattern counts for every file in this directory except itself.
