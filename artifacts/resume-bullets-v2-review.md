# Resume Bullets — Qualified Review Candidate

These bullets update scope qualifications for portfolio review. The historical Day 3 artifact
remains byte-for-byte unchanged at [`resume-bullets.md`](resume-bullets.md) because its digest is
part of the retained [owner-gate evidence](day-3/raw/human-gate.json). This file is proposed wording
for review; it does not declare a new milestone or publication gate.

- Developed an RL-environment validation pipeline covering
  [10 deterministic fast-backend resets](validation-report.json),
  [5 same-host real OSWorld resets](day-2-rev-2026-09-06-issues-95-101/raw/real-reset.json),
  [122 reward-timing trajectories](day-2/raw/reward-timing.json),
  [500 sampled actions](day-2/raw/space-integrity.json), and
  [14 adversarial reward-hacking surfaces](day-2-rev-2026-09-06-issues-95-101/raw/reward-hacking.json)
  for one deterministic synthetic form, with structured evidence.

- Built a Gymnasium-compatible, screenshot-only GUI environment with a backend pinned to
  [OSWorld-V2 `v2026.06.24`](validation-report.json), bounded coordinate/keystroke actions,
  privileged exact-state sparse reward, and deterministic seeded tasks; recorded a
  [112-action real episode](day-2/raw/real-golden-episode.json) whose only positive reward followed
  exact terminal submission.

- Built a [100-example paired GUI-grounding benchmark](grounding-report.md) on one 1024×768
  synthetic layout and measured the moving `gpt-5.4-mini` alias at
  [56% raw-coordinate versus 100% set-of-marks accuracy, a +44.0-point difference with 95% CI
  [+35.0, +54.0]](grounding-results.json). The marks condition used target-agnostic,
  DOM-derived control locations collected offline: the model selected a mark ID and the adapter
  supplied its stored center coordinates. The frozen allocation confounds target with screen state
  and does not establish broader GUI, model, or end-to-end pixel-proposal performance.
