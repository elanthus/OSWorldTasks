# D5.8 focus-repaired end-to-end calibration

Complete: **False**. Stop: `five_consecutive_non_normal_episodes`.

| Mode | Assigned | Attempted | Terminal successes | Reached both consumers | Correct first memory attempts / attempted |
|---|---:|---:|---:|---:|---:|
| history | 50 | 3 | 0 | 0 | 0 / 0 |
| stateless | 50 | 2 | 0 | 1 | 1 / 2 |

New phase: 53 wire requests; USD 0.278438250 known charges. Aggregate known charges: USD 5.931273525; unknown holds: USD 1.52342730; in-flight holds: USD 0. Shared ceiling: USD 20.00.

All episodes start at reset with model actions only. The task seeds, order, generator, delayed feedback and matched screenshot policies remain fixed. The focus cue and request-local transport match the completed diagnostic. Historical cohorts remain separate; every failed and unrun assignment remains in the denominator.

Exposure means reaching memory consumers, separately from correctness and terminal success. Proposed calibration criteria are history exposure of at least 40/50 and terminal success between 20% and 80%; a positive or significant memory effect is not required. Incomplete observations cannot establish complete-cohort rates or confirmatory power. D5.8 final approval remains an owner decision.

[Stored summary](summary.json), [execution plan](execution-plan.json), [price snapshot](price-recheck.json). Provider responses and checkpoints remain in the ignored aggregate journal.
