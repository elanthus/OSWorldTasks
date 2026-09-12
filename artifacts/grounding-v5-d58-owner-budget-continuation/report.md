# D5.8 calibration with owner-authorized zero unresolved budget holds

Complete: **True**. Stop: `phase_time_stop`.

| Mode | Assigned | Attempted | Terminal successes | Reached both consumers | Correct first memory choices / attempted |
|---|---:|---:|---:|---:|---:|
| history | 50 | 50 | 42 | 43 | 85 / 86 |
| stateless | 50 | 50 | 6 | 45 | 36 / 90 |

New phase: 106 wire requests; USD 0.702578250 confirmed charges. Aggregate: USD 23.978227275 confirmed and USD 0 reserved for active requests; USD 23.978227275 accounted against USD 28. Unresolved outcomes carry USD 0 budget weight under the owner's instruction.

The owner checked OpenRouter activity and reports that failed calls are not billed. This accounting rule retains all unknown outcomes and original request bounds; it does not create provider-reported zero-cost receipts. Confirmed charges and active request bounds still count toward the cap.

All prior outcomes remain unchanged. This phase continues only untouched assignments with the same repaired curl transport, with an initial send and at most two same-request retries. Episodes start from reset with model actions only. Seeds, order, screenshots, generator, focus cue and delayed correctness are preserved. The six-hour limit starts at the original reliable continuation, including intervening setup time. Every failed and unrun assignment remains in the evidence.

Consumer exposure, first-choice correctness and terminal success are distinct measures. Supplied-state diagnostics add no episodes. No confirmatory tasks or final D5.8 verdict are included.

[Summary](summary.json), [execution plan](execution-plan.json), and [prices](price-recheck.json). Response envelopes and checkpoints remain in the ignored authoritative journal.
