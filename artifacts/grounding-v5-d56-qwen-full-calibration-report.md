# Qwen3-VL 8B v5 Calibration Report

**Status:** incomplete negative calibration evidence; not a benchmark score or milestone-gate verdict

This report describes the frozen `B-qwen-stateful-v2` run from its sealed, response-content-free derivative. The run retained all 50 assignments in the denominator and stopped at the first invalid output, as its approved plan required.

## Result and coverage

| Measure | Stored result |
|---|---:|
| Assigned tasks | 50 |
| Attempted tasks | 13 |
| Exact-success terminations | 0 / 13 attempted (0.0%) |
| Step-limit truncations | 12 |
| Invalid outputs | 1 |
| Unattempted assignments | 37 |
| Completed all assigned tasks | no |

The 0/13 attempted-task success rate is descriptive negative calibration evidence. It is not a complete-run or confirmatory score.

## Family breakdown

| Family | Assigned | Attempted | Success | Truncated | Invalid | Unattempted | Actions / attempted cap | Known cost | Median / P95 latency (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `deferred_join` | 8 | 8 | 0 | 8 | 0 | 0 | 228 / 228 | $0.082930120 | 946.0 / 1,325.6 |
| `conditional_precedence` | 8 | 5 | 0 | 4 | 1 | 3 | 121 / 140 | $0.042935841 | 954.4 / 1,338.7 |
| `revision_after_reveal` | 8 | 0 | 0 | 0 | 0 | 8 | 0 / 0 | $0.000000000 | n/a / n/a |
| `visible_error_recovery` | 9 | 0 | 0 | 0 | 0 | 9 | 0 / 0 | $0.000000000 | n/a / n/a |
| `review_and_commit` | 9 | 0 | 0 | 0 | 0 | 9 | 0 / 0 | $0.000000000 | n/a / n/a |
| `evidence_aggregation` | 8 | 0 | 0 | 0 | 0 | 8 | 0 / 0 | $0.000000000 | n/a / n/a |

## Action budget

| Measure | Stored result |
|---|---:|
| Assigned environment-action cap | 1431 |
| Attempted-task action cap | 368 |
| Committed environment actions | 349 |
| Attempted-cap utilization | 94.8% |

The 12 truncations consumed their full task horizons. The terminal invalid-output task committed nine actions before parsing failed at step 9.

## Provider latency and cost

Latency covers all completed response records and uses linear interpolation over sorted response latencies for P95.

| Measure | Stored result |
|---|---:|
| Provider wire requests | 350 |
| Completed responses | 350 |
| Unknown outcomes | 0 |
| HTTP 429 retry events | 0 |
| Mean response latency | 1,007.4 ms |
| Median response latency | 947.4 ms |
| P95 response latency | 1,330.4 ms |
| Maximum response latency | 2,143.4 ms |
| Qwen incremental spend | $0.125865961 |
| Known aggregate spend | $4.678631918 |
| Reserved prior Gemini exposure | $0.099532800 |
| Budget-accounted aggregate spend | $4.778164718 |
| Remaining shared cap | $5.221835282 |

## Terminal invalid output

Task `v5-2d305fda4e9ebe2d9075a384` stopped at step 9 with `JSONDecodeError`. The response contained 504 characters, of which 56 were non-trailing JSON whitespace. It began with an opening brace, contained 1 opening brace and 0 closing braces, while the provider reported `finish_reason=stop` and 192 completion tokens.

The approved retry rule covered confirmed HTTP 429 and exact zero-token, zero-cost error envelopes only. It did not permit retrying malformed JSON, so the runner retained the invalid response and stopped without replacing the task.

## Robustness-pair breakdown

The partition contains 6 complete logical twin pairs. 2 were attempted; 4 were not. All attempted pairs were concordant failures because neither twin succeeded.

| Logical pair | Family | Twin A | Twin B | Consistency |
|---|---|---|---|---|
| `calibration-conditional_precedence-logical-01` | `conditional_precedence` | `step_limit_truncation` (28 actions) | `step_limit_truncation` (28 actions) | `concordant` |
| `calibration-deferred_join-logical-01` | `deferred_join` | `step_limit_truncation` (28 actions) | `step_limit_truncation` (28 actions) | `concordant` |
| `calibration-evidence_aggregation-replacement-logical-00` | `evidence_aggregation` | `unattempted_due_to_prior_invalid_output_stop` (0 actions) | `unattempted_due_to_prior_invalid_output_stop` (0 actions) | `unattempted` |
| `calibration-review_and_commit-logical-01` | `review_and_commit` | `unattempted_due_to_prior_invalid_output_stop` (0 actions) | `unattempted_due_to_prior_invalid_output_stop` (0 actions) | `unattempted` |
| `calibration-revision_after_reveal-logical-01` | `revision_after_reveal` | `unattempted_due_to_prior_invalid_output_stop` (0 actions) | `unattempted_due_to_prior_invalid_output_stop` (0 actions) | `unattempted` |
| `calibration-visible_error_recovery-logical-01` | `visible_error_recovery` | `unattempted_due_to_prior_invalid_output_stop` (0 actions) | `unattempted_due_to_prior_invalid_output_stop` (0 actions) | `unattempted` |

## Failure routes

| Terminal route | Tasks |
|---|---:|
| `invalid_output` | 1 |
| `step_limit_truncation` | 12 |
| `unattempted_due_to_prior_invalid_output_stop` | 37 |

| Privileged diagnostic event | Count |
|---|---:|
| `correct_transition` | 13 |
| `invalid_text_value` | 1 |
| `missed_control` | 114 |
| `text_input_focused` | 221 |

## Task breakdown

| # | Task | Family | Band | Variant | Outcome | Actions / cap | Requests | Known cost | Median / P95 latency (ms) | Diagnostics |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|
| 0 | `v5-bfe5707f6b44202a0e7f493e` | `deferred_join` | `frontier` | `twin_a` | `step_limit_truncation` | 28 / 28 | 28 | $0.010089768 | 954.5 / 1,224.9 | `correct_transition`=1; `missed_control`=3; `text_input_focused`=24 |
| 1 | `v5-bd12a958c63d3d4a642f2388` | `deferred_join` | `frontier` | `twin_b` | `step_limit_truncation` | 28 / 28 | 28 | $0.010116301 | 937.6 / 1,111.9 | `correct_transition`=1; `invalid_text_value`=1; `missed_control`=12; `text_input_focused`=14 |
| 2 | `v5-d988bea3daaaee44bd4a66dd` | `deferred_join` | `frontier` | `base` | `step_limit_truncation` | 28 / 28 | 28 | $0.010036520 | 937.8 / 1,111.8 | `correct_transition`=1; `missed_control`=19; `text_input_focused`=8 |
| 3 | `v5-40c9c8dad25c35d18fcab3ac` | `deferred_join` | `frontier` | `base` | `step_limit_truncation` | 28 / 28 | 28 | $0.010058971 | 931.3 / 1,679.3 | `correct_transition`=1; `missed_control`=11; `text_input_focused`=16 |
| 4 | `v5-2dd66e920cec66fc7baa0340` | `deferred_join` | `frontier` | `base` | `step_limit_truncation` | 28 / 28 | 28 | $0.010083866 | 926.3 / 1,335.9 | `correct_transition`=1; `missed_control`=8; `text_input_focused`=19 |
| 5 | `v5-c8f7b5bf10a2be15e395f872` | `deferred_join` | `frontier` | `base` | `step_limit_truncation` | 28 / 28 | 28 | $0.010072101 | 945.2 / 1,322.9 | `correct_transition`=1; `missed_control`=4; `text_input_focused`=23 |
| 6 | `v5-f30e93f386d30cede2b72ce0` | `deferred_join` | `ceiling_probe` | `base` | `step_limit_truncation` | 30 / 30 | 30 | $0.011247834 | 995.1 / 1,250.5 | `correct_transition`=1; `missed_control`=11; `text_input_focused`=18 |
| 7 | `v5-0aafe18062befe07f24d8cab` | `deferred_join` | `ceiling_probe` | `base` | `step_limit_truncation` | 30 / 30 | 30 | $0.011224759 | 977.2 / 1,349.8 | `correct_transition`=1; `missed_control`=10; `text_input_focused`=19 |
| 8 | `v5-8b44ff7f55397462e532374f` | `conditional_precedence` | `frontier` | `twin_a` | `step_limit_truncation` | 28 / 28 | 28 | $0.010130536 | 989.8 / 1,181.9 | `correct_transition`=1; `missed_control`=10; `text_input_focused`=17 |
| 9 | `v5-a3b213b43f485af0c0e40c16` | `conditional_precedence` | `frontier` | `twin_b` | `step_limit_truncation` | 28 / 28 | 28 | $0.010093317 | 952.9 / 1,320.0 | `correct_transition`=1; `missed_control`=12; `text_input_focused`=15 |
| 10 | `v5-d0b04b9c0ddb6c2c020e0463` | `conditional_precedence` | `frontier` | `base` | `step_limit_truncation` | 28 / 28 | 28 | $0.010073037 | 937.0 / 1,312.0 | `correct_transition`=1; `missed_control`=2; `text_input_focused`=25 |
| 11 | `v5-cad52e15b441cb87019ae777` | `conditional_precedence` | `frontier` | `base` | `step_limit_truncation` | 28 / 28 | 28 | $0.010106850 | 934.4 / 1,281.6 | `correct_transition`=1; `missed_control`=9; `text_input_focused`=18 |
| 12 | `v5-2d305fda4e9ebe2d9075a384` | `conditional_precedence` | `frontier` | `base` | `invalid_output` | 9 / 28 | 10 | $0.002532101 | 1,001.0 / 1,702.1 | `correct_transition`=1; `missed_control`=3; `text_input_focused`=5 |
| 13 | `v5-29efce8f49cbc9384485d87b` | `conditional_precedence` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 14 | `v5-7e64b6bc5406e56f2c674749` | `conditional_precedence` | `ceiling_probe` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 30 | 0 | $0.000000000 | n/a / n/a | — |
| 15 | `v5-da795237a0d228c30efffdfa` | `conditional_precedence` | `ceiling_probe` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 30 | 0 | $0.000000000 | n/a / n/a | — |
| 16 | `v5-988e5eb5bd2f5d95773297ba` | `revision_after_reveal` | `frontier` | `twin_a` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 17 | `v5-3d7d660ccc1f9499de3def52` | `revision_after_reveal` | `frontier` | `twin_b` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 18 | `v5-f7334ee5d914b46715d56299` | `revision_after_reveal` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 19 | `v5-325495ed3572932e13e71595` | `revision_after_reveal` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 20 | `v5-6f12521c75f36647ae21ba6a` | `revision_after_reveal` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 21 | `v5-1a8a343b209ef6849bca6d5e` | `revision_after_reveal` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 22 | `v5-7c84fe9903d32bc16a62075a` | `revision_after_reveal` | `ceiling_probe` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 30 | 0 | $0.000000000 | n/a / n/a | — |
| 23 | `v5-7a5342a6b8bc0ccc70df72e8` | `revision_after_reveal` | `ceiling_probe` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 30 | 0 | $0.000000000 | n/a / n/a | — |
| 24 | `v5-321776e3a5121a93c5651938` | `visible_error_recovery` | `regression_canary` | `twin_b` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 27 | 0 | $0.000000000 | n/a / n/a | — |
| 25 | `v5-d439dd1631040477ef5a907b` | `visible_error_recovery` | `frontier` | `twin_a` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 29 | 0 | $0.000000000 | n/a / n/a | — |
| 26 | `v5-5852db880cf4dbdcaaf254bf` | `visible_error_recovery` | `frontier` | `twin_b` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 29 | 0 | $0.000000000 | n/a / n/a | — |
| 27 | `v5-5044beb30209bdf36a0a2d12` | `visible_error_recovery` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 29 | 0 | $0.000000000 | n/a / n/a | — |
| 28 | `v5-260bef26ef62a0d66cae0449` | `visible_error_recovery` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 29 | 0 | $0.000000000 | n/a / n/a | — |
| 29 | `v5-d98bd8f9f5ba030ec79201db` | `visible_error_recovery` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 29 | 0 | $0.000000000 | n/a / n/a | — |
| 30 | `v5-ae6332844ec7190db0b9bd18` | `visible_error_recovery` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 29 | 0 | $0.000000000 | n/a / n/a | — |
| 31 | `v5-dd7009a65e1bff896173378a` | `visible_error_recovery` | `ceiling_probe` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 32 | 0 | $0.000000000 | n/a / n/a | — |
| 32 | `v5-48860ad9b285908aa000a26b` | `visible_error_recovery` | `ceiling_probe` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 32 | 0 | $0.000000000 | n/a / n/a | — |
| 33 | `v5-25572b1551ed10079d842b8b` | `review_and_commit` | `regression_canary` | `twin_b` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 26 | 0 | $0.000000000 | n/a / n/a | — |
| 34 | `v5-8ed6a65fd1b37ddfb1390872` | `review_and_commit` | `frontier` | `twin_a` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 35 | `v5-e2e262766fce6bc662d505ff` | `review_and_commit` | `frontier` | `twin_b` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 36 | `v5-b9c743e4f89345b3397ffa1e` | `review_and_commit` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 37 | `v5-12d672051d0ecd6ff95c89ce` | `review_and_commit` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 38 | `v5-ebcf125c0c5332c245a09f0e` | `review_and_commit` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 39 | `v5-46c7d567496ada92c96cee32` | `review_and_commit` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 40 | `v5-af3926afbbb9f3ad94f668a0` | `review_and_commit` | `ceiling_probe` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 30 | 0 | $0.000000000 | n/a / n/a | — |
| 41 | `v5-c1e3ad39ecbaa0beb626a8ec` | `review_and_commit` | `ceiling_probe` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 30 | 0 | $0.000000000 | n/a / n/a | — |
| 42 | `v5-5eb145d169ebee7b66c83a99` | `evidence_aggregation` | `frontier` | `twin_a` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 43 | `v5-87b2f8109114f0dbf237649e` | `evidence_aggregation` | `frontier` | `twin_b` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 44 | `v5-33b66723d47aace8c622a202` | `evidence_aggregation` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 45 | `v5-ee24ce36d84d9fd23e9a997a` | `evidence_aggregation` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 46 | `v5-ac0f8e633af1f60a2646fd1a` | `evidence_aggregation` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 47 | `v5-2efb29a2ca94ec51c81c5018` | `evidence_aggregation` | `frontier` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 28 | 0 | $0.000000000 | n/a / n/a | — |
| 48 | `v5-289ad343e824d3ed28d36cbd` | `evidence_aggregation` | `ceiling_probe` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 30 | 0 | $0.000000000 | n/a / n/a | — |
| 49 | `v5-e4ba1dcff8d6223bcb69aa26` | `evidence_aggregation` | `ceiling_probe` | `base` | `unattempted_due_to_prior_invalid_output_stop` | 0 / 30 | 0 | $0.000000000 | n/a / n/a | — |

## Evidence and redaction

- Report schema: `pixelgym-agent-v5-d56-qwen-calibration-report-v1`
- Approved plan: `sha256:fc1f41d00df8c847d55765ea6af6b4c688463a893281f995f3eee3b770c43f7c`
- Restricted journal: `sha256:81e9137cb5c5074c3a0fd02f8f20ef7c2368ede9dbf95f1a92a1805a61818671`
- Journal event chain: `sha256:6117a1454d0cbe834a2b6a0f46cc0be8536ae44650b687a30ad1360c5ff0999d`
- Integrity audit: `sha256:38450081f871b77cf7d2721b6b0820062589cfa0498d6a9848732579235b7d56`
- Publishable derivative: `sha256:4a3f1faf42e186e29e093b2df135381ca1eaa4572edec8ba20a0a1bef19a74d5`
- [Publishable derivative](grounding-v5-d56-qwen-full-calibration-publishable.json)
- [Integrity audit](grounding-v5-d56-qwen-full-calibration-integrity-audit.json)
- [Publication relation](grounding-v5-d56-qwen-full-calibration-publication-relation.json)
- [V5 protocol](../plans/grounding-v5-agent-benchmark.md)

The derivative excludes provider response bodies and identifiers, request bodies, prompts, task instructions, screenshots, checkpoint contents, host paths, idempotency keys, and private transport metadata. The restricted journal remains local and is not part of this publication package.

## Limitations

- The approved run stopped after the first invalid output; 37 assignments were not attempted.
- The 13 attempted tasks produced no exact-success termination; 12 exhausted their full horizons.
- The terminal response was structurally incomplete despite a provider finish reason of stop; raw text remains restricted.
- No HTTP 429 occurred, so the bounded rate-limit retry path was not exercised by this run.
- Known aggregate spend excludes the prior Gemini request with unconfirmed charge status; budget-accounted spend retains its full reservation.
- Calibration evidence is separate from confirmatory evaluation and is not a final benchmark score.
- This single-policy successor does not satisfy the planned four-policy calibration comparison by itself.
- This report was generated from frozen calibration evidence without model calls; it does not reinterpret unattempted assignments as failures or successes.
- The human owner retains the D5.10 benchmark verdict and approval of public resume or README performance wording.
