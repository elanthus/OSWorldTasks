# Completed D5.6 V5 Calibration Results

**Status:** descriptive completed calibration evidence; not a benchmark score or milestone-gate verdict

This report publishes every retained D5.6 full-calibration run that completed its assigned denominator and passed the committed-evidence checks. It was generated without provider calls from the response-content-free derivative.

## Calibration table

| Policy slot | Assigned | Attempted | Success | Invalid output | Request failure | Infrastructure failure | Policy violation | Truncation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `A-gemini-stateful-v3` | 50 | 50 | 35 (70.0%) | 1 | 1 | 0 | 0 | 13 |
| `B-qwen-stateful-v3` | 50 | 50 | 0 (0.0%) | 3 | 0 | 0 | 0 | 47 |

Null and negative results are shown unchanged. Attempted is kept separate from every terminal classification.

## Policy identity, dates, spend, and stopping

### `A-gemini-stateful-v3`

- Provider/model alias: `openrouter/google-vertex/global` / `google/gemini-3.7-flash`
- Execution date: not recorded in committed plan or summary
- Endpoint record observed: `2026-08-28T00:32:11Z`; source evidence committed: `2026-09-02T22:49:08-07:00`
- Prompt digest: `sha256:e749173bb13d9c54487fcd3e353effe073cc1308c4c82d27169b9ea7405209be`
- Memory/parser/response policy: `pixelgym-agent-v5-visible-action-history-v1` / `pixelgym-agent-v5-json-action-normalized-1000x1000-parser-v1` / `pixelgym-agent-v5-canonical-response-v2`
- Coordinate/retry policy: `normalized-1000x1000` / `bounded-same-route-zero-completion-http-429-or-transient-transport-fault-after-bounded-backoff-v3`
- Known run spend: `$4.911249750`; unknown-charge reservation: `$1.990656000` across 20 outcomes; budget-accounted run spend: `$6.901905750`
- Observed stop: `completed_assigned_denominator`; stop guard tripped: `False`; trip reason: `None`
- Approved hard stops: `run spend ledger blocked`, `policy or request identity mismatch`, `a charge above the per-request theoretical maximum`, `non-retryable HTTP status`, `evidence-integrity failure`

### `B-qwen-stateful-v3`

- Provider/model alias: `openrouter/alibaba` / `qwen/qwen3-vl-8b-instruct`
- Execution date: not recorded in committed plan or summary
- Endpoint record observed: `2026-08-28T13:34:08Z`; source evidence committed: `2026-09-02T22:49:08-07:00`
- Prompt digest: `sha256:e749173bb13d9c54487fcd3e353effe073cc1308c4c82d27169b9ea7405209be`
- Memory/parser/response policy: `pixelgym-agent-v5-visible-action-history-v1` / `pixelgym-agent-v5-json-action-normalized-1000x1000-parser-v1` / `pixelgym-agent-v5-canonical-response-v2`
- Coordinate/retry policy: `normalized-1000x1000` / `bounded-same-route-zero-completion-http-429-or-transient-transport-fault-after-bounded-backoff-v3`
- Known run spend: `$0.498141709`; unknown-charge reservation: `$0.000000000` across 0 outcomes; budget-accounted run spend: `$0.498141709`
- Observed stop: `completed_assigned_denominator`; stop guard tripped: `False`; trip reason: `None`
- Approved hard stops: `run spend ledger blocked`, `policy or request identity mismatch`, `missing, invalid, or exceeded price guard`, `non-retryable HTTP status`, `evidence-integrity failure`

## Retained runs not published

| Run | Policy slot | Attempted | Failed publication checks |
|---|---|---:|---|
| `qwen-v2` | `B-qwen-stateful-v2` | 13 | `completed_assigned_denominator` |
| `gemini-v3` | `A-gemini-stateful-v3` | 5 | `completed_assigned_denominator`, `publication_relation_verified` |

Gemini v3 stopped after 5 of 50 assignments when its run ledger blocked. The older Qwen v2 run stopped after 13 of 50 assignments under its approved first-invalid-output rule. Neither incomplete run is included in the calibration table.

## Fault-taxonomy disclosure

PR #155 changed classification of CLI process failures. These four retained runs used HTTP/OpenRouter transports, not CLI transports, so the number of `invalid_output` rows that can be former-taxonomy CLI process failures is zero for every run. Stored labels were not rewritten or reinterpreted.

## Evidence and redaction

- Report schema: `pixelgym-agent-v5-d56-completed-calibrations-report-v1`
- Publishable derivative: `sha256:ca8d76d84b9ef92da64a6f2877354e0fd6cc85ffe30537b009d2159fe653d666`
- [Publishable derivative](grounding-v5-d56-completed-calibrations-publishable.json)
- [Integrity audit](grounding-v5-d56-completed-calibrations-integrity-audit.json)
- [Publication relation](grounding-v5-d56-completed-calibrations-publication-relation.json)
- [Evidence errata](grounding-v5-d56-gemini-qwen-v3-calibration-evidence-errata.json)
- [V5 protocol](../plans/grounding-v5-agent-benchmark.md)

The committed summaries expose aggregate transport rows and journal event-chain digests, but not restricted journals or their file hashes; row-level journal verification is unavailable from a public clone.

The derivative and report contain no response bodies, request bodies, screenshots, checkpoint contents, credentials, or absolute operator paths. Restricted journals remain untracked and ignored.

## Limitations

- Calibration is development evidence, not a benchmark score or milestone-gate verdict.
- Gemini v3b reserves USD 1.990656000 for 20 outcomes whose charges are unknown.
- Qwen v3 completed 50 assignments with zero exact-success terminations.
- The two completed slots use different model/provider routes and are descriptive, not a controlled model comparison.
- Execution dates and restricted-journal file hashes were not retained in committed evidence.
- The historical classification labels are preserved without reinterpretation.
