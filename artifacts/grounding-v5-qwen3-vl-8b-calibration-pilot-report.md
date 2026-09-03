# PixelGym v5 Qwen3-VL calibration-pilot report

**Status:** completed bounded pilot; local review derivative

**Model and route:** `qwen/qwen3-vl-8b-instruct` through OpenRouter, Alibaba only

## Bound execution

- Approved plan digest: `sha256:fe6e9b03fd5b4c13d417596d1712073e2d375de03711a3aeca6e04cf2f55fd7a`
- Bound source revision: `5619c120683c9650e715d85ee14e1ea23dd90269`
- Calibration tasks attempted: 10 across all six workflow families
- Action limit: 2 per task
- Model-attempt reservations: 20 of 20
- Provider wire requests: 20 of 20
- Provider control requests: 0 of 0
- Retries and replacement tasks: 0
- Confirmatory tasks exposed: 0

Every task reached the planned two-action boundary. No transport, provider-identity, price, parse,
action-validation, dispatch, or evidence-integrity failure triggered an early stop.

## Observed actions

All twenty responses produced one exact parsed action and one accepted dispatch.

| Step within task | Attempts | Backend event | Visible error | Irreversible failure |
|---:|---:|---|---:|---:|
| 1 | 10 | `correct_transition` | 0 | 0 |
| 2 | 10 | `text_input_focused` | 0 | 0 |

All parsed actions were clicks. The first click made the required initial transition for every task;
the second click focused a text input for every task. The pilot intentionally stopped before text
entry and therefore did not measure episode completion or success reward.

## Provider and cost evidence

- Responses attributed to Alibaba: 20 of 20
- Price guard: `ok` for 20 of 20
- Prompt tokens: 31,020
- Completion tokens: 698
- Total tokens: 31,718
- Pilot incremental spend: $0.003946930
- Aggregate spend including prior diagnostics: $0.004228237
- Approved aggregate cap: $5.00
- Remaining cap after this evidence: $4.995771763

Observed request latency was 913.6–1,683.9 ms, with a 1,022.2 ms median and a 1,571.8 ms nearest-rank
p95 over twenty requests.

## Evidence integrity

- Journal objects verified: 185
- Journal events: 150
- Event-chain digest: `sha256:2ac576b2296298681f2de00bbaea495296fe1c133f821c4ae470b0107b4507e7`
- Summary SHA-256: `d2895f3dba6bda96c515990930547b1ea74ed012e651b1456a0c4ca3526d1d0b`
- SQLite journal SHA-256: `2ddc0a6f20eb7b72221c88c898860da972e0a7903bf1c0781d99ecfc9397583a`
- Cleanup recorded: journal closed; policy and environments closed

The integrity report was recomputed from the stored journal after execution and matched the summary.

## Interpretation boundary

This pilot supports the provider integration, coordinate adapter, stateful two-action path, durable
evidence capture, and spend controls for the selected policy. It does not estimate full-episode
success, compare policies, expose the confirmatory partition, or constitute the complete four-policy
D5.6 calibration. D4.12 and D5.6 verdicts remain human-owned.

The authoritative canonical provider responses remain only in the local SQLite journal. Do not
publish that journal. This report intentionally contains no raw provider response.
