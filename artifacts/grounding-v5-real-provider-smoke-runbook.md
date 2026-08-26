# PixelGym v5 real-provider smoke-test runbook

**Status:** proposed procedure; no provider call or spend is authorized

**Use this when:** a candidate v5 provider adapter and policy manifest are implemented, the owner
wants a few real-provider checks before D5.6 calibration approval, and no calibration or
confirmatory task has been exposed

## Outcome

Prove that each selected provider can accept the frozen screenshot request, return a response that
the canonical capture and parser can process, preserve durable attempt lineage, enforce the
coordinate convention, and settle within the frozen deadline. Smoke results diagnose integration;
they are not calibration results, model-quality evidence, or a basis for changing an individual
task.

## Safety and authority

- Use development tasks only. Never use calibration or confirmatory seeds in a smoke test.
- Obtain explicit approval for the exact policy IDs, provider/model identities, assigned
  development seeds, model-attempt cap, control-request cap, total wire-request cap, and maximum
  spend before the first request.
- Keep provider SDK and proxy automatic retries disabled. The journaled runner is the only retry
  authority.
- A response-producing call is final. An unknown post-send outcome is not retried.
- Stop when any approved cap is reached. Do not substitute another task, model, prompt, adapter, or
  endpoint under the same approval.
- Load credentials only from ignored environment variables at transport time. Do not print them,
  embed them in URLs or manifests, or pass them to the task application, backend, or policy state.

## Proposed smoke allocation

Use two bounded stages per policy. The approval may authorize stage 1 alone.

### Stage 1: transport and parser smoke

- One fixed development task from the policy's assigned workflow family.
- At most two response-producing model attempts: the initial screenshot and, only if the first
  action validates and dispatches, the next screenshot.
- At most the manifest's declared cancellation and reconciliation requests for those two attempts.
- Stop on transport failure, unknown outcome, canonical capture failure, parser failure, invalid
  action, sandbox denial, missing usage, missing price, or evidence-integrity failure. The sole
  exception is the frozen retry rule for a pre-send failure proven to have produced no response;
  that retry must remain within every approved cap.

Stage 1 does not claim episode success. Its purpose is to exercise the provider boundary and one
state transition without exposing calibration items.

### Stage 2: one development episode

- Run only after every stage-1 record verifies and the owner explicitly approves the larger
  per-episode cap.
- Use one predeclared development episode per policy. Prefer different development tasks across
  policies while preserving family coverage across the panel.
- Cap environment actions at that task's `max_episode_steps` and derive model/control/wire caps
  from the final policy manifest.
- Retain the episode unchanged whether it succeeds, fails, truncates, or encounters an
  infrastructure error.

Stage 2 validates end-to-end wiring. It remains development evidence and never enters calibration
or confirmatory metrics.

## Prerequisites

Before requesting smoke approval, produce:

1. A clean source revision and exact dependency-lock digest.
2. One valid `PolicyManifest` per policy and a credential-free provider origin.
3. A content-addressed policy sandbox runtime and passing OS-level isolation evidence.
4. Passing fake-transport interruption and recovery tests for the same parser, state reducer, and
   retry configuration.
5. Passing native and normalized-coordinate adapter boundary tests as applicable.
6. A provider price record with source URL, effective timestamp, and fail-closed unknown-price
   handling.
7. A plan-only smoke record that names development seeds and reports exact action, attempt,
   control-request, wire-request, and maximum-cost caps with `provider_calls_made: 0`.

The repository implements a one-call, development-only transport/parser/action diagnostic in
`scripts/run_grounding_v5_provider_smoke.py`. It has separate `--plan-only` and execution modes,
does not initialize a credential-bearing transport or launch the task in plan mode, binds execution
to the exact approved plan digest, requires a clean matching source revision, and refuses to replace
an existing output. This diagnostic is **pre-stage-1 evidence**: it does not substitute for a final
stateful policy package, its content-addressed OS sandbox, interruption/recovery evidence, or a
complete `PolicyManifest`.

The current diagnostic freezes `max_tokens=4096` for
`qwen/qwen3-vl-8b-instruct`. The earlier 128-token limit was intentionally scoped to one terse JSON
action, but it proved too small when a reasoning model consumed the allowance without emitting the
action. The larger bound leaves room for provider-internal reasoning while remaining far below the
[Alibaba endpoint's published 32,768-token completion
limit](https://openrouter.ai/api/v1/models/qwen/qwen3-vl-8b-instruct/endpoints). At the frozen
endpoint prices and maximum prompt size, the one-request theoretical maximum is $0.016959488, below
the approved $5 aggregate cap.

Freeze a proposed one-call plan without network access:

```bash
python scripts/run_grounding_v5_provider_smoke.py \
  --plan-only \
  --maximum-spend-usd 5.00 \
  --output artifacts/grounding-v5-openrouter-smoke-plan-qwen3-vl-8b-instruct.json
```

After the owner explicitly approves the printed digest and all identities in that plan, execute it
once into a fresh result path:

```bash
python scripts/run_grounding_v5_provider_smoke.py \
  --execute \
  --plan artifacts/grounding-v5-openrouter-smoke-plan-qwen3-vl-8b-instruct.json \
  --approved-plan-sha256 sha256:<approved-plan-digest> \
  --output artifacts/grounding-v5-openrouter-smoke-result-qwen3-vl-8b-instruct.json
```

The authoritative local result retains the raw provider response, including invalid or unparseable
output, under a `publication_status: restricted` boundary. Do not publish that file. The CLI stdout
and any publishable derivative omit the restricted response while retaining its digest, parsed
action, usage, attributed cost, latency, dispatch diagnostic, and provider identity. A successful
diagnostic authorizes neither runbook stage 1 nor D5.6 calibration.

## Procedure

1. Verify the worktree and runtime identities match every policy manifest.
2. Generate and review the no-call smoke plan. Confirm that all assigned seeds are development
   seeds and that every count is within the signed approval.
3. Start the policy sandbox and run its no-cost endpoint/egress probe against the exact runtime.
4. Run credential-boundary validation before task reset or screenshot creation. If it passes,
   reset the assigned task, create the screenshot, and seal the canonical request bytes without a
   credential. Only then inject the credential directly into the transport at the send boundary,
   without echoing it. Fail closed and do not inject the credential if either gate is incomplete.
5. Run stage 1 for one policy at a time. Do not run policies concurrently during the initial
   provider integration check.
6. Seal and verify the attempt journal, canonical response, parser result, action validation,
   dispatch record, usage, price, latency, and redacted derivative before starting another policy.
7. If stage 1 verifies for every approved policy, stop and request the separate stage-2 approval.
8. Run at most one assigned development episode per approved policy, then seal and verify the
   complete episode evidence.
9. Remove credentials from the process environment and close the policy sandbox and backend even
   on failure.

## Verification

The smoke packet is complete only when its integrity report verifies every referenced byte and the
summary includes, per policy:

- exact policy, provider, model, runtime, source, prompt, parser, adapter, and price identities;
- assigned development seed and task ID;
- environment actions, model attempts, provider control requests, and total wire requests;
- canonical response, parsed candidate, action intent, and dispatch lineage when each was
  produced; otherwise its explicit absence, the failure phase, and the failure code;
- usage, attributed cost, and request latency without estimates when produced; otherwise their
  explicit absence, the failure phase, and the failure code;
- terminal classification and every failure code; and
- provider and backend cleanup status, including cleanup evidence after every failure.

## Failure routing

| Observation | Required action |
|---|---|
| Pre-send failure proven to have produced no response | Apply only the frozen retry rule and remain inside every approved cap |
| Unknown outcome, including any outcome not proven pre-send/no-response | Seal infrastructure failure; do not retry |
| Parse or action-validation failure | Retain the response and failure; audit without another call |
| Coordinate mismatch | Stop that policy; create and test a new adapter identity before requesting new approval |
| Missing usage or price | Fail evidence validation; do not estimate cost |
| Sandbox or secret-boundary failure | Stop all real-provider work until the no-cost boundary is repaired and revalidated |
| Provider behavior requires a prompt, schema, parameter, or endpoint change | Create a new policy manifest and request new smoke approval |

Smoke success does not authorize D5.6 calibration. The owner must separately approve the complete
[D5.6 calibration package](grounding-v5-d56-calibration-approval.md).
