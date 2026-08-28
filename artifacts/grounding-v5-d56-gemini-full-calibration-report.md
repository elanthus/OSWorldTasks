# Gemini 3.7 Flash v5 Calibration Report

**Status:** incomplete calibration evidence; not a benchmark score or milestone-gate verdict

This report describes the frozen `A-gemini-stateful-v2` successor run using only its sealed evidence and publishable derivative. The run stopped after an unknown provider outcome, so missing assignments remain visible and no denominator is changed.

## Result and coverage

| Measure | Stored result |
|---|---:|
| Assigned tasks | 50 |
| Attempted tasks | 41 |
| Exact-success terminations | 29 / 41 attempted (70.7%) |
| Step-limit truncations | 11 |
| Infrastructure failures | 1 |
| Unattempted assignments | 9 |
| Completed all assigned tasks | no |

The attempted-task percentage is descriptive calibration evidence, not a complete-run or confirmatory score. In particular, the run contains no observations for `evidence_aggregation`.

## Family breakdown

| Family | Assigned | Attempted | Success | Truncated | Infrastructure | Unattempted | Actions / attempted cap | Known cost | Median latency (ms) | P95 latency (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `deferred_join` | 8 | 8 | 6 | 2 | 0 | 0 | 216 / 228 | $0.437237625 | 4,489.0 | 12,137.1 |
| `conditional_precedence` | 8 | 8 | 6 | 2 | 0 | 0 | 205 / 228 | $0.403198500 | 4,707.6 | 12,512.4 |
| `revision_after_reveal` | 8 | 8 | 7 | 1 | 0 | 0 | 213 / 228 | $0.404624625 | 4,505.8 | 9,040.7 |
| `visible_error_recovery` | 9 | 9 | 5 | 4 | 0 | 0 | 244 / 265 | $0.469521750 | 5,060.6 | 11,615.4 |
| `review_and_commit` | 9 | 8 | 5 | 2 | 1 | 1 | 189 / 224 | $0.349537125 | 4,393.5 | 9,770.4 |
| `evidence_aggregation` | 8 | 0 | 0 | 0 | 0 | 8 | 0 / 0 | $0.000000000 | n/a | n/a |

## Action budget

| Measure | Count |
|---|---:|
| Assigned environment-action cap | 1431 |
| Action cap for attempted tasks | 1173 |
| Committed environment actions | 1067 |
| Attempted-cap utilization | 91.0% |

Step-limit truncations consumed their full task horizons. Successful episodes could terminate earlier; task `v5-af3926afbbb9f3ad94f668a0` committed 20 actions before its next provider attempt became an unknown outcome.

## Provider latency and cost

Latency uses completed response records only. P95 uses linear interpolation over sorted response latencies. The unknown request has no retained latency value.

| Measure | Stored result |
|---|---:|
| Provider wire requests | 1068 |
| Completed provider responses | 1067 |
| Unknown provider outcomes | 1 |
| Measured latency count | 1067 |
| Known latency sum | 6,015,125.1 ms |
| Mean response latency | 5,637.4 ms |
| Median response latency | 4,664.5 ms |
| P95 response latency | 11,275.9 ms |
| Maximum response latency | 53,835.7 ms |
| Known incremental calibration spend | $2.064119625 |
| Known aggregate spend | $4.552765957 |
| Recorded remaining shared cap | $5.447234043 |
| Possible additional charge | 1 unknown request(s); unconfirmed |

## Robustness-pair breakdown

The frozen 50-task successor partition contains 6 complete logical twin pairs. 5 were attempted and 1 were unattempted. Among attempted pairs, 0 were concordant and 5 were discordant. This is descriptive evidence of variant sensitivity; the run does not establish its cause.

| Logical pair | Family | Twin A | Twin B | Consistency |
|---|---|---|---|---|
| `calibration-conditional_precedence-logical-01` | `conditional_precedence` | `success_termination` (23 actions) | `step_limit_truncation` (28 actions) | `discordant` |
| `calibration-deferred_join-logical-01` | `deferred_join` | `success_termination` (25 actions) | `step_limit_truncation` (28 actions) | `discordant` |
| `calibration-evidence_aggregation-replacement-logical-00` | `evidence_aggregation` | `unattempted_due_to_prior_infrastructure_stop` (0 actions) | `unattempted_due_to_prior_infrastructure_stop` (0 actions) | `unattempted` |
| `calibration-review_and_commit-logical-01` | `review_and_commit` | `success_termination` (22 actions) | `step_limit_truncation` (28 actions) | `discordant` |
| `calibration-revision_after_reveal-logical-01` | `revision_after_reveal` | `success_termination` (28 actions) | `step_limit_truncation` (28 actions) | `discordant` |
| `calibration-visible_error_recovery-logical-01` | `visible_error_recovery` | `success_termination` (24 actions) | `step_limit_truncation` (29 actions) | `discordant` |

2 additional twin-labelled record(s) have no counterpart in this successor partition and are excluded from the pair-consistency denominator.

## Failure routes

| Terminal route | Tasks |
|---|---:|
| `infrastructure_failure` | 1 |
| `step_limit_truncation` | 11 |
| `success_termination` | 29 |
| `unattempted_due_to_prior_infrastructure_stop` | 9 |

The committed-action diagnostic stream contained:

| Privileged diagnostic event | Count |
|---|---:|
| `correct_transition` | 329 |
| `entered_declared_recovery` | 5 |
| `incorrect_choice` | 36 |
| `key_without_focus` | 20 |
| `missed_control` | 1 |
| `text_input_focused` | 602 |
| `text_value_accepted` | 69 |
| `visible_error_repaired` | 5 |

The infrastructure route occurred on task `v5-af3926afbbb9f3ad94f668a0` at step 20. The journal retained `provider_request_unknown`, the transport retained `TimeoutError`, and neither a response nor usage was persisted. Its charge status remains unconfirmed.

## Task breakdown

Latency and cost are sums or distributions of the provider records assigned to each task. A measured-response count smaller than requests indicates an unknown outcome.

| # | Task | Family | Band | Variant | Outcome | Actions / cap | Requests / responses | Known cost | Median / P95 latency (ms) | Diagnostic events |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|
| 0 | `v5-bfe5707f6b44202a0e7f493e` | `deferred_join` | `frontier` | `twin_a` | `success_termination` | 25 / 28 | 25 / 25 | $0.051039750 | 5,201.5 / 11,745.8 | `correct_transition`=10; `key_without_focus`=1; `text_input_focused`=12; `text_value_accepted`=2 |
| 1 | `v5-bd12a958c63d3d4a642f2388` | `deferred_join` | `frontier` | `twin_b` | `step_limit_truncation` | 28 / 28 | 28 / 28 | $0.051716250 | 4,036.2 / 5,908.7 | `correct_transition`=1; `text_input_focused`=27 |
| 2 | `v5-d988bea3daaaee44bd4a66dd` | `deferred_join` | `frontier` | `base` | `success_termination` | 25 / 28 | 25 / 25 | $0.047820000 | 4,160.4 / 8,395.4 | `correct_transition`=10; `key_without_focus`=1; `text_input_focused`=12; `text_value_accepted`=2 |
| 3 | `v5-40c9c8dad25c35d18fcab3ac` | `deferred_join` | `frontier` | `base` | `success_termination` | 24 / 28 | 24 / 24 | $0.044212875 | 4,404.4 / 7,620.4 | `correct_transition`=10; `text_input_focused`=12; `text_value_accepted`=2 |
| 4 | `v5-2dd66e920cec66fc7baa0340` | `deferred_join` | `frontier` | `base` | `success_termination` | 28 / 28 | 28 / 28 | $0.059808000 | 4,820.5 / 10,396.4 | `correct_transition`=10; `key_without_focus`=1; `missed_control`=1; `text_input_focused`=14; `text_value_accepted`=2 |
| 5 | `v5-c8f7b5bf10a2be15e395f872` | `deferred_join` | `frontier` | `base` | `success_termination` | 26 / 28 | 26 / 26 | $0.049235625 | 3,984.5 / 18,666.0 | `correct_transition`=10; `text_input_focused`=14; `text_value_accepted`=2 |
| 6 | `v5-f30e93f386d30cede2b72ce0` | `deferred_join` | `ceiling_probe` | `base` | `success_termination` | 30 / 30 | 30 / 30 | $0.059921625 | 4,534.8 / 8,941.2 | `correct_transition`=12; `key_without_focus`=1; `text_input_focused`=15; `text_value_accepted`=2 |
| 7 | `v5-0aafe18062befe07f24d8cab` | `deferred_join` | `ceiling_probe` | `base` | `step_limit_truncation` | 30 / 30 | 30 / 30 | $0.073483500 | 5,348.9 / 14,159.9 | `correct_transition`=3; `incorrect_choice`=14; `text_input_focused`=11; `text_value_accepted`=2 |
| 8 | `v5-8b44ff7f55397462e532374f` | `conditional_precedence` | `frontier` | `twin_a` | `success_termination` | 23 / 28 | 23 / 23 | $0.039696000 | 4,605.3 / 45,785.7 | `correct_transition`=10; `key_without_focus`=1; `text_input_focused`=10; `text_value_accepted`=2 |
| 9 | `v5-a3b213b43f485af0c0e40c16` | `conditional_precedence` | `frontier` | `twin_b` | `step_limit_truncation` | 28 / 28 | 28 / 28 | $0.053412750 | 4,761.2 / 6,989.7 | `correct_transition`=3; `text_input_focused`=24; `text_value_accepted`=1 |
| 10 | `v5-d0b04b9c0ddb6c2c020e0463` | `conditional_precedence` | `frontier` | `base` | `success_termination` | 23 / 28 | 23 / 23 | $0.042750000 | 5,297.2 / 9,941.5 | `correct_transition`=10; `key_without_focus`=1; `text_input_focused`=10; `text_value_accepted`=2 |
| 11 | `v5-cad52e15b441cb87019ae777` | `conditional_precedence` | `frontier` | `base` | `success_termination` | 25 / 28 | 25 / 25 | $0.049804875 | 4,691.6 / 13,158.0 | `correct_transition`=10; `text_input_focused`=13; `text_value_accepted`=2 |
| 12 | `v5-2d305fda4e9ebe2d9075a384` | `conditional_precedence` | `frontier` | `base` | `step_limit_truncation` | 28 / 28 | 28 / 28 | $0.066855375 | 6,033.1 / 16,357.2 | `correct_transition`=3; `incorrect_choice`=11; `key_without_focus`=1; `text_input_focused`=11; `text_value_accepted`=2 |
| 13 | `v5-29efce8f49cbc9384485d87b` | `conditional_precedence` | `frontier` | `base` | `success_termination` | 24 / 28 | 24 / 24 | $0.046714875 | 3,782.5 / 10,624.6 | `correct_transition`=10; `incorrect_choice`=1; `key_without_focus`=1; `text_input_focused`=10; `text_value_accepted`=2 |
| 14 | `v5-7e64b6bc5406e56f2c674749` | `conditional_precedence` | `ceiling_probe` | `base` | `success_termination` | 26 / 30 | 26 / 26 | $0.049529625 | 4,322.2 / 9,090.2 | `correct_transition`=12; `key_without_focus`=1; `text_input_focused`=11; `text_value_accepted`=2 |
| 15 | `v5-da795237a0d228c30efffdfa` | `conditional_precedence` | `ceiling_probe` | `base` | `success_termination` | 28 / 30 | 28 / 28 | $0.054435000 | 4,315.5 / 9,224.4 | `correct_transition`=12; `key_without_focus`=1; `text_input_focused`=13; `text_value_accepted`=2 |
| 16 | `v5-988e5eb5bd2f5d95773297ba` | `revision_after_reveal` | `frontier` | `twin_a` | `success_termination` | 28 / 28 | 28 / 28 | $0.052214250 | 4,176.5 / 8,325.3 | `correct_transition`=10; `key_without_focus`=1; `text_input_focused`=15; `text_value_accepted`=2 |
| 17 | `v5-3d7d660ccc1f9499de3def52` | `revision_after_reveal` | `frontier` | `twin_b` | `step_limit_truncation` | 28 / 28 | 28 / 28 | $0.056181750 | 4,976.5 / 7,527.4 | `correct_transition`=3; `text_input_focused`=24; `text_value_accepted`=1 |
| 18 | `v5-f7334ee5d914b46715d56299` | `revision_after_reveal` | `frontier` | `base` | `success_termination` | 22 / 28 | 22 / 22 | $0.038157000 | 4,164.3 / 8,276.5 | `correct_transition`=10; `text_input_focused`=10; `text_value_accepted`=2 |
| 19 | `v5-325495ed3572932e13e71595` | `revision_after_reveal` | `frontier` | `base` | `success_termination` | 28 / 28 | 28 / 28 | $0.055165875 | 4,074.2 / 9,168.0 | `correct_transition`=10; `text_input_focused`=16; `text_value_accepted`=2 |
| 20 | `v5-6f12521c75f36647ae21ba6a` | `revision_after_reveal` | `frontier` | `base` | `success_termination` | 23 / 28 | 23 / 23 | $0.039953250 | 4,914.1 / 6,807.0 | `correct_transition`=10; `text_input_focused`=11; `text_value_accepted`=2 |
| 21 | `v5-1a8a343b209ef6849bca6d5e` | `revision_after_reveal` | `frontier` | `base` | `success_termination` | 28 / 28 | 28 / 28 | $0.054406875 | 4,732.5 / 9,146.9 | `correct_transition`=10; `text_input_focused`=16; `text_value_accepted`=2 |
| 22 | `v5-7c84fe9903d32bc16a62075a` | `revision_after_reveal` | `ceiling_probe` | `base` | `success_termination` | 27 / 30 | 27 / 27 | $0.050666250 | 4,436.2 / 8,823.3 | `correct_transition`=12; `key_without_focus`=1; `text_input_focused`=12; `text_value_accepted`=2 |
| 23 | `v5-7a5342a6b8bc0ccc70df72e8` | `revision_after_reveal` | `ceiling_probe` | `base` | `success_termination` | 29 / 30 | 29 / 29 | $0.057879375 | 5,198.5 / 10,891.8 | `correct_transition`=12; `text_input_focused`=15; `text_value_accepted`=2 |
| 24 | `v5-321776e3a5121a93c5651938` | `visible_error_recovery` | `regression_canary` | `twin_b` | `step_limit_truncation` | 27 / 27 | 27 / 27 | $0.051920625 | 4,931.1 / 7,214.0 | `correct_transition`=1; `text_input_focused`=26 |
| 25 | `v5-d439dd1631040477ef5a907b` | `visible_error_recovery` | `frontier` | `twin_a` | `success_termination` | 24 / 29 | 24 / 24 | $0.044381250 | 4,775.3 / 23,648.2 | `correct_transition`=9; `entered_declared_recovery`=1; `key_without_focus`=1; `text_input_focused`=10; `text_value_accepted`=2; `visible_error_repaired`=1 |
| 26 | `v5-5852db880cf4dbdcaaf254bf` | `visible_error_recovery` | `frontier` | `twin_b` | `step_limit_truncation` | 29 / 29 | 29 / 29 | $0.059305125 | 5,660.5 / 10,653.0 | `correct_transition`=1; `text_input_focused`=28 |
| 27 | `v5-5044beb30209bdf36a0a2d12` | `visible_error_recovery` | `frontier` | `base` | `step_limit_truncation` | 29 / 29 | 29 / 29 | $0.056422125 | 5,104.8 / 9,147.8 | `correct_transition`=3; `text_input_focused`=25; `text_value_accepted`=1 |
| 28 | `v5-260bef26ef62a0d66cae0449` | `visible_error_recovery` | `frontier` | `base` | `success_termination` | 25 / 29 | 25 / 25 | $0.045342375 | 5,009.4 / 9,768.3 | `correct_transition`=9; `entered_declared_recovery`=1; `key_without_focus`=1; `text_input_focused`=11; `text_value_accepted`=2; `visible_error_repaired`=1 |
| 29 | `v5-d98bd8f9f5ba030ec79201db` | `visible_error_recovery` | `frontier` | `base` | `success_termination` | 28 / 29 | 28 / 28 | $0.049824000 | 4,566.3 / 13,761.2 | `correct_transition`=9; `entered_declared_recovery`=1; `text_input_focused`=15; `text_value_accepted`=2; `visible_error_repaired`=1 |
| 30 | `v5-ae6332844ec7190db0b9bd18` | `visible_error_recovery` | `frontier` | `base` | `step_limit_truncation` | 29 / 29 | 29 / 29 | $0.062533875 | 6,345.6 / 12,676.4 | `correct_transition`=3; `incorrect_choice`=10; `key_without_focus`=1; `text_input_focused`=13; `text_value_accepted`=2 |
| 31 | `v5-dd7009a65e1bff896173378a` | `visible_error_recovery` | `ceiling_probe` | `base` | `success_termination` | 26 / 32 | 26 / 26 | $0.048165375 | 4,908.8 / 9,252.6 | `correct_transition`=11; `entered_declared_recovery`=1; `text_input_focused`=11; `text_value_accepted`=2; `visible_error_repaired`=1 |
| 32 | `v5-48860ad9b285908aa000a26b` | `visible_error_recovery` | `ceiling_probe` | `base` | `success_termination` | 27 / 32 | 27 / 27 | $0.051627000 | 5,051.8 / 9,768.0 | `correct_transition`=11; `entered_declared_recovery`=1; `key_without_focus`=1; `text_input_focused`=11; `text_value_accepted`=2; `visible_error_repaired`=1 |
| 33 | `v5-25572b1551ed10079d842b8b` | `review_and_commit` | `regression_canary` | `twin_b` | `step_limit_truncation` | 26 / 26 | 26 / 26 | $0.051042750 | 5,284.2 / 12,204.5 | `correct_transition`=1; `text_input_focused`=25 |
| 34 | `v5-8ed6a65fd1b37ddfb1390872` | `review_and_commit` | `frontier` | `twin_a` | `success_termination` | 22 / 28 | 22 / 22 | $0.039615750 | 4,607.9 / 14,007.6 | `correct_transition`=10; `text_input_focused`=10; `text_value_accepted`=2 |
| 35 | `v5-e2e262766fce6bc662d505ff` | `review_and_commit` | `frontier` | `twin_b` | `step_limit_truncation` | 28 / 28 | 28 / 28 | $0.055126500 | 4,913.4 / 6,471.5 | `correct_transition`=1; `text_input_focused`=27 |
| 36 | `v5-b9c743e4f89345b3397ffa1e` | `review_and_commit` | `frontier` | `base` | `success_termination` | 23 / 28 | 23 / 23 | $0.040383750 | 4,124.3 / 6,446.6 | `correct_transition`=10; `key_without_focus`=1; `text_input_focused`=10; `text_value_accepted`=2 |
| 37 | `v5-12d672051d0ecd6ff95c89ce` | `review_and_commit` | `frontier` | `base` | `success_termination` | 23 / 28 | 23 / 23 | $0.043246500 | 3,977.3 / 8,696.7 | `correct_transition`=10; `key_without_focus`=1; `text_input_focused`=10; `text_value_accepted`=2 |
| 38 | `v5-ebcf125c0c5332c245a09f0e` | `review_and_commit` | `frontier` | `base` | `success_termination` | 24 / 28 | 24 / 24 | $0.042997125 | 4,069.2 / 8,281.1 | `correct_transition`=10; `key_without_focus`=1; `text_input_focused`=11; `text_value_accepted`=2 |
| 39 | `v5-46c7d567496ada92c96cee32` | `review_and_commit` | `frontier` | `base` | `success_termination` | 23 / 28 | 23 / 23 | $0.041522625 | 3,977.7 / 8,667.4 | `correct_transition`=10; `key_without_focus`=1; `text_input_focused`=10; `text_value_accepted`=2 |
| 40 | `v5-af3926afbbb9f3ad94f668a0` | `review_and_commit` | `ceiling_probe` | `base` | `infrastructure_failure` | 20 / 30 | 21 / 20 | $0.035602125 | 4,271.2 / 9,266.3 | `correct_transition`=7; `text_input_focused`=11; `text_value_accepted`=2 |
| 41 | `v5-c1e3ad39ecbaa0beb626a8ec` | `review_and_commit` | `ceiling_probe` | `base` | `unattempted_due_to_prior_infrastructure_stop` | 0 / 30 | 0 / 0 | $0.000000000 | n/a / n/a | — |
| 42 | `v5-5eb145d169ebee7b66c83a99` | `evidence_aggregation` | `frontier` | `twin_a` | `unattempted_due_to_prior_infrastructure_stop` | 0 / 28 | 0 / 0 | $0.000000000 | n/a / n/a | — |
| 43 | `v5-87b2f8109114f0dbf237649e` | `evidence_aggregation` | `frontier` | `twin_b` | `unattempted_due_to_prior_infrastructure_stop` | 0 / 28 | 0 / 0 | $0.000000000 | n/a / n/a | — |
| 44 | `v5-33b66723d47aace8c622a202` | `evidence_aggregation` | `frontier` | `base` | `unattempted_due_to_prior_infrastructure_stop` | 0 / 28 | 0 / 0 | $0.000000000 | n/a / n/a | — |
| 45 | `v5-ee24ce36d84d9fd23e9a997a` | `evidence_aggregation` | `frontier` | `base` | `unattempted_due_to_prior_infrastructure_stop` | 0 / 28 | 0 / 0 | $0.000000000 | n/a / n/a | — |
| 46 | `v5-ac0f8e633af1f60a2646fd1a` | `evidence_aggregation` | `frontier` | `base` | `unattempted_due_to_prior_infrastructure_stop` | 0 / 28 | 0 / 0 | $0.000000000 | n/a / n/a | — |
| 47 | `v5-2efb29a2ca94ec51c81c5018` | `evidence_aggregation` | `frontier` | `base` | `unattempted_due_to_prior_infrastructure_stop` | 0 / 28 | 0 / 0 | $0.000000000 | n/a / n/a | — |
| 48 | `v5-289ad343e824d3ed28d36cbd` | `evidence_aggregation` | `ceiling_probe` | `base` | `unattempted_due_to_prior_infrastructure_stop` | 0 / 30 | 0 / 0 | $0.000000000 | n/a / n/a | — |
| 49 | `v5-e4ba1dcff8d6223bcb69aa26` | `evidence_aggregation` | `ceiling_probe` | `base` | `unattempted_due_to_prior_infrastructure_stop` | 0 / 30 | 0 / 0 | $0.000000000 | n/a / n/a | — |

## Evidence and redaction

- Report schema: `pixelgym-agent-v5-d56-gemini-calibration-report-v1`
- Approved plan: `sha256:68469a8057cbfa4e380f40e7bb27f2264c49b638aa5951ff7ac93866e8dc1e38`
- Restricted journal: `sha256:99d6520bc24ab6dc2515d70d4977aed19602dfa6fa87f0e4102d61c583557c82`
- Journal event chain: `sha256:9c21971b4ac2878aef796a9a6fd8afa878a173f0f7095aeea986d51960458134`
- Integrity audit: `sha256:26c6ba1fa0d02d1a35bc4a6b987303995869230ed0d50711118274a2225ce6ac`
- Publishable derivative: `sha256:8193d556fe10e327e62603b6e3621f46b7134bd8c9b49d7e6aef6dc4ededcb67`
- [Publishable derivative](grounding-v5-d56-gemini-full-calibration-publishable.json)
- [Integrity audit](grounding-v5-d56-gemini-full-calibration-integrity-audit.json)
- [Publication relation](grounding-v5-d56-gemini-full-calibration-publication-relation.json)
- [V5 protocol](../plans/grounding-v5-agent-benchmark.md)

The derivative excludes provider response bodies and identifiers, request bodies, prompts, task instructions, screenshots, checkpoint contents, host paths, idempotency keys, and private transport metadata. The restricted 1.3 GB SQLite journal remains local and is not part of this publication package.

## Limitations

- The run stopped after one unknown provider outcome; nine assigned tasks were not attempted.
- All evidence_aggregation tasks and one review_and_commit task were unattempted.
- Known spend excludes any unconfirmed charge for the unknown provider outcome.
- Latency summaries exclude the unknown request because it has no retained latency value.
- Calibration results are separate from confirmatory evaluation and are not a final benchmark score.
- This single-policy successor run does not satisfy the planned four-policy calibration comparison by itself.
- The report is generated from the frozen calibration evidence; it performs no model calls and does not reinterpret missing assignments as failures or successes.
- The human owner retains the D5.10 benchmark verdict and approval of any public resume or README wording.
