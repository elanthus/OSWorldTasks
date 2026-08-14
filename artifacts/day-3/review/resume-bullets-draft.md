# Resume Bullets — Review Draft

> Not approved for public use. Every completed number below links to stored evidence.

- Developed an RL-environment validation pipeline covering 10 deterministic fast-backend resets,
  5 real OSWorld resets, 122 reward-timing trajectories, 500 sampled actions, and 14 adversarial
  reward-hacking surfaces, with structured evidence and a human release gate
  ([validation report](../../validation-report.json)).

- Built a Gymnasium-compliant, pixel-only GUI environment on OSWorld-V2 `v2026.06.24` with bounded
  coordinate/keystroke actions, execution-based sparse rewards, deterministic seeded tasks, and a
  recorded 112-action real episode whose only positive reward occurred on exact terminal submission
  ([real episode](../../day-2/raw/real-golden-episode.json)).

- Created a 100-example GUI-grounding benchmark and measured `gpt-5.4-mini` at 56% raw-coordinate
  versus 100% set-of-marks accuracy (+44.0 points; paired-bootstrap 95% CI [+35.0, +54.0]), with
  100% proposal coverage reported separately from 100% conditional mark-selection accuracy
  ([grounding results](../../grounding-results.json)).
