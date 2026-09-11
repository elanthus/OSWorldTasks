# Completed calibration supplement

Generated from committed response-free snapshots. Calibration evidence only; no confirmatory score or human-gate verdict.

| Policy | Tasks | Success | Action-limit failures |
|---|---:|---:|---:|
| `C-mistral-small-4-stateful-v3-calibration` | 50 | 0 | 50 |
| `qwen-controlled-stateful-v2` | 50 | 0 | 50 |
| `qwen-controlled-stateless-v2` | 50 | 0 | 50 |

All three policies attempted the same fifty task IDs. The two Qwen arms are a matched memory comparison; both scored zero, so they do not establish a memory benefit. Mistral is a separate model/provider/runtime run and is not a controlled comparison with Qwen or historical Gemini.

## Spend and reliability

| Run | Calls | Unknown outcomes | Known USD | Reserved USD | Accounted USD |
|---|---:|---:|---:|---:|---:|
| mistral.json | 1446 | 15 | 0.19692492 | 0.03404025 | 0.23096517 |
| qwen-pair.json | 2903 | 40 | 0.798249790 | 0.066974934 | 0.865224724 |

Spend for Qwen covers both arms; it is not a per-arm estimate. Mistral retained fifteen unknown-charge reservations and completed every task. Its bounded retries recovered all transport failures; no task exhausted them. Older stopped runs are not pooled into these results.

## Provenance and reproduction

The snapshots retain exact approved plans, summaries with transport rows removed, per-task outcomes, and selected local audit receipts. Their original-source hashes bind the restricted originals. The public verifier checks snapshot hashes, plan identity, allocation, classifications, spend, and deterministic report generation. It does not independently repeat the journal audit or verify source files absent from a public clone.

Restricted journals, raw responses, screenshots, checkpoints, credentials, and operator paths are excluded. Historical Gemini/Qwen evidence remains in the [earlier report](../grounding-v5-d56-completed-calibrations-report.md).

From the repository root:

```sh
.venv/bin/python -m scripts.publish_grounding_v5_calibration_supplement --verify
```

The local audit receipts report immutable source bytes and completed journal validation. Mistral diagnostics show no critical decision entered; unentered decisions are not observed incorrect decisions. No new paid calls are authorized by publication.
