# D5.8 focus calibration continuation

Complete: **False**. Stop: `five_consecutive_non_normal_episodes`.

| Mode | Assigned | Attempted | Terminal successes | Reached both consumers | Correct first memory attempts / attempted |
|---|---:|---:|---:|---:|---:|
| history | 50 | 5 | 0 | 0 | 0 / 0 |
| stateless | 50 | 5 | 0 | 3 | 4 / 6 |

New phase: 67 wire requests; USD 0.302328000 known charges. Aggregate known charges: USD 6.233601525; unknown holds: USD 1.69356555; in-flight holds: USD 0. Shared ceiling: USD 28.00.

The cohort retains five original infrastructure failures; this continuation executes only the 95 untouched assignments. All episodes start at reset with model actions only. The task seeds, order, generator, delayed feedback and matched screenshot policies remain fixed. The focus cue and request-local transport match the completed diagnostic. Credential-free exception diagnostics add no retries or request changes. Earlier renderer cohorts remain separate; every failed and unrun assignment remains in the denominator.

Exposure means reaching memory consumers, separately from correctness and terminal success. Proposed calibration criteria are history exposure of at least 40/50 and terminal success between 20% and 80%; a positive or significant memory effect is not required. Incomplete observations cannot establish complete-cohort rates or confirmatory power. D5.8 final approval remains an owner decision.

[Stored summary](summary.json), [execution plan](execution-plan.json), [price snapshot](price-recheck.json). Provider responses and checkpoints remain in the ignored aggregate journal.
