# PixelGym v5 four-policy D5.6 calibration runbook

**Status:** the first approved panel smoke is frozen after an unknown-outcome infrastructure
failure; the current panel cannot advance to calibration

## Outcome

Run four frozen policy systems over 50 calibration tasks that were not exposed by the Qwen pilot.
The run may reserve at most 5,724 model attempts and wire requests, makes no provider control
requests, and shares one $10 aggregate spend ledger that includes prior diagnostics and smoke
calls. This run produces calibration evidence only. It does not expose confirmatory tasks or
declare D4.12 or D5.6 passed.

## Frozen design

- Slot A: `google/gemini-3.7-flash`, Google AI Studio only, stateful, normalized coordinates.
- Slot B: `qwen/qwen3-vl-8b-instruct`, Alibaba only, stateful, normalized coordinates.
- Slot C: `meta-llama/llama-4-scout`, DeepInfra FP8 only, stateful, native coordinates.
- Slot D: `qwen/qwen3-vl-8b-instruct`, Alibaba only, stateless, normalized coordinates.
- Calibration manifest: `artifacts/grounding-v5-manifests/calibration-d56.json`.
- Task count: 50 per policy; action cap: 1,431 per policy.
- Retry rule: no retry after send; cancellation and reconciliation disabled.
- Failure rule: freeze the complete run after the first infrastructure, identity, price, parse,
  adapter, invalid-action, or evidence-integrity failure.

## Phase 1: exact four-call panel smoke

For a newly approved policy panel, first commit the implementation and verify that tracked files
are clean. Generate the no-call plan into unused paths:

```bash
python scripts/run_grounding_v5_panel_smoke.py \
  --plan-only \
  --output artifacts/grounding-v5-d56-panel-smoke-plan-NEXT.json
```

The plan must report four development tasks, four environment actions, four model attempts, zero
control requests, four wire requests, and `provider_calls_made: 0`. Record its printed digest and
obtain explicit owner approval for that exact digest before continuing.

Execute only the approved plan into a fresh local restricted-evidence directory:

```bash
python scripts/run_grounding_v5_panel_smoke.py \
  --execute \
  --plan artifacts/grounding-v5-d56-panel-smoke-plan-NEXT.json \
  --approved-plan-sha256 'sha256:EXACT_APPROVED_DIGEST' \
  --output artifacts/grounding-v5-d56-panel-smoke-run-NEXT
```

Stop unless every policy reaches `pilot_action_limit`, all four response identities and routes
match, every action parses and validates through its frozen adapter, usage and cost are present,
and journal integrity verifies. Raw canonical responses remain in the local SQLite journal and are
not committed or published.

### Frozen first smoke

The owner approved plan
`sha256:7b49a52754b25435f89a534bbd3c02a64963e16cabb60ad1839e3883f9a054fd` on
2026-08-26. Slot A sent one request and received an HTTP error before any environment action. The
runner classified the episode as `infrastructure_failure`, attributed no cost, closed the journal,
and did not attempt slots B, C, or D. The run remains immutable in the local restricted-evidence
directory `artifacts/grounding-v5-d56-panel-smoke-run/`.

The no-call audit verified that the live OpenRouter model metadata still lists the selected Gemini
model, Google AI Studio route, image input, and requested inference parameters. The failed run did
not retain the HTTP status or a safe provider error code, so it cannot establish the exact rejection
reason. The transport now retains bounded non-message HTTP diagnostics for future policy packages.

The request crossed the send boundary and the runner recorded an unknown outcome. The frozen retry
rule therefore prohibits another request for this policy and prohibits a replacement assignment.
Do not approve or execute a regenerated plan for the current panel, reuse the consumed digest, or
overwrite either first-run artifact. To continue, the owner must approve a materially new Slot A
policy identity, such as a different provider route or model, and a new panel-smoke package. The
owner may instead stop the panel calibration.

## Phase 2: exact D5.6 calibration plan

After the smoke evidence verifies, generate the calibration plan:

```bash
python scripts/run_grounding_v5_d56_calibration.py \
  --plan-only \
  --smoke-output artifacts/grounding-v5-d56-panel-smoke-run-NEXT \
  --output artifacts/grounding-v5-d56-calibration-plan.json
```

Review the exact policy IDs, policy-manifest digests, price records, 50 task IDs, derived partition
digest, smoke-evidence digests, per-policy caps, aggregate caps, prior actual spend, and shared $10
guard. The plan must report `provider_calls_made: 0`. Obtain a second explicit owner approval for
the exact printed calibration-plan digest.

## Phase 3: execute only the approved calibration

```bash
python scripts/run_grounding_v5_d56_calibration.py \
  --execute \
  --plan artifacts/grounding-v5-d56-calibration-plan.json \
  --approved-plan-sha256 'sha256:EXACT_APPROVED_DIGEST' \
  --smoke-output artifacts/grounding-v5-d56-panel-smoke-run-NEXT \
  --output artifacts/grounding-v5-d56-calibration-run
```

Run slots sequentially in A, B, C, D order and tasks in manifest order. Continue after a scored
success or step-limit truncation so assigned tasks remain in the denominator. Stop before any
request whose theoretical maximum cannot fit under the remaining aggregate balance; retain every
completed response, invalid output, failure, exhausted budget, and unattempted assignment.

## Handoff evidence

Report raw commands and exit statuses, actual wire requests, model reservations, spend before and
after the run, classifications, attempted and successful policy-task pairs, and the complete
journal integrity report. The human reviews that evidence and owns the D5.6 verdict.
