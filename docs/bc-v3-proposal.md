# Proposal: behaviour cloning v3 (one change: train to convergence)

- **Status:** proposal only, written 2026-11-15. Nothing here has been run. It changes no layout,
  resolution, task, action space, or observation; those would be scope changes.
- **Evidence:** the [BC v2 diagnosis](../artifacts/bc-diagnosis-v1/REPORT.md) and its probes, all
  from stored predictions and digest-verified stored weights, plus three probes under 30 minutes
  each (the convergence probe ran 32 minutes in total; see its provenance row).

## What the evidence rules in and out

| Candidate direction | Evidence | Supported as the v3 change? |
| --- | --- | --- |
| Full-resolution or cropped input because the card is unreadable | A small classifier reads one card character from a crop at the policy's 384x512 resolution with 100.0% held-out accuracy (3,449 crops from validation seeds), the same as at full resolution. The characters survive downsampling. | No. Legibility is not the bottleneck. A crop could still help the model *find* the right character, which this probe does not test (it is told where the character is). |
| Recovery data from the oracle (DAgger) | In all 16 runs, all 50 closed-loop episodes first go wrong on a Company-name keystroke (800 of 800; position 0–18, and 796 of them at position 7 or earlier; per-run median step 1–2), with no closed-loop frame drift before it. The first error happens on the oracle's own states. | No. Compounding distribution shift does not cause the first error. This evidence does not establish whether recovery data would help: later errors, or failure to recover after the first one, could still contribute to the 0/800 result. |
| A short frame history | The current frame already shows what has been typed into the focused field, which is what a key depends on. | No. |
| Longer training | 15 of 16 v2 runs selected their last checkpoint. Continuing `standard-n256-seed0` from step 6,000 raised validation step accuracy from 59.3% to 69.3% by step 9,000 (69.2% at 9,500); it stopped on the wall-clock cap, not a plateau. On test, scored once afterwards, step accuracy went from 57.7% to 68.1% and key accuracy from 56.0% to 67.3%. Both are still below the card-blind baseline (75.7% step, 81.3% key), and the closed loop stayed at 0/50. The key head can fit one episode's 105 key frames (99.0% from scratch, 100% from the stored weights). | **Yes**, as the only direction with direct positive evidence. The evidence does not say it will be enough. |

The diagnosis also locates the failure. Keys whose correct value differs from the card-blind
guess, so the card must be read, are right 15.6% (standard) and 29.3% (name holdout) of the time
for the n=256 runs. Keys that match the guess are right 65.3% and 69.2% of the time. At the first
character of a field the policy beats card-blind in both splits (78.4% vs 67.2% and 76.4% vs
68.8%), so it reads the card a little. Click errors concentrate on card-dependent choices (payment term,
expedited checkbox). In both standard n=256 runs every text-field and country click is correct;
Submit is 50/50 and 47/50.

## v3 design

Exactly one change from v2: the step budget and stopping rule. Everything else is copied from
`scripts/run_bc_experiment_v2.py` `Config` unchanged: model, 384x512 grayscale input, 64x48 click
grid, loss, AdamW at 3e-4 with weight decay 1e-4, batch 64, splits, seeds, validation every 500
steps, and best-validation checkpoint selection.

| Setting | v2 | v3 |
| --- | --- | --- |
| Configurations | 4 demo counts × 2 seeds × 2 splits | 256 demos × 2 training seeds × 2 splits (4 runs) |
| Steps | 6,000 fixed | up to 60,000, from scratch (no warm start, so no optimizer restart) |
| Stopping | none | stop when validation step accuracy has not improved for 6 evaluations (3,000 steps), or at 60,000 |
| Selection | best validation step accuracy | unchanged |
| Test use | once per run, after training | unchanged. The stopping rule uses validation only. The v2 test results and the convergence probe's test results informed the choice to train longer, and the test seeds were already scored, so v3 test numbers on these seeds are a comparison with v2, not a fresh confirmation. |
| New reported metric | — | key accuracy on keys whose correct value is not the card-blind guess, from stored per-frame predictions |

Everything else in the report is unchanged, including both baselines, the lookup table, closed
loop on all 50 test episodes per split, and first wrong action per episode.

## Sanity gates (must pass before any test frame is scored)

1. The three v2 gates, unchanged: overfit 30 click frames; the one-demonstration closed-loop
   replay succeeds; the frame-to-action lookup table completes every test episode.
2. Copy gate: from scratch, the key loss alone fits one training episode's key frames to at least
   99% (the probe reached 99.0% in 1,500 full-batch steps).
3. Data identity: the demonstration producer fingerprint and the train/validation/test seed lists
   equal v2's, so v3 differs from v2 only in the step budget.
4. Curve sanity: each run's validation curve over its first 6,000 steps is recorded next to v2's
   for the same configuration. It is reported, not gated, because MPS training is not bitwise
   reproducible.

## Expected wall-clock time

Measured on this host (Apple Silicon, MPS):
- one process alone ran at most about 0.51 s per step including validation (convergence probe: 1,799.5 s over 3,500 steps; that time also includes test scoring, because training time alone was not recorded);
- the v2 grid ran about 0.42 s per step per process with 8 processes in parallel (6,000 steps in about 42 min).

| Case | Steps per run | Four runs in parallel |
| --- | ---: | ---: |
| Early stop near the probe's flattening point | about 10,000 | about 1.5 h |
| Early stop at 30,000 | 30,000 | about 4 h |
| Cap | 60,000 | about 8 h |

Demonstration generation and evaluation add about 15 minutes. No paid model call is involved.

## What v3 can and cannot show

It can show whether the v2 null was mostly a training-length artifact: whether key accuracy,
especially on card-dependent keys, rises past the card-blind baseline, and whether any test
episode completes. A null or negative result is a fine outcome. If card-dependent key accuracy
stays low after convergence, the evidence would point next at helping the model locate the right
card character, for example aligned crops of the card row and the focused field. That is a
separate proposal and is not part of v3.

## Not decided here

Whether to run v3, and the host and time window, are the owner's decisions.
