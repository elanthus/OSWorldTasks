# PixelGym v5 Qwen3-VL calibration-pilot runbook

**Status:** implemented; execution requires approval of the exact generated plan digest

**Primary reader:** the project owner or operator running the bounded Qwen3-VL pilot from a clean,
reviewed source revision

## Outcome

Run `qwen/qwen3-vl-8b-instruct` through OpenRouter's Alibaba route on ten frozen calibration tasks,
with at most two actions per task and twenty provider requests in total. The pilot exercises the
stateful v5 policy, durable attempt journal, normalized-coordinate adapter, parser, dispatch path,
and cost accounting. It is diagnostic evidence, not the complete D5.6 four-policy calibration.

## Safety and authority

- Generate the plan without a provider credential or network request.
- Approve the exact canonical plan digest before execution. A model, route, prompt, parser,
  adapter, source revision, price, task allocation, cap, or smoke-evidence change produces a
  different plan and requires new approval.
- Route only to Alibaba. Provider fallbacks, provider-side data collection, SDK retries, proxy
  retries, cancellation, and reconciliation are disabled.
- Never retry or replace a response-producing, failed, incomplete, or unknown request.
- Stop after the first transport, identity, cost, parse, action, or evidence failure.
- Do not expose confirmatory tasks.
- Load `OPENROUTER_API_KEY` only from the ignored process environment. The request journal and plan
  remain credential-free.
- Keep the authoritative output local. Canonical responses, including invalid output, are stored
  in the SQLite attempt journal.

## Frozen bounds

The plan selects one task from each of the six workflow families, then a second task from the first
four families, preserving calibration-manifest order within each family. Each task receives at
most two actions.

| Bound | Value |
|---|---:|
| Calibration tasks | 10 |
| Actions per task | 2 |
| Model-attempt reservations | 20 |
| Provider control requests | 0 |
| Provider wire requests | 20 |
| Maximum output tokens per request | 4,096 |
| Approved aggregate spend cap | $5.00 |
| Prior diagnostic spend included in the cap | $0.000281307 |
| Conservative per-request maximum | $0.016959488 |
| Conservative aggregate upper bound | $0.339471067 |

The per-request maximum prices the published maximum prompt and the frozen 4,096-token output
allowance together. This is deliberately conservative. The transport checks the aggregate cap
before every send and checks the returned cost before any parsed action can be dispatched. An
unknown or anomalous cost is retained as evidence and stops the pilot.

## Prerequisites

1. PR #90 contains the reviewed pilot source and all required checks are green.
2. The checked-out revision matches the plan's `code_revision`, with no tracked changes.
3. The frozen calibration manifest verifies its embedded digest.
4. The corrected development smoke plan and restricted result exist at the paths named in the
   plan and match their recorded file digests.
5. `OPENROUTER_API_KEY` is available in the process environment only when executing the approved
   plan.
6. The requested output directory does not exist. The runner refuses to overwrite evidence.

## Generate the no-call plan

Run this command from the repository root:

```bash
python scripts/run_grounding_v5_calibration_pilot.py \
  --plan-only \
  --output artifacts/grounding-v5-qwen3-vl-8b-calibration-pilot-plan.json
```

Expected result: the command writes a JSON plan and prints its `plan_sha256`. It does not create a
transport, read the provider credential, or issue a provider request.

Inspect the plan, confirm every identity and cap, then record the exact printed digest. Do not run
execution mode until the owner approves that digest.

## Execute the approved plan

Choose a fresh local output directory and replace the placeholder with the exact approved digest:

```bash
python scripts/run_grounding_v5_calibration_pilot.py \
  --execute \
  --plan artifacts/grounding-v5-qwen3-vl-8b-calibration-pilot-plan.json \
  --approved-plan-sha256 'sha256:EXACT_PRINTED_DIGEST' \
  --output artifacts/grounding-v5-qwen3-vl-8b-calibration-pilot-run
```

Replace the entire quoted value with the printed `plan_sha256`; do not add another `sha256:`
prefix.

Expected result: the runner creates `attempts.sqlite` before the first reservation, executes no
more than two actions per task, and writes `summary.json` after closing the journal. A normal
two-action diagnostic ends each non-terminal task with `pilot_action_limit`; any other
classification stops the remaining tasks.

## Verify and retain evidence

Check the summary and journal before drawing any model-quality conclusion:

1. `approved_plan_sha256`, model, and upstream provider match the approved plan, which binds the
   policy manifest and code revision.
2. `model_attempt_reservations` and `provider_wire_requests` do not exceed twenty; provider control
   requests remain zero.
3. Actual aggregate spend does not exceed $5.00, and incremental spend reconciles with the
   per-request transport records.
4. The journal integrity report verifies all recorded events and objects.
5. Every attempted task remains in the result, including invalid output and infrastructure
   failure.
6. The output contains no confirmatory task identity and no credential.

Retain the plan, summary, and SQLite journal together. Do not publish the raw journal. Produce a
separate redacted derivative before sharing results outside the project.

## Failure routing

| Observation | Required action |
|---|---|
| Plan or runtime identity mismatch | Stop before sending; regenerate a plan and request new approval |
| Pre-send spend guard | Stop; inspect accumulated spend and price inputs without retrying |
| Unknown transport outcome | Retain the journal and stop; do not retry or replace the task |
| Wrong model or upstream provider | Retain the response and stop before dispatch |
| Missing, invalid, or anomalous cost | Retain the response and stop before dispatch |
| Invalid JSON, coordinate, key, or action | Retain the response and stop before dispatch |
| Journal or evidence-integrity failure | Stop all paid work and perform a no-call audit |

After the pilot, report raw counts, costs, classifications, and integrity output. Do not declare
D4.12 or D5.6 passed; those verdicts remain human-owned.

After the owner reviews this pilot and explicitly approves full episodes on the same ten tasks, use
the [expanded calibration runbook](grounding-v5-qwen3-vl-expanded-calibration-runbook.md). That
larger single-policy run still does not constitute the four-policy D5.6 calibration.
