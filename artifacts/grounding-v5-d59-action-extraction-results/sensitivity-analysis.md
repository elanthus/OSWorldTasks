# D5.9 action-extraction sensitivity: D5.10 review

Generated from the audited, response-free projection using the frozen statistical functions. This is a post-hoc study on the same inspected tasks, not independent confirmation or a D5.10 verdict.

## Primary comparison

| Campaign | History successes | Stateless successes | Difference |
|---|---:|---:|---:|
| [historical_d59](../../artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.json) | 106/192 | 15/192 | 47.395833% |
| [original_replication](../../artifacts/grounding-v5-d59-replication-results/confirmatory-analysis.json) | 62/192 | 20/192 | 21.875000% |
| Parser sensitivity | 132/192 | 20/192 | 58.333333% |

These are separate campaigns. Cross-campaign differences are descriptive, not estimates of a causal parser effect. The original reporting rule and both original analyses remain unchanged.

Paired table: both success 15; history only 117; stateless only 5; both failure 55; 192 pairs.

Two-sided exact McNemar p = 8.13909046026e-29. History minus stateless = 58.333333%; 95% family-stratified logical-cluster bootstrap interval [50.802139%, 65.803109%]. Frozen mechanics: seed 20260911, 10000 resamples, 168 clusters, 6 strata. The inherited alpha 0.05 and 20-point minimum relevant difference are not a new confirmatory claim.

## Reliability and termination (separate denominators)

| Phase | Mode | Success | Step limit | Invalid | Episodes |
|---|---|---:|---:|---:|---:|
| primary | history | 132 | 58 | 2 | 192 |
| primary | stateless | 20 | 171 | 1 | 192 |
| reliability | history | 18 | 6 | 0 | 24 |
| reliability | stateless | 2 | 22 | 0 | 24 |

The reliability JSON includes repeat consistency on the inherited representative schedule. Neither reliability repeats nor the four pilot assignments enter the 192 primary pairs.

## Retained diagnostics and accounting

| Phase | Mode | First critical correct / attempted | Reached both / episodes | Usage observed / attempts |
|---|---|---:|---:|---:|
| primary | history | 313/320 | 162/192 | 4800/4804 |
| primary | stateless | 118/361 | 181/192 | 5308/5308 |
| reliability | history | 38/40 | 20/24 | 581/582 |
| reliability | stateless | 13/43 | 21/24 | 640/640 |

Unreached consumers are excluded from this conditional first-attempt diagnostic; invalid choices at reached consumers are retained. This does not replace episode success. Reported token totals are in JSON; no timeout token use is imputed. Trace diagnostics without frozen estimators remain unestimated.

Full: 432/432 completed; 11334 attempts/wires, 11326 actions, zero controls. Caps: {'environment_action_cap': 12134, 'model_attempt_cap': 24268, 'provider_control_request_cap': 0, 'provider_wire_request_cap': 24268}; 168 active hours. Active 42950.207 s; wall 44928.482 s; drained login wait excluded 1978.275 s.

Separate pilot: 4 assignments, 97 attempts; never pooled.

Stored extraction receipt audit: `{'actions_reparsed_or_rescored': 0, 'decisions': {'accepted': 11326, 'rejected': 3}, 'dispatched': 11326, 'envelopes': {'bare_json': 81, 'single_json_fence': 11247, 'unselected': 1}, 'extraction_applied': 390, 'parse_receipts': 11329, 'response_hashes_and_selected_spans_verified': True}`. No actions were reparsed or rescored.

## Five unresolved timeouts

| Trial | Step | Attempt | Local process | Provider completion / usage | Counted attempts at action |
|---|---:|---:|---|---|---:|
| `d59-extract-primary-6058-history-r0` | 26 | 0 | confirmed stopped | unknown / unknown | 2 |
| `d59-extract-primary-6130-history-r0` | 12 | 0 | confirmed stopped | unknown / unknown | 2 |
| `d59-extract-reliability-6034-history-r2` | 14 | 0 | confirmed stopped | unknown / unknown | 2 |
| `d59-extract-primary-6111-history-r0` | 10 | 0 | confirmed stopped | unknown / unknown | 2 |
| `d59-extract-primary-6145-history-r0` | 15 | 0 | confirmed stopped | unknown / unknown | 2 |

Post-hoc parser sensitivity on previously inspected tasks; not independent confirmation. Original results, policies, and denominators remain unchanged. Pilot excluded from all full denominators. All five confirmed-stopped post-send timeouts retain unknown provider completion and usage. Each received one counted same-request retry; both reservations remain. Login did not settle them. Strict identity and sandbox checks remained active. No model/auth call, task execution, action reparsing or response rescoring occurred in this audit/analysis. Token totals cover reported usage only. Subscription charge accounting is not a provider billing audit. Verified archive/backup copies share one local disk; no new restore drill or off-host backup. D5.10 and public wording remain human-owned.

Both retained copies were rehashed and SQLite integrity checked. Archive snapshots precede the final retention receipt in the working summary. Raw responses, screenshots and local paths stay private. Public projections reproduce the statistics but cannot replay private raw evidence.

D5.10 verdict: `null`. The owner decides the verdict, treatment of limitations, and any public wording.
