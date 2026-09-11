# D5.8 revised memory calibration pilot

This is a scripted-prefix, first-attempt memory diagnostic. It is not an end-to-end episode score.

| Condition | Assigned | Attempted | Correct first choices | Valid consumer choices |
|---|---:|---:|---:|---:|
| history | 10 | 10 | 9 | 9 |
| stateless | 10 | 10 | 6 | 10 |

Stop reason: `all_conditions_completed`.

Provider wire requests: **20**. Known spend: **USD 0.19008750**. Unknown-charge reservations: **USD 0**. In-flight reservations: **USD 0**. Aggregate ceiling: **USD 5.00**.

Every assigned condition remains in the stored summary, including invalid outputs, infrastructure failures and assignments not run. No retry or response-dependent task selection is permitted.

Both conditions use the same admitted consumer screen and scripted lead-in. The history condition receives the chronologically observed screenshots and executed actions; the stateless condition receives only the consumer screenshot. Condition order alternates by the frozen case index.

[Stored summary](summary.json), [execution approval and caps](execution-plan.json), [price recheck](price-recheck.json). Provider text and private checkpoints remain in the ignored authoritative journal. The ledger is shared with future D5.8 phases and must be preserved.

These ten paired diagnostic cases cannot estimate end-to-end consumer reachability, terminal success, or confirmatory power. A full calibration requires the next explicit approval; the final D5.8 freeze and confirmatory execution remain open.
