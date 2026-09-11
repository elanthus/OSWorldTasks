# Prepare Slot C with Mistral Small 4

The owner selected Mistral Small 4 on 2026-09-10 after the Llama Scout DeepInfra runs stopped
on rate limits and both Vertex probes returned HTTP 404. This replaces the planned Slot C
model; it does not complete Slot C or approve paid execution. Keep all predecessor evidence
separate. The [Vertex procedure](slot-c-vertex-successor.md) records its historical configuration.
The separately approved Mistral smoke is now complete and technically audited. The owner requested
full calibration; the full run still needs approval of its exact plan and spend ceiling.

## Frozen smoke configuration

`C-mistral-small-4-stateful-v1-smoke` requests `mistralai/mistral-small-2603` through
OpenRouter's `mistral` provider with fallbacks disabled, data collection denied, and required
parameter support. Response parsing requires that model ID and the `Mistral` provider label.
The [public endpoint catalog](https://openrouter.ai/api/v1/models/mistralai/mistral-small-2603/endpoints)
checked on 2026-09-10 lists seed, structured outputs, response format, and reasoning support,
at $0.15/$0.60 per million prompt/completion tokens. Quantization is unspecified.
The request also caps provider prices at those values using `provider.max_price`; more
expensive endpoints are ineligible even if they share the Mistral provider slug.
Catalog availability does not establish account access or successful inference.

The smoke preserves the Scout smoke's screenshot-only observation, visible-action history,
normalized 0..999 coordinate adapter, strict action schema, temperature 0, seed 20260809,
4096-output-token limit, and 180-second deadline. It explicitly requests
[`reasoning.effort: none`](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
for Mistral's instruct mode. This setting is bound into the policy manifest. Historical
policies continue to omit the reasoning parameter. Routing metadata is enabled from the first
request and retained only through the existing bounded allowlist.

The model, reasoning setting, route, and runtime differ from the historical Scout runs; this
is not a matched comparison or a claim of equivalent GUI quality. The provider ID is an alias,
not an immutable model snapshot.

## Prepare the exact plan

Commit the implementation and use a clean tracked checkout. The following commands need no
credentials and make no model calls. Both output paths must be fresh.

```bash
.venv/bin/python scripts/prepare_grounding_v5_slot_c.py \
  --candidate mistral --phase smoke --maximum-spend-usd 0.45 \
  --run-output artifacts/grounding-v5-slot-c-mistral-smoke-run \
  --output artifacts/grounding-v5-slot-c-mistral-smoke-plan.json
.venv/bin/python scripts/run_grounding_v5_calibration.py --validate-only \
  --plan artifacts/grounding-v5-slot-c-mistral-smoke-plan.json
```

The smoke allocates ten development tasks across six families, at most two actions per task,
one model attempt per action, twenty wire requests total, zero control requests, and no retries.
The maximum request reservation is $0.02150400; twenty reserve at most $0.43008000.
The proposed $0.45 hard ceiling includes unknown-charge reservations and is new incremental
spend. Recheck the catalog before approval. The CLI defaults to the historical Vertex candidate;
always pass `--candidate mistral` for this replacement.

## Execute only after exact approval

Obtain approval of the generated plan digest before using the runner's `--execute` mode with
that plan and `--approved-plan-sha256`. The model-selection decision and consumed Vertex
approvals do not authorize this smoke. Stop on infrastructure failure, request failure, or
policy violation. Retain invalid outputs without retry; three consecutive failures stop the run.
Never resume or overwrite a stopped run. Close the provider adapter and journal on every exit.

Audit the summary against the journal, including spend replay, response model/provider identity,
parsing, and action-history carryover. Two actions per task test compatibility, not episode
success accuracy. Any subsequent paid run requires a fresh exact plan and approval.

## Reviewed smoke and full calibration

The [versioned technical review](slot-c-mistral-smoke-review.json) binds the completed smoke's
plan, summary, and restricted journal hashes. Its full summary/journal audit validated twenty
responses and reconstructed request digests, with history carryover in all ten episodes.
Known spend was $0.00548670 with zero unknown reservations. Every outcome was `pilot_action_limit`;
these results establish compatibility only. The source evidence remains local and unchanged.

Calibration preparation verifies all three source-file hashes against that receipt, checks complete
smoke outcomes, and compares the declared policy contract with the smoke's manifest. A missing or
changed source, incomplete review, different model/route/settings, or failed smoke prevents planning.
The receipt represents the previously performed technical audit; checking its hashes does not rerun
inference or treat an arbitrary success flag as a new review. The new code revision and runtime
digest account for calibration registration and preparation changes; other policy fields retain
the tested contract. The receipt digest is included in the exact calibration plan's stop conditions.

`C-mistral-small-4-stateful-v1-calibration` keeps the smoke's request configuration and no-retry
policy. It allocates all fifty tasks in the unchanged `calibration-d56.json` order, at each task's
full action limit: at most 1,431 actions and model/wire calls, with zero control requests.
The proposed fresh $2.00 hard cap includes all unknown-charge reservations. It may stop the run
before all tasks finish; allocating calls is not a guarantee of completing the denominator.

From a clean committed checkout with the reviewed local source files intact:

```bash
.venv/bin/python scripts/prepare_grounding_v5_slot_c.py \
  --candidate mistral --phase calibration --maximum-spend-usd 2.00 \
  --run-output artifacts/grounding-v5-slot-c-mistral-calibration-run \
  --output artifacts/grounding-v5-slot-c-mistral-calibration-plan.json
.venv/bin/python scripts/run_grounding_v5_calibration.py --validate-only \
  --plan artifacts/grounding-v5-slot-c-mistral-calibration-plan.json
```

Obtain the owner's exact plan-digest approval before execution. The consumed smoke approval is
not reusable. Preserve failures and invalid outputs, stop under the plan's hard breakers, close
the adapter and journal, and audit stored evidence before reporting the full denominator and
item/family diagnostics. Vertex calibration and all confirmatory planning remain unavailable.
