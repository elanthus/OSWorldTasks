# Complete Slot C through a fixed Google Vertex route

The owner selected Llama Scout through Google Vertex on 2026-09-10 after two local DeepInfra
runs stopped on rate-limit retry exhaustion. This approves preparation of a successor, not paid
execution. The operator must preserve the predecessor runs and obtain an exact plan approval
before each new phase. Slot C and the full calibration panel remain incomplete.

## Frozen successor

`C-llama-stateful-vertex-v1` uses `meta-llama/llama-4-scout` through OpenRouter's fixed
`google-vertex/us-east5` route with fallbacks disabled. It preserves the DeepInfra v2 successor's
prompts, visible-action history, normalized coordinate adapter, exact-action parser, strict JSON
schema, temperature, 180-second deadline, and four-attempt limit. Its three shared retries use
15/30/60-second fallback waits; provider retry hints retain the existing bounded precedence.

The [OpenRouter endpoint catalog](https://openrouter.ai/api/v1/models/meta-llama/llama-4-scout/endpoints)
checked on 2026-09-10 lists structured outputs and response format support for this route, with
prompt/completion prices of $0.25/$0.70 per million tokens. Its quantization is unspecified, so the
successor does not require or claim FP8. The route and prices change the policy identity. The
model remains an alias, and this is not a controlled comparison with the historical provider.
Catalog availability does not establish account access, response identity, or route reliability;
the approved smoke checks those observed properties without predicting full-run completion.

## Prepare and review the smoke

Use a clean checkout with the implementation committed. Recheck the public route catalog and
prices before requesting execution approval. The planner neither accesses credentials nor makes
provider calls, and refuses an existing output file or run directory.

```bash
.venv/bin/python scripts/prepare_grounding_v5_slot_c.py \
  --phase smoke --maximum-spend-usd 1.00 \
  --run-output artifacts/grounding-v5-slot-c-vertex-smoke-run \
  --output artifacts/grounding-v5-slot-c-vertex-smoke-plan.json
.venv/bin/python scripts/run_grounding_v5_calibration.py --validate-only \
  --plan artifacts/grounding-v5-slot-c-vertex-smoke-plan.json
```

The smoke allocates ten development tasks across all six families, with at most two actions per
task and one attempt per action: twenty model/wire requests, zero control requests, and a fresh
$1 ceiling. It uses the distinct `C-llama-stateful-vertex-v1-smoke` identity with no retries.
It tests bounded route compatibility and action-history carryover, not episode success accuracy.
The spend cap includes unknown-charge reservations and may stop execution before the call cap.

After the owner approves the exact printed plan digest, use the existing runner's `--execute`
mode with that plan and `--approved-plan-sha256`. Never substitute a predecessor's approval.
Stop on infrastructure failure, request failure, or policy violation. Retain invalid outputs
without retry and stop after three consecutive failures. Audit the stored summary against its
restricted journal; check model/provider identity, parse results, spend, and the second request's
action history. A `pilot_action_limit` outcome is expected and is not a success claim.

## Prepare full calibration after smoke review

Only after review of the smoke, prepare a fresh plan:

```bash
.venv/bin/python scripts/prepare_grounding_v5_slot_c.py \
  --phase calibration --maximum-spend-usd 5.00 \
  --run-output artifacts/grounding-v5-slot-c-vertex-calibration-run \
  --output artifacts/grounding-v5-slot-c-vertex-calibration-plan.json
```

This proposes fifty fresh assignments from the unchanged D5.6 calibration manifest, 1,431
environment actions, at most 5,724 model/wire requests, zero control requests, and a fresh $5
ceiling. The dollar ceiling is independent of the call cap and does not guarantee all assignments
finish. Obtain a second exact approval before execution. Never fill predecessor gaps, resume a
stopped run, or pool its outcomes with the successor. Preserve every failure and unattempted task.

Completion requires a journal-verified full denominator and the item/family diagnostics required
by the [v5 benchmark plan](grounding-v5-agent-benchmark.md#paid-calibration-panel). This route
successor alone does not complete the panel, authorize confirmatory calls, or declare a human gate.
