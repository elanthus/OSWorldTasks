# Behaviour cloning v2 (grid phase)

Generated from stored results `results.json`, recorded `2026-11-14T11:36:37.781181+00:00` at revision `72428d02a6d5066b83f8d6e3a299b294bb6da9af` (worktree clean: yes) by `scripts/run_bc_experiment_v2.py grid --processes 8`.

Host: Darwin 27.0.0 (arm64); Python 3.12.15, NumPy 1.26.4, torch 2.15.0; device `mps`. Total time 766.3 min.

## Sanity gates

| Gate | Result | Threshold | Passed |
| --- | --- | --- | --- |
| Overfit 30 training click frames (2000 steps) | 30/30 click targets correct | ≥ 29 | yes |
| One demonstration (seed 0, 113 frames, 5000 steps), replayed closed-loop on its own seed | success: yes; 7/7 fields, 113 steps | success | yes |
| Frame-to-action lookup table on the `name_holdout` test seeds | 50/50 episodes; 0 conflicting frames | all episodes | yes |
| Frame-to-action lookup table on the `standard` test seeds | 50/50 episodes; 0 conflicting frames | all episodes | yes |

All gates passed: yes.

## Split `name_holdout`

Training pool 256 episodes (28002 frames), validation 32 episodes (3500 frames), test 50 episodes (5267 frames). The test episodes use 12 distinct company names, 0 of which occur in the training pool.

Teacher-forced metrics are on the test frames (the oracle's own states); closed-loop columns are test episodes. Baselines are defined below the table.

| Policy | Action type | Click target | Key | Whole step | Successes | Fields |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BC, 4 demos, training seed 0 | 97.1% | 84.1% | 34.1% | 36.0% | 0/50 | 0.90/7 |
| BC, 4 demos, training seed 1 | 97.5% | 84.3% | 32.4% | 34.5% | 0/50 | 0.86/7 |
| BC, 16 demos, training seed 0 | 98.5% | 84.6% | 38.9% | 41.2% | 0/50 | 0.90/7 |
| BC, 16 demos, training seed 1 | 98.8% | 82.4% | 37.5% | 40.0% | 0/50 | 0.88/7 |
| BC, 64 demos, training seed 0 | 99.0% | 83.5% | 46.1% | 48.0% | 0/50 | 0.96/7 |
| BC, 64 demos, training seed 1 | 99.1% | 85.1% | 44.0% | 46.5% | 0/50 | 0.60/7 |
| BC, 256 demos, training seed 0 | 99.0% | 82.4% | 48.6% | 50.3% | 0/50 | 0.78/7 |
| BC, 256 demos, training seed 1 | 99.2% | 85.9% | 47.2% | 49.2% | 0/50 | 1.14/7 |
| Baseline: always key | 93.0% | 0.0% | 7.1% | 6.6% | 0/50 | 0.60/7 |
| Baseline: constant click | 7.0% | 13.5% | 1.0% | 0.9% | 0/50 | 0.60/7 |
| Baseline: card blind | 100.0% | 0.0% | 48.4% | 45.0% | — | — |
| Baseline: lookup table | 100.0% | 100.0% | 100.0% | 100.0% | 50/50 | 7.00/7 |
| Baseline: oracle | 100.0% | 100.0% | 100.0% | 100.0% | 50/50 | — |

- **always key**: KEY with the most frequent training key on every frame.
- **constant click**: CLICK on the most frequent training click cell on every frame.
- **card blind**: oracle action type; for KEY, the most frequent training continuation of the keys already typed into the clicked control; every click wrong. Uses privileged form state, never the request card, so it has no closed-loop rollout.
- **lookup table**: frame digest -> oracle action, built on the test demonstrations.
- **oracle**: the scripted oracle that produced the demonstrations.

## Split `standard`

Training pool 256 episodes (27762 frames), validation 32 episodes (3468 frames), test 50 episodes (5451 frames). The test episodes use 29 distinct company names, 29 of which occur in the training pool.

Teacher-forced metrics are on the test frames (the oracle's own states); closed-loop columns are test episodes. Baselines are defined below the table.

| Policy | Action type | Click target | Key | Whole step | Successes | Fields |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BC, 4 demos, training seed 0 | 97.4% | 86.7% | 36.4% | 38.8% | 0/50 | 0.82/7 |
| BC, 4 demos, training seed 1 | 97.3% | 86.2% | 33.5% | 35.8% | 0/50 | 0.76/7 |
| BC, 16 demos, training seed 0 | 98.6% | 86.4% | 47.4% | 49.3% | 0/50 | 0.72/7 |
| BC, 16 demos, training seed 1 | 99.2% | 83.5% | 43.8% | 46.0% | 0/50 | 0.90/7 |
| BC, 64 demos, training seed 0 | 99.5% | 86.2% | 52.7% | 54.6% | 0/50 | 0.90/7 |
| BC, 64 demos, training seed 1 | 99.2% | 83.2% | 51.4% | 53.0% | 0/50 | 1.06/7 |
| BC, 256 demos, training seed 0 | 99.5% | 83.8% | 56.0% | 57.7% | 0/50 | 0.96/7 |
| BC, 256 demos, training seed 1 | 99.5% | 84.6% | 55.0% | 56.7% | 0/50 | 0.96/7 |
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
| `standard-n4-seed0-steps6000` | 427 | 6000 | 6000 | 37.2% | 47.3 min | `13738e197149d304…` | 14961441 |
| `standard-n4-seed1-steps6000` | 427 | 6000 | 5000 | 35.8% | 48.2 min | `2f7281c12471214c…` | 14961441 |
| `standard-n16-seed0-steps6000` | 1748 | 6000 | 6000 | 49.9% | 48.5 min | `38af84ee5e308036…` | 14961485 |
| `standard-n16-seed1-steps6000` | 1748 | 6000 | 6000 | 47.7% | 47.3 min | `b3b83aa0c3642f0b…` | 14961485 |
| `standard-n64-seed0-steps6000` | 6964 | 6000 | 6000 | 55.5% | 46.8 min | `b547e49daec588b8…` | 14961485 |
| `standard-n64-seed1-steps6000` | 6964 | 6000 | 6000 | 53.3% | 43.7 min | `5365afbccbc9bdc0…` | 14961485 |
| `standard-n256-seed0-steps6000` | 27762 | 6000 | 6000 | 59.3% | 41.9 min | `db8ea1488ecb38e5…` | 14961529 |
| `standard-n256-seed1-steps6000` | 27762 | 6000 | 6000 | 59.1% | 45.4 min | `d2ae4713cd1bb572…` | 14961529 |
| `name_holdout-n4-seed0-steps6000` | 427 | 6000 | 6000 | 42.8% | 45.1 min | `1fe47dab273afdd7…` | 14961617 |
| `name_holdout-n4-seed1-steps6000` | 427 | 6000 | 6000 | 41.7% | 39.4 min | `54d7bc1dce5c9c80…` | 14961617 |
| `name_holdout-n16-seed0-steps6000` | 1762 | 6000 | 6000 | 51.3% | 38.7 min | `b89867e92b71a5a0…` | 14961661 |
| `name_holdout-n16-seed1-steps6000` | 1762 | 6000 | 6000 | 51.3% | 38.4 min | `bc748e2aae43c6dc…` | 14961661 |
| `name_holdout-n64-seed0-steps6000` | 7012 | 6000 | 6000 | 55.4% | 38.4 min | `7a76be1526d13c49…` | 14961661 |
| `name_holdout-n64-seed1-steps6000` | 7012 | 6000 | 6000 | 54.4% | 38.5 min | `ca78fd401afdc994…` | 14961661 |
| `name_holdout-n256-seed0-steps6000` | 28002 | 6000 | 6000 | 59.9% | 38.7 min | `bf4b966c5270a556…` | 14961705 |
| `name_holdout-n256-seed1-steps6000` | 28002 | 6000 | 6000 | 57.1% | 38.7 min | `0638e0130986e2ee…` | 14961705 |

Per-frame test predictions are stored under `predictions/`, with the test frames' oracle actions in `test-frames-<split>.json`. Weights are not checked in.

## Limits

- Design: the policy sees one grayscale 384x512 frame per step, with no memory, and is trained only on the oracle's own states (no recovery data).
- Design: checkpoints are selected on validation seeds every 500 steps; there was no hyperparameter search, and test seeds were used only for the reported numbers.
- Design: clicks are predicted on a 64x48 grid of 16-pixel cells and executed at the cell centre.
- The generator has 48 company names. In the standard split, 50 of 50 test episodes use a name that also occurs in the training pool.
- In the name-holdout split, 0 of 50 test episodes use a training name. Of the 12 held-out names, 12 share their prefix and 12 their suffix with a training name.
- In 15 of 16 runs the selected checkpoint was the last validation evaluation.
- Each configuration ran with 2 training seed(s) on one host (`mps`).
- The card-blind baseline uses privileged form state and has no closed-loop rollout.
