# PixelGym v5 D5.6 calibration runbook

**Status:** the replacement panel smoke completed; the four-slot calibration stopped during Slot A
and is frozen; a B/C/D-only successor is prepared but its exact plan digest remains unapproved

## Outcome

Run the frozen policy systems over the approved 50-task calibration partition while preserving each
consumed request and approval as immutable evidence. The current successor schedules only Slots B,
C, and D: at most 4,293 environment actions and 8,586 model attempts or wire requests, with no
provider control requests. Prior attributed spend is `$2.032875185`, leaving `$7.967124815` under
the shared `$10.00` ceiling. This run produces calibration evidence only. It does not expose
confirmatory tasks or declare D4.12 or D5.6 passed.

## Frozen design

- Slot A: `google/gemini-3.7-flash`, Google Vertex Global only, stateful, normalized coordinates;
  omit unsupported `temperature` while retaining seed and structured output.
- Slot B: `qwen/qwen3-vl-8b-instruct`, Alibaba only, stateful, normalized coordinates.
- Slot C: `meta-llama/llama-4-scout`, DeepInfra FP8 only, stateful, native coordinates.
- Slot D: `qwen/qwen3-vl-8b-instruct`, Alibaba only, stateless, normalized coordinates.
- Calibration manifest: `artifacts/grounding-v5-manifests/calibration-d56.json`.
- Task count: 50 per policy; action cap: 1,431 per policy.
- Retry rule: at most one same-route retry only when a canonical response has matching model and
  upstream identities, `finish_reason=error`, empty content, zero completion tokens, zero cost, and
  a successful price guard. The first response and attempt remain sealed in evidence. A second
  qualifying response is an infrastructure failure. No retry is issued after process interruption.
  Cancellation and reconciliation remain disabled.
- Failure rule: freeze the complete run after the first infrastructure, identity, price, parse,
  adapter, invalid-action, or evidence-integrity failure.

## Phase 1: exact four-call panel smoke

For a newly approved policy panel, first commit the implementation and verify that tracked files
are clean. Generate the no-call plan into unused paths:

```bash
python scripts/run_grounding_v5_panel_smoke.py \
  --plan-only \
  --output artifacts/grounding-v5-d56-panel-smoke-plan-v4.json
```

The plan must report four fresh development tasks, four environment actions, no more than eight
model attempts, zero control requests, no more than eight wire requests, and
`provider_calls_made: 0`. Its uncapped theoretical maximum is `$0.205324288`; prior spend plus that
maximum is `$0.576213483`, within the shared `$10` ceiling. Record its printed digest and obtain
explicit owner approval for that exact digest before continuing.

Execute only the approved plan into a fresh local restricted-evidence directory:

```bash
python scripts/run_grounding_v5_panel_smoke.py \
  --execute \
  --plan artifacts/grounding-v5-d56-panel-smoke-plan-v4.json \
  --approved-plan-sha256 'sha256:EXACT_APPROVED_DIGEST' \
  --output artifacts/grounding-v5-d56-panel-smoke-run-v4
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
overwrite either first-run artifact. The owner approved a new Slot A identity using Google Vertex
Global on 2026-08-26. The new four-policy smoke uses four previously unexposed development tasks
and still requires approval of its exact generated digest before execution.

### Consumed partial calibration

The owner approved calibration plan
`sha256:270d4b1941cac585fa51907df170463460e723639372c5f68eee5e1f888857d1`.
The run attempted eight Slot A assignments, seeds 5102 through 5109: five succeeded, two reached the
step limit, and one ended with an empty canonical response carrying `finish_reason=error`, zero
completion tokens, and zero cost. The parser correctly retained that response as invalid output
under the then-approved no-retry policy. The complete consumed run remains immutable at
`artifacts/grounding-v5-d56-calibration-run/`; do not resume it or reuse its plan.

The fresh calibration manifest excludes those eight assignments and replaces them with seeds 5160
through 5167 while preserving the 50-task family and difficulty counts and the 1,431-action cap per
policy. The new retry rule addresses only the exact zero-completion provider-error envelope. Parse
failures, invalid actions, identity mismatches, priced responses, non-empty content, and unknown
outcomes remain final and stop the run.

## Phase 2: exact D5.6 calibration plan

After the smoke evidence verifies, generate the calibration plan:

```bash
python scripts/run_grounding_v5_d56_calibration.py \
  --plan-only \
  --smoke-output artifacts/grounding-v5-d56-panel-smoke-run-v4 \
  --output artifacts/grounding-v5-d56-calibration-plan-v2.json
```

Review the exact policy IDs, policy-manifest digests, price records, 50 task IDs, derived partition
digest, smoke-evidence digests, per-policy caps, aggregate caps, prior actual spend, and shared $10
guard. The plan must report `provider_calls_made: 0`. Obtain a second explicit owner approval for
the exact printed calibration-plan digest.

## Phase 3: execute only the approved calibration

```bash
python scripts/run_grounding_v5_d56_calibration.py \
  --execute \
  --plan artifacts/grounding-v5-d56-calibration-plan-v2.json \
  --approved-plan-sha256 'sha256:EXACT_APPROVED_DIGEST' \
  --smoke-output artifacts/grounding-v5-d56-panel-smoke-run-v4 \
  --output artifacts/grounding-v5-d56-calibration-run-v2
```

Run slots sequentially in A, B, C, D order and tasks in manifest order. Continue after a scored
success or step-limit truncation so assigned tasks remain in the denominator. Stop before any
request whose theoretical maximum cannot fit under the remaining aggregate balance; retain every
completed response, invalid output, failure, exhausted budget, and unattempted assignment.

## Phase 4: B/C/D-only successor after the frozen Slot A failure

Use this phase only for the owner-selected B/C/D continuation. Do not resume the four-slot run or
retry its terminal Slot A request. First generate a new no-call plan into an unused path:

```bash
python scripts/run_grounding_v5_d56_bcd_calibration.py \
  --plan-only \
  --smoke-output artifacts/grounding-v5-d56-panel-smoke-run-v4 \
  --frozen-calibration-output artifacts/grounding-v5-d56-calibration-run-v2 \
  --output artifacts/grounding-v5-d56-bcd-calibration-plan.json
```

The planner verifies the frozen predecessor summary and journal byte digests, its request counts and
integrity report, and the terminal `unknown_outcome_infrastructure_failure` event. It must report
`provider_calls_made: 0`, policy order B/C/D, 150 assigned policy-task pairs, an aggregate action cap
of 4,293, a model-attempt and wire-request cap of 8,586, and `$7.967124815` remaining. Obtain owner
approval for the exact printed digest before executing:

```bash
python scripts/run_grounding_v5_d56_bcd_calibration.py \
  --execute \
  --plan artifacts/grounding-v5-d56-bcd-calibration-plan.json \
  --approved-plan-sha256 'sha256:EXACT_APPROVED_DIGEST' \
  --smoke-output artifacts/grounding-v5-d56-panel-smoke-run-v4 \
  --frozen-calibration-output artifacts/grounding-v5-d56-calibration-run-v2 \
  --output artifacts/grounding-v5-d56-bcd-calibration-run
```

Run the three slots sequentially in B, C, D order and preserve the frozen task order. Continue after
success and step-limit truncation. Freeze the B/C/D run after the first other failure or before a
request whose theoretical maximum does not fit under the remaining shared ledger. Do not overwrite
the predecessor or successor evidence directories.

## Handoff evidence

Report raw commands and exit statuses, actual wire requests, model reservations, spend before and
after the run, classifications, attempted and successful policy-task pairs, and the complete
journal integrity report. The human reviews that evidence and owns the D5.6 verdict.
