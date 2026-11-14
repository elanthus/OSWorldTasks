# Behaviour cloning v2 (small phase)

Generated from stored results `results.json`, recorded `2026-11-13T22:09:30.598203+00:00` at revision `a042fdcec5441f3320341cf1e52261ca1a7e8a65` (worktree clean: yes) by `scripts/run_bc_experiment_v2.py small --output-dir artifacts/bc-experiment-v2/small-2 --processes 8`.

Host: Darwin 27.0.0 (arm64); Python 3.12.15, NumPy 1.26.4, torch 2.15.0; device `mps`. Total time 66.7 min.

## Sanity gates

| Gate | Result | Threshold | Passed |
| --- | --- | --- | --- |
| Overfit 30 training click frames (2000 steps) | 30/30 click targets correct | ≥ 29 | yes |
| One demonstration (seed 0, 113 frames, 5000 steps), replayed closed-loop on its own seed | success: yes; 7/7 fields, 113 steps | success | yes |
| Frame-to-action lookup table on the `standard` test seeds | 50/50 episodes; 0 conflicting frames | all episodes | yes |

All gates passed: yes.

## Split `standard`

Training pool 256 episodes (27762 frames), validation 32 episodes (3468 frames), test 50 episodes (5451 frames). The test episodes use 29 distinct company names, 29 of which occur in the training pool.

Teacher-forced metrics are on the test frames (the oracle's own states); closed-loop columns are test episodes. Baselines are defined below the table.

| Policy | Action type | Click target | Key | Whole step | Successes | Fields |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BC, 16 demos, training seed 0 | 97.7% | 85.1% | 27.3% | 30.3% | 0/50 | 1.12/7 |
| Baseline: always key | 93.1% | 0.0% | 6.9% | 6.4% | 0/50 | 0.48/7 |
| Baseline: constant click | 6.9% | 13.3% | 1.1% | 0.9% | 0/50 | 0.48/7 |
| Baseline: card blind | 100.0% | 0.0% | 81.3% | 75.7% | — | — |
| Baseline: lookup table | 100.0% | 100.0% | 100.0% | 100.0% | 50/50 | 7.00/7 |
| Baseline: oracle | 100.0% | 100.0% | 100.0% | 100.0% | 50/50 | — |

- **always key**: KEY with the most frequent training key on every frame.
- **constant click**: CLICK on the most frequent training click cell on every frame.
- **card blind**: oracle action type; for KEY, the most frequent training continuation of the keys already typed into the clicked control; every click wrong. Uses privileged form state, never the request card, so it has no closed-loop rollout.
- **lookup table**: frame digest -> oracle action, built on the test demonstrations.
- **oracle**: the scripted oracle that produced the demonstrations.

## Runs

| Run | Frames | Steps | Best validation step | Validation step acc. | Train time | Weights SHA-256 | Weights bytes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `standard-n16-seed0-steps2000` | 1748 | 2000 | 2000 | 30.7% | 16.4 min | `1b0febcf37f08e54…` | 14961485 |

Per-frame test predictions are stored under `predictions/`, with the test frames' oracle actions in `test-frames-<split>.json`. Weights are not checked in.

## Limits

- Design: the policy sees one grayscale 384x512 frame per step, with no memory, and is trained only on the oracle's own states (no recovery data).
- Design: checkpoints are selected on validation seeds every 500 steps; there was no hyperparameter search, and test seeds were used only for the reported numbers.
- Design: clicks are predicted on a 64x48 grid of 16-pixel cells and executed at the cell centre.
- The generator has 48 company names. In the standard split, 50 of 50 test episodes use a name that also occurs in the training pool.
- This phase did not run the name-holdout split.
- In 1 of 1 runs the selected checkpoint was the last validation evaluation.
- Each configuration ran with 1 training seed(s) on one host (`mps`).
- The card-blind baseline uses privileged form state and has no closed-loop rollout.
