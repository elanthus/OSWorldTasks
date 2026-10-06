# D5.10 raw evidence index

Evidence revision: `b0f00f8dc0a5f588e62ce4846cf7ba9fab037f61`

Evidence branch: `evidence/d510-b0f00f8d`

Observation window: `2026-10-06T05:09:16.508748+00:00` to `2026-10-06T06:06:48.719733+00:00`

Verdict: `null`

Verdict owner: project owner

This index copies and links stored observations only. It does not rerun commands, reinterpret gate semantics, declare a milestone verdict, approve public wording, or change human-owned checklist state.

## Path placeholders

| Placeholder | Meaning |
| --- | --- |
| `.venv` | In-tree dev virtual environment at the documented location, ignored by Git (records 79-81). |
| `<home>` | Home directory of the recording account. |
| `<path-0>` | Evidence worktree (repository root) at the evidence revision. |
| `<path-1>` | Collection directory outside the worktree that held the command records, probe scripts, and the two scratch virtual environments during collection. |
| `<path-2>` | Scratch dev virtual environment: python3.12 -m venv, then pip install -e ".[dev]" (records 06-07). |
| `<path-3>` | Scratch integration virtual environment: python3.12 -m venv, then pip install -e ".[dev,osworld]" playwright==1.62.0 (records 62-63). |
| `<path-4>` | Session scratch directory. |
| `<path-5>` | Session scratch directory, alternate spelling without the /private prefix. |
| `<system-temp>` | System temporary directory of the recording process. |

## Recording notes

- Every record was written by scripts/record_gate_command.py at the evidence revision, run as /opt/homebrew/bin/python3.12 -m scripts.record_gate_command from <path-0> (schema pixelgym-d412-command-record-v1). Records are numbered in start order. Gaps in the numbering are the withdrawn records listed below.
- Non-git commands were recorded under env -i with only PATH, HOME, TMPDIR, LANG, and LC_ALL passed through; records 11 and 66 list the environment variable names a child process observed. Git commands (records 00, 01, 05, 54, 89, 90, 92) are invoked as git -C <path-0> and were recorded without the env -i wrapper because the recording session's worktree guard rejected wrapped git invocations. Record 00 was recorded with only the --cwd path redaction; the later git records also pass the scratch and home --redact-path arguments.
- Record 03 runs uname -mprsv instead of uname -a. It prints every uname -a field except the network node hostname.
- Records 10, 11, 45, 64, 66, and 81 run probe scripts from <path-1>/probes. Each probe prints its own source before its output.
- Record 61 ran the browser suites with Playwright 1.63.0 from the dev extra. The command output names a chromium_headless_shell-1243 executable that was absent from the local Playwright cache. No browser was downloaded. Record 67 ran the same pytest command in <path-3>, where record 63 installed playwright==1.62.0 (inside the declared playwright>=1.52); its browsers.json names chromium and chromium-headless-shell revision 1234, which were already in the local cache.
- OSWorld: record 72 shows no guest image in the worktree and record 73 ran the suite in that state. Records 74-75 created the ignored .cache/osworld directory and linked the existing local guest image file from the primary checkout's ignored .cache/osworld directory; record 77 lists its size and modification time. The installed OSWorld Docker provider bind-mounts the image with "mode": "ro" (desktop_env/providers/docker/provider.py in <path-3>). Record 78 ran the suite with the link, and record 86 removed the link. Records 69-71 and 82-85 list Docker containers, images, and volumes; record 84 has no before-run counterpart. No image, VM disk, or browser was pulled or downloaded.
- Records 79-81, 87, and 88 repeat the dev setup and both fast-suite invocations with the in-tree .venv. They replace withdrawn records 16 and 17, whose pytest warning summaries printed the scratch virtual environment path in a ../-relative form that the recorder's path redaction does not replace.
- Records 55-59 select test node IDs chosen by reading test names in tests/unit. Each command lists its node IDs. Every test function in tests/unit/test_grounding_v5_runner.py (57) and tests/unit/test_grounding_v5.py (27) is listed in exactly one of the five groups; other files may also be covered by the fast-suite records.
- Record 90 is git show of the retired plan at f0bcbd6f3e379f880baeca233d4b7e05528bcfc5. The generator checks every checklist entry against that record's text and line numbers.
- Network use during collection: the pip installs in records 07, 63, and 80. No provider credential was present (records 11 and 66), no paid model call was made, and no evaluation was rerun.
- The redaction scan (record 93, and redaction-scan.json for this directory) uses the seven D4.12 pattern categories from scripts/generate_d412_evidence_report.py plus system_temp_paths, provider_api_keys, and bearer_tokens.

## Checklist evidence

Checklist text is copied verbatim from `f0bcbd6f3e379f880baeca233d4b7e05528bcfc5:plans/grounding-v5-agent-benchmark.md`. The observation column copies exit statuses and parsed pytest counts from the listed records.

| # | Checklist line | Source | Stored raw observation | Records | Artifacts |
| ---: | --- | --- | --- | --- | --- |
| 1 | Review raw commands, outputs, evidence integrity, limitations, benchmark verdict, and any public wording | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:658` | `00-git-revision`: exit 0<br>`01-worktree-status`: exit 0<br>`54-worktree-status-after-verifiers`: exit 0<br>`89-worktree-status-after-integration`: exit 0<br>`91-public-wording-inventory`: exit 0<br>`92-worktree-status-before-redaction-scan`: exit 0<br>`93-redaction-scan-collected-records`: exit 0 | `commands/00-git-revision.json`, `commands/01-worktree-status.json`, `commands/54-worktree-status-after-verifiers.json`, `commands/89-worktree-status-after-integration.json`, `commands/91-public-wording-inventory.json`, `commands/92-worktree-status-before-redaction-scan.json`, `commands/93-redaction-scan-collected-records.json` | `docs/v5-benchmark-design.md` |
| 2 | The human explicitly approves D5.1 and sequencing relative to the separate D4.12 gate. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:785` | no stored observation | none | `artifacts/grounding-v5-d56-calibration-approval.md`, `artifacts/decisions/d4.12.json` |
| 3 | Versioned schemas and disjoint development, calibration, and confirmatory seed lists are committed before task capture or model calls. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:786` | `55-v5-group-generator-partitions-admission`: exit 0; pytest {"passed": 49, "runtime": 8.94} | `commands/55-v5-group-generator-partitions-admission.json` | `artifacts/grounding-v5-contracts.md`, `artifacts/grounding-v5-manifests/development.json`, `artifacts/grounding-v5-manifests/calibration.json`, `artifacts/grounding-v5-manifests/calibration-d56.json`, `artifacts/grounding-v5-manifests/confirmatory.json`, `pixelgym/grounding/v5/seeds.py`, `pixelgym/grounding/v5/schemas/task.schema.json`, `pixelgym/grounding/v5/schemas/policy.schema.json`, `pixelgym/grounding/v5/schemas/attempt-event.schema.json`, `pixelgym/grounding/v5/schemas/memory-task.schema.json`, `pixelgym/grounding/v5/schemas/memory-task-v3.schema.json` |
| 4 | Six generator families produce the frozen allocations and satisfy every admission rule. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:788` | `55-v5-group-generator-partitions-admission`: exit 0; pytest {"passed": 49, "runtime": 8.94}<br>`46-verify-d59-freeze`: exit 1 | `commands/55-v5-group-generator-partitions-admission.json`, `commands/46-verify-d59-freeze.json` | `artifacts/grounding-v5-development-admission.json`, `artifacts/grounding-v5-d59-freeze/admission.json`, `artifacts/grounding-v5-d59-freeze/task-manifest.json` |
| 5 | Every declared valid and adversarial trace replays twice with identical semantic state and bitwise-identical corresponding screenshots. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:789` | `61-browser-integration`: exit 1; pytest {"failed": 15, "passed": 1, "runtime": 12.65}<br>`67-browser-integration-playwright-1-62-retry`: exit 0; pytest {"passed": 16, "runtime": 155.46}<br>`55-v5-group-generator-partitions-admission`: exit 0; pytest {"passed": 49, "runtime": 8.94} | `commands/61-browser-integration.json`, `commands/67-browser-integration-playwright-1-62-retry.json`, `commands/55-v5-group-generator-partitions-admission.json` | `artifacts/grounding-v5-development-admission.json`, `artifacts/grounding-v5-development-sample/manifest.json` |
| 6 | Golden policies reach reward `1.0` exactly once; all declared near-miss and reward-hacking mutations remain at `0.0`. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:791` | `55-v5-group-generator-partitions-admission`: exit 0; pytest {"passed": 49, "runtime": 8.94}<br>`18-golden-trajectory-check`: exit 0 | `commands/55-v5-group-generator-partitions-admission.json`, `commands/18-golden-trajectory-check.json` | `artifacts/grounding-v5-development-admission.json`, `artifacts/grounding-v5-d59-freeze/admission.json` |
| 7 | Stateful policy reset, policy-egress isolation, pre-send attempt persistence, unknown-attempt reconciliation, bounded deadline/cancellation settlement, post-attempt and post-dispatch state reconstruction, canonical parser-input recovery, atomic candidate/checkpoint persistence, sealed parser failure, exact backend-state restore or reconnect, durable dispatch ordering, invalid output, call caps, resume, authoritative replay, credential-free policy inputs, secret exclusion, redaction binding, and evidence integrity have no-cost failure-path tests. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:793` | `56-v5-group-stateful-harness-journal-resume-dispatch-restore`: exit 0; pytest {"passed": 124, "runtime": 6.69}<br>`57-v5-group-credential-secret-redaction-integrity`: exit 0; pytest {"passed": 183, "runtime": 3.83}<br>`59-v5-group-call-cap-planning`: exit 0; pytest {"passed": 27, "runtime": 14.08}<br>`73-osworld-v5-integration`: exit 0; pytest {"runtime": 0.75, "skipped": 1}<br>`78-osworld-v5-integration-linked-guest-image`: exit 0; pytest {"passed": 1, "runtime": 398.09, "warnings": 6} | `commands/56-v5-group-stateful-harness-journal-resume-dispatch-restore.json`, `commands/57-v5-group-credential-secret-redaction-integrity.json`, `commands/59-v5-group-call-cap-planning.json`, `commands/73-osworld-v5-integration.json`, `commands/78-osworld-v5-integration-linked-guest-image.json` | `artifacts/grounding-v5-contracts.md` |
| 8 | No-cost OS-level integration evidence denies unauthorized policy egress and preserves required provider-fake, backend, application, and controller traffic without internet or real-provider calls. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:799` | `60-v5-sandbox-integration`: exit 0; pytest {"passed": 2, "runtime": 1.67} | `commands/60-v5-sandbox-integration.json` | `artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json` |
| 9 | The documented fast suite passes without browser, network, OSWorld, provider credentials, or model calls. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:801` | `11-boundary-inventory`: exit 0<br>`79-create-in-tree-dev-venv`: exit 0<br>`80-install-dev-in-tree-venv`: exit 0<br>`87-fast-suite-ci-invocation-in-tree-venv`: exit 0; pytest {"passed": 2568, "runtime": 162.54, "skipped": 6, "warnings": 12}<br>`88-unit-suite-serial-in-tree-venv`: exit 0; pytest {"deselected": 1, "passed": 2568, "runtime": 398.43, "skipped": 6, "warnings": 3} | `commands/11-boundary-inventory.json`, `commands/79-create-in-tree-dev-venv.json`, `commands/80-install-dev-in-tree-venv.json`, `commands/87-fast-suite-ci-invocation-in-tree-venv.json`, `commands/88-unit-suite-serial-in-tree-venv.json` | none |
| 10 | A free plan-only command reports exact environment-action, model-attempt, provider-control, and total wire-request caps for each approved phase. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:803` | `59-v5-group-call-cap-planning`: exit 0; pytest {"passed": 27, "runtime": 14.08}<br>`45-d59-recorded-source-revisions`: exit 0<br>`46-verify-d59-freeze`: exit 1<br>`48-verify-d59-haiku-freeze`: exit 1<br>`49-verify-d59-haiku-retry-successor`: exit 1<br>`50-verify-d59-haiku-network-retry`: exit 1 | `commands/59-v5-group-call-cap-planning.json`, `commands/45-d59-recorded-source-revisions.json`, `commands/46-verify-d59-freeze.json`, `commands/48-verify-d59-haiku-freeze.json`, `commands/49-verify-d59-haiku-retry-successor.json`, `commands/50-verify-d59-haiku-network-retry.json` | `artifacts/grounding-v5-scripted-call-cap-plan.json`, `artifacts/grounding-v5-d59-freeze/execution-plan.json`, `artifacts/grounding-v5-d59-haiku-freeze/execution-plan.json`, `artifacts/grounding-v5-d59-haiku-api-retry-successor/execution-plan.json`, `artifacts/grounding-v5-d59-haiku-network-retry/execution-plan.json` |
| 11 | The agent stops for explicit calibration approval and again for explicit confirmatory approval. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:805` | no stored observation | none | `artifacts/grounding-v5-d56-calibration-approval.md`, `artifacts/grounding-v5-d58-final-design/decision.json`, `artifacts/grounding-v5-d59-haiku-selection/owner-selection.json`, `artifacts/grounding-v5-d59-haiku-execution/owner-approval.json`, `artifacts/grounding-v5-d59-haiku-api-retry-execution/owner-approval.json`, `artifacts/grounding-v5-d59-haiku-results/authorization-history.json` |
| 12 | The confirmatory report is generated only from sealed evidence, retains every failure, and separates primary, diagnostic, ablation, reliability, and exploratory results. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:807` | `52-check-d59-confirmatory-analysis`: exit 0<br>`51-report-d59-haiku-help`: exit 0<br>`53-d59-private-run-directories-present`: exit 1<br>`58-v5-group-metrics-statistics`: exit 0; pytest {"passed": 48, "runtime": 1.47} | `commands/52-check-d59-confirmatory-analysis.json`, `commands/51-report-d59-haiku-help.json`, `commands/53-d59-private-run-directories-present.json`, `commands/58-v5-group-metrics-statistics.json` | `artifacts/grounding-v5-d59-haiku-results/report.md`, `artifacts/grounding-v5-d59-haiku-results/report.json`, `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md`, `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.json`, `artifacts/grounding-v5-d59-haiku-results/publication-relation.json`, `artifacts/grounding-v5-d59-haiku-results/authorization-history.json`, `artifacts/grounding-v5-d59-freeze/analysis-plan.md` |
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

Checklist row 12: `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md` line 126

> The stop rule was amended after repeated intermittent network failures to allow for retries in the case of intermittent network issues. Each failed assignment remains in the results. No prior assignment was replayed.

Checklist row 12: `artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md` lines 111-121

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

## Records withdrawn before packaging

- `commands/09-dev-package-versions.json` (sha256 `3329b317e87b5a5f0d9323fb8671e5cfe31813a33ab43f83b4497da1b862a557`, exit 0): pip freeze --all printed the editable install's Git remote in its git@host form, which matches the redaction scan's email pattern. Withdrawn before the redaction scan and retained outside the tree.
  - Replacement records: `commands/10-dev-package-versions.json`
- `commands/16-fast-suite-ci-invocation.json` (sha256 `b19752e3115d13f0273857f3f5c63f24083d5a37bebeb2f4e13a4d229864e9d4`, exit 0): The pytest warning summary printed the scratch dev virtual environment path in a ../-relative form that the recorder does not redact (15 local_absolute_paths matches across records 16 and 17 in an unrecorded pre-check). Withdrawn before the recorded redaction scan and retained outside the tree.
  - Last output line, copied verbatim: `2568 passed, 6 skipped, 12 warnings in 191.32s (0:03:11)`
  - Replacement records: `commands/87-fast-suite-ci-invocation-in-tree-venv.json`
- `commands/17-unit-suite-serial.json` (sha256 `cecd6875cbdfee4ab9db09e9ec73cdcbe22d017406def84eb6f5e6aef5a9ea78`, exit 0): Same ../-relative scratch path in the pytest warning summary as record 16. Withdrawn before the recorded redaction scan and retained outside the tree.
  - Last output line, copied verbatim: `2568 passed, 6 skipped, 1 deselected, 3 warnings in 420.72s (0:07:00)`
  - Replacement records: `commands/88-unit-suite-serial-in-tree-venv.json`
- `commands/76-linked-guest-image-listing.json` (sha256 `7347efb8ca1a8ba868f6ab8d88c529a4198549ab770a5505f47d60f0cd66c235`, exit 0): ls -lL printed the local account and group names. Withdrawn before the redaction scan and retained outside the tree.
  - Replacement records: `commands/77-linked-guest-image-size.json`

## Items not run

- `scripts/build_grounding_benchmark_v2.py`: Marked as a frozen reproduction entry point. It has no argument parser and no verify or check mode; any invocation, including --help, calls build_benchmark_v2 and writes the tracked v2 benchmark files. Not invoked.
- `scripts/generate_grounding_report.py (default mode)`: No verify or check mode; the default invocation writes the tracked report and results. --help is recorded; the read-only verifier scripts/verify_grounding_report.py is recorded separately.
  - Record: `commands/21-generate-grounding-report-v1-help.json`
  - Record: `commands/19-verify-grounding-report-v1.json`
- `scripts/generate_grounding_report_v2.py (default mode)`: No verify or check mode; without --output-dir it writes the tracked report and provenance. --help is recorded; scripts/verify_grounding_report_v2.py is recorded separately.
  - Record: `commands/22-generate-grounding-report-v2-help.json`
  - Record: `commands/20-verify-grounding-report-v2.json`
- `scripts/generate_grounding_clustered_analysis.py (default mode)`: No verify or check mode; without --output-dir it writes the tracked supplement files. --help is recorded.
  - Record: `commands/23-generate-grounding-clustered-analysis-help.json`
- `scripts/record_grounding_v5_d56_retained_development_runs.py (default mode)`: No verify or check mode; it reads ignored local originals and writes the registry. --help is recorded.
  - Record: `commands/24-record-d56-retained-development-runs-help.json`
- `scripts/report_grounding_v5_d59_haiku.py --verify`: --verify requires --input, the private D5.9 run directory. Record 53 shows the ignored D5.9 run directories absent from the worktree. --help is recorded. No input was substituted.
  - Record: `commands/51-report-d59-haiku-help.json`
  - Record: `commands/53-d59-private-run-directories-present.json`
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

Copied verbatim from `commands/91-public-wording-inventory.json` (exit 0). Each line is `file:line:text` as printed by:

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
docs/evidence-index.md:27:| What does the D5.9 confirmatory analysis show? | The [confirmatory analysis](../artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md) and its [JSON source](../artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.json) are generated by `scripts/analyze_grounding_v5_d59.py` from the public projection and the bound execution plan. They report the pre-registered paired McNemar test and family-stratified logical-cluster bootstrap for history minus stateless on the primary phase, the reliability subset separately, and labelled exploratory rates, termination profiles, and resource use. Diagnostics that need the private journals are listed as not computable. D5.10 is the owner's review. |
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
docs/evidence-index.md:90:The planning directory was retired after the D5.9 execution. Four frozen reports still contain
docs/evidence-index.md:91:relative links into it: the [completed-calibrations report](../artifacts/grounding-v5-d56-completed-calibrations-report.md),
docs/evidence-index.md:92:the [incomplete Qwen report](../artifacts/grounding-v5-d56-qwen-full-calibration-report.md), the
docs/evidence-index.md:93:[calibration approval](../artifacts/grounding-v5-d56-calibration-approval.md), and the
docs/evidence-index.md:94:[PR196 report](../artifacts/grounding-v5-pr196-calibration/report.md). These links no longer
docs/evidence-index.md:96:digests. Two generators still emit those links: `scripts/publish_grounding_v5_d56_completed_calibrations.py`
docs/evidence-index.md:99:those links named is now summarized in the [v5 benchmark design](v5-benchmark-design.md).
```

## Raw command inventory

Each JSON record stores the exact argv/command, cwd, public environment, UTC timestamps, process runtime, exit status, and complete combined output. Failed and skipped observations remain in the inventory.

| Record | Exit | Runtime (s) | Parsed pytest summary |
| --- | ---: | ---: | --- |
| `commands/00-git-revision.json` | 0 | 0.012257 | `null` |
| `commands/01-worktree-status.json` | 0 | 0.034486 | `null` |
| `commands/02-python-version.json` | 0 | 0.010275 | `null` |
| `commands/03-os-architecture.json` | 0 | 0.00356 | `null` |
| `commands/04-docker-version.json` | 0 | 0.024356 | `null` |
| `commands/05-evidence-branch.json` | 0 | 0.01428 | `null` |
| `commands/06-create-dev-venv.json` | 0 | 1.835164 | `null` |
| `commands/07-install-dev.json` | 0 | 32.636257 | `null` |
| `commands/08-dev-pip-check.json` | 0 | 0.276086 | `null` |
| `commands/10-dev-package-versions.json` | 0 | 0.113393 | `null` |
| `commands/11-boundary-inventory.json` | 0 | 0.031801 | `null` |
| `commands/12-ruff-check.json` | 0 | 0.604644 | `null` |
| `commands/13-ruff-format-check.json` | 0 | 0.044233 | `null` |
| `commands/14-mypy-pixelgym.json` | 0 | 7.149322 | `null` |
| `commands/15-mypy-flows.json` | 0 | 1.022868 | `null` |
| `commands/18-golden-trajectory-check.json` | 0 | 2.934114 | `null` |
| `commands/19-verify-grounding-report-v1.json` | 0 | 0.568686 | `null` |
| `commands/20-verify-grounding-report-v2.json` | 0 | 0.081428 | `null` |
| `commands/21-generate-grounding-report-v1-help.json` | 0 | 0.202852 | `null` |
| `commands/22-generate-grounding-report-v2-help.json` | 0 | 0.085894 | `null` |
| `commands/23-generate-grounding-clustered-analysis-help.json` | 0 | 0.06224 | `null` |
| `commands/24-record-d56-retained-development-runs-help.json` | 0 | 0.280072 | `null` |
| `commands/25-verify-d56-completed-calibrations.json` | 0 | 4.891066 | `null` |
| `commands/26-verify-calibration-supplement.json` | 0 | 1.079763 | `null` |
| `commands/27-verify-d58-memory-admission.json` | 1 | 0.350178 | `null` |
| `commands/28-verify-d58-full-calibration.json` | 0 | 0.359166 | `null` |
| `commands/29-verify-d58-gemini38-calibration.json` | 0 | 0.323609 | `null` |
| `commands/30-verify-d58-reliable-continuation.json` | 1 | 0.325897 | `null` |
| `commands/31-verify-d58-owner-budget-continuation.json` | 1 | 0.408484 | `null` |
| `commands/32-verify-d58-owner-budget-reconciliation.json` | 1 | 0.352024 | `null` |
| `commands/33-verify-d58-at-revision-focus-calibration.json` | 0 | 0.814857 | `null` |
| `commands/34-verify-d58-at-revision-focus-continuation.json` | 0 | 0.820214 | `null` |
| `commands/35-verify-d58-at-revision-reliable-diagnostic.json` | 0 | 0.836504 | `null` |
| `commands/36-verify-d58-at-revision-reliable-continuation.json` | 0 | 0.839798 | `null` |
| `commands/37-verify-d58-at-revision-reliable-extension.json` | 0 | 0.808401 | `null` |
| `commands/38-verify-d58-at-revision-owner-budget-continuation.json` | 0 | 0.908335 | `null` |
| `commands/39-verify-d58-at-revision-owner-budget.json` | 0 | 0.853471 | `null` |
| `commands/40-verify-pr196-calibration.json` | 0 | 0.360718 | `null` |
| `commands/41-verify-haiku-cli-replication.json` | 0 | 0.342205 | `null` |
| `commands/42-verify-d58-review-corrections.json` | 1 | 0.252406 | `null` |
| `commands/43-verify-d58-power-report.json` | 1 | 0.612836 | `null` |
| `commands/44-verify-d58-haiku-successor.json` | 1 | 0.369482 | `null` |
| `commands/45-d59-recorded-source-revisions.json` | 0 | 0.060349 | `null` |
| `commands/46-verify-d59-freeze.json` | 1 | 418.566835 | `null` |
| `commands/47-verify-d59-haiku-selection.json` | 0 | 0.051479 | `null` |
| `commands/48-verify-d59-haiku-freeze.json` | 1 | 0.308171 | `null` |
| `commands/49-verify-d59-haiku-retry-successor.json` | 1 | 0.568956 | `null` |
| `commands/50-verify-d59-haiku-network-retry.json` | 1 | 0.459468 | `null` |
| `commands/51-report-d59-haiku-help.json` | 0 | 0.260608 | `null` |
| `commands/52-check-d59-confirmatory-analysis.json` | 0 | 0.480918 | `null` |
| `commands/53-d59-private-run-directories-present.json` | 1 | 0.013499 | `null` |
| `commands/54-worktree-status-after-verifiers.json` | 0 | 0.043136 | `null` |
| `commands/55-v5-group-generator-partitions-admission.json` | 0 | 9.897569 | `{"passed": 49, "runtime": 8.94}` |
| `commands/56-v5-group-stateful-harness-journal-resume-dispatch-restore.json` | 0 | 7.753152 | `{"passed": 124, "runtime": 6.69}` |
| `commands/57-v5-group-credential-secret-redaction-integrity.json` | 0 | 4.537791 | `{"passed": 183, "runtime": 3.83}` |
| `commands/58-v5-group-metrics-statistics.json` | 0 | 2.178809 | `{"passed": 48, "runtime": 1.47}` |
| `commands/59-v5-group-call-cap-planning.json` | 0 | 14.813159 | `{"passed": 27, "runtime": 14.08}` |
| `commands/60-v5-sandbox-integration.json` | 0 | 2.047603 | `{"passed": 2, "runtime": 1.67}` |
| `commands/61-browser-integration.json` | 1 | 13.125247 | `{"failed": 15, "passed": 1, "runtime": 12.65}` |
| `commands/62-create-integration-venv.json` | 0 | 1.53483 | `null` |
| `commands/63-install-integration-dev-osworld.json` | 0 | 350.135519 | `null` |
| `commands/64-integration-package-versions.json` | 0 | 0.417539 | `null` |
| `commands/65-integration-pip-check.json` | 0 | 0.697 | `null` |
| `commands/66-integration-boundary-inventory.json` | 0 | 0.039056 | `null` |
| `commands/67-browser-integration-playwright-1-62-retry.json` | 0 | 157.849416 | `{"passed": 16, "runtime": 155.46}` |
| `commands/68-curl-wire-integration.json` | 0 | 28.080174 | `{"passed": 1, "runtime": 27.48}` |
| `commands/69-docker-osworld-containers-before.json` | 0 | 0.03339 | `null` |
| `commands/70-docker-all-containers-before.json` | 0 | 0.058479 | `null` |
| `commands/71-docker-osworld-images.json` | 0 | 0.039736 | `null` |
| `commands/72-osworld-guest-image-in-worktree.json` | 1 | 0.006602 | `null` |
| `commands/73-osworld-v5-integration.json` | 0 | 1.387345 | `{"runtime": 0.75, "skipped": 1}` |
| `commands/74-create-worktree-osworld-cache-dir.json` | 0 | 0.005184 | `null` |
| `commands/75-link-local-guest-image.json` | 0 | 0.004784 | `null` |
| `commands/77-linked-guest-image-size.json` | 0 | 0.006971 | `null` |
| `commands/78-osworld-v5-integration-linked-guest-image.json` | 0 | 399.061145 | `{"passed": 1, "runtime": 398.09, "warnings": 6}` |
| `commands/79-create-in-tree-dev-venv.json` | 0 | 2.304868 | `null` |
| `commands/80-install-dev-in-tree-venv.json` | 0 | 37.777186 | `null` |
| `commands/81-in-tree-venv-package-versions.json` | 0 | 0.280979 | `null` |
| `commands/82-docker-osworld-containers-after.json` | 0 | 0.029498 | `null` |
| `commands/83-docker-all-containers-after.json` | 0 | 0.050206 | `null` |
| `commands/84-docker-dangling-volumes-after.json` | 0 | 0.026385 | `null` |
| `commands/85-osworld-image-declared-volumes.json` | 0 | 0.030734 | `null` |
| `commands/86-unlink-local-guest-image.json` | 0 | 0.006344 | `null` |
| `commands/87-fast-suite-ci-invocation-in-tree-venv.json` | 0 | 172.240636 | `{"passed": 2568, "runtime": 162.54, "skipped": 6, "warnings": 12}` |
| `commands/88-unit-suite-serial-in-tree-venv.json` | 0 | 399.940711 | `{"deselected": 1, "passed": 2568, "runtime": 398.43, "skipped": 6, "warnings": 3}` |
| `commands/89-worktree-status-after-integration.json` | 0 | 0.042114 | `null` |
| `commands/90-checklist-source.json` | 0 | 0.020569 | `null` |
| `commands/91-public-wording-inventory.json` | 0 | 0.011686 | `null` |
| `commands/92-worktree-status-before-redaction-scan.json` | 0 | 0.027181 | `null` |
| `commands/93-redaction-scan-collected-records.json` | 0 | 0.104884 | `null` |

## Integrity and redaction indexes

`evidence-manifest.json` lists the SHA-256 and size of every other file in this directory and of each referenced repository artifact. `redaction-scan.json` lists the prohibited-pattern counts for every file in this directory except itself.
