# Complete Slot C through a fixed Google Vertex route

Status: superseded by the owner's [Mistral Small 4 selection](slot-c-mistral-successor.md)
on 2026-09-10. Preserve this procedure and its stopped evidence as historical context.

The owner selected Llama Scout through Google Vertex on 2026-09-10 after two local DeepInfra
runs stopped on rate-limit retry exhaustion. The owner subsequently approved only the exact
smoke plan described below. The operator must preserve the predecessor runs and obtain an exact plan approval
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

## Frozen smoke outcome and next diagnostic

The approved smoke plan digest was
`sha256:534ba1ac11d8f303b531196fe9f057f2987af10f1fc4c96d17f376994f4d91c0`,
prepared at revision `2e8268d5107571bbe25d838162e96411a2f0b38f`. It stopped on its first
request with HTTP 404 before dispatching an action. The runner retained a $0.03461120
unknown-charge reservation and closed the provider adapter and journal. This is neither a
known charge nor model-quality evidence. The response body was not retained; its bounded
metadata does not establish the cause of the 404. The approval is consumed.

The frozen run is local at `artifacts/grounding-v5-slot-c-vertex-smoke-run/`, alongside its
plan, execution record, and separate audit. Its journal integrity and spend amounts agree
with the summary, but full summary validation fails: the historical ledger did not journal
its explicit block, so replay reports `blocked: false` while the summary has `blocked: true`.
The transport now journals explicit blocks for future runs. Do not patch the frozen journal
or summary, infer a historical block during replay, or resume this stopped run.

Account-scoped read-only catalog checks still list Scout and the fixed Vertex route. They do
not establish why the request failed. A distinct diagnostic policy,
`C-llama-stateful-vertex-v1-routing-diagnostic`, preserves the smoke request and enables
[OpenRouter routing metadata](https://openrouter.ai/docs/guides/features/router-metadata).
Only the existing bounded metadata allowlist is retained; provider message content is excluded.
Routing metadata may distinguish a pre-provider routing failure from an upstream attempt, but
it is not guaranteed to appear or to explain every filter.

Prepare a fresh, separately approved one-call diagnostic:

```bash
.venv/bin/python scripts/prepare_grounding_v5_slot_c.py \
  --phase diagnostic --maximum-spend-usd 0.05 \
  --run-output artifacts/grounding-v5-slot-c-vertex-routing-diagnostic-run \
  --output artifacts/grounding-v5-slot-c-vertex-routing-diagnostic-plan.json
.venv/bin/python scripts/run_grounding_v5_calibration.py --validate-only \
  --plan artifacts/grounding-v5-slot-c-vertex-routing-diagnostic-plan.json
```

This allocates the first smoke development task, one action, one model/wire request, zero
control requests, no retries, and a fresh $0.05 ceiling including unknown-charge reservations.
It does not authorize another smoke or calibration. Audit the result before proposing any
next phase. Every subsequent paid phase requires its own exact approval.

## Full calibration remains blocked

The planner rejects calibration and confirmatory phases while there is no reviewed successful
smoke. Reintroducing calibration planning requires validated smoke review evidence tied to the
smoke plan and journal, followed by separate exact execution approval. The intended full
allocation remains fifty unchanged D5.6 calibration tasks, 1,431 environment actions, at most
5,724 model/wire requests with bounded retries, zero control requests, and a proposed fresh $5
ceiling. This is a design target, not a runnable approved plan.

Completion requires a journal-verified full denominator and the item/family diagnostics required
by the [v5 benchmark plan](grounding-v5-agent-benchmark.md#paid-calibration-panel). Never fill
predecessor gaps, resume a stopped run, or pool its outcomes with a successor. Slot C and the
full panel remain incomplete; no confirmatory calls or human gate verdict are authorized here.
