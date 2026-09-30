# Scripts

Every script in this directory, what it does, and what it needs. Run scripts from the repository
root as modules or files with the project virtual environment active.

**Needs** says whether a script requires OSWorld, a browser, Docker, or a paid provider. "Paid
provider" covers both metered API calls and subscription model CLIs; those scripts only spend
after an explicit, digest-bound owner approval, and most have a no-call planning or verification
mode. "None" means the script reads and writes local files only (a few also run `git` or `gh`, as
noted).

**Frozen reproduction entry point** is "yes" when the script regenerates or verifies checked-in
evidence from stored inputs without new model calls. See [docs/reproduction.md](../docs/reproduction.md)
and [docs/evidence-index.md](../docs/evidence-index.md) for the evidence each one covers.

## Environment and validation

| Script | Purpose | Inputs | Outputs | Needs | Frozen reproduction entry point |
| --- | --- | --- | --- | --- | --- |
| `demo_fake_backend.py` | Run `PixelGuiEnv` on the fake backend and print an incomplete-form reward trace | `--seed` | stdout; optional `--screenshot` PNG | none | no |
| `golden_trajectory.py` | Generate, verify, and report the frozen golden trajectory | `--seed`, `--fixture` | golden trajectory fixture and report | none | yes |
| `validate_vendor_form_browser_boundary.py` | Run and store vendor-form browser-boundary evidence | `--seed` | `--output` JSON | browser | no |
| `prepare_osworld_docker.py` | Prepare the pinned local Docker host for OSWorld-V2 | `--cache-dir` | local Docker host setup | OSWorld, Docker | no |
| `smoke_osworld_reset.py` | Capture one real `PixelGuiEnv` reset through the local OSWorld Docker host | `--guest-image` | reset screenshot and evidence JSON under `artifacts/day-2/` | OSWorld, Docker | no |
| `osworld_space_smoke.py` | Run a bounded action/observation and empty-submit smoke on real OSWorld | `--guest-image` | `--output` JSON | OSWorld, Docker | no |
| `osworld_golden_trajectory.py` | Generate, check, or record the seed-7 golden trajectory on OSWorld | `--seed`, `--fixture`, `--guest-image` | `artifacts/day-2/real-golden` | OSWorld, Docker, browser | no |
| `validate_day2.py` | Run environment and OSWorld validators and store structured evidence | `--golden`, `--preparation`, `--guest-image` | `artifacts/day-2/raw`, `artifacts/validation-report.json` | OSWorld, Docker | no |
| `generate_validation_report.py` | Render Markdown from stored validation JSON without rerunning checks | `artifacts/validation-report.json` | `artifacts/validation-report.md` | none | yes |

## Grounding v1 and v2

| Script | Purpose | Inputs | Outputs | Needs | Frozen reproduction entry point |
| --- | --- | --- | --- | --- | --- |
| `capture_grounding_dataset.py` | Capture and validate the frozen grounding benchmark | task app | grounding dataset | browser | no |
| `build_grounding_benchmark_v2.py` | Build the balanced v2 benchmark from the checked-in v1 capture assets | v1 capture assets | v2 benchmark files | none | yes |
| `run_grounding_evaluation.py` | Run or plan the frozen grounding evaluation with an explicit call cap | `--provider`, `--pilot`/`--full`, `--max-new-calls` | predictions JSONL at `--output`; `--plan-only` makes no calls | paid provider | no |
| `generate_grounding_report.py` | Recreate grounding statistics, figures, gallery, and report without model calls | predictions, error review, results | `artifacts/grounding-report.md` and figures | none | yes |
| `verify_grounding_report.py` | Verify the canonical grounding report from frozen evidence without writing files | frozen grounding evidence | exit status | none | yes |
| `generate_grounding_clustered_analysis.py` | Generate the target-clustered supplement to the frozen v1 analysis | frozen results and predictions | `artifacts/grounding-clustered-analysis-v1.{json,md}` | none | yes |
| `measure_grounding_maintenance.py` | Compare grounding maintenance surfaces and built-wheel contents at two revisions | `--before-revision`, `--after-revision` | stdout JSON | none (runs `git`) | no |

## Release evidence

| Script | Purpose | Inputs | Outputs | Needs | Frozen reproduction entry point |
| --- | --- | --- | --- | --- | --- |
| `release_observations.py` | Library: collect raw grounding-release observations with no verdict | checked-in release evidence | used by `collect_release_observations.py` | none | no |
| `collect_release_observations.py` | Print raw grounding-release evidence without declaring a verdict | checked-in release evidence | stdout or `--output` JSON | none | no |
| `run_clean_install_check.py` | Capture raw clean-install command evidence without issuing a verdict | repository checkout | `artifacts/day-3/release/clean-install.json` | none | no |
| `inventory_public_release.py` | Inventory public-release links, redaction risks, and restricted evidence surfaces | `--root`, `--mode` | stdout JSON | none | no |
| `generate_workflow_history_evidence.py` | Generate bounded Git and GitHub workflow-history evidence | `--repository`, `--end-ref` | `artifacts/agent-assisted-workflow-history.json` | none (runs `git` and `gh`) | no |
| `package_evidence_images.py` | Package release-hosted evidence PNGs into deterministic archives | `artifacts/*/images.manifest.json` and PNGs | archives and `SHA256SUMS` in `dist/` | none | no |
| `fetch_evidence_images.py` | Restore release-hosted evidence PNGs and verify them against the manifests | `--tag`, `--repo`, `--set` | PNGs under `artifacts/` | none (downloads a GitHub release) | yes |

## Platform (Milestone 4)

| Script | Purpose | Inputs | Outputs | Needs | Frozen reproduction entry point |
| --- | --- | --- | --- | --- | --- |
| `platform_compose.py` | Run the local platform stack with derived, build-bound source provenance | repository checkout | running local stack | Docker | no |
| `platform_migrate.py` | Run the control-plane migration exactly once before serving | `--database` | migrated database | none | no |
| `platform_demo.py` | Prepare the two demo candidates plus a no-cost rollback seed | `--database`, `--fixture` | control-plane records | none | no |
| `capture_platform_api.py` | Append a redacted lifecycle API exchange to the platform transcript | `--base-url`, `--candidate-id`, `--image`, `--target` | `artifacts/platform/demo-api-transcript.jsonl` | none (needs the running local stack) | no |
| `capture_d412_metaflow_resume.py` | Capture structured Metaflow resume ledgers from the real local runtime | `--repository-root` | resume ledgers | none (runs local Metaflow) | no |
| `record_gate_command.py` | Record one platform-gate command as redacted raw evidence without judging it | command, `--cwd`, `--redact-path` | `--output` JSON | none | no |
| `export_platform_evidence.py` | Export reviewer-safe lifecycle evidence from stored control-plane records | `--database` | `--output` directory under `artifacts/platform` | none | no |
| `verify_immutable_artifacts.py` | Verify pinned candidate artifacts without parsing, scoring, or calling a provider | `--database`, `--local-root` | `--output` JSON | none | no |
| `generate_d412_evidence_report.py` | Index stored platform-gate observations without rerunning or judging them | `--evidence-dir` | `artifacts/platform/EVIDENCE_REVIEW.md` | none | yes |

## Grounding v5: planning, calibration, and smoke runs

| Script | Purpose | Inputs | Outputs | Needs | Frozen reproduction entry point |
| --- | --- | --- | --- | --- | --- |
| `run_grounding_v5_calibration.py` | Validate or execute one versioned manifest-driven grounding plan | `--plan`, `--approved-plan-sha256` | run directory from the plan; `--validate-only` makes no calls | paid provider | no |
| `run_grounding_v5_provider_smoke.py` | Plan or execute one approved development-only smoke request | `--plan`, `--approved-plan-sha256`, `--maximum-spend-usd` | `--output`; `--plan-only` makes no calls | paid provider | no |
| `run_grounding_v5_calibration_pilot.py` | Plan or execute the approved bounded calibration pilot | `--plan`, `--approved-plan-sha256` | `--output`; `--plan-only` makes no calls | paid provider | no |
| `run_grounding_v5_expanded_calibration.py` | Plan or execute the approved full-episode calibration expansion | `--plan`, `--approved-plan-sha256` | `--output`; `--plan-only` makes no calls | paid provider | no |
| `run_grounding_v5_panel_smoke.py` | Plan or execute the exact four-call D5.6 panel integration smoke | `--plan`, `--approved-plan-sha256` | `--output`; `--plan-only` makes no calls | paid provider | no |
| `prepare_grounding_v5_comparison.py` | Prepare a no-call comparison plan, or derive diagnostics from stored outcomes | `--phase`, `--generation`, `--plan`, `--summary` | plan or diagnostics JSON at `--output` | none | no |
| `prepare_grounding_v5_slot_c.py` | Prepare a fresh Slot C plan without credentials or provider calls | `--phase`, `--candidate`, `--generation`, `--maximum-spend-usd` | plan JSON at `--output` | none | no |
| `record_grounding_v5_d56_retained_development_runs.py` | Record digest-only entries for retained D5.6 development runs | D5.6 plan and run summaries | `artifacts/grounding-v5-d56-retained-development-runs.json` | none | yes |
| `publish_grounding_v5_d56_completed_calibrations.py` | Publish completed D5.6 calibrations from committed, response-free evidence | committed D5.6 evidence | `artifacts/grounding-v5-d56-*`; `--verify` checks them | none | yes |
| `publish_grounding_v5_calibration_supplement.py` | Reproduce response-free calibration results without journals or provider calls | committed calibration evidence | `artifacts/grounding-v5-calibration-supplement/` | none | yes |

## Grounding v5: D5.8 design and calibration

| Script | Purpose | Inputs | Outputs | Needs | Frozen reproduction entry point |
| --- | --- | --- | --- | --- | --- |
| `prepare_grounding_v5_memory.py` | Generate response-free development admission and the non-executable D5.8 pilot plan | task generator, price snapshot | `artifacts/grounding-v5-d58-design/memory-repair`; `--verify` checks it | none | yes |
| `run_grounding_v5_memory_pilot.py` | Prepare, execute once, or report the approved twenty-call D5.8 memory pilot | pilot plan, `--approved-plan-digest` | `artifacts/grounding-v5-d58-calibration-pilot` | paid provider | no |
| `run_grounding_v5_memory_calibration.py` | Admit, freeze, execute, and report the approved full D5.8 matched calibration | pilot evidence, `--approved-plan-digest` | D5.8 full-calibration directory | paid provider | no |
| `verify_grounding_v5_full_calibration.py` | Verify the D5.8 full calibration with checks that stay active under `python -O` | `artifacts/grounding-v5-d58-full-calibration`, optional `--journal` | exit status | none | yes |
| `run_grounding_v5_gemini38_calibration.py` | Freeze and execute the owner-approved Gemini 3.8 comparison | `--approved-plan-digest` | `artifacts/grounding-v5-d58-gemini38-calibration` | paid provider | no |
| `verify_d58_gemini38_calibration.py` | Verify the closed Gemini 3.8 calibration evidence against its analysis and manifest | calibration directory, optional `--journal` | exit status; `--record` writes the analysis once | none | yes |
| `run_grounding_v5_focus_diagnostic.py` | Prepare and execute the approved twenty-call focus/timeout repair diagnostic | `--approved-plan-digest` | `artifacts/grounding-v5-d58-focus-diagnostic` | paid provider | no |
| `run_grounding_v5_focus_calibration.py` | Freeze and execute the approved focus-repaired calibration | `--approved-plan-digest` | `artifacts/grounding-v5-d58-focus-calibration` | paid provider | no |
| `run_grounding_v5_focus_continuation.py` | Continue untouched focus-calibration assignments under the approved budget | `--approved-plan-digest` | `artifacts/grounding-v5-d58-focus-continuation` | paid provider | no |
| `run_grounding_v5_reliable_diagnostic.py` | Source-bound reliability diagnostic under a capped send count and budget | `--approved-plan-digest` | `artifacts/grounding-v5-d58-reliable-diagnostic` | paid provider | no |
| `run_grounding_v5_reliable_continuation.py` | Run the untouched assignments with the repaired transport under the shared ceiling | `--approved-plan-digest` | `artifacts/grounding-v5-d58-reliable-continuation` | paid provider | no |
| `verify_d58_reliable_continuation.py` | Audit the repaired-transport continuation and its exact file manifest | continuation directory, optional `--journal` | exit status | none | yes |
| `run_grounding_v5_reliable_extension.py` | Continue untouched assignments within the approved total budget | `--approved-plan-digest` | `artifacts/grounding-v5-d58-reliable-extension` | paid provider | no |
| `run_grounding_v5_owner_budget_continuation.py` | Continue untouched assignments with the owner-approved zero unresolved holds | `--approved-plan-digest` | `artifacts/grounding-v5-d58-owner-budget-continuation` | paid provider | no |
| `verify_d58_owner_budget_continuation.py` | Audit the owner budget adjustment, its reconciliation, and the continued calibration | owner-budget evidence, optional `--journal` | exit status | none | yes |
| `verify_d58_at_revision.py` | Run a closed D5.8 verifier at its recorded source revision in a temporary tree | evidence directory name, optional `--journal` | verifier result JSON | none (runs `git archive`) | yes |
| `run_grounding_v5_cli_memory.py` | Prepare and run the PR196 CLI calibration, one model per process | `--model`, `--approval-file` | `--output` run directory | paid provider | no |
| `publish_pr196_calibration.py` | Audit restricted PR196 journals and publish response-free calibration evidence | restricted journals (`--export`) | `artifacts/grounding-v5-pr196-calibration`; `--verify` checks it | none | yes |
| `publish_haiku_cli_replication.py` | Publish and verify the response-free Haiku CLI calibration evidence | restricted journals (`--export`) | `artifacts/grounding-v5-haiku-cli-replication`; `--verify` checks it | none | yes |
| `prepare_grounding_v5_review_corrections.py` | Publish response-free corrections without replacing frozen calibration bundles | frozen calibration bundles | `artifacts/grounding-v5-d58-review-corrections`; `--verify` checks it | none | yes |
| `prepare_grounding_v5_power_report.py` | Render corrected prospective prose from frozen structured evidence | `artifacts/grounding-v5-d58-final-design/power.json` | review-corrections report; `--verify` checks it | none | yes |
| `prepare_grounding_v5_d58_haiku_successor.py` | Build the response-free Haiku successor to the D5.8 power calculation | D5.8 design evidence | `artifacts/grounding-v5-d58-haiku-successor`; `--verify` checks it | none | yes |

## Grounding v5: D5.9 confirmatory run

| Script | Purpose | Inputs | Outputs | Needs | Frozen reproduction entry point |
| --- | --- | --- | --- | --- | --- |
| `prepare_grounding_v5_d59_freeze.py` | Build or verify the response-free D5.9 freeze artifacts | `--source-revision` | `artifacts/grounding-v5-d59-freeze`; `--verify` checks it | none | yes |
| `prepare_grounding_v5_d59_haiku_selection.py` | Build or verify the response-free D5.9 Haiku owner-selection record | Haiku successor analysis | `artifacts/grounding-v5-d59-haiku-selection`; `--verify` checks it | none | yes |
| `prepare_grounding_v5_d59_haiku_freeze.py` | Build or verify the response-free D5.9 Haiku freeze successor | `--source-revision` | `artifacts/grounding-v5-d59-haiku-freeze`; `--verify` checks it | none | yes |
| `run_grounding_v5_d59_haiku.py` | Prepare or execute the exactly approved Haiku D5.9 confirmatory campaign | freeze artifacts, owner approval | `--output` run directory | paid provider | no |
| `continue_grounding_v5_d59_haiku.py` | Continue a stopped campaign under explicit assignment-failure amendments | `--predecessor`, `--owner-statement` | `--output` run directory | paid provider | no |
| `prepare_grounding_v5_d59_haiku_retry_successor.py` | Audit the stopped D5.9 run and build its no-call API-retry successor | `--source-revision`, `--private-evidence` | successor plan; `--verify` checks it | none | yes |
| `d59_haiku_retry_execution.py` | Library: exact authorization and runtime binding for the retry successor | owner approval, execution plan | used by `run_grounding_v5_d59_haiku_retry_successor.py` | none | no |
| `run_grounding_v5_d59_haiku_retry_successor.py` | Prepare or execute the exactly approved Haiku D5.9 retry successor | successor plan, owner approval | run directory | paid provider | no |
| `prepare_grounding_v5_d59_haiku_network_retry.py` | Prepare or verify the response-free counted-network-retry D5.9 successor | `--source-revision` | successor plan; `--verify` checks it | none | yes |
| `run_grounding_v5_d59_haiku_network_retry.py` | Execute a separately approved D5.9 plan with counted connection-reset retries | `--approval` | `--output` run directory | paid provider | no |
| `report_grounding_v5_d59_haiku.py` | Publish an allowlisted D5.9 result projection without raw responses | `--input` run directory | `--output` projection; `--verify` checks it | none | yes |
| `analyze_grounding_v5_d59.py` | Generate the D5.9 confirmatory analysis from the public result projection | `artifacts/grounding-v5-d59-haiku-results`, execution plan | confirmatory analysis JSON and Markdown; `--check` verifies them | none | yes |
