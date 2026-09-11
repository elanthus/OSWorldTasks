# Completed calibration supplement

Generated from committed response-free snapshots. Calibration evidence only; no confirmatory score or human-gate verdict.

| Policy | Tasks | Success | Action-limit failures |
|---|---:|---:|---:|
| `C-mistral-small-4-stateful-v3-calibration` | 50 | 0 | 50 |
| `qwen-controlled-stateful-v2` | 50 | 0 | 50 |
| `qwen-controlled-stateless-v2` | 50 | 0 | 50 |

All 3 published policies attempted the same fifty task IDs. The Qwen arms are a matched memory comparison. Both Qwen arms scored zero, so this comparison does not establish a memory benefit. Mistral is a separate model/provider/runtime run and is not a controlled comparison with Qwen or historical Gemini.

## Spend and reliability

| Run | Calls | Unknown outcomes | Known USD | Reserved USD | Accounted USD |
|---|---:|---:|---:|---:|---:|
| mistral.json | 1446 | 15 | 0.19692492 | 0.03404025 | 0.23096517 |
| qwen-pair.json | 2903 | 40 | 0.798249790 | 0.066974934 | 0.865224724 |

Spend for Qwen covers its published arms; it is not a per-arm estimate. Unknown outcomes above are counts reported by the source summaries. Transport rows are excluded, so this public verifier does not establish which failures recovered or exhausted retries. Older stopped runs are not pooled into these results.

## Provenance and reproduction

The snapshots retain exact approved plans, summaries with transport rows removed, per-task outcomes, and selected local audit receipts. Their original-source hashes bind the restricted originals. The public verifier checks snapshot hashes, plan identity, allocation, classifications, spend, and deterministic report generation. It does not independently repeat the journal audit or verify source files absent from a public clone.

Restricted journals, raw responses, screenshots, checkpoints, credentials, and operator paths are excluded. Historical Gemini/Qwen evidence remains in the [earlier report](../grounding-v5-d56-completed-calibrations-report.md).

From the repository root:

```sh
.venv/bin/python -m scripts.publish_grounding_v5_calibration_supplement --verify
```

The local audit receipts report immutable source bytes and completed journal validation. Selected local diagnostic receipts are retained as audit data, not independently recomputed by this public verifier. No new paid calls are authorized by publication.
