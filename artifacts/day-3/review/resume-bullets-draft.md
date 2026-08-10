# Resume Bullets — Review Draft

> Not approved for public use. Every completed number below links to stored evidence; the grounding
> bullet retains placeholders until the full paired report is generated and reviewed.

- Developed an RL-environment validation pipeline covering 10 deterministic fast-backend resets,
  5 real OSWorld resets, 122 reward-timing trajectories, 500 sampled actions, and 14 adversarial
  reward-hacking surfaces, with structured evidence and a human release gate
  ([validation report](../../validation-report.json)).

- Built a Gymnasium-compliant, pixel-only GUI environment on OSWorld-V2 `v2026.06.24` with bounded
  coordinate/keystroke actions, execution-based sparse rewards, deterministic seeded tasks, and a
  recorded 112-action real episode whose only positive reward occurred on exact terminal submission
  ([real episode](../../day-2/raw/real-golden-episode.json)).

- Created a 100-example GUI-grounding benchmark and measured a **[DELTA]-point** raw-versus-set-of-
  marks accuracy difference with a paired 95% confidence interval of **[[LOW], [HIGH]]** using
  **[MODEL/DATE]**; proposal coverage was **[COVERAGE]** and is reported separately from conditional
  mark-selection accuracy (**pending `artifacts/grounding-results.json`**).

