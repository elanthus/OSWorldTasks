# PR196 Luna and Haiku calibration

All 100 final assignments per model have terminal outcomes. These are calibration results, not a confirmatory benchmark, model ranking, or human-gate verdict. The task bank stays frozen at PR196 revision `1e5d9c0d19acf51505919deefe0d155c2ab22b26`.

## Final outcomes

| Model / condition | Episodes | Successes | Step limits | Invalid outputs |
|---|---:|---:|---:|---:|
| luna / history | 50 | 46 | 4 | 0 |
| luna / stateless | 50 | 8 | 42 | 0 |
| haiku / history | 50 | 31 | 14 | 5 |
| haiku / stateless | 50 | 5 | 44 | 1 |

The final-outcome view retains every normal terminal result, including invalid output. Only missing or infrastructure-interrupted assignments were restarted under explicit owner approval. It is a continuation view, not an intention-to-treat estimate of a single fixed policy. Earlier interrupted attempts remain below and in the snapshots.

## Paired outcomes

| Model / subset | Pairs | Both succeed | History only | Stateless only | Neither |
|---|---:|---:|---:|---:|---:|
| luna / all seeds | 50 | 8 | 38 | 0 | 4 |
| luna / designated representatives | 44 | 8 | 32 | 0 | 4 |
| haiku / all seeds | 50 | 2 | 29 | 3 | 16 |
| haiku / designated representatives | 44 | 2 | 24 | 3 | 15 |

The fifty seed pairs contain 44 logical clusters. Representatives come from the pre-existing Gemini calibration analysis, not from these outcomes. No significance test, new power analysis, or independent-sample claim is made.

## Memory observations

| Model / condition | Reached both consumers | Correct first choices / attempted | Valid first choices | Actions | Model attempts |
|---|---:|---:|---:|---:|---:|
| luna / history | 46/50 | 92/92 | 92 | 1190 | 1190 |
| luna / stateless | 50/50 | 39/100 | 100 | 1388 | 1388 |
| haiku / history | 36/50 | 73/73 | 73 | 1206 | 1213 |
| haiku / stateless | 44/50 | 33/88 | 88 | 1415 | 1417 |

First-choice denominators count attempted memory choices, including attempts without a valid choice. Episodes that never reach a consumer contribute to the episode denominator but cannot supply a choice observation. These measurements come from stored host checkpoints and dispatches; they are not model self-reports.

## Cohorts and reliability

| Cohort | Recorded / assigned | History terminal successes / terminal episodes | Stateless terminal successes / terminal episodes | Elapsed minutes |
|---|---:|---:|---:|---:|
| [luna-original](luna-original.json) | 91/100 | 42/45 | 8/45 | 358.5 |
| [luna-continuation](luna-continuation.json) | 10/10 | 4/5 | 0/5 | 29.5 |
| [haiku-v4](haiku-v4.json) | 32/100 | 10/15 | 1/16 | 237.5 |
| [haiku-cli-continuation](haiku-cli-continuation.json) | 47/69 | 15/24 | 3/22 | 343.2 |
| [haiku-openrouter](haiku-openrouter.json) | 23/23 | 6/11 | 1/12 | 88.0 |
| [haiku-v1](haiku-v1.json) | 1/100 | 0/0 | 0/0 | 0.0 |
| [haiku-v2](haiku-v2.json) | 1/100 | 0/0 | 0/0 | 0.1 |
| [haiku-v3](haiku-v3.json) | 100/100 | 0/50 | 0/50 | 12.9 |

Haiku v1 stopped on isolated authentication; v2 stopped on stream parsing. Haiku v3 recorded 100 invalid outputs before the versioned whole-JSON-fence parser repair. Those outcomes are preserved separately and have not been rescored. The owner approved a fresh v4 run; the final Haiku view starts there.

The Luna continuation adds one retry after a CLI timeout whose process is confirmed stopped. Haiku’s CLI continuation uses the same repair; its last interruption was malformed CLI telemetry. The OpenRouter successor allows one recorded transient-transport retry and keeps unknown charges reserved. Earlier source versions and policy identities remain in each cohort snapshot.

| Model | Interrupted attempt | Classification |
|---|---|---|
| luna | 5148 / stateless (luna-original) | phase_time_stop |
| haiku | 5143 / history (haiku-v4) | infrastructure_failure |
| haiku | 5138 / stateless (haiku-cli-continuation) | infrastructure_failure |

These interrupted attempts are excluded only from the final replacement view. `results.json` also reports all attempted episodes in the continuation chain, without deleting failures; their repeated seeds are not independent observations.

## Provider comparability and cost

Luna uses Codex CLI with medium effort. Haiku’s retained CLI outcomes use Claude Code with default effort; the final 23 assignments use direct Anthropic through OpenRouter with API thinking enabled and no native effort parameter. Different wrappers, prompts added by the CLIs, retry settings, token limits and provider aliases prevent treating this as a controlled model ranking. Haiku’s provider switch was selected for unfinished assignments, not randomized.

Haiku’s 77 retained CLI terminal outcomes and 23 OpenRouter outcomes remain separately identifiable. Pair provenance, including pairs whose two arms crossed cohorts, is available in `results.json`. The OpenRouter subset is not an independent full-panel replication.

OpenRouter reported **USD 7.458311** in confirmed charges, plus **USD 0.440960** reserved for 2 unknown outcomes: **USD 7.899271** accounted against the approved USD 20 cap. There are no in-flight reservations. The 664 wire requests include the recorded retries. Reservations are conservative budget accounting, not confirmed bills.

CLI runs recorded zero incremental experiment charges under existing subscriptions. That excludes subscription fees and is not a zero inference-cost claim. CLI cost telemetry is not pooled with API bills. Cohort elapsed times include setup, provider waits and failures, so they are not model-latency comparisons.

## Historical Gemini context

The [earlier Gemini report](../grounding-v5-d58-owner-budget-continuation/report.md) and [final-design package](../../plans/grounding-v5-d58-final-design.md) remain unchanged. They retain infrastructure failures in their denominator, unlike this explicitly labelled final-outcome continuation view. Do not pool the cohorts or directly compare their percentages as if they shared execution and failure handling.

## Provenance, verification and remaining work

Each response-free snapshot binds the original plan and summary digests, exact policy manifests, adapter revision, assignments, all recorded outcomes, and private audit receipt. Adapter commits are included in this review branch; benchmark generator/backend/history source bytes are checked against PR196. No confirmatory tasks were generated or inspected.

The private audit opens SQLite read-only, verifies every stored-object digest and the event-chain digest, matches result events to summaries, remeasures exposure and memory choices, checks success against host dispatches, verifies CLI invocation receipts, and reconstructs the OpenRouter ledger. It does not rerun an episode or reinterpret a previously invalid response. It is not a full independent replay of all request construction, parser behavior, or backend evaluation.

The public verifier checks snapshot hashes, assignment identities, continuation coverage, retained invalid outcomes, model-panel equality, and deterministic report generation. It cannot repeat the private journal audit without the restricted journals. Raw responses, screenshots, checkpoint contents, credentials and operator paths are excluded.

```sh
.venv/bin/python -m scripts.publish_pr196_calibration --verify
```

To repeat the private audit, supply `--export /path/to/cohort-directories.json`, a local mapping from the eight cohort IDs above to their original directories. Export requires original adapter commits and journals; verification does not require provider access. The output is deterministic and makes zero model calls.

Next is the D5.8 owner decision on candidate policy, consistent execution route, sample size and budget. This report does not select them, authorize paid execution, change difficulty, declare a milestone, or complete v5 serving.
