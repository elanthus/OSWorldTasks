# D5.8 calibration with repaired transport

Complete: **False**. Stop: `phase_time_stop`.

| Mode | Assigned | Attempted | Terminal successes | Reached both consumers | Correct first memory choices / attempted |
|---|---:|---:|---:|---:|---:|
| history | 50 | 16 | 10 | 10 | 19 / 20 |
| stateless | 50 | 16 | 1 | 14 | 14 / 28 |

New phase: 570 wire requests; USD 4.232186775 confirmed charges and USD 1.39394100 new unknown holds. Aggregate: USD 10.565320050 confirmed, USD 3.08750655 held, USD 0 in flight; USD 13.652826600 accounted against USD 28.

The ten prior infrastructure failures remain unchanged. Only the 90 untouched assignments use the repaired curl transport, with an initial send and at most two same-request retries. All new episodes start from reset with model actions only. Seeds, order, screenshots, generator, focus cue and delayed correctness are preserved; the execution-version boundary is explicit. Raw attempts, failed episodes and unrun assignments remain in the evidence.

Consumer exposure, first-choice correctness and terminal success are distinct measures. Supplied-state diagnostics add no episodes. No confirmatory tasks or final D5.8 verdict are included.

[Summary](summary.json), [execution plan](execution-plan.json), and [prices](price-recheck.json). Response envelopes and checkpoints remain in the ignored authoritative journal.
