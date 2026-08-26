# PixelGym v5 Qwen3-VL expanded calibration runbook

**Status:** implemented; execution requires approval of the exact generated plan digest

**Primary reader:** the project owner or operator running full episodes on the ten tasks from the
completed Qwen3-VL calibration pilot

## Outcome

Rerun the same ten frozen calibration tasks from reset through their complete action horizons using
`qwen/qwen3-vl-8b-instruct` through OpenRouter's Alibaba route. The run records full-episode success
or step-limit truncation for this single policy. It is larger than the two-action pilot, but it is
not the complete four-policy D5.6 calibration.

## Safety and authority

- Generate the plan without a provider credential or network request.
- Approve the exact canonical plan digest before execution. A source, evidence, model, route,
  prompt, parser, adapter, task, horizon, price, or cap change requires a new plan and approval.
- Route only to Alibaba. Keep provider fallbacks, provider-side data collection, SDK retries, proxy
  retries, cancellation, and reconciliation disabled.
- Never retry or replace a response-producing, failed, incomplete, or unknown request.
- Continue after ordinary scored success or step-limit truncation so every assigned task remains in
  the denominator. Stop on transport, identity, price, parse, invalid-action, or evidence-integrity
  failure.
- Do not expose confirmatory tasks.
- Load `OPENROUTER_API_KEY` only from the ignored process environment during execution.
- Keep the raw SQLite journal local; it contains canonical provider responses.

## Frozen bounds

The run restarts the same ten tasks used by the completed pilot. Nine tasks have 26-action horizons
and one has a 27-action horizon.

| Bound | Value |
|---|---:|
| Assigned full episodes | 10 |
| Maximum environment actions | 261 |
| Maximum model attempts | 261 |
| Provider control requests | 0 |
| Maximum provider wire requests | 261 |
| Maximum output tokens per request | 4,096 |
| Existing aggregate spend cap | $5.00 |
| Spend before this run | $0.004228237 |
| Conservative run maximum | $4.426426368 |
| Conservative aggregate upper bound | $4.430654605 |
| Conservative remaining headroom | $0.569345395 |

The transport reserves the conservative per-request maximum before every send. It also checks each
returned cost before the parsed action can be dispatched. The request cap is an upper bound; an
episode that terminates successfully can use fewer calls.

## Prerequisites

1. PR #90 contains the reviewed expanded-run source and all required checks are green.
2. The checked-out revision matches the plan's `code_revision`, with no tracked changes.
3. The completed pilot plan, summary, and restricted journal exist at the paths named in the plan
   and match their recorded digests.
4. The completed pilot summary records the approved pilot digest, twenty wire requests, and
   $0.004228237 aggregate spend.
5. `OPENROUTER_API_KEY` is present only in the process environment during execution.
6. The requested plan and output paths do not exist; the command refuses to overwrite evidence.

## Generate the no-call plan

Run from the repository root:

```bash
python scripts/run_grounding_v5_expanded_calibration.py \
  --plan-only \
  --output artifacts/grounding-v5-qwen3-vl-8b-expanded-calibration-plan.json
```

Expected result: the command writes the plan and prints its `plan_sha256`. It hashes the completed
pilot evidence but does not construct a provider transport, load a credential, or issue a request.

Inspect every identity, task, horizon, cap, and evidence digest. Execution remains blocked until
the owner approves the exact printed `plan_sha256`.

## Execute the approved plan

Replace the entire quoted digest with the exact printed `plan_sha256`:

```bash
python scripts/run_grounding_v5_expanded_calibration.py \
  --execute \
  --plan artifacts/grounding-v5-qwen3-vl-8b-expanded-calibration-plan.json \
  --approved-plan-sha256 'sha256:EXACT_PRINTED_DIGEST' \
  --output artifacts/grounding-v5-qwen3-vl-8b-expanded-calibration-run
```

Expected result: the runner creates `attempts.sqlite` before the first reservation, runs each task
until success or its frozen step limit, and writes `summary.json` after closing the journal. It
stops the remaining assignments if a non-scoring infrastructure or evidence failure occurs.

## Verify and retain evidence

1. Confirm the approved digest, code revision, policy manifest, task IDs, and horizons match the
   plan.
2. Confirm model-attempt reservations and wire requests do not exceed 261; control requests and
   retries remain zero.
3. Confirm every provider response is attributed to Alibaba and every returned cost passed the
   price guard before dispatch.
4. Reconcile incremental and aggregate spend against the transport records and the $5 cap.
5. Recompute the journal integrity report and compare it with the summary.
6. Retain every assigned success, truncation, invalid output, and request failure in its applicable
   denominator.
7. Keep the plan, summary, and SQLite journal together. Create a redacted derivative before sharing
   results outside the project.

## Failure routing

| Observation | Required action |
|---|---|
| Plan, source, task, or evidence mismatch | Stop before sending; regenerate and request new approval |
| Pre-send spend guard | Stop; inspect accumulated spend and price inputs without retrying |
| Unknown transport outcome | Retain the journal and stop; do not retry or replace the task |
| Wrong model or upstream provider | Retain the response and stop before dispatch |
| Missing, invalid, or anomalous cost | Retain the response and stop before dispatch |
| Invalid JSON, coordinate, key, or action | Retain the response and stop before dispatch |
| Scored success or step-limit truncation | Record the outcome and continue to the next assigned task |
| Journal or evidence-integrity failure | Stop all paid work and perform a no-call audit |

After the run, report raw assignments, successes, truncations, failures, calls, costs, latencies,
and integrity output. Do not declare D4.12 or D5.6 passed; those verdicts remain human-owned.
